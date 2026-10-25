from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

import pandas as pd

from agents.technical import add_indicators, clip_score


DEFAULT_SHORT_TERM_WEIGHTS = {
    "trend": 0.30,
    "setup": 0.25,
    "rsi": 0.15,
    "volume": 0.15,
    "breakout": 0.15,
}


def build_short_term_signal(
    regime_df: pd.DataFrame,
    hourly_df: pd.DataFrame,
    *,
    ticker: str,
    symbol: str,
    config: dict[str, Any],
    scan_time: str | None = None,
    market_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Evaluate one long-only spot opportunity.

    4h candles: market regime.
    1h candles: setup, entry reference, ATR, and breakout/pullback trigger.

    Generates a signal only; never places an exchange order.
    """
    if not ticker or not symbol:
        raise ValueError("Ticker and symbol are required.")

    strategy = config.get("strategy", {})
    scoring = config.get("scoring", {})

    atr_period = int(strategy.get("atr_period", 14))
    lookback = int(strategy.get("breakout_lookback", 5))

    regime = add_indicators(
        regime_df,
        atr_period=atr_period,
        breakout_lookback=lookback,
    )
    hourly = add_indicators(
        hourly_df,
        atr_period=atr_period,
        breakout_lookback=lookback,
    )

    if len(regime) < 55:
        raise ValueError("Insufficient 4h candles for EMA50 regime.")

    if len(hourly) < max(30, lookback + 15):
        raise ValueError("Insufficient 1h candles for signal indicators.")

    r = regime.iloc[-1]
    s = hourly.iloc[-1]
    previous = hourly.iloc[-2]

    for row, fields in (
        (r, ["close", "ema20", "ema50"]),
        (
            s,
            [
                "close", "ema9", "ema21", "rsi14", "atr14",
                "volume_ratio", "momentum_6", "momentum_12",
                "breakout_high", "breakout_strength",
            ],
        ),
        (previous, ["close", "ema9"]),
    ):
        for field in fields:
            if field not in row or pd.isna(row[field]):
                raise ValueError(
                    f"Missing indicator {field} for {ticker}."
                )

    # 4h bullish regime.
    regime_ok = (
        float(r["close"]) > float(r["ema20"])
        and float(r["ema20"]) > float(r["ema50"])
    )

    # 1h trend setup.
    setup_ok = (
        float(s["ema9"]) > float(s["ema21"])
        and float(s["close"]) > float(s["ema21"])
    )

    rsi = float(s["rsi14"])
    rsi_min = float(strategy.get("rsi_min", 50))
    rsi_max = float(strategy.get("rsi_max", 70))
    rsi_ok = rsi_min <= rsi <= rsi_max

    volume_ratio = float(s["volume_ratio"])
    min_volume_ratio = float(strategy.get("min_volume_ratio", 1.2))
    volume_ok = (
        isfinite(volume_ratio)
        and volume_ratio >= min_volume_ratio
    )

    entry = float(s["close"])
    previous_high = float(s["breakout_high"])

    # Entry pattern A: confirmed 1h breakout.
    breakout_ok = (
        isfinite(entry)
        and isfinite(previous_high)
        and entry > previous_high
        and entry > float(s["ema9"])
    )

    # Entry pattern B: price reclaims EMA9 after a pullback,
    # while the EMA9/EMA21 trend remains constructive.
    pullback_reclaim = (
        float(previous["close"]) <= float(previous["ema9"])
        and entry > float(s["ema9"])
        and entry > float(s["ema21"])
    )

    trigger_ok = breakout_ok or pullback_reclaim

    rejection_reasons: list[str] = []

    if not regime_ok:
        rejection_reasons.append("regime_not_bullish")
    if not setup_ok:
        rejection_reasons.append("setup_not_bullish")
    if not rsi_ok:
        rejection_reasons.append("rsi_out_of_range")
    if not volume_ok:
        rejection_reasons.append("relative_volume_too_low")
    if not trigger_ok:
        rejection_reasons.append("no_hourly_breakout_or_reclaim")

    # Component scores are diagnostic. All entry conditions above
    # still need to pass for entry_eligible to be True.
    trend_score = (
        50.0 * float(float(r["close"]) > float(r["ema20"]))
        + 50.0 * float(float(r["ema20"]) > float(r["ema50"]))
    )

    setup_score = (
        50.0 * float(float(s["ema9"]) > float(s["ema21"]))
        + 50.0 * float(float(s["close"]) > float(s["ema21"]))
    )

    if 50 <= rsi <= 65:
        rsi_score = 100.0
    elif 45 <= rsi <= 70:
        rsi_score = 70.0
    else:
        rsi_score = 20.0

    volume_score = clip_score(
        50.0 + (volume_ratio - 1.0) * 100.0
    )

    breakout_strength = float(s["breakout_strength"])
    breakout_score = clip_score(
        50.0 + breakout_strength * 1000.0
    )

    mom6 = float(s["momentum_6"])
    mom12 = float(s["momentum_12"])

    momentum_score = (
        0.60 * clip_score(50.0 + mom6 * 1000.0)
        + 0.40 * clip_score(50.0 + mom12 * 500.0)
    )

    weights = dict(
        scoring.get(
            "short_term_weights",
            DEFAULT_SHORT_TERM_WEIGHTS,
        )
    )

    if set(weights) != set(DEFAULT_SHORT_TERM_WEIGHTS):
        raise ValueError(
            "short_term_weights must define trend, setup, rsi, volume, breakout."
        )

    if any(
        not isfinite(float(value)) or float(value) < 0
        for value in weights.values()
    ):
        raise ValueError("Scoring weights must be non-negative.")

    total_weight = sum(float(value) for value in weights.values())
    if total_weight <= 0:
        raise ValueError("At least one scoring weight must be positive.")

    weights = {
        key: float(value) / total_weight
        for key, value in weights.items()
    }

    technical_score = (
        trend_score * weights["trend"]
        + setup_score * weights["setup"]
        + rsi_score * weights["rsi"]
        + volume_score * weights["volume"]
        + breakout_score * weights["breakout"]
    )

    atr = float(s["atr14"])
    stop_multiple = float(strategy.get("stop_atr_multiple", 1.5))
    target_multiple = float(strategy.get("target_atr_multiple", 2.25))

    if (
        not isfinite(entry)
        or entry <= 0
        or not isfinite(atr)
        or atr <= 0
        or stop_multiple <= 0
        or target_multiple <= 0
    ):
        raise ValueError(f"Invalid entry or ATR risk values for {ticker}.")

    stop_distance = atr * stop_multiple
    target_distance = atr * target_multiple

    stop_price = entry - stop_distance
    target_price = entry + target_distance

    if stop_price <= 0:
        raise ValueError(f"Calculated stop is not positive for {ticker}.")

    risk_reward = target_distance / stop_distance

    if scan_time is None:
        scan_time = datetime.now(timezone.utc).isoformat()

    stats = market_stats or {}

    return {
        "ticker": ticker.upper(),
        "symbol": symbol,
        "scan_time": scan_time,
        "strategy_type": "SHORT_TERM_MOMENTUM",
        "price": entry,
        "atr": atr,
        "stop_price": stop_price,
        "target_price": target_price,
        "risk_reward": risk_reward,
        "technical_score": round(technical_score, 4),
        "trend_score": round(trend_score, 4),
        "momentum_score": round(momentum_score, 4),
        "rsi_score": round(rsi_score, 4),
        "volume_score": round(volume_score, 4),
        "breakout_score": round(breakout_score, 4),
        "rsi14": rsi,
        "volume_ratio_1h": volume_ratio,
        "breakout_strength": breakout_strength,
        "regime_ok": regime_ok,
        "setup_ok": setup_ok,
        "rsi_ok": rsi_ok,
        "volume_ok": volume_ok,
        "breakout_ok": breakout_ok,
        "pullback_reclaim": pullback_reclaim,
        "entry_eligible": not rejection_reasons,
        "rejection_reasons": rejection_reasons,
        "quote_volume_24h_usdt": stats.get("quote_volume_24h_usdt"),
        "spread_bps": stats.get("spread_bps"),
    }


def rank_signals(
    signals: list[dict[str, Any]],
    *,
    eligible_only: bool = True,
) -> list[dict[str, Any]]:
    """Rank valid signals. By default, exclude ineligible setups."""
    valid = []

    for signal in signals:
        try:
            if eligible_only and not signal.get("entry_eligible", True):
                continue

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

            if price <= 0 or stop <= 0 or stop >= price:
                continue

            if target <= price or rr <= 0:
                continue

            if not signal.get("ticker") or not signal.get("symbol"):
                continue

            valid.append(signal)

        except (KeyError, TypeError, ValueError):
            continue

    return sorted(
        valid,
        key=lambda item: (
            -float(item["technical_score"]),
            -float(item["risk_reward"]),
            str(item["ticker"]),
        ),
    )


def select_top_signals(
    signals: list[dict[str, Any]],
    limit: int = 10,
    *,
    eligible_only: bool = True,
) -> list[dict[str, Any]]:
    """Return up to `limit` ranked signals."""
    if limit < 1:
        raise ValueError("limit must be at least 1.")

    return rank_signals(
        signals,
        eligible_only=eligible_only,
    )[:limit]
