from __future__ import annotations

from datetime import datetime, timezone

import ccxt
import pandas as pd

from agents.scanner import (
    get_tradable_crypto_universe,
    load_config,
)
from database.db import init_db, insert_scan_results
from scoring.signal_engine import (
    build_scalp_signal,
    rank_signals,
    select_top_signals,
)


def fetch_closed_ohlcv(
    exchange,
    symbol: str,
    timeframe: str,
    limit: int,
) -> pd.DataFrame:
    """Fetch OHLCV and discard candles which have not closed yet."""
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

    timeframe_seconds = exchange.parse_timeframe(timeframe)
    now = pd.Timestamp.now(tz="UTC")
    candle_close = df["timestamp"] + pd.to_timedelta(
        timeframe_seconds,
        unit="s",
    )

    df = df.loc[candle_close <= now].copy()
    df = df.sort_values("timestamp").drop_duplicates(
        subset=["timestamp"]
    )

    return df.reset_index(drop=True)


def _number(value) -> float | None:
    """Safely convert a value to a finite float."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not pd.notna(number) or number < 0:
        return None

    return number


def get_market_stats(ticker: dict) -> dict | None:
    """
    Extract 24h quote volume and bid/ask spread from a CCXT ticker.

    Assets without a reliable quote-volume or spread snapshot are rejected
    rather than treated as liquid by assumption.
    """
    info = ticker.get("info") or {}

    quote_volume = _number(
        ticker.get("quoteVolume", info.get("quoteVolume"))
    )
    bid = _number(ticker.get("bid", info.get("bidPrice")))
    ask = _number(ticker.get("ask", info.get("askPrice")))

    if quote_volume is None or bid is None or ask is None:
        return None

    if quote_volume <= 0 or bid <= 0 or ask < bid:
        return None

    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None

    spread_bps = ((ask - bid) / mid) * 10000.0

    return {
        "quote_volume_24h_usdt": quote_volume,
        "spread_bps": spread_bps,
    }


def fetch_ticker_snapshots(exchange, symbols: list[str]) -> dict:
    """Get a bulk ticker snapshot, falling back to individual requests."""
    try:
        tickers = exchange.fetch_tickers()
        if isinstance(tickers, dict) and tickers:
            return tickers
    except Exception as exc:
        print(f"Bulk ticker request failed; using fallback: {exc}")

    result = {}

    for symbol in symbols:
        try:
            result[symbol] = exchange.fetch_ticker(symbol)
        except Exception as exc:
            print(f"Ticker unavailable for {symbol}: {exc}")

    return result


def main() -> None:
    config = load_config()
    init_db(
        starting_cash=float(
            config.get("strategy", {}).get("starting_cash", 10000.0)
        )
    )

    market = config["market"]
    strategy = config["strategy"]
    data_config = config["data"]

    exchange_name = str(market.get("exchange", "binance")).lower()
    if not hasattr(ccxt, exchange_name):
        raise ValueError(f"Unsupported CCXT exchange: {exchange_name}")

    exchange_class = getattr(ccxt, exchange_name)
    exchange = exchange_class(
        {
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
    )

    regime_tf = str(market.get("regime_timeframe", "15m"))
    setup_tf = str(market.get("setup_timeframe", "5m"))
    entry_tf = str(market.get("entry_timeframe", "1m"))

    ohlcv_limit = int(data_config.get("ohlcv_limit", 250))
    minimum_24h_volume = float(
        market.get("min_quote_volume_24h_usdt", 10_000_000)
    )
    max_spread_bps = float(market.get("max_spread_bps", 10))
    atr_period = int(strategy.get("atr_period", 14))

    universe = get_tradable_crypto_universe(
        config,
        exchange=exchange,
    )

    if not universe:
        raise RuntimeError("No eligible Binance spot pairs were found.")

    symbols = [coin["exchange_symbol"] for coin in universe]
    ticker_snapshots = fetch_ticker_snapshots(exchange, symbols)

    scan_time = datetime.now(timezone.utc).isoformat()
    signals = []

    volume_rejected = 0
    spread_rejected = 0
    ticker_rejected = 0
    historical_rejected = 0
    data_errors = 0

    print("\nCRYPTO SCOUT AI — SCALP SCAN")
    print("=" * 95)
    print(f"Exchange: {exchange_name}")
    print(f"Timeframes: regime={regime_tf}, setup={setup_tf}, entry={entry_tf}")
    print(f"Pairs in starting universe: {len(universe)}")
    print(f"Minimum 24h quote volume: ${minimum_24h_volume:,.0f}")
    print(f"Maximum spread: {max_spread_bps:.1f} bps")
    print("Execution mode: SCAN ONLY")
    print()

    for index, coin in enumerate(universe, start=1):
        ticker = coin["ticker"]
        symbol = coin["exchange_symbol"]

        market_ticker = ticker_snapshots.get(symbol)
        if not market_ticker:
            ticker_rejected += 1
            print(f"[{index}/{len(universe)}] {ticker}: no ticker snapshot")
            continue

        stats = get_market_stats(market_ticker)
        if stats is None:
            ticker_rejected += 1
            print(f"[{index}/{len(universe)}] {ticker}: missing volume/spread")
            continue

        if stats["quote_volume_24h_usdt"] < minimum_24h_volume:
            volume_rejected += 1
            print(
                f"[{index}/{len(universe)}] {ticker}: "
                f"24h volume too low "
                f"(${stats['quote_volume_24h_usdt']:,.0f})"
            )
            continue

        if stats["spread_bps"] > max_spread_bps:
            spread_rejected += 1
            print(
                f"[{index}/{len(universe)}] {ticker}: "
                f"spread too wide ({stats['spread_bps']:.2f} bps)"
            )
            continue

        try:
            regime_df = fetch_closed_ohlcv(
                exchange, symbol, regime_tf, ohlcv_limit
            )
            setup_df = fetch_closed_ohlcv(
                exchange, symbol, setup_tf, ohlcv_limit
            )
            entry_df = fetch_closed_ohlcv(
                exchange, symbol, entry_tf, ohlcv_limit
            )

            if (
                len(regime_df) < 55
                or len(setup_df) < 30
                or len(entry_df) < 20
            ):
                historical_rejected += 1
                print(
                    f"[{index}/{len(universe)}] {ticker}: "
                    "insufficient closed-candle history"
                )
                continue

            signal = build_scalp_signal(
                regime_df,
                setup_df,
                entry_df,
                ticker=ticker,
                symbol=symbol,
                config=config,
                scan_time=scan_time,
                market_stats=stats,
            )

            signals.append(signal)

            outcome = (
                "ENTRY SETUP"
                if signal["entry_eligible"]
                else "NO ENTRY: " + ", ".join(
                    signal["rejection_reasons"]
                )
            )

            print(
                f"[{index}/{len(universe)}] {ticker}: "
                f"score={signal['technical_score']:.2f} | "
                f"spread={stats['spread_bps']:.2f} bps | {outcome}"
            )

        except Exception as exc:
            data_errors += 1
            print(f"[{index}/{len(universe)}] {ticker}: ERROR — {exc}")

    eligible = rank_signals(signals, eligible_only=True)
    top10 = select_top_signals(
        eligible,
        limit=10,
        eligible_only=True,
    )

    print("\nCRYPTO SCOUT AI — SCALP DIAGNOSTICS")
    print("=" * 95)
    print(f"Pairs in starting universe: {len(universe)}")
    print(f"Rejected: insufficient ticker data: {ticker_rejected}")
    print(f"Rejected: 24h volume: {volume_rejected}")
    print(f"Rejected: spread: {spread_rejected}")
    print(f"Rejected: historical candles: {historical_rejected}")
    print(f"Data errors: {data_errors}")
    print(f"Pairs assessed by scalp engine: {len(signals)}")
    print(f"Entry-eligible signals: {len(eligible)}")
    print(f"Top eligible signals: {len(top10)}")
    print()

    print(
        f"{'Ticker':10s} {'Score':>7s} {'Regime':>8s} "
        f"{'Setup':>7s} {'RSI':>7s} {'Vol5m':>8s} "
        f"{'Spread':>8s} {'Eligible':>9s}  Decision"
    )
    print("-" * 110)

    for signal in rank_signals(signals, eligible_only=False):
        reasons = (
            "READY"
            if signal["entry_eligible"]
            else ", ".join(signal["rejection_reasons"])
        )

        print(
            f"{signal['ticker']:10s} "
            f"{signal['technical_score']:7.2f} "
            f"{str(signal['regime_ok']):>8s} "
            f"{str(signal['setup_ok']):>7s} "
            f"{signal['rsi14']:7.2f} "
            f"{signal['volume_ratio_5m']:8.2f} "
            f"{signal['spread_bps']:8.2f} "
            f"{str(signal['entry_eligible']):>9s}  {reasons}"
        )

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

    print("\nENTRY-ELIGIBLE SCALP SETUPS")
    print("=" * 95)

    if not top10:
        print("No entry setup satisfied all conditions in this scan.")
    else:
        for rank, signal in enumerate(top10, start=1):
            print(
                f"{rank:2d}. {signal['ticker']:10s} "
                f"entry={signal['price']:.8g} "
                f"stop={signal['stop_price']:.8g} "
                f"target={signal['target_price']:.8g} "
                f"R/R={signal['risk_reward']:.2f} "
                f"score={signal['technical_score']:.2f}"
            )

    print(
        "\nSCAN ONLY: no paper positions were opened. "
        "The risk-sized entry and continuous exit monitor "
        "must be validated before paper execution."
    )


if __name__ == "__main__":
    main()