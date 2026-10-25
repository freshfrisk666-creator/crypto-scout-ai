import numpy as np
import pandas as pd

from scoring.signal_engine import (
    build_scalp_signal,
    rank_signals,
    select_top_signals,
)


def make_ohlcv(n=120, breakout=True):
    x = np.arange(n)
    close = 100 + 0.04 * x + 0.12 * np.sin(x * 1.2)

    if breakout:
        previous_high = np.max(close[-6:-1] + 0.03)
        close[-1] = previous_high + 0.01
    else:
        close[-1] = close[-2] - 0.05

    volume = np.full(n, 1000.0)
    volume[-1] = 1800.0

    return pd.DataFrame({
        "open": close - 0.01,
        "high": close + 0.03,
        "low": close - 0.03,
        "close": close,
        "volume": volume,
    })


def make_config():
    return {
        "market": {
            "regime_timeframe": "15m",
            "setup_timeframe": "5m",
            "entry_timeframe": "1m",
        },
        "strategy": {
            "type": "SCALP",
            "atr_period": 14,
            "breakout_lookback": 5,
            "rsi_min": 50,
            "rsi_max": 85,
            "min_volume_ratio": 1.2,
            "stop_atr_multiple": 1.0,
            "target_atr_multiple": 1.5,
        },
    }


def build_test_signal(entry_breakout=True):
    candles = make_ohlcv(breakout=True)
    entry_candles = make_ohlcv(breakout=entry_breakout)

    return build_scalp_signal(
        candles,
        candles,
        entry_candles,
        ticker="TEST",
        symbol="TEST/USDT",
        config=make_config(),
        market_stats={
            "quote_volume_24h_usdt": 20_000_000,
            "spread_bps": 5.0,
        },
    )


def test_valid_scalp_signal_has_correct_risk_reward():
    signal = build_test_signal(entry_breakout=True)

    assert signal["strategy_type"] == "SCALP"
    assert signal["entry_eligible"] is True
    assert signal["stop_price"] < signal["price"]
    assert signal["target_price"] > signal["price"]
    assert abs(signal["risk_reward"] - 1.5) < 1e-9
    assert 0 <= signal["technical_score"] <= 100


def test_missing_entry_breakout_rejects_signal():
    signal = build_test_signal(entry_breakout=False)

    assert signal["entry_eligible"] is False
    assert "one_minute_breakout_not_confirmed" in (
        signal["rejection_reasons"]
    )


def test_ranking_excludes_ineligible_signals_by_default():
    good = build_test_signal(entry_breakout=True)
    bad = build_test_signal(entry_breakout=False)

    good["ticker"] = "GOOD"
    bad["ticker"] = "BAD"

    ranked = rank_signals([bad, good])
    assert all(item["entry_eligible"] for item in ranked)
    assert "GOOD" in [item["ticker"] for item in ranked]
    assert "BAD" not in [item["ticker"] for item in ranked]

    diagnostic = select_top_signals(
        [bad, good],
        limit=10,
        eligible_only=False,
    )
    assert len(diagnostic) == 2