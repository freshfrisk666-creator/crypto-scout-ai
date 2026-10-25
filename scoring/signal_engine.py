from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

import pandas as pd

from agents.technical import add_indicators, clip_score


DEFAULT_SCALP_WEIGHTS = {
    "trend": 0.30,
    "setup": 0.25,
    "rsi": 0.15,
    "volume": 0.15,
    "breakout": 0.15,
}


def build_scalp_signal(
    regime_df: pd.DataFrame,
    setup_df: pd.DataFrame,
    entry_df: pd.DataFrame,
    *,
    ticker: str,
    symbol: str,
    config: dict[str, Any],
    scan_time: str | None = None,
    market_stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Evaluate one crypto using three completed-candle timeframes.

    15m: market regime.
    5m: setup and ATR-based risk levels.
    1m: entry trigger.

    This function generates a signal only. It does not execute trades.
    """
    if not ticker or not symbol:
        raise ValueError("Ticker and symbol are required.")

    strategy = config.get("strategy", {})
    market = config.get("market", {})
    scoring = config.get("scoring", {})

    atr_period = int(strategy.get("atr_period", 14))
    lookback = int(strategy.get("breakout_lookback", 5))

    regime = add_indicators(
        regime_df,
        atr_period=atr_period,
        breakout_lookback=lookback,
    )

    setup = add_indicators(
        setup_df,
        atr_period=atr_period,
        breakout_lookback=lookback,
    )

    trigger = add_indicators(
        entry_df,
        atr_period=atr_period,
        breakout_lookback=lookback,
    )

    # Enough data for EMA50, EMA21, RSI and the entry breakout.
    if len(regime) < 55:
        raise ValueError("Insufficient 15m candles for regime analysis.")

    if len(setup) < 30:
        raise ValueError("Insufficient 5m candles for setup analysis.")

    if len(trigger) < lookback + 15:
        raise ValueError("Insufficient 1m candles for entry analysis.")

    r = regime.iloc[-1]
    s = setup.iloc[-1]
    e = trigger.iloc[-1]

    needed = [
        (r, ["close", "ema20", "ema50"]),
        (s, ["close", "ema9", "ema21", "rsi14", "atr14",
             "volume_ratio"]),
        (e, ["close", "ema9", "breakout_high"]),
    ]

    for row, fields in needed:
        for field in fields:
            if field not in row or pd.isna(row[field]):
                raise ValueError(
                    f"Missing indicator {field} for {ticker}."
                )

    # 15m market regime: bullish structure required for long-only V1.
    regime_ok = (
        float(r["close"]) > float(r["ema20"])
        and float(r["ema20"]) > float(r["ema50"])
    )

    # 5m setup: short EMA above medium EMA and price above short EMA.
    setup_ok = (
        float(s["ema9"]) > float(s["ema21"])
        and float(s["close"]) > float(s["ema9"])
    )

    rsi = float(s["rsi14"])
    rsi_min = float(strategy.get("rsi_min", 50))
    rsi_max = float(strategy.get("rsi_max", 70))
    rsi_ok = rsi_min <= rsi <= rsi_max

    volume_ratio = float(s["volume_ratio"])
    minimum_volume_ratio = float(
        strategy.get("min_volume_ratio", 1.2)
    )
    volume_ok = (
        isfinite(volume_ratio)
        and volume_ratio >= minimum_volume_ratio
    )

    # 1m trigger: a completed candle closes above the previous N-candle high.
    entry_price = float(e["close"])
    previous_high = float(e["breakout_high"])

    breakout_ok = (
        isfinite(entry_price)
        and isfinite(previous_high)
        and entry_price > previous_high
        and entry_price > float(e["ema9"])
    )

    rejection_reasons: list[str] = []

    if not regime_ok:
        rejection_reasons.append("regime_not_bullish")

    if not setup_ok:
        rejection_reasons.append("setup_not_bullish")

    if not rsi_ok:
        rejection_reasons.append("rsi_out_of_range")

    if not volume_ok:
        rejection_reasons.append("relative_volume_too_low")

    if not breakout_ok:
        rejection_reasons.append("one_minute_breakout_not_confirmed")

    # Component scores are diagnostic, not substitutes for entry conditions.
    trend_score = 100.0 if regime_ok else 0.0
    setup_score = 100.0 if setup_ok else 0.0

    if rsi_ok:
        rsi_score = 100.0
    elif 45 <= rsi <= 75:
        rsi_score = 50.0
    else:
        rsi_score = 20.0

    volume_score = clip_score(
        50.0 + (volume_ratio - 1.0) * 100.0
    )

    breakout_strength = float(e["breakout_strength"])
    breakout_score = clip_score(
        50.0 + breakout_strength * 1000.0
    )

    weights = dict(
        scoring.get("scalp_weights", DEFAULT_SCALP_WEIGHTS)
    )

    if set(weights) != set(DEFAULT_SCALP_WEIGHTS):
        raise ValueError(
            "scalp_weights must define trend, setup, rsi, volume, breakout."
        )

    if any(
        not isfinite(float(weight)) or float(weight) < 0
        for weight in weights.values()
    ):
        raise ValueError("Scalp scoring weights must be non-negative.")

    weight_total = sum(float(weight) for weight in weights.values())
    if weight_total <= 0:
        raise ValueError("At least one scalp scoring weight must be positive.")

    weights = {
        key: float(value) / weight_total
        for key, value in weights.items()
    }

    technical_score = (
        trend_score * weights["trend"]
        + setup_score * weights["setup"]
        + rsi_score * weights["rsi"]
        + volume_score * weights["volume"]
        + breakout_score * weights["breakout"]
    )

    # The stop is based on 5m ATR; target multiples are configurable.
    atr = float(s["atr14"])
    stop_multiple = float(strategy.get("stop_atr_multiple", 1.0))
    target_multiple = float(strategy.get("target_atr_multiple", 1.5))

    if (
        not isfinite(entry_price)
        or entry_price <= 0
        or not isfinite(atr)
        or atr <= 0
        or stop_multiple <= 0
        or target_multiple <= 0
    ):
        raise ValueError(f"Invalid entry or risk values for {ticker}.")

    stop_distance = atr * stop_multiple
    target_distance = atr * target_multiple

    stop_price = entry_price - stop_distance
    target_price = entry_price + target_distance

    if stop_price <= 0:
        raise ValueError(
            f"Calculated stop is not positive for {ticker}."
        )

    risk_reward = target_distance / stop_distance

    if scan_time is None:
        scan_time = datetime.now(timezone.utc).isoformat()

    stats = market_stats or {}

    return {
        "ticker": ticker.upper(),
        "symbol": symbol,
        "scan_time": scan_time,
        "strategy_type": "SCALP",
        "price": entry_price,
        "atr": atr,
        "stop_price": stop_price,
        "target_price": target_price,
        "risk_reward": risk_reward,
        "technical_score": round(technical_score, 4),
        "trend_score": round(trend_score, 4),
        "momentum_score": round(setup_score, 4),
        "rsi_score": round(rsi_score, 4),
        "volume_score": round(volume_score, 4),
        "breakout_score": round(breakout_score, 4),
        "rsi14": rsi,
        "volume_ratio_5m": volume_ratio,
        "breakout_strength": breakout_strength,
        "regime_ok": regime_ok,
        "setup_ok": setup_ok,
        "rsi_ok": rsi_ok,
        "volume_ok": volume_ok,
        "breakout_ok": breakout_ok,
        "entry_eligible": len(rejection_reasons) == 0,
        "rejection_reasons": rejection_reasons,
        "quote_volume_24h_usdt": stats.get("quote_volume_24h_usdt"),
        "spread_bps": stats.get("spread_bps"),
    }


def rank_signals(
    signals: list[dict[str, Any]],
    *,
    eligible_only: bool = True,
) -> list[dict[str, Any]]:
    """Rank valid signals; by default, include only entry-eligible signals."""
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

            if not all(map(isfinite, (score, rr, price, stop, target))):
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
    """Select the highest-ranked valid scalp signals."""
    if limit < 1:
        raise ValueError("limit must be at least 1.")

    return rank_signals(
        signals,
        eligible_only=eligible_only,
    )[:limit]