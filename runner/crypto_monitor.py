from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import ccxt

from database.db import close_position, open_positions
from agents.scanner import load_config


DEFAULT_MONITOR_TIMEFRAME = "5m"
DEFAULT_INTERVAL_MINUTES = 5
DEFAULT_MAX_HOLDING_HOURS = 48


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO timestamp and normalize it to UTC."""
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def utc_now() -> datetime:
    """Return current UTC time."""
    return datetime.now(timezone.utc)


def create_exchange(config: dict[str, Any]):
    """Create a public CCXT exchange client."""
    market = config.get("market", {})

    exchange_name = str(
        market.get("exchange", "binance")
    ).lower()

    if not hasattr(ccxt, exchange_name):
        raise ValueError(
            f"Unsupported CCXT exchange: {exchange_name}"
        )

    exchange_class = getattr(ccxt, exchange_name)

    return exchange_class(
        {
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
            },
        }
    )


def fetch_latest_5m_candle(
    exchange,
    symbol: str,
    timeframe: str = DEFAULT_MONITOR_TIMEFRAME,
) -> dict[str, float]:
    """
    Fetch the most recent OHLCV candle.

    The latest candle may still be forming. That is intentional:
    its high/low are used to detect whether stop or target has been
    touched since the last monitoring cycle.
    """
    candles = exchange.fetch_ohlcv(
        symbol,
        timeframe=timeframe,
        limit=2,
    )

    if not candles:
        raise RuntimeError(
            f"No OHLCV data returned for {symbol}."
        )

    candle = candles[-1]

    if len(candle) < 6:
        raise RuntimeError(
            f"Invalid OHLCV response for {symbol}."
        )

    return {
        "timestamp": float(candle[0]),
        "open": float(candle[1]),
        "high": float(candle[2]),
        "low": float(candle[3]),
        "close": float(candle[4]),
        "volume": float(candle[5]),
    }


def evaluate_position(
    position,
    candle: dict[str, float],
    *,
    now: datetime,
    max_holding_hours: float,
) -> tuple[str | None, float | None]:
    """
    Determine whether an open position must be closed.

    Priority:
    1. STOP
    2. TARGET
    3. 48-hour time limit

    If both stop and target are touched inside the same 5m candle,
    STOP wins because it is the conservative assumption.
    """
    stop_price = float(position["stop_price"])
    target_price = float(position["target_price"])

    entry_time = parse_iso_datetime(
        str(position["entry_time"])
    )

    holding_deadline = (
        entry_time
        + timedelta(hours=max_holding_hours)
    )

    candle_low = float(candle["low"])
    candle_high = float(candle["high"])

    stop_hit = candle_low <= stop_price
    target_hit = candle_high >= target_price

    if stop_hit:
        return "STOP", stop_price

    if target_hit:
        return "TARGET", target_price

    if now >= holding_deadline:
        return "TIME_LIMIT", float(candle["close"])

    return None, None


def monitor_once(
    config: dict[str, Any],
    *,
    exchange=None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Perform one monitoring cycle.

    No exchange orders are ever sent.
    All closures are paper-trading database operations.
    """
    if now is None:
        now = utc_now()

    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    now = now.astimezone(timezone.utc)

    strategy = config.get("strategy", {})
    market = config.get("market", {})
    monitor = config.get("monitor", {})

    max_holding_hours = float(
        strategy.get(
            "max_holding_hours",
            DEFAULT_MAX_HOLDING_HOURS,
        )
    )

    monitor_timeframe = str(
        market.get(
            "monitor_timeframe",
            DEFAULT_MONITOR_TIMEFRAME,
        )
    )

    if max_holding_hours <= 0:
        raise ValueError(
            "max_holding_hours must be positive."
        )

    if monitor_timeframe != "5m":
        raise ValueError(
            "Crypto monitor V1 expects a 5m monitoring timeframe."
        )

    positions = open_positions()

    print(
        "\nCRYPTO SCOUT AI — PAPER POSITION MONITOR"
    )
    print("=" * 90)
    print(
        f"Time: {now.isoformat()}"
    )
    print(
        f"Monitoring timeframe: {monitor_timeframe}"
    )
    print(
        f"Maximum holding period: "
        f"{max_holding_hours:.0f} hours"
    )
    print(
        f"Open positions: {len(positions)}"
    )

    if not positions:
        print(
            "No open paper positions."
        )
        return []

    if exchange is None:
        exchange = create_exchange(config)

    closed: list[dict[str, Any]] = []

    for position in positions:
        position_id = int(position["id"])
        ticker = str(position["ticker"])
        symbol = str(position["symbol"])

        print(
            f"\n[{position_id}] "
            f"{ticker} ({symbol})"
        )
        print(
            f"  entry={float(position['entry_price']):.8g} | "
            f"stop={float(position['stop_price']):.8g} | "
            f"target={float(position['target_price']):.8g}"
        )

        try:
            candle = fetch_latest_5m_candle(
                exchange=exchange,
                symbol=symbol,
                timeframe=monitor_timeframe,
            )

            print(
                f"  5m OHLC="
                f"{candle['open']:.8g}/"
                f"{candle['high']:.8g}/"
                f"{candle['low']:.8g}/"
                f"{candle['close']:.8g}"
            )

            reason, exit_price = evaluate_position(
                position,
                candle,
                now=now,
                max_holding_hours=max_holding_hours,
            )

            if reason is None:
                print(
                    "  HOLD — no exit condition triggered."
                )
                continue

            close_position(
                position_id=position_id,
                exit_time=now.isoformat(),
                exit_price=float(exit_price),
                exit_reason=reason,
            )

            closed_item = {
                "position_id": position_id,
                "ticker": ticker,
                "symbol": symbol,
                "exit_reason": reason,
                "exit_price": float(exit_price),
            }

            closed.append(closed_item)

            print(
                f"  CLOSE PAPER — "
                f"{reason} @ {float(exit_price):.8g}"
            )

        except Exception as exc:
            print(
                f"  ERROR — monitoring failed: {exc}"
            )

    print(
        f"\nPaper positions closed this cycle: "
        f"{len(closed)}"
    )

    return closed


def run_loop(
    config: dict[str, Any],
) -> None:
    """Run the local 5-minute monitor continuously."""
    monitor = config.get("monitor", {})

    interval_minutes = float(
        monitor.get(
            "interval_minutes",
            DEFAULT_INTERVAL_MINUTES,
        )
    )

    if interval_minutes <= 0:
        raise ValueError(
            "interval_minutes must be positive."
        )

    interval_seconds = (
        interval_minutes * 60.0
    )

    exchange = create_exchange(config)

    while True:
        cycle_started = time.monotonic()

        try:
            monitor_once(
                config,
                exchange=exchange,
            )
        except KeyboardInterrupt:
            print(
                "\nMonitor stopped by user."
            )
            return
        except Exception as exc:
            print(
                f"\nMONITOR ERROR: {exc}"
            )

        elapsed = (
            time.monotonic()
            - cycle_started
        )

        sleep_seconds = max(
            0.0,
            interval_seconds - elapsed,
        )

        print(
            f"\nNext monitoring cycle in "
            f"{sleep_seconds / 60.0:.1f} minutes."
        )

        try:
            time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            print(
                "\nMonitor stopped by user."
            )
            return


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Crypto Scout AI paper position monitor"
        )
    )

    parser.add_argument(
        "--once",
        action="store_true",
        help=(
            "Run one monitoring cycle and exit."
        ),
    )

    args = parser.parse_args()

    config = load_config()

    if args.once:
        monitor_once(config)
    else:
        run_loop(config)


if __name__ == "__main__":
    main()