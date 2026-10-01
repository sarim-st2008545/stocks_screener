#!/usr/bin/env python3
"""
One-time migration: local SQLite (data/records.db) → Neon PostgreSQL.

Usage:
    DATABASE_URL="postgresql://..." python scripts/migrate_to_neon.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = PROJECT_ROOT / "data" / "records.db"


def migrate():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: Set DATABASE_URL environment variable first.")
        print('  export DATABASE_URL="postgresql://neondb_owner:...@.../neondb?sslmode=require"')
        sys.exit(1)

    if not SQLITE_PATH.exists():
        print(f"WARNING: No SQLite database at {SQLITE_PATH} — creating empty Neon schema only.")

    # ── Connect to Neon ──────────────────────────────────────────────
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        print("ERROR: psycopg2-binary not installed. Run: pip install psycopg2-binary")
        sys.exit(1)

    pg = psycopg2.connect(db_url)
    pg_cur = pg.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # ── Create tables ────────────────────────────────────────────────
    print("Creating PostgreSQL tables on Neon...")
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.db import _PG_SCHEMA_STMTS

    for stmt in _PG_SCHEMA_STMTS:
        pg_cur.execute(stmt.strip())
    pg.commit()
    print("  ✓ Tables created.")

    if not SQLITE_PATH.exists():
        pg.close()
        print("Done (no local data to migrate).")
        return

    # ── Connect to local SQLite ──────────────────────────────────────
    lite = sqlite3.connect(str(SQLITE_PATH))
    lite.row_factory = sqlite3.Row

    # ── Migrate settings ─────────────────────────────────────────────
    lite_cur = lite.cursor()
    lite_cur.execute("SELECT key, value FROM settings")
    settings_rows = lite_cur.fetchall()
    for row in settings_rows:
        pg_cur.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (row["key"], row["value"]),
        )
    print(f"  ✓ Settings: {len(settings_rows)} rows migrated.")

    # ── Migrate signals (preserve IDs for trade FK linkage) ──────────
    lite_cur.execute("SELECT * FROM signals ORDER BY id")
    signals = [dict(r) for r in lite_cur.fetchall()]
    for s in signals:
        pg_cur.execute(
            """
            INSERT INTO signals (
                id, system, signal_date, ticker, name, segment, signal_type,
                price, stop_loss, target_price, stop_pct, target_pct, rr_ratio,
                shariah_screen, metadata, status, outcome_pnl_pct, outcome_date, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (system, signal_date, ticker) DO NOTHING
            """,
            (
                s["id"], s["system"], s["signal_date"], s["ticker"], s["name"],
                s["segment"], s["signal_type"], s["price"], s["stop_loss"],
                s["target_price"], s["stop_pct"], s["target_pct"], s["rr_ratio"],
                s["shariah_screen"], s["metadata"], s["status"],
                s["outcome_pnl_pct"], s["outcome_date"], s["created_at"],
            ),
        )
    if signals:
        pg_cur.execute("SELECT setval('signals_id_seq', (SELECT COALESCE(MAX(id), 1) FROM signals))")
    print(f"  ✓ Signals: {len(signals)} rows migrated.")

    # ── Migrate trades (preserve IDs) ────────────────────────────────
    lite_cur.execute("SELECT * FROM trades ORDER BY id")
    trades = [dict(r) for r in lite_cur.fetchall()]
    for t in trades:
        pg_cur.execute(
            """
            INSERT INTO trades (
                id, signal_id, system, ticker, direction, shares, entry_date,
                entry_price, stop_loss, target_price, status, exit_date, exit_price,
                exit_reason, pnl_amount, pnl_pct, notes, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                t["id"], t["signal_id"], t["system"], t["ticker"], t["direction"],
                t["shares"], t["entry_date"], t["entry_price"], t["stop_loss"],
                t["target_price"], t["status"], t["exit_date"], t["exit_price"],
                t["exit_reason"], t["pnl_amount"], t["pnl_pct"], t["notes"],
                t["created_at"],
            ),
        )
    if trades:
        pg_cur.execute("SELECT setval('trades_id_seq', (SELECT COALESCE(MAX(id), 1) FROM trades))")
    print(f"  ✓ Trades: {len(trades)} rows migrated.")

    pg.commit()

    # ── Verify ───────────────────────────────────────────────────────
    print("\n── Verification ──")
    for table in ("settings", "signals", "trades"):
        pg_cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
        row = pg_cur.fetchone()
        count = row["cnt"] if isinstance(row, dict) else row[0]
        print(f"  {table}: {count} rows in Neon PostgreSQL")

    pg.close()
    lite.close()
    print("\n✅ Migration complete! Data is now persistent on Neon.")


if __name__ == "__main__":
    migrate()
