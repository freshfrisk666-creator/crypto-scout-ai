import pytest

from database import db
from portfolio.paper_trading import (
    calculate_position_size,
    sync_from_top10,
)


def make_signal(
    ticker,
    score,
    entry=100.0,
    stop=90.0,
    target=115.0,
):
    return {
        "ticker": ticker,
        "symbol": f"{ticker}/USDT",
        "strategy_type": "SHORT_TERM_MOMENTUM",
        "price": entry,
        "stop_price": stop,
        "target_price": target,
        "risk_reward": 1.5,
        "technical_score": float(score),
        "trend_score": 80.0,
        "momentum_score": 75.0,
        "rsi_score": 70.0,
        "volume_score": 65.0,
        "breakout_score": 60.0,
        "entry_eligible": True,
    }


def make_config():
    return {
        "strategy": {
            "type": "SHORT_TERM_MOMENTUM",
            "starting_cash": 10000.0,
            "max_positions": 3,
            "risk_per_trade_pct": 0.25,
            "max_position_notional_pct": 0.10,
        },
        "portfolio": {
            "max_positions": 3,
            "replace_open_positions": False,
        },
        "costs": {
            "fee_rate_per_side": 0.001,
            "slippage_rate_per_side": 0.0005,
        },
    }


def test_position_size_is_risk_based():
    config = make_config()

    sizing = calculate_position_size(
        signal=make_signal("BTC", 90),
        config=config,
        available_cash=10000.0,
    )

    assert sizing["shares"] > 0
    assert sizing["notional"] > 0
    assert sizing["notional"] <= 1000.0
    assert sizing["risk_amount"] <= 25.0 + 1e-9
    assert sizing["cash_committed"] <= 10000.0 + 1e-9
    assert sizing["entry_fee"] > 0


def test_risk_budget_is_included_in_position_size():
    config = make_config()

    sizing = calculate_position_size(
        signal=make_signal(
            "TEST",
            90,
            entry=100.0,
            stop=99.0,
        ),
        config=config,
        available_cash=10000.0,
    )

    # A very tight stop requires a larger position, but the
    # notional ceiling must still cap it at $1,000.
    assert sizing["notional"] <= 1000.0 + 1e-9
    assert sizing["risk_amount"] <= 25.0 + 1e-9


def test_opens_positions_within_budget_and_avoids_duplicates(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        db,
        "DB_PATH",
        tmp_path / "paper_test.db",
    )

    config = make_config()

    signals = [
        make_signal("BTC", 90),
        make_signal("ETH", 85),
    ]

    opened = sync_from_top10(
        signals,
        config,
    )

    assert len(opened) == 2
    assert len(db.open_positions()) == 2

    assert db.get_cash() < 10000.0
    assert db.get_cash() > 9000.0

    for position in db.open_positions():
        assert position["risk_amount"] <= 25.0 + 1e-9
        assert position["notional"] <= 1000.0 + 1e-9
        assert position["entry_fee"] > 0

    # Repeating the same signals must not duplicate open tickers.
    opened_again = sync_from_top10(
        signals,
        config,
    )

    assert len(opened_again) == 0
    assert len(db.open_positions()) == 2


def test_does_not_open_positions_when_portfolio_is_full(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        db,
        "DB_PATH",
        tmp_path / "paper_test.db",
    )

    config = make_config()

    config["strategy"]["max_positions"] = 1
    config["portfolio"]["max_positions"] = 1

    first_open = sync_from_top10(
        [make_signal("BTC", 90)],
        config,
    )

    second_open = sync_from_top10(
        [make_signal("ETH", 95)],
        config,
    )

    assert len(first_open) == 1
    assert len(second_open) == 0
    assert len(db.open_positions()) == 1
    assert db.open_positions()[0]["ticker"] == "BTC"


def test_invalid_stop_is_rejected():
    config = make_config()

    with pytest.raises(ValueError):
        calculate_position_size(
            signal=make_signal(
                "BAD",
                90,
                entry=100.0,
                stop=100.0,
            ),
            config=config,
            available_cash=10000.0,
        )