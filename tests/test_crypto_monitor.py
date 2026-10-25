from datetime import datetime, timezone

from database import db
from runner.crypto_monitor import (
    evaluate_position,
    monitor_once,
)


def make_position(
    *,
    entry_time="2026-10-24T10:00:00+00:00",
    stop=90.0,
    target=120.0,
):
    return {
        "id": 1,
        "ticker": "BTC",
        "symbol": "BTC/USDT",
        "entry_time": entry_time,
        "entry_price": 100.0,
        "stop_price": stop,
        "target_price": target,
    }


def make_candle(
    *,
    high=110.0,
    low=95.0,
    close=105.0,
):
    return {
        "timestamp": 0.0,
        "open": 100.0,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000.0,
    }


def test_stop_has_priority_when_stop_and_target_are_both_touched():
    position = make_position()

    reason, price = evaluate_position(
        position,
        make_candle(
            high=125.0,
            low=85.0,
            close=105.0,
        ),
        now=datetime(
            2026,
            10,
            24,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        max_holding_hours=48,
    )

    assert reason == "STOP"
    assert price == 90.0


def test_target_is_triggered():
    position = make_position()

    reason, price = evaluate_position(
        position,
        make_candle(
            high=121.0,
            low=105.0,
            close=118.0,
        ),
        now=datetime(
            2026,
            10,
            24,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        max_holding_hours=48,
    )

    assert reason == "TARGET"
    assert price == 120.0


def test_time_limit_is_triggered():
    position = make_position()

    reason, price = evaluate_position(
        position,
        make_candle(
            high=110.0,
            low=95.0,
            close=105.0,
        ),
        now=datetime(
            2026,
            10,
            26,
            10,
            1,
            tzinfo=timezone.utc,
        ),
        max_holding_hours=48,
    )

    assert reason == "TIME_LIMIT"
    assert price == 105.0


def test_monitor_closes_stop_position(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        db,
        "DB_PATH",
        tmp_path / "monitor_test.db",
    )

    db.init_db(
        starting_cash=10000.0
    )

    db.set_cash(9000.0)

    position_id = db.insert_position(
        {
            "ticker": "BTC",
            "symbol": "BTC/USDT",
            "strategy_type": "SHORT_TERM_MOMENTUM",
            "entry_time": (
                "2026-10-24T10:00:00+00:00"
            ),
            "entry_price": 100.0,
            "shares": 10.0,
            "notional": 1000.0,
            "stop_price": 90.0,
            "target_price": 120.0,
            "technical_score": 80.0,
            "risk_amount": 25.0,
            "entry_fill_price": 100.0,
            "entry_fee": 1.0,
            "cash_committed": 1001.0,
            "fee_rate_per_side": 0.001,
            "slippage_rate_per_side": 0.0005,
        }
    )

    class FakeExchange:
        def fetch_ohlcv(
            self,
            symbol,
            timeframe,
            limit,
        ):
            assert symbol == "BTC/USDT"
            assert timeframe == "5m"
            return [
                [
                    0,
                    100.0,
                    101.0,
                    99.0,
                    100.0,
                    1000.0,
                ],
                [
                    1,
                    95.0,
                    97.0,
                    89.0,
                    92.0,
                    1000.0,
                ],
            ]

    config = {
        "market": {
            "monitor_timeframe": "5m",
        },
        "strategy": {
            "max_holding_hours": 48,
        },
        "monitor": {
            "interval_minutes": 5,
        },
    }

    closed = monitor_once(
        config,
        exchange=FakeExchange(),
        now=datetime(
            2026,
            10,
            24,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert len(closed) == 1
    assert closed[0]["position_id"] == position_id
    assert closed[0]["exit_reason"] == "STOP"

    assert len(db.open_positions()) == 0

    closed_positions = db.closed_positions()
    assert len(closed_positions) == 1
    assert closed_positions[0]["exit_reason"] == "STOP"