from __future__ import annotations

import numpy as np
import pandas as pd


DEFAULT_WEIGHTS = {
    "trend": 0.30,
    "momentum": 0.25,
    "rsi": 0.15,
    "volume": 0.15,
    "breakout": 0.15,
}


def clip_score(value: float) -> float:
    """Keep a technical score between 0 and 100."""
    return float(np.clip(value, 0.0, 100.0))


def add_indicators(
    df: pd.DataFrame,
    atr_period: int = 14,
) -> pd.DataFrame:
    """
    Calculate technical indicators from OHLCV candles.

    Required columns:
        open, high, low, close, volume

    Crypto signals use 4-hour candles in the initial configuration.
    Momentum periods therefore represent candles, not calendar days.
    """
    required_columns = {"open", "high", "low", "close", "volume"}

    missing = required_columns.difference(df.columns)
    if missing:
        raise ValueError(
            f"Missing OHLCV columns: {', '.join(sorted(missing))}"
        )

    if atr_period < 1:
        raise ValueError("atr_period must be >= 1.")

    out = df.copy()

    for column in required_columns:
        out[column] = pd.to_numeric(out[column], errors="coerce")

    out = out.replace([np.inf, -np.inf], np.nan)

    if (out["close"].dropna() <= 0).any():
        raise ValueError("Closing prices must be positive.")

    # Moving averages.
    out["sma20"] = out["close"].rolling(20).mean()
    out["sma50"] = out["close"].rolling(50).mean()
    out["sma200"] = out["close"].rolling(200).mean()

    # RSI using Wilder-style exponential smoothing.
    delta = out["close"].diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)

    avg_gain = gains.ewm(
        alpha=1 / 14,
        min_periods=14,
        adjust=False,
    ).mean()

    avg_loss = losses.ewm(
        alpha=1 / 14,
        min_periods=14,
        adjust=False,
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["rsi14"] = 100.0 - (100.0 / (1.0 + rs))

    # Handle uninterrupted rises, falls, and unchanged prices.
    out.loc[(avg_loss == 0) & (avg_gain > 0), "rsi14"] = 100.0
    out.loc[(avg_gain == 0) & (avg_loss > 0), "rsi14"] = 0.0
    out.loc[(avg_gain == 0) & (avg_loss == 0), "rsi14"] = 50.0

    # ATR using true range and Wilder-style smoothing.
    previous_close = out["close"].shift(1)

    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - previous_close).abs(),
            (out["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    out["atr14"] = true_range.ewm(
        alpha=1 / atr_period,
        min_periods=atr_period,
        adjust=False,
    ).mean()

    # Relative volume.
    out["volume_avg20"] = out["volume"].rolling(20).mean()
    out["volume_ratio"] = (
        out["volume"]
        / out["volume_avg20"].replace(0, np.nan)
    )

    # Momentum over 21 and 63 candles.
    out["mom21"] = out["close"].pct_change(21)
    out["mom63"] = out["close"].pct_change(63)

    # Previous 20-candle high; current candle is excluded.
    out["breakout_high20"] = (
        out["high"].rolling(20).max().shift(1)
    )

    # Breakout strength is a fractional price difference.
    # Example: 0.01 means 1% above the previous 20-candle high.
    out["breakout_strength"] = (
        out["close"] / out["breakout_high20"].replace(0, np.nan)
    ) - 1.0

    return out


def score_row(
    row: pd.Series,
    weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """Calculate component scores and the weighted technical score."""
    selected_weights = dict(weights or DEFAULT_WEIGHTS)

    if set(selected_weights) != set(DEFAULT_WEIGHTS):
        raise ValueError(
            "Weights must contain trend, momentum, rsi, volume and breakout."
        )

    if any(
        not np.isfinite(float(weight)) or float(weight) < 0
        for weight in selected_weights.values()
    ):
        raise ValueError("Scoring weights must be finite and non-negative.")

    total_weight = sum(float(w) for w in selected_weights.values())
    if total_weight <= 0:
        raise ValueError("At least one scoring weight must be positive.")

    # Normalize weights so they always sum to 1.
    selected_weights = {
        key: float(value) / total_weight
        for key, value in selected_weights.items()
    }

    required_values = [
        "close",
        "sma20",
        "sma50",
        "sma200",
        "rsi14",
        "volume_ratio",
        "mom21",
        "mom63",
        "breakout_strength",
    ]

    for key in required_values:
        if key not in row or pd.isna(row[key]):
            raise ValueError(f"Insufficient data for indicator: {key}")

    close = float(row["close"])
    sma20 = float(row["sma20"])
    sma50 = float(row["sma50"])
    sma200 = float(row["sma200"])

    # Trend: four equally weighted conditions.
    trend_score = (
        25.0 * float(close > sma20)
        + 25.0 * float(sma20 > sma50)
        + 25.0 * float(sma50 > sma200)
        + 25.0 * float(close > sma200)
    )

    # Momentum: combine shorter and intermediate candle horizons.
    momentum_score = (
        0.55 * clip_score(50.0 + float(row["mom21"]) * 250.0)
        + 0.45 * clip_score(50.0 + float(row["mom63"]) * 150.0)
    )

    # RSI: reward constructive momentum without automatically
    # rewarding increasingly overbought conditions.
    rsi = float(row["rsi14"])

    if 55.0 <= rsi <= 68.0:
        rsi_score = 100.0
    elif 50.0 <= rsi < 55.0 or 68.0 < rsi <= 72.0:
        rsi_score = 70.0
    elif 45.0 <= rsi < 50.0 or 72.0 < rsi <= 78.0:
        rsi_score = 45.0
    else:
        rsi_score = 20.0

    # Volume: compare current volume with its 20-candle average.
    volume_score = clip_score(
        50.0 + (float(row["volume_ratio"]) - 1.0) * 80.0
    )

    # Breakout: 1% above the prior high adds 10 score points.
    breakout_score = clip_score(
        50.0 + float(row["breakout_strength"]) * 1000.0
    )

    technical_score = (
        trend_score * selected_weights["trend"]
        + momentum_score * selected_weights["momentum"]
        + rsi_score * selected_weights["rsi"]
        + volume_score * selected_weights["volume"]
        + breakout_score * selected_weights["breakout"]
    )

    return {
        "trend_score": round(trend_score, 4),
        "momentum_score": round(momentum_score, 4),
        "rsi_score": round(rsi_score, 4),
        "volume_score": round(volume_score, 4),
        "breakout_score": round(breakout_score, 4),
        "technical_score": round(technical_score, 4),
    }
