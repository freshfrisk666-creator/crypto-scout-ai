from __future__ import annotations

import numpy as np
import pandas as pd


def clip_score(value: float) -> float:
    """Limit a score to the interval 0-100."""
    return float(np.clip(value, 0.0, 100.0))


def add_indicators(
    df: pd.DataFrame,
    atr_period: int = 14,
    breakout_lookback: int = 5,
) -> pd.DataFrame:
    """
    Calculate indicators for the Crypto Scout AI scalp strategy.

    Expected input: completed OHLCV candles.
    Timeframe is selected by the caller (15m, 5m or 1m).
    """
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(df.columns)

    if missing:
        raise ValueError(
            f"Missing OHLCV columns: {', '.join(sorted(missing))}"
        )

    if atr_period < 1 or breakout_lookback < 1:
        raise ValueError("Indicator periods must be positive.")

    out = df.copy()

    for column in required:
        out[column] = pd.to_numeric(out[column], errors="coerce")

    out = out.replace([np.inf, -np.inf], np.nan)

    if (out["close"].dropna() <= 0).any():
        raise ValueError("Closing prices must be positive.")

    if (out["volume"].dropna() < 0).any():
        raise ValueError("Volume cannot be negative.")

    # Moving averages for the market regime and scalp setup.
    for span in (9, 20, 21, 50):
        out[f"ema{span}"] = out["close"].ewm(
            span=span,
            adjust=False,
            min_periods=span,
        ).mean()

    # RSI 14 with Wilder-style exponential smoothing.
    delta = out["close"].diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(
        alpha=1 / 14,
        min_periods=14,
        adjust=False,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / 14,
        min_periods=14,
        adjust=False,
    ).mean()

    relative_strength = avg_gain / avg_loss.replace(0, np.nan)
    out["rsi14"] = 100.0 - (
        100.0 / (1.0 + relative_strength)
    )

    out.loc[
        (avg_loss == 0) & (avg_gain > 0),
        "rsi14",
    ] = 100.0

    out.loc[
        (avg_gain == 0) & (avg_loss > 0),
        "rsi14",
    ] = 0.0

    out.loc[
        (avg_gain == 0) & (avg_loss == 0),
        "rsi14",
    ] = 50.0

    # True Range and ATR.
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

    # Highest high of the previous N candles, excluding current candle.
    out["breakout_high"] = (
        out["high"]
        .rolling(breakout_lookback)
        .max()
        .shift(1)
    )

    out["breakout_strength"] = (
        out["close"]
        / out["breakout_high"].replace(0, np.nan)
    ) - 1.0

    return out