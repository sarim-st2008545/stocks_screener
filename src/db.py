"""
Database abstraction layer for Aura Quant trading system.

Detects DATABASE_URL environment variable:
  - Set     → connects to PostgreSQL (Neon serverless)
  - Not set → falls back to local SQLite (data/records.db)

This ensures the portal works identically on Render (cloud) and local development.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
SQLITE_PATH = BASE_DIR / "data" / "records.db"

DATABASE_URL: str = os.environ.get("DATABASE_URL", "")


# ─── Public helpers ───────────────────────────────────────────────────

def is_postgres() -> bool:
    """True when connected to PostgreSQL (Neon), False for local SQLite."""
    return bool(DATABASE_URL)


def get_connection(db_path: Optional[Path] = None):
    """
    Return a wrapped database connection.

    * PostgreSQL when DATABASE_URL is set (db_path is ignored).
    * SQLite otherwise, using *db_path* or the default SQLITE_PATH.
    """
    if is_postgres():
        import psycopg2
        raw = psycopg2.connect(DATABASE_URL)
        return _ConnectionWrapper(raw, pg=True)
    else:
        path = Path(db_path) if db_path else SQLITE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(str(path))
        raw.row_factory = sqlite3.Row
        return _ConnectionWrapper(raw, pg=False)


# ─── Thin wrappers so callers never need backend-specific code ────────

class _ConnectionWrapper:
    """Normalises SQLite / psycopg2 connection objects."""

    def __init__(self, conn, pg: bool):
        self._conn = conn
        self._pg = pg

    # Cursor -----------------------------------------------------------
    def cursor(self):
        if self._pg:
            import psycopg2.extras
            real = self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            return _CursorWrapper(real, translate=True)
        return _CursorWrapper(self._conn.cursor(), translate=False)

    # Transaction helpers ----------------------------------------------
    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def rollback(self):
        self._conn.rollback()


class _CursorWrapper:
    """
    Thin cursor proxy:
      * Translates ``?`` → ``%s`` for PostgreSQL automatically.
      * Delegates all standard cursor operations.
    """

    def __init__(self, cur, translate: bool):
        self._cur = cur
        self._translate = translate

    def execute(self, sql: str, params=None):
        if self._translate:
            sql = sql.replace("?", "%s")
        if params is not None:
            self._cur.execute(sql, params)
        else:
            self._cur.execute(sql)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size=None):
        return self._cur.fetchmany(size) if size else self._cur.fetchmany()

    @property
    def lastrowid(self) -> Optional[int]:
        return getattr(self._cur, "lastrowid", None)

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description


# ─── Schema DDL ───────────────────────────────────────────────────────

_PG_SCHEMA_STMTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        id             SERIAL PRIMARY KEY,
        system         TEXT NOT NULL,
        signal_date    TEXT NOT NULL,
        ticker         TEXT NOT NULL,
        name           TEXT,
        segment        TEXT,
        signal_type    TEXT NOT NULL,
        price          REAL NOT NULL,
        stop_loss      REAL NOT NULL,
        target_price   REAL NOT NULL,
        stop_pct       REAL NOT NULL,
        target_pct     REAL NOT NULL,
        rr_ratio       REAL,
        shariah_screen TEXT,
        metadata       TEXT,
        status         TEXT DEFAULT 'PENDING',
        outcome_pnl_pct REAL,
        outcome_date   TEXT,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(system, signal_date, ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trades (
        id             SERIAL PRIMARY KEY,
        signal_id      INTEGER,
        system         TEXT NOT NULL,
        ticker         TEXT NOT NULL,
        direction      TEXT DEFAULT 'LONG',
        shares         REAL NOT NULL,
        entry_date     TEXT NOT NULL,
        entry_price    REAL NOT NULL,
        stop_loss      REAL NOT NULL,
        target_price   REAL NOT NULL,
        status         TEXT DEFAULT 'OPEN',
        exit_date      TEXT,
        exit_price     REAL,
        exit_reason    TEXT,
        pnl_amount     REAL,
        pnl_pct        REAL,
        notes          TEXT,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (signal_id) REFERENCES signals (id)
    )
    """,
]

_SQLITE_SCHEMA_STMTS: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS signals (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        system         TEXT NOT NULL,
        signal_date    TEXT NOT NULL,
        ticker         TEXT NOT NULL,
        name           TEXT,
        segment        TEXT,
        signal_type    TEXT NOT NULL,
        price          REAL NOT NULL,
        stop_loss      REAL NOT NULL,
        target_price   REAL NOT NULL,
        stop_pct       REAL NOT NULL,
        target_pct     REAL NOT NULL,
        rr_ratio       REAL,
        shariah_screen TEXT,
        metadata       TEXT,
        status         TEXT DEFAULT 'PENDING',
        outcome_pnl_pct REAL,
        outcome_date   TEXT,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(system, signal_date, ticker)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trades (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        signal_id      INTEGER,
        system         TEXT NOT NULL,
        ticker         TEXT NOT NULL,
        direction      TEXT DEFAULT 'LONG',
        shares         REAL NOT NULL,
        entry_date     TEXT NOT NULL,
        entry_price    REAL NOT NULL,
        stop_loss      REAL NOT NULL,
        target_price   REAL NOT NULL,
        status         TEXT DEFAULT 'OPEN',
        exit_date      TEXT,
        exit_price     REAL,
        exit_reason    TEXT,
        pnl_amount     REAL,
        pnl_pct        REAL,
        notes          TEXT,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (signal_id) REFERENCES signals (id)
    )
    """,
]


# ─── Schema initialization ───────────────────────────────────────────

def init_db(db_path: Optional[Path] = None):
    """Create all required tables if they don't exist and seed defaults."""
    conn = get_connection(db_path)
    cur = conn.cursor()

    stmts = _PG_SCHEMA_STMTS if is_postgres() else _SQLITE_SCHEMA_STMTS
    for stmt in stmts:
        cur.execute(stmt.strip())

    # Seed default portfolio capital if not yet present
    cur.execute("SELECT value FROM settings WHERE key = 'total_capital'")
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO settings (key, value) VALUES ('total_capital', '10000.0')"
        )

    conn.commit()
    conn.close()
