from __future__ import annotations

import argparse
from datetime import datetime, timezone

import ccxt
import pandas as pd

from agents.scanner import (
    get_tradable_crypto_universe,
    load_config,
)
from agents.technical import add_indicators
from database.db import init_db, insert_scan_results
from portfolio.paper_trading import sync_from_top10
from scoring.signal_engine import build_signal, select_top_signals


def fetch_closed_ohlcv(
    exchange,
    symbol: str,
    timeframe: str,
    limit: int,
) -> pd.DataFrame:
    """Fetch OHLCV data and exclude candles that have not closed yet."""

    candles = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=limit,
    )

    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(
        candles,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        unit="ms",
        utc=True,
    )

    # Use only completed candles to avoid scoring an unfinished 4h candle.
    timeframe_seconds = exchange.parse_timeframe(timeframe)
    now = pd.Timestamp.now(tz="UTC")

    candle_close_time = df["timestamp"] + pd.to_timedelta(
        timeframe_seconds,
        unit="s",
    )

    df = df.loc[candle_close_time <= now].copy()
    df = df.sort_values("timestamp").drop_duplicates(
        subset=["timestamp"]
    )

    return df.reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crypto Scout AI — cryptocurrency scanner"
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Open paper positions from the Top 10 signals.",
    )
    args = parser.parse_args()

    config = load_config()

    starting_cash = float(
        config["strategy"].get("starting_cash", 10000.0)
    )
    init_db(starting_cash=starting_cash)

    exchange_name = config["market"]["exchange"].lower()

    if not hasattr(ccxt, exchange_name):
        raise ValueError(
            f"Exchange not supported by CCXT: {exchange_name}"
        )

    exchange_class = getattr(ccxt, exchange_name)

    exchange = exchange_class(
        {
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
    )

    timeframe = config["market"]["signal_timeframe"]
    candle_limit = int(config["data"]["ohlcv_limit"])
    minimum_bars = int(config["market"]["signal_bars"])
    minimum_dollar_volume = float(
        config["market"]["min_avg_dollar_volume"]
    )
    atr_period = int(config["strategy"].get("atr_period", 14))

    universe = get_tradable_crypto_universe(
        config,
        exchange=exchange,
    )

    if not universe:
        raise RuntimeError(
            "No tradable crypto pairs were found. "
            "Check CoinGecko, exchange connectivity and configuration."
        )

    print("\nCRYPTO SCOUT AI — SCAN STARTED")
    print("=" * 90)
    print(f"Exchange: {exchange_name}")
    print(f"Signal timeframe: {timeframe}")
    print(f"Tradable pairs found: {len(universe)}")
    print(f"Minimum average dollar volume: ${minimum_dollar_volume:,.0f}")
    print(f"Virtual starting capital: ${starting_cash:,.2f}")
    print(f"Mode: {'PAPER TRADING' if args.paper else 'SCAN ONLY'}")
    print()

    signals = []
    successful_ohlcv = 0
    sufficient_history = 0
    liquid_assets = 0
    scan_time = datetime.now(timezone.utc).isoformat()

    seen_symbols = set()

    for index, coin in enumerate(universe, start=1):
        symbol = coin["exchange_symbol"]
        ticker = coin["ticker"]

        # Avoid scoring the same exchange market more than once.
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)

        print(
            f"[{index}/{len(universe)}] "
            f"{ticker} ({symbol}) ... ",
            end="",
            flush=True,
        )

        try:
            df = fetch_closed_ohlcv(
                exchange=exchange,
                symbol=symbol,
                timeframe=timeframe,
                limit=candle_limit,
            )

            if df.empty:
                print("SKIP — no closed candles")
                continue

            successful_ohlcv += 1

            if len(df) < minimum_bars:
                print(
                    f"SKIP — insufficient history "
                    f"({len(df)} candles)"
                )
                continue

            sufficient_history += 1

            # Approximate quote-currency turnover per 4-hour candle.
            # This is not a 24-hour volume measure.
            dollar_turnover = df["close"] * df["volume"]
            avg_dollar_volume = float(
                dollar_turnover.rolling(20).mean().iloc[-1]
            )

            if (
                pd.isna(avg_dollar_volume)
                or avg_dollar_volume < minimum_dollar_volume
            ):
                print(
                    f"SKIP — low average 4h turnover "
                    f"(${avg_dollar_volume:,.0f})"
                )
                continue

            liquid_assets += 1

            df = add_indicators(
                df,
                atr_period=atr_period,
            )

            row = df.iloc[-1]

            required_indicators = [
                "sma20",
                "sma50",
                "sma200",
                "rsi14",
                "atr14",
                "volume_ratio",
                "mom21",
                "mom63",
                "breakout_strength",
            ]

            if any(
                pd.isna(row[column])
                for column in required_indicators
            ):
                print("SKIP — incomplete indicators")
                continue

            signal = build_signal(
                row,
                ticker=ticker,
                symbol=symbol,
                config=config,
                scan_time=scan_time,
            )

            signal["avg_dollar_volume"] = avg_dollar_volume
            signal["market_cap_rank"] = coin.get(
                "market_cap_rank"
            )

            signals.append(signal)

            print(
                f"OK — score={signal['technical_score']:.2f}, "
                f"turnover=${avg_dollar_volume:,.0f}"
            )

        except Exception as exc:
            print(f"ERROR — {exc}")

    if successful_ohlcv == 0:
        raise RuntimeError(
            "No usable OHLCV data was retrieved. "
            "Previous scan results have not been replaced."
        )

    top10 = select_top_signals(
        signals,
        limit=10,
    )

    # Persist the latest scan snapshot in the local SQLite database.
    scan_rows = []

    for rank, signal in enumerate(top10, start=1):
        scan_rows.append(
            {
                "scan_time": signal["scan_time"],
                "rank": rank,
                "ticker": signal["ticker"],
                "symbol": signal["symbol"],
                "price": signal["price"],
                "technical_score": signal["technical_score"],
                "trend_score": signal["trend_score"],
                "momentum_score": signal["momentum_score"],
                "rsi_score": signal["rsi_score"],
                "volume_score": signal["volume_score"],
                "breakout_score": signal["breakout_score"],
                "atr": signal["atr"],
                "stop_price": signal["stop_price"],
                "target_price": signal["target_price"],
                "risk_reward": signal["risk_reward"],
            }
        )

    insert_scan_results(scan_rows)

    print("\nCRYPTO SCOUT AI — TOP OPPORTUNITIES")
    print("=" * 100)
    print(f"Pairs found: {len(universe)}")
    print(f"Pairs with candle data: {successful_ohlcv}")
    print(f"Pairs with sufficient history: {sufficient_history}")
    print(f"Pairs passing liquidity filter: {liquid_assets}")
    print(f"Valid signals: {len(signals)}")
    print(f"Top opportunities: {len(top10)}")
    print()

    if not top10:
        print("No valid signals met the configured requirements.")
        print("No paper positions will be opened.")
        return

    for rank, signal in enumerate(top10, start=1):
        print(
            f"{rank:2d}. {signal['ticker']:10s} "
            f"score={signal['technical_score']:6.2f} "
            f"entry={signal['price']:.8g} "
            f"stop={signal['stop_price']:.8g} "
            f"target={signal['target_price']:.8g} "
            f"R/R={signal['risk_reward']:.2f}"
        )

    if args.paper:
        opened = sync_from_top10(top10, config)
        print(f"\nNew paper positions opened: {len(opened)}")
    else:
        print("\nSCAN ONLY: no paper positions were opened.")
        print(
            "After reviewing the results, run with --paper "
            "to enable simulated position opening."
        )


if __name__ == "__main__":
    main()