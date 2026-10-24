from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


# Independent database for Crypto Scout AI.
DB_PATH = Path("data/crypto_scout.db")

DEFAULT_STARTING_CASH = 10000.0


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Open a database connection and handle commit, rollback and cleanup."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")

    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(starting_cash: float = DEFAULT_STARTING_CASH) -> None:
    """Create the database schema and initialize the virtual account once."""
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS account (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                cash REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy_type TEXT NOT NULL DEFAULT 'SWING',
                status TEXT NOT NULL CHECK (
                    status IN ('OPEN', 'CLOSED')
                ),
                entry_time TEXT NOT NULL,
                exit_time TEXT,
                entry_price REAL NOT NULL CHECK (entry_price > 0),
                exit_price REAL,
                shares REAL NOT NULL CHECK (shares > 0),
                notional REAL NOT NULL CHECK (notional > 0),
                stop_price REAL NOT NULL,
                target_price REAL NOT NULL,
                technical_score REAL NOT NULL,
                trend_score REAL,
                momentum_score REAL,
                rsi_score REAL,
                volume_score REAL,
                breakout_score REAL,
                risk_reward REAL,
                trade_reason TEXT,
                realized_pnl REAL,
                realized_return_pct REAL,
                exit_reason TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_positions_status
                ON positions(status);

            CREATE INDEX IF NOT EXISTS idx_positions_ticker
                ON positions(ticker);

            CREATE TABLE IF NOT EXISTS scan_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time TEXT NOT NULL,
                rank INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                symbol TEXT NOT NULL,
                price REAL NOT NULL,
                technical_score REAL NOT NULL,
                trend_score REAL,
                momentum_score REAL,
                rsi_score REAL,
                volume_score REAL,
                breakout_score REAL,
                atr REAL,
                stop_price REAL,
                target_price REAL,
                risk_reward REAL
            );
            """
        )

        account = conn.execute(
            "SELECT id FROM account WHERE id = 1"
        ).fetchone()

        if account is None:
            conn.execute(
                "INSERT INTO account (id, cash) VALUES (1, ?)",
                (float(starting_cash),),
            )


def get_cash() -> float:
    """Return the available virtual cash balance."""
    init_db()

    with get_connection() as conn:
        row = conn.execute(
            "SELECT cash FROM account WHERE id = 1"
        ).fetchone()

    return float(row["cash"])


def set_cash(value: float) -> None:
    """Update the virtual cash balance."""
    if value < -0.000001:
        raise ValueError("Virtual cash balance cannot be negative.")

    init_db()

    with get_connection() as conn:
        conn.execute(
            "UPDATE account SET cash = ? WHERE id = 1",
            (max(0.0, float(value)),),
        )


def open_positions() -> list[sqlite3.Row]:
    """Return all currently open paper positions."""
    init_db()

    with get_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM positions
            WHERE status = 'OPEN'
            ORDER BY id
            """
        ).fetchall()


def closed_positions() -> list[sqlite3.Row]:
    """Return closed positions for future performance analysis."""
    init_db()

    with get_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM positions
            WHERE status = 'CLOSED'
            ORDER BY exit_time, id
            """
        ).fetchall()


def all_positions() -> list[sqlite3.Row]:
    """Return the complete paper-trading history."""
    init_db()

    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM positions ORDER BY id"
        ).fetchall()


def ticker_is_open(ticker: str) -> bool:
    """Check whether a ticker already has an open paper position."""
    init_db()

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM positions
            WHERE UPPER(ticker) = UPPER(?)
              AND status = 'OPEN'
            LIMIT 1
            """,
            (ticker,),
        ).fetchone()

    return row is not None


def insert_scan_results(rows: list[dict[str, Any]]) -> None:
    """Replace the saved scan snapshot with the latest ranked results."""
    init_db()

    columns = [
        "scan_time",
        "rank",
        "ticker",
        "symbol",
        "price",
        "technical_score",
        "trend_score",
        "momentum_score",
        "rsi_score",
        "volume_score",
        "breakout_score",
        "atr",
        "stop_price",
        "target_price",
        "risk_reward",
    ]

    with get_connection() as conn:
        conn.execute("DELETE FROM scan_results")

        if not rows:
            return

        placeholders = ", ".join(f":{column}" for column in columns)
        column_names = ", ".join(columns)

        conn.executemany(
            f"""
            INSERT INTO scan_results ({column_names})
            VALUES ({placeholders})
            """,
            rows,
        )


