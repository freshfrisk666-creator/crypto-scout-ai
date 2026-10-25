from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
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


DEFAULT_FEE_RATE = 0.001
DEFAULT_SLIPPAGE_RATE = 0.0005
DEFAULT_RISK_PER_TRADE_PCT = 0.25
DEFAULT_MAX_POSITION_NOTIONAL_PCT = 0.10


def now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def _validate_rate(
    value: float,
    name: str,
) -> float:
    if not isfinite(value) or value < 0 or value >= 1:
        raise ValueError(
            f"{name} must be >= 0 and < 1."
        )

    return value


def calculate_position_size(
    signal: dict[str, Any],
    config: dict[str, Any],
    available_cash: float,
) -> dict[str, float]:
    """
    Calculate paper position size from risk, notional and cash limits.

    Risk includes:
    - entry slippage
    - stop-exit slippage
    - entry fee
    - stop-exit fee

    The resulting position is constrained by:
    - maximum risk per trade
    - maximum position notional
    - currently available cash
    """
    if available_cash <= 0:
        raise ValueError("Available cash must be positive.")

    strategy = config.get("strategy", {})
    costs = config.get("costs", {})

    starting_cash = float(
        strategy.get("starting_cash", 10000.0)
    )

    risk_per_trade_pct = float(
        strategy.get(
            "risk_per_trade_pct",
            DEFAULT_RISK_PER_TRADE_PCT,
        )
    )

    max_position_notional_pct = float(
        strategy.get(
            "max_position_notional_pct",
            DEFAULT_MAX_POSITION_NOTIONAL_PCT,
        )
    )

    fee_rate = _validate_rate(
        float(
            costs.get(
                "fee_rate_per_side",
                DEFAULT_FEE_RATE,
            )
        ),
        "fee_rate_per_side",
    )

    slippage_rate = _validate_rate(
        float(
            costs.get(
                "slippage_rate_per_side",
                DEFAULT_SLIPPAGE_RATE,
            )
        ),
        "slippage_rate_per_side",
    )

    if starting_cash <= 0:
        raise ValueError(
            "Starting cash must be positive."
        )

    if not 0 < risk_per_trade_pct <= 100:
        raise ValueError(
            "risk_per_trade_pct must be > 0 and <= 100."
        )

    if not 0 < max_position_notional_pct <= 100:
        raise ValueError(
            "max_position_notional_pct must be > 0 and <= 100."
        )

    entry_price = float(signal["price"])
    stop_price = float(signal["stop_price"])

    if entry_price <= 0:
        raise ValueError(
            "Signal entry price must be positive."
        )

    if stop_price <= 0 or stop_price >= entry_price:
        raise ValueError(
            "Stop price must be below the entry price."
        )

    # Long spot:
    # entry slippage makes the acquisition more expensive.
    entry_fill_price = (
        entry_price
        * (1.0 + slippage_rate)
    )

    # At the stop, adverse slippage lowers the exit price.
    stop_exit_price = (
        stop_price
        * (1.0 - slippage_rate)
    )

    entry_fee_per_unit = (
        entry_fill_price * fee_rate
    )

    stop_exit_fee_per_unit = (
        stop_exit_price * fee_rate
    )

    # Approximate worst-case loss per unit, including costs.
    risk_per_unit = (
        entry_fill_price
        - stop_exit_price
        + entry_fee_per_unit
        + stop_exit_fee_per_unit
    )

    if risk_per_unit <= 0:
        raise ValueError(
            "Calculated risk per unit must be positive."
        )

    risk_budget = (
        starting_cash
        * risk_per_trade_pct
        / 100.0
    )

    max_position_notional = (
        starting_cash
        * max_position_notional_pct
        / 100.0
    )

    shares_by_risk = (
        risk_budget / risk_per_unit
    )

    shares_by_notional = (
        max_position_notional
        / entry_fill_price
    )

    cash_required_per_share = (
        entry_fill_price
        + entry_fee_per_unit
    )

    shares_by_cash = (
        available_cash
        / cash_required_per_share
    )

    shares = min(
        shares_by_risk,
        shares_by_notional,
        shares_by_cash,
    )

    if shares <= 0 or not isfinite(shares):
        raise ValueError(
            "Calculated paper position size is invalid."
        )

    notional = entry_price * shares

    entry_fee = (
        entry_fill_price
        * shares
        * fee_rate
    )

    cash_committed = (
        entry_fill_price * shares
        + entry_fee
    )

    estimated_risk = (
        risk_per_unit * shares
    )

    # The position may be smaller than the theoretical risk budget
    # because of notional or cash constraints.
    if cash_committed > available_cash + 1e-9:
        raise ValueError(
            "Calculated cash commitment exceeds available cash."
        )

    if notional > max_position_notional + 1e-9:
        raise ValueError(
            "Calculated notional exceeds the configured limit."
        )

    if estimated_risk > risk_budget + 1e-9:
        raise ValueError(
            "Calculated risk exceeds the configured risk budget."
        )

    return {
        "shares": shares,
        "notional": notional,
        "entry_fill_price": entry_fill_price,
        "entry_fee": entry_fee,
        "cash_committed": cash_committed,
        "risk_amount": estimated_risk,
        "risk_budget": risk_budget,
        "max_position_notional": max_position_notional,
        "fee_rate_per_side": fee_rate,
        "slippage_rate_per_side": slippage_rate,
    }


