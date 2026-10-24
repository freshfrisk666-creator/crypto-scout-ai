from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from database.db import (
    get_cash,
    init_db,
    insert_position,
    open_positions,
    set_cash,
    ticker_is_open,
)
from scoring.signal_engine import rank_signals


def now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def sync_from_top10(
    signals: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Open paper positions from ranked crypto signals.

    Rules:
    - Paper trading only.
    - Never open more than the configured maximum positions.
    - Never duplicate an already-open ticker.
    - Never spend more than available virtual cash.
    - Use the configured fraction of initial capital as the
      target allocation per position.
    - Never replace an existing open position automatically.
    """
    strategy_config = config.get("strategy", {})
    portfolio_config = config.get("portfolio", {})

    starting_cash = float(
        strategy_config.get("starting_cash", 10000.0)
    )

    strategy_max_positions = int(
        strategy_config.get("max_positions", 10)
    )
    portfolio_max_positions = int(
        portfolio_config.get("max_positions", strategy_max_positions)
    )

    max_positions = min(
        strategy_max_positions,
        portfolio_max_positions,
    )

    allocation_fraction = float(
        portfolio_config.get("position_size_fraction", 0.10)
    )

    if starting_cash <= 0:
        raise ValueError("Starting cash must be positive.")

    if max_positions < 1:
        raise ValueError("Maximum positions must be at least 1.")

    if not 0 < allocation_fraction <= 1:
        raise ValueError(
            "position_size_fraction must be greater than 0 and at most 1."
        )

    init_db(starting_cash=starting_cash)

    current_positions = open_positions()
    slots_available = max_positions - len(current_positions)

    if slots_available <= 0:
        print(
            f"Portfolio full: {len(current_positions)}/"
            f"{max_positions} open positions. No new positions."
        )
        return []

    cash = get_cash()
    if cash <= 0:
        print("No virtual cash available.")
        return []

    allocation_per_position = starting_cash * allocation_fraction
    ranked_signals = rank_signals(signals)

    opened: list[dict[str, Any]] = []

    for signal in ranked_signals:
        if slots_available <= 0:
            break

        ticker = str(signal["ticker"]).strip().upper()
        symbol = str(signal["symbol"]).strip()

        if not ticker or not symbol:
            continue

        if ticker_is_open(ticker):
            continue

        entry_price = float(signal["price"])
        stop_price = float(signal["stop_price"])
        target_price = float(signal["target_price"])

        if entry_price <= 0 or stop_price <= 0:
            print(f"SKIP {ticker}: invalid entry or stop price.")
            continue

        if not stop_price < entry_price < target_price:
            print(f"SKIP {ticker}: invalid stop/entry/target relationship.")
            continue

        # Fixed target allocation based on starting capital,
        # capped by the cash currently available.
        notional = min(allocation_per_position, cash)

        if notional <= 0:
            break

        # Fractional units are allowed for paper accounting.
        shares = notional / entry_price

        position_data = {
            "ticker": ticker,
            "symbol": symbol,
            "strategy_type": signal.get(
                "strategy_type",
                strategy_config.get("type", "SWING"),
            ),
            "entry_time": now_iso(),
            "entry_price": entry_price,
            "shares": shares,
            "notional": notional,
            "stop_price": stop_price,
            "target_price": target_price,
            "technical_score": float(signal["technical_score"]),
            "trend_score": float(signal["trend_score"]),
            "momentum_score": float(signal["momentum_score"]),
            "rsi_score": float(signal["rsi_score"]),
            "volume_score": float(signal["volume_score"]),
            "breakout_score": float(signal["breakout_score"]),
            "risk_reward": float(signal["risk_reward"]),
            "trade_reason": "TOP_CRYPTO_TECHNICAL_SIGNAL",
        }

        position_id = insert_position(position_data)

        cash -= notional
        set_cash(cash)

        opened_item = {
            "position_id": position_id,
            **position_data,
        }
        opened.append(opened_item)
        slots_available -= 1

        print(
            f"OPEN PAPER {ticker} | "
            f"entry={entry_price:.8g} | "
            f"notional=${notional:.2f} | "
            f"stop={stop_price:.8g} | "
            f"target={target_price:.8g} | "
            f"score={position_data['technical_score']:.2f}"
        )

    print(
        f"\nPaper positions opened: {len(opened)} | "
        f"Available cash: ${cash:.2f} | "
        f"Open positions: {len(open_positions())}/{max_positions}"
    )

    return opened