def latest_scan_results() -> list[sqlite3.Row]:
    """Return the most recently saved scan snapshot."""
    init_db()

    with get_connection() as conn:
        return conn.execute(
            """
            SELECT *
            FROM scan_results
            ORDER BY rank ASC
            """
        ).fetchall()


def insert_position(data: dict[str, Any]) -> int:
    """Insert a new open paper position and return its database ID."""
    required = [
        "ticker",
        "symbol",
        "entry_time",
        "entry_price",
        "shares",
        "notional",
        "stop_price",
        "target_price",
        "technical_score",
    ]

    missing = [key for key in required if key not in data]
    if missing:
        raise ValueError(
            f"Missing required position fields: {', '.join(missing)}"
        )

    if float(data["entry_price"]) <= 0:
        raise ValueError("Entry price must be positive.")

    if float(data["shares"]) <= 0:
        raise ValueError("Position size must be positive.")

    init_db()

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO positions (
                ticker,
                symbol,
                strategy_type,
                status,
                entry_time,
                entry_price,
                shares,
                notional,
                stop_price,
                target_price,
                technical_score,
                trend_score,
                momentum_score,
                rsi_score,
                volume_score,
                breakout_score,
                risk_reward,
                trade_reason
            )
            VALUES (
                :ticker,
                :symbol,
                :strategy_type,
                'OPEN',
                :entry_time,
                :entry_price,
                :shares,
                :notional,
                :stop_price,
                :target_price,
                :technical_score,
                :trend_score,
                :momentum_score,
                :rsi_score,
                :volume_score,
                :breakout_score,
                :risk_reward,
                :trade_reason
            )
            """,
            {
                "ticker": data["ticker"],
                "symbol": data["symbol"],
                "strategy_type": data.get("strategy_type", "SWING"),
                "entry_time": data["entry_time"],
                "entry_price": float(data["entry_price"]),
                "shares": float(data["shares"]),
                "notional": float(data["notional"]),
                "stop_price": float(data["stop_price"]),
                "target_price": float(data["target_price"]),
                "technical_score": float(data["technical_score"]),
                "trend_score": data.get("trend_score"),
                "momentum_score": data.get("momentum_score"),
                "rsi_score": data.get("rsi_score"),
                "volume_score": data.get("volume_score"),
                "breakout_score": data.get("breakout_score"),
                "risk_reward": data.get("risk_reward"),
                "trade_reason": data.get("trade_reason"),
            },
        )

        return int(cursor.lastrowid)


def close_position(
    position_id: int,
    exit_time: str,
    exit_price: float,
    exit_reason: str,
) -> None:
    """Close a paper position, record its P&L and release allocated cash."""
    if exit_price <= 0:
        raise ValueError("Exit price must be positive.")

    init_db()

    with get_connection() as conn:
        position = conn.execute(
            """
            SELECT *
            FROM positions
            WHERE id = ?
              AND status = 'OPEN'
            """,
            (position_id,),
        ).fetchone()

        # Prevent duplicate closures.
        if position is None:
            return

        entry_price = float(position["entry_price"])
        shares = float(position["shares"])
        notional = float(position["notional"])

        pnl = (float(exit_price) - entry_price) * shares
        return_pct = (
            (float(exit_price) / entry_price) - 1.0
        ) * 100.0

        conn.execute(
            """
            UPDATE positions
            SET status = 'CLOSED',
                exit_time = ?,
                exit_price = ?,
                realized_pnl = ?,
                realized_return_pct = ?,
                exit_reason = ?
            WHERE id = ?
              AND status = 'OPEN'
            """,
            (
                exit_time,
                float(exit_price),
                pnl,
                return_pct,
                exit_reason,
                position_id,
            ),
        )

        account = conn.execute(
            "SELECT cash FROM account WHERE id = 1"
        ).fetchone()

        new_cash = float(account["cash"]) + notional + pnl

        conn.execute(
            "UPDATE account SET cash = ? WHERE id = 1",
            (new_cash,),
        )
