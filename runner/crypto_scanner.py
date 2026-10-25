from __future__ import annotations

from datetime import datetime, timezone

import ccxt
import pandas as pd

from agents.scanner import (
    get_tradable_crypto_universe,
    load_config,
)
from database.db import init_db, insert_scan_results
from portfolio.paper_trading import sync_from_top10
from scoring.signal_engine import (
    build_short_term_signal,
    rank_signals,
    select_top_signals,
)


def fetch_closed_ohlcv(
    exchange,
    symbol: str,
    timeframe: str,
    limit: int,
) -> pd.DataFrame:
    """Fetch OHLCV and exclude candles that have not closed yet."""
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

    candle_close_time = (
        df["timestamp"]
        + pd.to_timedelta(
            timeframe_seconds,
            unit="s",
        )
    )

    df = df.loc[
        candle_close_time <= now
    ].copy()

    return (
        df.sort_values("timestamp")
        .drop_duplicates(
            subset=["timestamp"]
        )
        .reset_index(drop=True)
    )


def to_finite_float(value) -> float | None:
    """Convert a value to a finite non-negative float."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    if not pd.notna(result) or result < 0:
        return None

    return result


def get_market_stats(
    ticker_data: dict,
) -> dict | None:
    """
    Read 24-hour quote volume and bid/ask spread.

    Assets with incomplete or invalid market data are rejected.
    """
    info = ticker_data.get("info") or {}

    quote_volume = to_finite_float(
        ticker_data.get(
            "quoteVolume",
            info.get("quoteVolume"),
        )
    )

    bid = to_finite_float(
        ticker_data.get(
            "bid",
            info.get("bidPrice"),
        )
    )

    ask = to_finite_float(
        ticker_data.get(
            "ask",
            info.get("askPrice"),
        )
    )

    if (
        quote_volume is None
        or bid is None
        or ask is None
        or quote_volume <= 0
        or bid <= 0
        or ask < bid
    ):
        return None

    midpoint = (
        bid + ask
    ) / 2.0

    if midpoint <= 0:
        return None

    spread_bps = (
        (ask - bid)
        / midpoint
    ) * 10000.0

    return {
        "quote_volume_24h_usdt": quote_volume,
        "spread_bps": spread_bps,
    }


def fetch_ticker_snapshots(
    exchange,
    symbols: list[str],
) -> dict:
    """Fetch tickers in bulk, with a fallback for individual symbols."""
    try:
        tickers = exchange.fetch_tickers()

        if (
            isinstance(tickers, dict)
            and tickers
        ):
            return tickers

    except Exception as exc:
        print(
            "Bulk ticker request failed; "
            f"trying individual tickers: {exc}"
        )

    result = {}

    for index, symbol in enumerate(
        symbols,
        start=1,
    ):
        try:
            result[symbol] = (
                exchange.fetch_ticker(symbol)
            )
        except Exception as exc:
            print(
                f"Ticker unavailable for {symbol}: "
                f"{exc}"
            )

        if index % 25 == 0:
            print(
                "Ticker fallback progress: "
                f"{index}/{len(symbols)}",
                flush=True,
            )

    return result


def main() -> None:
    config = load_config()

    market = config["market"]
    strategy = config["strategy"]
    data_config = config["data"]
    safety = config.get("safety", {})

    # Hard safety guard:
    # this runner is allowed to manage PAPER positions only.
    if not bool(
        safety.get(
            "paper_trading_only",
            True,
        )
    ):
        raise RuntimeError(
            "Safety check failed: "
            "paper_trading_only must be true."
        )

    if bool(
        safety.get(
            "live_orders_enabled",
            False,
        )
    ):
        raise RuntimeError(
            "Safety check failed: "
            "live_orders_enabled must remain false."
        )

    if bool(
        safety.get(
            "leverage_enabled",
            False,
        )
    ):
        raise RuntimeError(
            "Safety check failed: "
            "leverage_enabled must remain false."
        )

    init_db(
        starting_cash=float(
            strategy.get(
                "starting_cash",
                10000.0,
            )
        )
    )

    exchange_name = str(
        market.get(
            "exchange",
            "binance",
        )
    ).lower()

    if not hasattr(
        ccxt,
        exchange_name,
    ):
        raise ValueError(
            f"Unsupported CCXT exchange: "
            f"{exchange_name}"
        )

    exchange_class = getattr(
        ccxt,
        exchange_name,
    )

    exchange = exchange_class(
        {
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
            },
        }
    )

    regime_timeframe = str(
        market.get(
            "regime_timeframe",
            "4h",
        )
    )

    signal_timeframe = str(
        market.get(
            "signal_timeframe",
            "1h",
        )
    )

    if (
        regime_timeframe != "4h"
        or signal_timeframe != "1h"
    ):
        raise ValueError(
            "This runner is configured for "
            "a 4h regime and 1h entry signals. "
            "Update the configuration and tests "
            "together before changing these timeframes."
        )

    candle_limit = int(
        data_config.get(
            "ohlcv_limit",
            250,
        )
    )

    minimum_history_4h = 55

    minimum_history_1h = max(
        30,
        int(
            strategy.get(
                "breakout_lookback",
                5,
            )
        ) + 15,
    )

    minimum_volume_24h = float(
        market.get(
            "min_quote_volume_24h_usdt",
            10_000_000,
        )
    )

    maximum_spread_bps = float(
        market.get(
            "max_spread_bps",
            10,
        )
    )

    print(
        "\nCRYPTO SCOUT AI — "
        "SHORT-TERM MOMENTUM SCAN"
    )
    print("=" * 95)
    print(
        f"Exchange: {exchange_name}"
    )
    print(
        f"Market regime timeframe: "
        f"{regime_timeframe}"
    )
    print(
        f"Entry signal timeframe: "
        f"{signal_timeframe}"
    )
    print(
        "Maximum intended holding period: "
        "48 hours"
    )
    print(
        "Configured universe: "
        f"{market.get('universe_size', 500)} "
        "CoinGecko assets"
    )
    print(
        f"Minimum 24h quote volume: "
        f"${minimum_volume_24h:,.0f}"
    )
    print(
        f"Maximum bid/ask spread: "
        f"{maximum_spread_bps:.2f} bps"
    )
    print(
        "Mode: PAPER — "
        "eligible signals may open virtual positions."
    )
    print()

    universe = get_tradable_crypto_universe(
        config,
        exchange=exchange,
    )

    if not universe:
        raise RuntimeError(
            "No eligible Binance spot pairs were found."
        )

    symbols = [
        coin["exchange_symbol"]
        for coin in universe
    ]

    ticker_snapshots = (
        fetch_ticker_snapshots(
            exchange,
            symbols,
        )
    )

    scan_time = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    signals = []

    ticker_rejected = 0
    volume_rejected = 0
    spread_rejected = 0
    history_rejected = 0
    data_errors = 0

    for index, coin in enumerate(
        universe,
        start=1,
    ):
        ticker = coin["ticker"]
        symbol = coin["exchange_symbol"]

        print(
            f"[{index}/{len(universe)}] "
            f"{ticker} ({symbol}) ",
            end="",
            flush=True,
        )

        ticker_data = (
            ticker_snapshots.get(symbol)
        )

        if not ticker_data:
            ticker_rejected += 1
            print(
                "SKIP — ticker data unavailable"
            )
            continue

        market_stats = get_market_stats(
            ticker_data
        )

        if market_stats is None:
            ticker_rejected += 1
            print(
                "SKIP — volume/spread data incomplete"
            )
            continue

        quote_volume = market_stats[
            "quote_volume_24h_usdt"
        ]

        spread_bps = market_stats[
            "spread_bps"
        ]

        if quote_volume < minimum_volume_24h:
            volume_rejected += 1
            print(
                "SKIP — 24h volume "
                f"${quote_volume:,.0f} "
                "below threshold"
            )
            continue

        if spread_bps > maximum_spread_bps:
            spread_rejected += 1
            print(
                "SKIP — spread "
                f"{spread_bps:.2f} bps "
                "exceeds threshold"
            )
            continue

        try:
            regime_df = fetch_closed_ohlcv(
                exchange=exchange,
                symbol=symbol,
                timeframe=regime_timeframe,
                limit=candle_limit,
            )

            hourly_df = fetch_closed_ohlcv(
                exchange=exchange,
                symbol=symbol,
                timeframe=signal_timeframe,
                limit=candle_limit,
            )

            if (
                len(regime_df)
                < minimum_history_4h
                or len(hourly_df)
                < minimum_history_1h
            ):
                history_rejected += 1
                print(
                    "SKIP — insufficient "
                    "closed candles "
                    f"(4h={len(regime_df)}, "
                    f"1h={len(hourly_df)})"
                )
                continue

            signal = build_short_term_signal(
                regime_df=regime_df,
                hourly_df=hourly_df,
                ticker=ticker,
                symbol=symbol,
                config=config,
                scan_time=scan_time,
                market_stats=market_stats,
            )

            signals.append(signal)

            if signal["entry_eligible"]:
                decision = (
                    "ENTRY SETUP"
                )
            else:
                decision = (
                    "NO ENTRY: "
                    + ", ".join(
                        signal[
                            "rejection_reasons"
                        ]
                    )
                )

            print(
                f"score="
                f"{signal['technical_score']:.2f} | "
                f"RSI="
                f"{signal['rsi14']:.1f} | "
                f"1h volume ratio="
                f"{signal['volume_ratio_1h']:.2f} | "
                f"spread="
                f"{spread_bps:.2f} bps | "
                f"{decision}"
            )

        except Exception as exc:
            data_errors += 1
            print(
                f"ERROR — {exc}"
            )

    eligible_signals = rank_signals(
        signals,
        eligible_only=True,
    )

    top_signals = select_top_signals(
        eligible_signals,
        limit=10,
        eligible_only=True,
    )

    print(
        "\nCRYPTO SCOUT AI — "
        "DIAGNOSTICS"
    )
    print("=" * 95)
    print(
        "Binance spot pairs in universe: "
        f"{len(universe)}"
    )
    print(
        "Rejected — ticker data: "
        f"{ticker_rejected}"
    )
    print(
        "Rejected — 24h volume: "
        f"{volume_rejected}"
    )
    print(
        "Rejected — spread: "
        f"{spread_rejected}"
    )
    print(
        "Rejected — historical candles: "
        f"{history_rejected}"
    )
    print(
        "Data/indicator errors: "
        f"{data_errors}"
    )
    print(
        "Pairs evaluated by the strategy: "
        f"{len(signals)}"
    )
    print(
        "Entry-eligible signals: "
        f"{len(eligible_signals)}"
    )
    print(
        "Top eligible signals: "
        f"{len(top_signals)}"
    )
    print()

    print(
        f"{'Ticker':10s} "
        f"{'Score':>7s} "
        f"{'4h Regime':>10s} "
        f"{'1h Setup':>9s} "
        f"{'RSI':>7s} "
        f"{'Vol 1h':>8s} "
        f"{'Spread':>8s} "
        f"{'Eligible':>9s}  Decision"
    )

    print(
        "-" * 115
    )

    for signal in rank_signals(
        signals,
        eligible_only=False,
    ):
        decision = (
            "READY"
            if signal[
                "entry_eligible"
            ]
            else ", ".join(
                signal[
                    "rejection_reasons"
                ]
            )
        )

        print(
            f"{signal['ticker']:10s} "
            f"{signal['technical_score']:7.2f} "
            f"{str(signal['regime_ok']):>10s} "
            f"{str(signal['setup_ok']):>9s} "
            f"{signal['rsi14']:7.2f} "
            f"{signal['volume_ratio_1h']:8.2f} "
            f"{signal['spread_bps']:8.2f} "
            f"{str(signal['entry_eligible']):>9s}  "
            f"{decision}"
        )

    # ------------------------------------------------------------------
    # SAVE SCAN RESULTS
    # ------------------------------------------------------------------

    scan_rows = []

    for rank, signal in enumerate(
        top_signals,
        start=1,
    ):
        scan_rows.append(
            {
                "scan_time": signal[
                    "scan_time"
                ],
                "rank": rank,
                "ticker": signal[
                    "ticker"
                ],
                "symbol": signal[
                    "symbol"
                ],
                "price": signal[
                    "price"
                ],
                "technical_score": signal[
                    "technical_score"
                ],
                "trend_score": signal[
                    "trend_score"
                ],
                "momentum_score": signal[
                    "momentum_score"
                ],
                "rsi_score": signal[
                    "rsi_score"
                ],
                "volume_score": signal[
                    "volume_score"
                ],
                "breakout_score": signal[
                    "breakout_score"
                ],
                "atr": signal[
                    "atr"
                ],
                "stop_price": signal[
                    "stop_price"
                ],
                "target_price": signal[
                    "target_price"
                ],
                "risk_reward": signal[
                    "risk_reward"
                ],
            }
        )

    insert_scan_results(
        scan_rows
    )

    # ------------------------------------------------------------------
    # PAPER PORTFOLIO
    # ------------------------------------------------------------------

    print(
        "\nPAPER PORTFOLIO"
    )
    print("=" * 95)

    if not top_signals:
        print(
            "No eligible signals. "
            "No paper position opened."
        )
    else:
        opened = sync_from_top10(
            signals=top_signals,
            config=config,
        )

        if not opened:
            print(
                "No new paper positions opened. "
                "The existing portfolio may be full "
                "or the signals may already be open."
            )

    # ------------------------------------------------------------------
    # OPPORTUNITIES
    # ------------------------------------------------------------------

    print(
        "\nENTRY-ELIGIBLE OPPORTUNITIES"
    )
    print("=" * 95)

    if not top_signals:
        print(
            "No entry satisfied all "
            "configured conditions."
        )
    else:
        for rank, signal in enumerate(
            top_signals,
            start=1,
        ):
            print(
                f"{rank:2d}. "
                f"{signal['ticker']:10s} "
                f"entry="
                f"{signal['price']:.8g} "
                f"stop="
                f"{signal['stop_price']:.8g} "
                f"target="
                f"{signal['target_price']:.8g} "
                f"R/R="
                f"{signal['risk_reward']:.2f} "
                f"score="
                f"{signal['technical_score']:.2f}"
            )

    print(
        "\nSCAN COMPLETE — "
        "paper execution only. "
        "No real exchange orders were sent."
    )


if __name__ == "__main__":
    main()