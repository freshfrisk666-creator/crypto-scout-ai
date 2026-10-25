import numpy as np
import pandas as pd

from agents.technical import add_indicators


def make_ohlcv(n=120):
    x = np.arange(n)
    close = 100 + 0.04 * x + 0.12 * np.sin(x * 1.2)

    return pd.DataFrame({
        "open": close - 0.01,
        "high": close + 0.03,
        "low": close - 0.03,
        "close": close,
        "volume": np.full(n, 1000.0),
    })


def test_scalp_indicators_are_created():
    out = add_indicators(make_ohlcv())

    for column in (
        "ema9",
        "ema20",
        "ema21",
        "ema50",
        "rsi14",
        "atr14",
        "volume_ratio",
        "breakout_high",
    ):
        assert column in out.columns
        assert pd.notna(out[column].iloc[-1])

    assert out["atr14"].iloc[-1] > 0
    assert 0 <= out["rsi14"].iloc[-1] <= 100


def test_breakout_level_excludes_current_candle():
    df = make_ohlcv()
    out = add_indicators(df, breakout_lookback=5)

    expected_previous_high = df["high"].iloc[-6:-1].max()

    assert out["breakout_high"].iloc[-1] == expected_previous_high