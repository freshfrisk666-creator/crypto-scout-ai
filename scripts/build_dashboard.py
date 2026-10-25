from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ccxt

from database.db import (
    closed_positions,
    get_cash,
    latest_scan_results,
    open_positions,
)

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD_DIR = ROOT / "dashboard"
OUTPUT_PATH = DASHBOARD_DIR / "data.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return default
        return number
    except (TypeError, ValueError):
        return default


def row_dict(row: Any) -> dict[str, Any]:
    return dict(row)


def get_current_prices(config: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, float]:
    if not positions:
        return {}

    market = config.get("market", {})
    exchange_name = str(market.get("exchange", "binance")).lower()
    if not hasattr(ccxt, exchange_name):
        return {}

    exchange_class = getattr(ccxt, exchange_name)
    exchange = exchange_class({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    prices: dict[str, float] = {}

    # At most three open positions are expected, so individual public ticker
    # calls keep this dashboard operation simple and bounded.
    for position in positions:
        symbol = str(position.get("symbol", ""))
        if not symbol:
            continue
        try:
            ticker = exchange.fetch_ticker(symbol)
            price = ticker.get("last") or ticker.get("close")
            if price is not None and as_float(price) > 0:
                prices[symbol] = as_float(price)
        except Exception as exc:
            print(f"Dashboard price unavailable for {symbol}: {exc}")

    return prices


def build_dashboard(
    config: dict[str, Any] | None = None,
    state: dict[str, Any] | None = None,
) -> Path:
    """Export current paper account and trade history to static JSON."""
    if config is None:
        from agents.scanner import load_config
        config = load_config()
    if state is None:
        state_path = ROOT / "data" / "actions_state.json"
        if state_path.exists():
            with state_path.open("r", encoding="utf-8") as file:
                state = json.load(file)
        else:
            state = {}

    strategy = config.get("strategy", {})
    starting_cash = as_float(strategy.get("starting_cash", 10000.0), 10000.0)
    cash = get_cash()

    open_rows = [row_dict(row) for row in open_positions()]
    closed_rows = [row_dict(row) for row in closed_positions()]
    signals = [row_dict(row) for row in latest_scan_results()]
    current_prices = get_current_prices(config, open_rows)

    now = datetime.now(timezone.utc)
    fee_rate_default = as_float(config.get("costs", {}).get("fee_rate_per_side", 0.001), 0.001)
    slippage_default = as_float(config.get("costs", {}).get("slippage_rate_per_side", 0.0005), 0.0005)

    positions_for_json: list[dict[str, Any]] = []
    unrealized_total = 0.0
    invested_basis = 0.0

    for position in open_rows:
        shares = as_float(position.get("shares"))
        entry_price = as_float(position.get("entry_price"))
        entry_fill = as_float(position.get("entry_fill_price"), entry_price) or entry_price
        entry_fee = as_float(position.get("entry_fee"))
        committed = as_float(position.get("cash_committed"), as_float(position.get("notional")))
        fee_rate = as_float(position.get("fee_rate_per_side"), fee_rate_default)
        slippage_rate = as_float(position.get("slippage_rate_per_side"), slippage_default)
        symbol = str(position.get("symbol", ""))
        current_price = current_prices.get(symbol)
        unrealized = None

        if current_price is not None and shares > 0:
            estimated_exit_fill = current_price * (1.0 - slippage_rate)
            estimated_exit_fee = estimated_exit_fill * shares * fee_rate
            unrealized = (
                (estimated_exit_fill - entry_fill) * shares
                - entry_fee
                - estimated_exit_fee
            )
            unrealized_total += unrealized

        invested_basis += committed
        entry_time = str(position.get("entry_time", ""))
        age_hours = None
        try:
            parsed_entry = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
            if parsed_entry.tzinfo is None:
                parsed_entry = parsed_entry.replace(tzinfo=timezone.utc)
            age_hours = max(0.0, (now - parsed_entry.astimezone(timezone.utc)).total_seconds() / 3600.0)
        except (TypeError, ValueError):
            pass

        positions_for_json.append({
            "id": position.get("id"),
            "ticker": position.get("ticker"),
            "symbol": symbol,
            "entry_time": entry_time,
            "entry_price": entry_price,
            "current_price": current_price,
            "shares": shares,
            "notional": as_float(position.get("notional")),
            "stop_price": as_float(position.get("stop_price")),
            "target_price": as_float(position.get("target_price")),
            "risk_amount": position.get("risk_amount"),
            "entry_fee": entry_fee,
            "unrealized_pnl_net_estimate": unrealized,
            "technical_score": as_float(position.get("technical_score")),
            "age_hours": age_hours,
            "max_holding_hours": as_float(strategy.get("max_holding_hours", 48), 48),
        })

    closed_rows_sorted = sorted(
        closed_rows,
        key=lambda item: (str(item.get("exit_time") or ""), as_float(item.get("id"))),
        reverse=True,
    )
    recent_closed = closed_rows_sorted[:25]

    realized_values = [as_float(item.get("realized_pnl")) for item in closed_rows]
    realized_pnl = sum(realized_values)
    winners = sum(1 for value in realized_values if value > 0)
    losers = sum(1 for value in realized_values if value < 0)
    closed_count = len(closed_rows)
    win_rate = (winners / closed_count * 100.0) if closed_count else 0.0

    # Mark-to-market equity = available cash + the original cash committed
    # for each open position + estimated net unrealized P&L (where priced).
    equity = cash + invested_basis + unrealized_total
    total_return_pct = ((equity / starting_cash) - 1.0) * 100.0 if starting_cash else 0.0

    payload = {
        "project": "Crypto Scout AI",
        "version": str(config.get("project", {}).get("version", "1.1.0")),
        "mode": "PAPER",
        "updated_at": utc_now_iso(),
        "last_scan_at": state.get("last_scan_at"),
        "last_scan_status": state.get("last_scan_status", "never_run"),
        "account": {
            "starting_cash": starting_cash,
            "available_cash": cash,
            "equity_estimate": equity,
            "realized_pnl_net": realized_pnl,
            "unrealized_pnl_net_estimate": unrealized_total,
            "total_return_pct_estimate": total_return_pct,
            "open_positions": len(open_rows),
            "closed_positions": closed_count,
            "max_positions": min(
                int(strategy.get("max_positions", 3)),
                int(config.get("portfolio", {}).get("max_positions", strategy.get("max_positions", 3))),
            ),
        },
        "performance": {
            "trades_closed": closed_count,
            "winners": winners,
            "losers": losers,
            "win_rate_pct": win_rate,
            "average_closed_pnl": (realized_pnl / closed_count) if closed_count else 0.0,
        },
        "open_positions": positions_for_json,
        "recent_closed_positions": recent_closed,
        "latest_eligible_signals": signals,
        "configuration": {
            "regime_timeframe": config.get("market", {}).get("regime_timeframe", "4h"),
            "signal_timeframe": config.get("market", {}).get("signal_timeframe", "1h"),
            "monitor_timeframe": config.get("market", {}).get("monitor_timeframe", "5m"),
            "max_holding_hours": as_float(strategy.get("max_holding_hours", 48), 48),
            "risk_per_trade_pct": as_float(strategy.get("risk_per_trade_pct", 0.25), 0.25),
            "max_position_notional_pct": as_float(strategy.get("max_position_notional_pct", 0.10), 0.10),
            "live_orders_enabled": bool(config.get("safety", {}).get("live_orders_enabled", False)),
            "leverage_enabled": bool(config.get("safety", {}).get("leverage_enabled", False)),
        },
    }

    DASHBOARD_DIR.mkdir(parents=True, exist_ok=True)
    temporary = OUTPUT_PATH.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2, allow_nan=False)
        file.write("\n")
    temporary.replace(OUTPUT_PATH)
    return OUTPUT_PATH


if __name__ == "__main__":
    path = build_dashboard()
    print(f"Dashboard JSON created: {path}")