def sync_from_top10(
    signals: list[dict[str, Any]],
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Open paper positions from ranked eligible crypto signals.

    Rules:
    - Paper trading only.
    - Maximum configured concurrent positions.
    - Never duplicate an already-open ticker.
    - Risk is capped per trade.
    - Notional is capped per position.
    - Available virtual cash is respected.
    - Existing positions are never replaced automatically.
    """
    strategy_config = config.get("strategy", {})
    portfolio_config = config.get("portfolio", {})

    starting_cash = float(
        strategy_config.get(
            "starting_cash",
            10000.0,
        )
    )

    strategy_max_positions = int(
        strategy_config.get(
            "max_positions",
            3,
        )
    )

    portfolio_max_positions = int(
        portfolio_config.get(
            "max_positions",
            strategy_max_positions,
        )
    )

    max_positions = min(
        strategy_max_positions,
        portfolio_max_positions,
    )

    if starting_cash <= 0:
        raise ValueError(
            "Starting cash must be positive."
        )

    if max_positions < 1:
        raise ValueError(
            "Maximum positions must be at least 1."
        )

    init_db(
        starting_cash=starting_cash
    )

    current_positions = open_positions()

    slots_available = (
        max_positions
        - len(current_positions)
    )

    if slots_available <= 0:
        print(
            f"Portfolio full: "
            f"{len(current_positions)}/"
            f"{max_positions} open positions. "
            "No new positions."
        )
        return []

    cash = get_cash()

    if cash <= 0:
        print(
            "No virtual cash available."
        )
        return []

    ranked_signals = rank_signals(
        signals,
        eligible_only=True,
    )

    opened: list[dict[str, Any]] = []

    for signal in ranked_signals:
        if slots_available <= 0:
            break

        ticker = str(
            signal["ticker"]
        ).strip().upper()

        symbol = str(
            signal["symbol"]
        ).strip()

        if not ticker or not symbol:
            continue

        if ticker_is_open(ticker):
            continue

        try:
            sizing = calculate_position_size(
                signal=signal,
                config=config,
                available_cash=cash,
            )
        except (KeyError, TypeError, ValueError) as exc:
            print(
                f"SKIP {ticker}: "
                f"position sizing failed — {exc}"
            )
            continue

        position_data = {
            "ticker": ticker,
            "symbol": symbol,
            "strategy_type": signal.get(
                "strategy_type",
                strategy_config.get(
                    "type",
                    "SHORT_TERM_MOMENTUM",
                ),
            ),
            "entry_time": now_iso(),
            "entry_price": float(
                signal["price"]
            ),
            "shares": sizing["shares"],
            "notional": sizing["notional"],
            "stop_price": float(
                signal["stop_price"]
            ),
            "target_price": float(
                signal["target_price"]
            ),
            "technical_score": float(
                signal["technical_score"]
            ),
            "trend_score": float(
                signal["trend_score"]
            ),
            "momentum_score": float(
                signal["momentum_score"]
            ),
            "rsi_score": float(
                signal["rsi_score"]
            ),
            "volume_score": float(
                signal["volume_score"]
            ),
            "breakout_score": float(
                signal["breakout_score"]
            ),
            "risk_reward": float(
                signal["risk_reward"]
            ),
            "trade_reason": (
                "TOP_CRYPTO_TECHNICAL_SIGNAL"
            ),
            "risk_amount": sizing[
                "risk_amount"
            ],
            "entry_fill_price": sizing[
                "entry_fill_price"
            ],
            "entry_fee": sizing[
                "entry_fee"
            ],
            "cash_committed": sizing[
                "cash_committed"
            ],
            "total_cost": sizing[
                "entry_fee"
            ],
            "fee_rate_per_side": sizing[
                "fee_rate_per_side"
            ],
            "slippage_rate_per_side": sizing[
                "slippage_rate_per_side"
            ],
        }

        position_id = insert_position(
            position_data
        )

        cash -= sizing["cash_committed"]

        set_cash(cash)

        opened_item = {
            "position_id": position_id,
            **position_data,
        }

        opened.append(opened_item)

        slots_available -= 1

        print(
            f"OPEN PAPER {ticker} | "
            f"entry={position_data['entry_price']:.8g} | "
            f"shares={position_data['shares']:.8g} | "
            f"notional=${position_data['notional']:.2f} | "
            f"risk=${position_data['risk_amount']:.2f} | "
            f"stop={position_data['stop_price']:.8g} | "
            f"target={position_data['target_price']:.8g} | "
            f"entry_fee=${position_data['entry_fee']:.4f} | "
            f"score={position_data['technical_score']:.2f}"
        )

    print(
        f"\nPaper positions opened: "
        f"{len(opened)} | "
        f"Available cash: "
        f"${cash:.2f} | "
        f"Open positions: "
        f"{len(open_positions())}/"
        f"{max_positions}"
    )

    return opened