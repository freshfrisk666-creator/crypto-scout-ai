from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

import pandas as pd

from agents.technical import score_row


def build_signal(
    row: pd.Series,
    *,
    ticker: str,
    symbol: str,
    config: dict[str, Any],
    scan_time: str | None = None,
) -> dict[str, Any]:
    """
    Build a technical trading signal with entry, stop and target.

    This function creates a signal only.
    It does not open a position or send an exchange order.
    """
    if not ticker or not symbol:
        raise ValueError("Ticker and exchange symbol are required.")

    if "close" not in row or "atr14" not in row:
        raise ValueError("The row must contain close and atr14.")

    entry = float(row["close"])
    atr = float(row["atr14"])

    if not isfinite(entry) or entry <= 0:
        raise ValueError("Entry price must be finite and positive.")

    if not isfinite(atr) or atr <= 0:
        raise ValueError("ATR must be finite and positive.")

    strategy_config = config.get("strategy", {})
    scoring_config = config.get("scoring", {})

    stop_multiple = float(
        strategy_config.get("stop_atr_multiple", 1.5)
    )
    target_multiple = float(
        strategy_config.get("target_atr_multiple", 3.0)
    )

    if (
        not isfinite(stop_multiple)
        or not isfinite(target_multiple)
        or stop_multiple <= 0
        or target_multiple <= 0
    ):
        raise ValueError("ATR multiples must be finite and positive.")

    stop_price = entry - stop_multiple * atr
    target_price = entry + target_multiple * atr

    if stop_price <= 0:
        raise ValueError(
            "Calculated stop price is not positive; reject this signal."
        )

    if target_price <= entry:
        raise ValueError("Target price must be above entry.")

    weights = scoring_config.get("weights")
    scores = score_row(row, weights=weights)

    risk_per_unit = entry - stop_price
    reward_per_unit = target_price - entry
    risk_reward = reward_per_unit / risk_per_unit

    if scan_time is None:
        scan_time = datetime.now(timezone.utc).isoformat()

    signal = {
        "ticker": ticker.upper(),
        "symbol": symbol,
        "scan_time": scan_time,
        "strategy_type": strategy_config.get("type", "SWING"),
        "price": entry,
        "atr": atr,
        "stop_price": stop_price,
        "target_price": target_price,
        "risk_reward": risk_reward,
        **scores,
    }

    return signal


def rank_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Rank valid signals by technical score, then risk/reward, then ticker.

    Ranking does not guarantee profitability and does not itself authorize
    a paper trade. Portfolio rules are applied separately.
    """
    valid_signals = []

    for signal in signals:
        try:
            score = float(signal["technical_score"])
            rr = float(signal["risk_reward"])
            price = float(signal["price"])
            stop = float(signal["stop_price"])
            target = float(signal["target_price"])

            if not all(
                isfinite(value)
                for value in (score, rr, price, stop, target)
            ):
                continue

            if not 0 <= score <= 100:
                continue

            if price <= 0 or stop <= 0 or target <= price or rr <= 0:
                continue

            if not signal.get("ticker") or not signal.get("symbol"):
                continue

            valid_signals.append(signal)

        except (KeyError, TypeError, ValueError):
            continue

    return sorted(
        valid_signals,
        key=lambda signal: (
            -float(signal["technical_score"]),
            -float(signal["risk_reward"]),
            str(signal["ticker"]),
        ),
    )


def select_top_signals(
    signals: list[dict[str, Any]],
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return up to `limit` ranked signals."""
    if limit < 1:
        raise ValueError("limit must be at least 1.")

    return rank_signals(signals)[:limit]