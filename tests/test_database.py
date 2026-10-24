import pytest
from database import db


def sample_position():
    return {
        "ticker": "BTC",
        "symbol": "BTC/USDT",
        "strategy_type": "SWING",
        "entry_time": "2026-10-24T10:00:00+00:00",
        "entry_price": 100.0,
        "shares": 10.0,
        "notional": 1000.0,
        "stop_price": 90.0,
        "target_price": 120.0,
        "technical_score": 80.0,
        "trend_score": 85.0,
        "momentum_score": 80.0,
        "rsi_score": 100.0,
        "volume_score": 70.0,
        "breakout_score": 65.0,
        "risk_reward": 2.0,
        "trade_reason": "TEST",
    }


def test_account_initialization_is_not_repeated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")

    db.init_db(starting_cash=10000.0)
    assert db.get_cash() == 10000.0

    db.set_cash(8000.0)
    db.init_db(starting_cash=5000.0)

    # Reinitialization must not overwrite an existing account.
    assert db.get_cash() == 8000.0


def test_position_opens_and_closes_correctly(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db(starting_cash=10000.0)

    # Simulate the cash remaining after a $1,000 allocation.
    db.set_cash(9000.0)

    position_id = db.insert_position(sample_position())

    assert position_id > 0
    assert len(db.open_positions()) == 1
    assert db.ticker_is_open("BTC")

    db.close_position(
        position_id=position_id,
        exit_time="2026-10-24T11:00:00+00:00",
        exit_price=110.0,
        exit_reason="TEST",
    )

    assert len(db.open_positions()) == 0
    assert len(db.closed_positions()) == 1
    assert not db.ticker_is_open("BTC")

    closed = db.closed_positions()[0]

    assert closed["realized_pnl"] == 100.0
    assert closed["realized_return_pct"] == pytest.approx(10.0)
    assert closed["exit_reason"] == "TEST"

    # $9,000 remaining + $1,000 allocated + $100 profit.
    assert db.get_cash() == 10100.0


def test_closing_the_same_position_twice_has_no_duplicate_effect(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db(starting_cash=10000.0)
    db.set_cash(9000.0)

    position_id = db.insert_position(sample_position())

    db.close_position(
        position_id,
        "2026-10-24T11:00:00+00:00",
        110.0,
        "TARGET",
    )

    cash_after_first_close = db.get_cash()

    db.close_position(
        position_id,
        "2026-10-24T11:05:00+00:00",
        115.0,
        "TARGET",
    )

    assert db.get_cash() == cash_after_first_close
    assert len(db.closed_positions()) == 1
