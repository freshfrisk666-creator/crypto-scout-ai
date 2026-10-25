from database import db
from portfolio.paper_trading import sync_from_top10


def make_signal(ticker, score):
    return {
        "ticker": ticker,
        "symbol": f"{ticker}/USDT",
        "strategy_type": "SWING",
        "price": 100.0,
        "stop_price": 90.0,
        "target_price": 120.0,
        "risk_reward": 2.0,
        "technical_score": float(score),
        "trend_score": 80.0,
        "momentum_score": 75.0,
        "rsi_score": 70.0,
        "volume_score": 65.0,
        "breakout_score": 60.0,
    }


def test_opens_positions_within_budget_and_avoids_duplicates(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "paper_test.db")

    config = {
        "strategy": {
            "type": "SWING",
            "starting_cash": 10000.0,
            "max_positions": 10,
        },
        "portfolio": {
            "max_positions": 10,
            "position_size_fraction": 0.10,
        },
    }

    signals = [
        make_signal("BTC", 90),
        make_signal("ETH", 85),
    ]

    opened = sync_from_top10(signals, config)

    assert len(opened) == 2
    assert len(db.open_positions()) == 2
    assert db.get_cash() == 8000.0

    # Repeating the same signals must not duplicate open tickers.
    opened_again = sync_from_top10(signals, config)

    assert len(opened_again) == 0
    assert len(db.open_positions()) == 2
    assert db.get_cash() == 8000.0


def test_does_not_open_positions_when_portfolio_is_full(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "paper_test.db")

    config = {
        "strategy": {
            "type": "SWING",
            "starting_cash": 10000.0,
            "max_positions": 1,
        },
        "portfolio": {
            "max_positions": 1,
            "position_size_fraction": 0.10,
        },
    }

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
    assert db.get_cash() == 9000.0
