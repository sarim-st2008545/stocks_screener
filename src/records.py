"""
Database and Performance Tracking Engine for Universe and Galaxy Trading Systems.

Handles:
1. Persistent signal logging with date & ticker deduplication in SQLite.
2. Signal outcome evaluation for ALL signals (even untraded ones).
3. Active and Closed portfolio trade tracking (P&L amount, P&L %, holding days, Islamic Qabd).
4. Configurable portfolio capital settings.
5. Weekly and Monthly Performance Analytics (System Expected vs. Actual Banked).
6. Forward return forecasts based on mathematical trade expectancy.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from src import db

BASE_DIR = Path(__file__).resolve().parent.parent


def get_connection(db_path: Optional[Path] = None):
    """Returns a database connection (PostgreSQL when DATABASE_URL is set, else SQLite)."""
    return db.get_connection(db_path)


def init_db(db_path: Optional[Path] = None):
    """Initializes the database tables (delegates to db module)."""
    db.init_db(db_path)



def get_setting(key: str, default: str = "", db_path: Optional[Path] = None) -> str:
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value: str, db_path: Optional[Path] = None):
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO settings (key, value, updated_at) 
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
    """, (key, value))
    conn.commit()
    conn.close()


def record_scanner_signals(
    system: str,
    setups: list[dict[str, Any]],
    scan_date: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> int:
    """
    Registers a list of setups for a given system and date.
    Deduplicates on (system, signal_date, ticker).
    Returns count of recorded/updated signals.
    """
    if not setups:
        return 0

    init_db(db_path)
    if not scan_date:
        scan_date = str(date.today())
    elif isinstance(scan_date, (date, datetime)):
        scan_date = scan_date.strftime("%Y-%m-%d")

    conn = get_connection(db_path)
    cur = conn.cursor()
    count = 0

    for s in setups:
        ticker = s.get("ticker")
        name = s.get("name", ticker)
        segment = s.get("segment", "General")
        signal_type = s.get("signal") or s.get("signal_type") or "Mean Reversion"
        price = float(s.get("price", 0.0))
        stop_loss = float(s.get("stop", 0.0))
        target_price = float(s.get("target") or s.get("t1", 0.0))
        stop_pct = float(s.get("stop_pct", 0.0))
        target_pct = float(s.get("target_pct") or s.get("t1_pct", 0.0))
        rr = float(s.get("rr", 0.0))
        shariah = s.get("shariah") or s.get("shariah_screen") or "Verified Halal"

        meta = {k: v for k, v in s.items() if k not in [
            "ticker", "name", "segment", "signal", "signal_type", "price",
            "stop", "target", "t1", "stop_pct", "target_pct", "t1_pct", "rr", "shariah"
        ]}

        cur.execute("""
            INSERT INTO signals (
                system, signal_date, ticker, name, segment, signal_type,
                price, stop_loss, target_price, stop_pct, target_pct,
                rr_ratio, shariah_screen, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(system, signal_date, ticker) DO UPDATE SET
                name = excluded.name,
                segment = excluded.segment,
                signal_type = excluded.signal_type,
                price = excluded.price,
                stop_loss = excluded.stop_loss,
                target_price = excluded.target_price,
                stop_pct = excluded.stop_pct,
                target_pct = excluded.target_pct,
                rr_ratio = excluded.rr_ratio,
                shariah_screen = excluded.shariah_screen,
                metadata = excluded.metadata
        """, (
            system, scan_date, ticker, name, segment, signal_type,
            price, stop_loss, target_price, stop_pct, target_pct,
            rr, shariah, json.dumps(meta)
        ))
        count += 1

    conn.commit()
    conn.close()
    return count


def load_all_cached_price_histories() -> dict[str, pd.DataFrame]:
    """Loads all available historical price data from disk."""
    histories: dict[str, pd.DataFrame] = {}

    # Universe price cache
    p_dir = Path("data/prices")
    if p_dir.exists():
        for f in p_dir.glob("*.csv"):
            sym = f.stem.upper()
            try:
                df = pd.read_csv(f, index_col=0, parse_dates=True)
                if not df.empty:
                    df.columns = [c.lower() for c in df.columns]
                    histories[sym] = df
            except Exception:
                pass

    # Galaxy price cache
    g_dir = Path("data/galaxy")
    if g_dir.exists():
        for f in g_dir.glob("*.csv"):
            sym = f.stem.replace("_5y", "").upper()
            try:
                df = pd.read_csv(f, index_col=0, parse_dates=True)
                if not df.empty:
                    df.columns = [c.lower() for c in df.columns]
                    if sym not in histories or len(df) > len(histories[sym]):
                        histories[sym] = df
            except Exception:
                pass

    return histories


def load_price_history_for_ticker(ticker: str) -> Optional[pd.DataFrame]:
    """Loads price history for a single ticker from disk."""
    sym = ticker.upper()
    # Check data/prices/
    p_path = BASE_DIR / "data" / "prices" / f"{sym}.csv"
    if p_path.exists():
        try:
            df = pd.read_csv(p_path, index_col=0, parse_dates=True)
            if not df.empty:
                df.columns = [c.lower() for c in df.columns]
                return df
        except Exception:
            pass

    # Check data/galaxy/
    g_path = BASE_DIR / "data" / "galaxy" / f"{sym}_5y.csv"
    if not g_path.exists():
        g_path = BASE_DIR / "data" / "galaxy" / f"{sym}.csv"
    if g_path.exists():
        try:
            df = pd.read_csv(g_path, index_col=0, parse_dates=True)
            if not df.empty:
                df.columns = [c.lower() for c in df.columns]
                return df
        except Exception:
            pass

    return None


def load_price_histories_for_tickers(tickers: set[str]) -> dict[str, pd.DataFrame]:
    """Loads price histories for specific requested tickers only."""
    histories: dict[str, pd.DataFrame] = {}
    for t in tickers:
        df = load_price_history_for_ticker(t)
        if df is not None:
            histories[t.upper()] = df
    return histories


def auto_evaluate_all_signals(
    price_histories: Optional[dict[str, pd.DataFrame]] = None,
    db_path: Optional[Path] = None,
) -> int:
    """
    Evaluates ALL signals in the database (both traded and untraded).
    Updates status to HIT_TARGET, HIT_STOP, EXPIRED, or IN_PLAY with outcome P&L %.
    Only loads price histories for tickers that have unresolved signals.
    """
    init_db(db_path)
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM signals WHERE status IN ('PENDING', 'ACTIVE', 'IN_PLAY')")
    signals = cur.fetchall()
    if not signals:
        conn.close()
        return 0

    if price_histories is None:
        needed_tickers = {s["ticker"].upper() for s in signals}
        price_histories = load_price_histories_for_tickers(needed_tickers)

    if not price_histories:
        conn.close()
        return 0

    updated_count = 0

    for s in signals:
        ticker = s["ticker"].upper()
        sig_date_str = s["signal_date"]
        target = float(s["target_price"])
        stop = float(s["stop_loss"])
        entry_p = float(s["price"])
        system = s["system"]
        max_days = 4 if system == "galaxy" else 25

        df = price_histories.get(ticker)
        if df is None or df.empty:
            continue

        try:
            sig_date = pd.to_datetime(sig_date_str)
            forward_bars = df.loc[df.index > sig_date]
        except Exception:
            continue

        if forward_bars.empty:
            # Signal was generated on latest bar: currently IN_PLAY at entry price
            if s["status"] != "IN_PLAY":
                cur.execute("UPDATE signals SET status = 'IN_PLAY', outcome_pnl_pct = 0.0 WHERE id = ?", (s["id"],))
                updated_count += 1
            continue

        resolved = False
        outcome = None
        outcome_pnl = None
        outcome_date = None

        bars_checked = 0
        latest_bar_close = entry_p

        for dt_idx, bar in forward_bars.iterrows():
            bars_checked += 1
            h = float(bar["high"])
            l = float(bar["low"])
            c = float(bar["close"])
            latest_bar_close = c
            dt_s = dt_idx.strftime("%Y-%m-%d") if hasattr(dt_idx, "strftime") else str(dt_idx)[:10]

            # Shariah 3-day minimum holding advisory
            if system == "galaxy" and bars_checked < 3:
                continue

            if h >= target:
                resolved = True
                outcome = "HIT_TARGET"
                outcome_pnl = ((target - entry_p) / entry_p) * 100.0
                outcome_date = dt_s
                break
            elif l <= stop:
                resolved = True
                outcome = "HIT_STOP"
                outcome_pnl = ((stop - entry_p) / entry_p) * 100.0
                outcome_date = dt_s
                break
            elif bars_checked >= max_days:
                resolved = True
                outcome = "EXPIRED"
                outcome_pnl = ((c - entry_p) / entry_p) * 100.0
                outcome_date = dt_s
                break

        if not resolved:
            outcome = "IN_PLAY"
            outcome_pnl = ((latest_bar_close - entry_p) / entry_p) * 100.0
            outcome_date = str(forward_bars.index[-1])[:10]

        if outcome:
            cur.execute("""
                UPDATE signals SET
                    status = ?,
                    outcome_pnl_pct = ?,
                    outcome_date = ?
                WHERE id = ?
            """, (outcome, outcome_pnl, outcome_date, s["id"]))
            updated_count += 1

    conn.commit()
    conn.close()
    return updated_count


# Alias for backward compatibility
evaluate_signals_outcomes = auto_evaluate_all_signals


def get_signals_grouped_by_date(
    system: Optional[str] = None,
    latest_prices: Optional[dict[str, float]] = None,
    db_path: Optional[Path] = None,
) -> dict[str, list[dict[str, Any]]]:
    """Retrieves all signals grouped chronologically by signal_date descending, enriched with live market performance."""
    init_db(db_path)

    # Evaluate any pending/active signals against cached price history
    try:
        auto_evaluate_all_signals(db_path=db_path)
    except Exception:
        pass

    conn = get_connection(db_path)
    cur = conn.cursor()

    query = "SELECT * FROM signals"
    params = []
    if system:
        query += " WHERE system = ?"
        params.append(system)
    query += " ORDER BY signal_date DESC, id DESC"

    cur.execute(query, params)
    rows = cur.fetchall()

    # Query trades to detect which setups have been executed by the user
    cur.execute("SELECT id, signal_id, ticker, system, shares, entry_price, status, entry_date FROM trades")
    trade_rows = [dict(tr) for tr in cur.fetchall()]
    conn.close()

    traded_by_sig_id: dict[int, dict[str, Any]] = {}
    for tr in trade_rows:
        sid = tr.get("signal_id")
        if sid is not None and str(sid).strip() and str(sid).strip().lower() != "null":
            try:
                traded_by_sig_id[int(sid)] = tr
            except (ValueError, TypeError):
                pass

    total_cap = float(get_setting("total_capital", "10000.0", db_path=db_path))

    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        d_str = r["signal_date"]
        if d_str not in grouped:
            grouped[d_str] = []
        item = dict(r)
        if item.get("metadata"):
            try:
                item["metadata"] = json.loads(item["metadata"])
            except Exception:
                pass

        ticker = item["ticker"].upper()
        sig_id = item.get("id")

        # Mark strictly if this specific signal was traded by its unique signal_id
        trade_match = traded_by_sig_id.get(int(sig_id)) if sig_id is not None else None

        if trade_match:
            item["is_traded"] = True
            item["trade_id"] = trade_match["id"]
            item["trade_status"] = trade_match["status"]
            item["trade_shares"] = trade_match["shares"]
            item["trade_entry_price"] = trade_match["entry_price"]
        else:
            item["is_traded"] = False
        entry_p = float(item["price"] or 0.0)
        target_p = float(item["target_price"] or 0.0)
        stop_p = float(item["stop_loss"] or 0.0)

        # Attach live current price
        curr_p = latest_prices.get(ticker, entry_p) if (latest_prices and ticker in latest_prices) else entry_p
        item["current_price"] = round(curr_p, 2)
        price_diff = curr_p - entry_p
        item["price_diff"] = round(price_diff, 2)

        # Live performance vs Trigger price
        if item["status"] in ("IN_PLAY", "ACTIVE", "PENDING"):
            if entry_p > 0:
                pnl_pct = ((curr_p - entry_p) / entry_p) * 100.0
                item["outcome_pnl_pct"] = round(pnl_pct, 2)
            else:
                item["outcome_pnl_pct"] = 0.0

        # Calculate visual target progress percentage (0% = Stop, 100% = Target, 50% = Entry)
        total_range = target_p - stop_p
        if total_range > 0:
            prog = ((curr_p - stop_p) / total_range) * 100.0
            item["progress_pct"] = max(0.0, min(100.0, round(prog, 1)))
        else:
            item["progress_pct"] = 50.0

        # Calculate dollar outcome strictly for 1 single stock (1 share)
        if item["status"] in ("HIT_TARGET", "WON"):
            item["hypothetical_pnl_dollar"] = round(target_p - entry_p, 2)
        elif item["status"] in ("HIT_STOP", "LOST"):
            item["hypothetical_pnl_dollar"] = round(stop_p - entry_p, 2)
        else:
            item["hypothetical_pnl_dollar"] = round(curr_p - entry_p, 2)

        item["price_diff"] = item["hypothetical_pnl_dollar"]
        item["shares"] = 1
        grouped[d_str].append(item)

    return grouped


def open_trade(
    system: str,
    ticker: str,
    shares: float,
    entry_price: float,
    stop_loss: float,
    target_price: float,
    entry_date: Optional[str] = None,
    signal_id: Optional[int] = None,
    notes: str = "",
    db_path: Optional[Path] = None,
) -> int:
    """Opens a new active portfolio position."""
    init_db(db_path)
    if not entry_date:
        entry_date = str(date.today())
    if signal_id is not None and str(signal_id).strip() and str(signal_id).strip().lower() != "null":
        try:
            signal_id = int(signal_id)
        except (ValueError, TypeError):
            signal_id = None
    else:
        signal_id = None

    conn = get_connection(db_path)
    cur = conn.cursor()

    # Deduplication & Idempotency:
    # 1. If linked to a specific signal_id and already OPEN, update it instead of creating a duplicate
    if signal_id is not None:
        cur.execute("SELECT id FROM trades WHERE signal_id = ? AND status = 'OPEN'", (signal_id,))
        existing = cur.fetchone()
        if existing:
            trade_id = existing["id"] if isinstance(existing, dict) else existing[0]
            update_sql = """
                UPDATE trades SET
                    shares = ?, entry_price = ?, stop_loss = ?, target_price = ?, entry_date = ?, notes = ?
                WHERE id = ?
            """
            cur.execute(update_sql, (shares, entry_price, stop_loss, target_price, entry_date, notes, trade_id))
            conn.commit()
            conn.close()
            return trade_id
    else:
        # 2. If entered manually without signal_id, prevent rapid double-clicks on identical trade
        cur.execute(
            "SELECT id FROM trades WHERE ticker = ? AND system = ? AND entry_date = ? AND entry_price = ? AND status = 'OPEN'",
            (ticker.upper(), system, entry_date, entry_price)
        )
        existing = cur.fetchone()
        if existing:
            trade_id = existing["id"] if isinstance(existing, dict) else existing[0]
            update_sql = "UPDATE trades SET shares = ?, stop_loss = ?, target_price = ?, notes = ? WHERE id = ?"
            cur.execute(update_sql, (shares, stop_loss, target_price, notes, trade_id))
            conn.commit()
            conn.close()
            return trade_id

    insert_sql = """
        INSERT INTO trades (
            signal_id, system, ticker, shares, entry_date,
            entry_price, stop_loss, target_price, status, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
    """
    params = (signal_id, system, ticker.upper(), shares, entry_date, entry_price, stop_loss, target_price, notes)

    if db.is_postgres():
        insert_sql += " RETURNING id"
        cur.execute(insert_sql, params)
        row = cur.fetchone()
        trade_id = row["id"]
    else:
        cur.execute(insert_sql, params)
        trade_id = cur.lastrowid

    conn.commit()
    conn.close()
    return trade_id


def close_trade(
    trade_id: int,
    exit_price: float,
    exit_reason: str = "Manual Exit",
    exit_date: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Closes an active portfolio trade and records realized P&L."""
    init_db(db_path)
    if not exit_date:
        exit_date = str(date.today())

    conn = get_connection(db_path)
    cur = conn.cursor()

    cur.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    row = cur.fetchone()
    if not row or row["status"] != "OPEN":
        conn.close()
        return False

    entry_price = float(row["entry_price"])
    shares = float(row["shares"])
    pnl_amount = (exit_price - entry_price) * shares
    pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0

    cur.execute("""
        UPDATE trades SET
            status = 'CLOSED',
            exit_date = ?,
            exit_price = ?,
            exit_reason = ?,
            pnl_amount = ?,
            pnl_pct = ?
        WHERE id = ?
    """, (exit_date, exit_price, exit_reason, pnl_amount, pnl_pct, trade_id))

    if row["signal_id"]:
        outcome = "WON" if pnl_pct > 0 else "LOST"
        cur.execute("""
            UPDATE signals SET
                status = ?,
                outcome_pnl_pct = ?,
                outcome_date = ?
            WHERE id = ?
        """, (outcome, pnl_pct, exit_date, row["signal_id"]))

    conn.commit()
    conn.close()
    return True


def edit_trade(
    trade_id: int,
    shares: Optional[float] = None,
    entry_price: Optional[float] = None,
    stop_loss: Optional[float] = None,
    target_price: Optional[float] = None,
    entry_date: Optional[str] = None,
    notes: Optional[str] = None,
    db_path: Optional[Path] = None,
) -> bool:
    """Updates an existing open trade's parameters (shares, prices, notes)."""
    init_db(db_path)
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades WHERE id = ?", (trade_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return False

    updates = []
    params = []
    if shares is not None:
        updates.append("shares = ?")
        params.append(float(shares))
    if entry_price is not None:
        updates.append("entry_price = ?")
        params.append(float(entry_price))
    if stop_loss is not None:
        updates.append("stop_loss = ?")
        params.append(float(stop_loss))
    if target_price is not None:
        updates.append("target_price = ?")
        params.append(float(target_price))
    if entry_date is not None:
        updates.append("entry_date = ?")
        params.append(str(entry_date))
    if notes is not None:
        updates.append("notes = ?")
        params.append(str(notes))

    if not updates:
        conn.close()
        return True

    params.append(trade_id)
    sql = f"UPDATE trades SET {', '.join(updates)} WHERE id = ?"
    cur.execute(sql, params)
    conn.commit()
    conn.close()
    return True


def delete_trade(trade_id: int, db_path: Optional[Path] = None) -> bool:
    """Permanently deletes a trade from the database."""
    init_db(db_path)
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    deleted = cur.rowcount > 0
    conn.commit()
    conn.close()
    return deleted


def clear_all_trades(db_path: Optional[Path] = None) -> int:
    """Clears all trades (both open and closed) from the database."""
    init_db(db_path)
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("DELETE FROM trades")
    count = cur.rowcount
    conn.commit()
    conn.close()
    return count


def get_active_trades(
    latest_prices: Optional[dict[str, float]] = None,
    db_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """Retrieves all open trades with live/current P&L and days held calculation."""
    init_db(db_path)
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades WHERE status = 'OPEN' ORDER BY entry_date ASC")
    rows = cur.fetchall()
    conn.close()

    today_dt = date.today()
    results = []

    for r in rows:
        item = dict(r)
        entry_p = float(item["entry_price"])
        shares = float(item["shares"])
        ticker = item["ticker"]

        # Days held calculation
        try:
            entry_dt = datetime.strptime(item["entry_date"], "%Y-%m-%d").date()
            days_held = (today_dt - entry_dt).days
        except Exception:
            days_held = 0
        item["days_held"] = max(days_held, 0)
        item["islamic_qabd_met"] = item["days_held"] >= 3

        # Price & Floating P&L
        current_p = latest_prices.get(ticker, entry_p) if latest_prices else entry_p
        item["current_price"] = current_p
        item["cost_basis"] = entry_p * shares
        item["market_value"] = current_p * shares
        item["unrealized_pnl"] = (current_p - entry_p) * shares
        item["unrealized_pnl_pct"] = ((current_p - entry_p) / entry_p) * 100.0

        # Target/Stop progress
        target_p = float(item["target_price"])
        stop_p = float(item["stop_loss"])
        total_range = target_p - stop_p
        if total_range > 0:
            progress = (current_p - stop_p) / total_range * 100.0
            item["progress_pct"] = max(0.0, min(100.0, progress))
        else:
            item["progress_pct"] = 50.0

        results.append(item)

    return results


def get_closed_trades(db_path: Optional[Path] = None) -> list[dict[str, Any]]:
    init_db(db_path)
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY exit_date DESC, id DESC")
    rows = cur.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_portfolio_summary(
    latest_prices: Optional[dict[str, float]] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Calculates overall portfolio status, capital allocation, realized and unrealized P&L."""
    init_db(db_path)
    total_capital = float(get_setting("total_capital", "1000.0", db_path=db_path))
    active = get_active_trades(latest_prices=latest_prices, db_path=db_path)
    closed = get_closed_trades(db_path=db_path)

    deployed_capital = sum(t["cost_basis"] for t in active)
    active_market_value = sum(t["market_value"] for t in active)
    unrealized_pnl = sum(t["unrealized_pnl"] for t in active)
    realized_pnl = sum(t["pnl_amount"] for t in closed if t["pnl_amount"] is not None)

    # Net account value
    equity_value = total_capital + realized_pnl + unrealized_pnl
    cash_available = max(0.0, total_capital + realized_pnl - deployed_capital)

    # Performance stats
    total_closed = len(closed)
    winning_trades = [t for t in closed if (t["pnl_amount"] or 0) > 0]
    losing_trades = [t for t in closed if (t["pnl_amount"] or 0) < 0]
    win_rate = (len(winning_trades) / total_closed * 100.0) if total_closed > 0 else 0.0

    total_wins_dollar = sum(t["pnl_amount"] for t in winning_trades)
    total_losses_dollar = abs(sum(t["pnl_amount"] for t in losing_trades))
    profit_factor = (total_wins_dollar / total_losses_dollar) if total_losses_dollar > 0 else (99.0 if total_wins_dollar > 0 else 0.0)

    return {
        "total_capital": total_capital,
        "equity_value": round(equity_value, 2),
        "cash_available": round(cash_available, 2),
        "deployed_capital": round(deployed_capital, 2),
        "active_market_value": round(active_market_value, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "unrealized_pnl_pct": round((unrealized_pnl / deployed_capital * 100.0) if deployed_capital > 0 else 0.0, 2),
        "realized_pnl": round(realized_pnl, 2),
        "active_trades_count": len(active),
        "closed_trades_count": total_closed,
        "win_rate": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
    }


def get_analytics_and_forecast(
    latest_prices: Optional[dict[str, float]] = None,
    db_path: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Computes Weekly and Monthly breakdowns:
    1. System Expected: 100% of signals evaluated as if taken with standardized sizing.
    2. Actual: What the trader actually executed and banked.
    3. Forward Forecast: 30-day and 90-day probabilistic projections.
    """
    init_db(db_path)

    total_capital = float(get_setting("total_capital", "10000.0", db_path=db_path))

    conn = get_connection(db_path)
    cur = conn.cursor()

    cur.execute("SELECT * FROM signals ORDER BY signal_date DESC")
    all_signals = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM trades ORDER BY entry_date DESC")
    all_trades = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Enrich trades with live P&L
    for t in all_trades:
        if t["status"] == "OPEN":
            cur_p = latest_prices.get(t["ticker"], t["entry_price"]) if latest_prices else t["entry_price"]
            t["current_price"] = round(cur_p, 2)
            t["pnl_amount"] = round((cur_p - t["entry_price"]) * t["shares"], 2)
            t["pnl_pct"] = round(((cur_p - t["entry_price"]) / t["entry_price"]) * 100.0, 2)
        else:
            t["current_price"] = round(t["exit_price"] or t["entry_price"], 2)
            t["pnl_amount"] = round(t["pnl_amount"] or 0.0, 2)
            t["pnl_pct"] = round(t["pnl_pct"] or 0.0, 2)
        t["cost_basis"] = round(t["entry_price"] * t["shares"], 2)

    # -------------------------------------------------------------
    # 1. System Overall Performance (All Signals)
    # -------------------------------------------------------------
    system_wins = [s for s in all_signals if s["status"] in ("HIT_TARGET", "WON")]
    system_losses = [s for s in all_signals if s["status"] in ("HIT_STOP", "LOST")]
    system_in_play = [s for s in all_signals if s["status"] in ("IN_PLAY", "ACTIVE", "PENDING")]
    system_expired = [s for s in all_signals if s["status"] == "EXPIRED"]

    resolved_count = len(system_wins) + len(system_losses) + len(system_expired)
    system_win_rate = (len(system_wins) / resolved_count * 100.0) if resolved_count > 0 else 0.0

    # Theoretical dollar calculation per signal
    total_system_expected_pnl = 0.0
    for s in all_signals:
        alloc = total_capital * 0.33 if s["system"] == "galaxy" else total_capital * 0.20
        pnl_pct = float(s["outcome_pnl_pct"] or 0.0)
        pnl_dollar = (pnl_pct / 100.0) * alloc
        s["hypothetical_pnl"] = pnl_dollar
        total_system_expected_pnl += pnl_dollar

    # -------------------------------------------------------------
    # 2. Actual Overall Performance (Trader Trades)
    # -------------------------------------------------------------
    actual_realized = sum(t["pnl_amount"] for t in all_trades if t["status"] == "CLOSED" and t["pnl_amount"] is not None)
    actual_unrealized = sum(t["pnl_amount"] for t in all_trades if t["status"] == "OPEN" and t["pnl_amount"] is not None)
    total_actual_pnl = actual_realized + actual_unrealized

    closed_trades = [t for t in all_trades if t["status"] == "CLOSED"]
    actual_wins = [t for t in closed_trades if (t["pnl_amount"] or 0) > 0]
    actual_win_rate = (len(actual_wins) / len(closed_trades) * 100.0) if closed_trades else 0.0

    capture_efficiency = (total_actual_pnl / total_system_expected_pnl * 100.0) if (total_system_expected_pnl > 0 and total_actual_pnl > 0) else 0.0
    capture_efficiency = min(100.0, max(0.0, capture_efficiency))

    # -------------------------------------------------------------
    # 3. Weekly Breakdown
    # -------------------------------------------------------------
    weekly_groups: dict[str, dict[str, Any]] = {}

    for s in all_signals:
        try:
            dt = datetime.strptime(s["signal_date"], "%Y-%m-%d")
            # Monday of the week
            monday = dt - timedelta(days=dt.weekday())
            week_key = monday.strftime("%Y-%m-%d")
            week_label = f"Week of {monday.strftime('%b %d, %Y')}"
        except Exception:
            week_key = s["signal_date"][:7]
            week_label = week_key

        if week_key not in weekly_groups:
            weekly_groups[week_key] = {
                "key": week_key,
                "label": week_label,
                "system_signals": 0,
                "system_wins": 0,
                "system_losses": 0,
                "system_pnl": 0.0,
                "actual_trades": 0,
                "actual_pnl": 0.0,
            }

        w = weekly_groups[week_key]
        w["system_signals"] += 1
        if s["status"] in ("HIT_TARGET", "WON"):
            w["system_wins"] += 1
        elif s["status"] in ("HIT_STOP", "LOST"):
            w["system_losses"] += 1
        w["system_pnl"] += s.get("hypothetical_pnl", 0.0)

    for t in all_trades:
        try:
            dt = datetime.strptime(t["entry_date"], "%Y-%m-%d")
            monday = dt - timedelta(days=dt.weekday())
            week_key = monday.strftime("%Y-%m-%d")
            week_label = f"Week of {monday.strftime('%b %d, %Y')}"
        except Exception:
            week_key = t["entry_date"][:7]
            week_label = week_key

        if week_key not in weekly_groups:
            weekly_groups[week_key] = {
                "key": week_key,
                "label": week_label,
                "system_signals": 0,
                "system_wins": 0,
                "system_losses": 0,
                "system_pnl": 0.0,
                "actual_trades": 0,
                "actual_pnl": 0.0,
            }

        w = weekly_groups[week_key]
        w["actual_trades"] += 1
        w["actual_pnl"] += float(t.get("pnl_amount") or 0.0)

    # Sort weeks descending
    weekly_list = []
    for k in sorted(weekly_groups.keys(), reverse=True):
        item = weekly_groups[k]
        item["system_pnl"] = round(item["system_pnl"], 2)
        item["actual_pnl"] = round(item["actual_pnl"], 2)
        sys_res = item["system_wins"] + item["system_losses"]
        item["system_win_rate"] = round((item["system_wins"] / sys_res * 100.0) if sys_res > 0 else 0.0, 1)
        cap_p = (item["actual_pnl"] / item["system_pnl"] * 100.0) if (item["system_pnl"] > 0 and item["actual_pnl"] > 0) else 0.0
        item["capture_pct"] = round(min(100.0, max(0.0, cap_p)), 1)
        weekly_list.append(item)

    # -------------------------------------------------------------
    # 4. Monthly Breakdown
    # -------------------------------------------------------------
    monthly_groups: dict[str, dict[str, Any]] = {}

    for s in all_signals:
        m_key = s["signal_date"][:7]  # YYYY-MM
        try:
            m_label = datetime.strptime(m_key + "-01", "%Y-%m-%d").strftime("%B %Y")
        except Exception:
            m_label = m_key

        if m_key not in monthly_groups:
            monthly_groups[m_key] = {
                "key": m_key,
                "label": m_label,
                "system_signals": 0,
                "system_wins": 0,
                "system_losses": 0,
                "system_pnl": 0.0,
                "actual_trades": 0,
                "actual_pnl": 0.0,
            }

        m = monthly_groups[m_key]
        m["system_signals"] += 1
        if s["status"] in ("HIT_TARGET", "WON"):
            m["system_wins"] += 1
        elif s["status"] in ("HIT_STOP", "LOST"):
            m["system_losses"] += 1
        m["system_pnl"] += s.get("hypothetical_pnl", 0.0)

    for t in all_trades:
        m_key = t["entry_date"][:7]
        try:
            m_label = datetime.strptime(m_key + "-01", "%Y-%m-%d").strftime("%B %Y")
        except Exception:
            m_label = m_key

        if m_key not in monthly_groups:
            monthly_groups[m_key] = {
                "key": m_key,
                "label": m_label,
                "system_signals": 0,
                "system_wins": 0,
                "system_losses": 0,
                "system_pnl": 0.0,
                "actual_trades": 0,
                "actual_pnl": 0.0,
            }

        m = monthly_groups[m_key]
        m["actual_trades"] += 1
        m["actual_pnl"] += float(t.get("pnl_amount") or 0.0)

    monthly_list = []
    for k in sorted(monthly_groups.keys(), reverse=True):
        item = monthly_groups[k]
        item["system_pnl"] = round(item["system_pnl"], 2)
        item["actual_pnl"] = round(item["actual_pnl"], 2)
        sys_res = item["system_wins"] + item["system_losses"]
        item["system_win_rate"] = round((item["system_wins"] / sys_res * 100.0) if sys_res > 0 else 0.0, 1)
        item["system_return_pct"] = round((item["system_pnl"] / total_capital) * 100.0, 2)
        item["actual_return_pct"] = round((item["actual_pnl"] / total_capital) * 100.0, 2)
        m_cap_p = (item["actual_pnl"] / item["system_pnl"] * 100.0) if (item["system_pnl"] > 0 and item["actual_pnl"] > 0) else 0.0
        item["capture_pct"] = round(min(100.0, max(0.0, m_cap_p)), 1)
        monthly_list.append(item)

    # -------------------------------------------------------------
    # 5. Probabilistic Forward Projections (Mathematical Expectancy)
    # -------------------------------------------------------------
    # Galaxy v2 empirical expectancy: WR 74.4%, avg win +7.79%, avg loss -3.96%, hold ~3.5d
    # Universe empirical expectancy: WR 64.0%, avg win +18.2%, avg loss -6.5%, hold ~18d
    # Blended empirical monthly expected return on total capital: ~5.5% to 8.5%
    monthly_rate = 0.065  # 6.5% base monthly expectation at 33% sizing
    forecast_30d_pnl = total_capital * monthly_rate
    forecast_90d_pnl = total_capital * ((1 + monthly_rate) ** 3 - 1)
    forecast_1yr_pnl = total_capital * ((1 + monthly_rate) ** 12 - 1)

    return {
        "summary": {
            "total_capital": total_capital,
            "system_total_signals": len(all_signals),
            "system_win_rate": round(system_win_rate, 1),
            "system_expected_pnl": round(total_system_expected_pnl, 2),
            "system_expected_return_pct": round((total_system_expected_pnl / total_capital * 100.0), 2),
            "actual_trades_count": len(all_trades),
            "actual_win_rate": round(actual_win_rate, 1),
            "actual_total_pnl": round(total_actual_pnl, 2),
            "actual_return_pct": round((total_actual_pnl / total_capital * 100.0), 2),
            "capture_efficiency_pct": round(capture_efficiency, 1),
        },
        "forecast": {
            "monthly_expected_rate_pct": round(monthly_rate * 100.0, 1),
            "forecast_30d_pnl": round(forecast_30d_pnl, 2),
            "forecast_30d_return_pct": round(monthly_rate * 100.0, 1),
            "forecast_90d_pnl": round(forecast_90d_pnl, 2),
            "forecast_90d_return_pct": round(((1 + monthly_rate) ** 3 - 1) * 100.0, 1),
            "forecast_1yr_pnl": round(forecast_1yr_pnl, 2),
            "forecast_1yr_return_pct": round(((1 + monthly_rate) ** 12 - 1) * 100.0, 1),
        },
        "weekly": weekly_list,
        "monthly": monthly_list,
        "executions": all_trades,
    }
