import numpy as np
import pandas as pd

from scoring.signal_engine import (
    build_short_term_signal,
    rank_signals,
    select_top_signals,
)


def make_ohlcv(n=120, breakout=True):
    x = np.arange(n, dtype=float)
    close = 100 + 0.04 * x + 0.3 * np.sin(x * 0.65)

    open_price = close - 0.01
    high = close + 0.03
    low = close - 0.03
    volume = np.full(n, 1000.0)

    if breakout:
        previous_high = high[-6:-1].max()
        close[-1] = previous_high + 0.05
        open_price[-1] = close[-1] - 0.02
        high[-1] = close[-1] + 0.03
        low[-1] = close[-1] - 0.04
        volume[-1] = 1800.0
    else:
        # Keep the last candle below the previous EMA9 to prevent
        # both breakout and pullback-reclaim triggers.
        previous_ema9 = pd.Series(close[:-1]).ewm(
            span=9,
            adjust=False,
        ).mean().iloc[-1]

        close[-1] = min(
            close[-2] - 0.1,
            previous_ema9 - 0.2,
        )
        open_price[-1] = close[-1] + 0.05
        high[-1] = open_price[-1] + 0.02
        low[-1] = close[-1] - 0.02
        volume[-1] = 1800.0

    return pd.DataFrame(
        {
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


def make_config():
    return {
        "market": {
            "regime_timeframe": "4h",
            "setup_timeframe": "1h",
            "signal_timeframe": "1h",
        },
        "strategy": {
            "type": "SHORT_TERM_MOMENTUM",
            "atr_period": 14,
            "breakout_lookback": 5,
            "rsi_min": 0,
            "rsi_max": 100,
            "min_volume_ratio": 1.2,
            "stop_atr_multiple": 1.5,
            "target_atr_multiple": 2.25,
        },
        "scoring": {
            "short_term_weights": {
                "trend": 0.30,
                "setup": 0.25,
                "rsi": 0.15,
                "volume": 0.15,
                "breakout": 0.15,
            }
        },
    }


def build_test_signal(entry_breakout=True):
    regime_df = make_ohlcv(breakout=True)
    hourly_df = make_ohlcv(breakout=entry_breakout)

    return build_short_term_signal(
        regime_df,
        hourly_df,
        ticker="TEST",
        symbol="TEST/USDT",
        config=make_config(),
        market_stats={
            "quote_volume_24h_usdt": 20_000_000,
            "spread_bps": 5.0,
        },
    )


def test_signal_uses_hourly_entry_and_correct_risk_reward():
    signal = build_test_signal(entry_breakout=True)

    assert signal["strategy_type"] == "SHORT_TERM_MOMENTUM"
    assert signal["entry_eligible"] is True
    assert signal["stop_price"] < signal["price"]
    assert signal["target_price"] > signal["price"]
    assert abs(signal["risk_reward"] - 1.5) < 1e-9
    assert 0 <= signal["technical_score"] <= 100


def test_missing_hourly_trigger_rejects_signal():
    signal = build_test_signal(entry_breakout=False)

    assert signal["entry_eligible"] is False
    assert "no_hourly_breakout_or_reclaim" in (
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
