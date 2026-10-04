"""
Autonomous Forward Lab & Paper Trading Portfolio Engine for Aura Quant.

Core Architecture:
1. Initial starting capital: 20,000 QAR (~$5,494.51 USD).
2. Recurring monthly salary deposits: +5,000 QAR (~$1,373.63 USD) on the 1st of each month.
3. 1.5% fixed-fractional risk parity sizing across unified capital pool (Galaxy + Universe).
4. 15% cash reserve buffer with automated Capitulation Cannon release (R:R >= 2.5 or RSI-2 < 5).
5. Dynamic scaling: 50% profit-take at Target 1, breakeven stop-trail on runner.
6. Multi-currency reporting: USD (base) and QAR (fixed 3.64 peg).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from src import db, records

QAR_PEG = 3.64


def qar_to_usd(amount_qar: float) -> float:
    return round(amount_qar / QAR_PEG, 2)


def usd_to_qar(amount_usd: float) -> float:
    return round(amount_usd * QAR_PEG, 2)


def get_auto_setting(key: str, default: str = "") -> str:
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("SELECT value FROM auto_settings WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row["value"] if row else default


def set_auto_setting(key: str, value: str):
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO auto_settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
    """, (key, value))
    conn.commit()
    conn.close()


def compute_position_size(
    equity_usd: float,
    cash_available_usd: float,
    entry_price: float,
    stop_loss: float,
    risk_pct: float = 1.5,
    is_capitulation: bool = False,
    max_position_equity_pct: float = 25.0,
) -> dict[str, Any]:
    """
    Computes exact shares to buy based on 1.5% risk parity, 15% reserve buffer, and single-stock cap.
    """
    if entry_price <= 0 or stop_loss <= 0 or entry_price <= stop_loss:
        return {"shares": 0, "reason": "Invalid entry or stop price"}

    stop_dist = entry_price - stop_loss
    max_risk_dollar = equity_usd * (risk_pct / 100.0)

    # 15% Reserve buffer protection (unless Capitulation Cannon is triggered)
    reserve_buffer = equity_usd * 0.15 if not is_capitulation else 0.0
    deployable_cash = max(0.0, cash_available_usd - reserve_buffer)

    if deployable_cash < entry_price:
        return {
            "shares": 0,
            "reason": f"Insufficient deployable cash (${deployable_cash:.2f} < ${entry_price:.2f}) with 15% reserve buffer"
        }

    # Sizing constraints:
    # 1. Risk-based shares
    risk_shares = math.floor(max_risk_dollar / stop_dist)
    # 2. Maximum single-position allocation (e.g. 25% of account)
    max_alloc_shares = math.floor((equity_usd * (max_position_equity_pct / 100.0)) / entry_price)
    # 3. Cash-available shares
    cash_shares = math.floor(deployable_cash / entry_price)

    final_shares = min(risk_shares, max_alloc_shares, cash_shares)
    final_shares = max(0, final_shares)

    cost = final_shares * entry_price
    actual_risk_usd = final_shares * stop_dist
    actual_risk_pct = (actual_risk_usd / equity_usd * 100.0) if equity_usd > 0 else 0.0

    return {
        "shares": int(final_shares),
        "cost_usd": round(cost, 2),
        "cost_qar": usd_to_qar(cost),
        "actual_risk_usd": round(actual_risk_usd, 2),
        "actual_risk_pct": round(actual_risk_pct, 2),
        "deployable_cash": round(deployable_cash, 2),
        "is_capitulation": is_capitulation,
    }


def replay_history() -> dict[str, Any]:
    """
    Replays all historical setups from day one (Sep 2026) using the exact 20,000 QAR
    starting capital and 5,000 QAR/month recurring inflows.
    Builds the baseline for the Forward Lab portfolio.
    """
    db.init_db()
    conn = db.get_connection()
    cur = conn.cursor()

    # 1. Clear existing automated forward portfolio tables
    cur.execute("DELETE FROM auto_trades")
    cur.execute("DELETE FROM auto_deposits")
    conn.commit()

    # 2. Fetch settings
    init_cap_qar = float(get_auto_setting("auto_initial_capital_qar", "20000.0"))
    monthly_dep_qar = float(get_auto_setting("auto_monthly_deposit_qar", "5000.0"))
    start_date_str = get_auto_setting("auto_start_date", "2026-09-08")
    risk_pct = float(get_auto_setting("auto_risk_per_trade_pct", "1.5"))

    # Initial deposit
    init_usd = qar_to_usd(init_cap_qar)
    cur.execute("""
        INSERT INTO auto_deposits (deposit_date, amount_qar, amount_usd, notes)
        VALUES (?, ?, ?, 'Initial Account Opening Balance (20,000 QAR)')
    """, (start_date_str, init_cap_qar, init_usd))
    conn.commit()

    cash_usd = init_usd
    total_deposits_usd = init_usd
    total_deposits_qar = init_cap_qar

    # Track processed months for recurring salary additions
    processed_months = {start_date_str[:7]}

    # 3. Load price histories for forward bar simulation
    cached_prices = records.load_all_cached_price_histories()

    # 4. Fetch all signals sorted chronologically
    cur.execute("SELECT * FROM signals ORDER BY signal_date ASC, id ASC")
    signals = [dict(r) for r in cur.fetchall()]

    active_trades: list[dict[str, Any]] = []
    closed_trades: list[dict[str, Any]] = []

    # Map signals by date
    signals_by_date: dict[str, list[dict[str, Any]]] = {}
    for s in signals:
        d = s["signal_date"]
        if d not in signals_by_date:
            signals_by_date[d] = []
        signals_by_date[d].append(s)

    sorted_dates = sorted(signals_by_date.keys())

    for dt_str in sorted_dates:
        # Check if new month started -> credit 5,000 QAR salary deposit
        m_key = dt_str[:7]
        if m_key not in processed_months:
            dep_usd = qar_to_usd(monthly_dep_qar)
            cash_usd += dep_usd
            total_deposits_usd += dep_usd
            total_deposits_qar += monthly_dep_qar
            processed_months.add(m_key)
            cur.execute("""
                INSERT INTO auto_deposits (deposit_date, amount_qar, amount_usd, notes)
                VALUES (?, ?, ?, 'Monthly Salary Inflow (+5,000 QAR)')
            """, (f"{m_key}-01", monthly_dep_qar, dep_usd))
            conn.commit()

        # Update active trades against bars on/before dt_str
        still_open = []
        for t in active_trades:
            sym = t["ticker"].upper()
            df = cached_prices.get(sym)
            if df is None or df.empty:
                still_open.append(t)
                continue

            try:
                sub_df = df.loc[(df.index > pd.to_datetime(t["entry_date"])) & (df.index <= pd.to_datetime(dt_str))]
            except Exception:
                still_open.append(t)
                continue

            if sub_df.empty:
                still_open.append(t)
                continue

            trade_resolved = False
            for bar_dt, bar in sub_df.iterrows():
                h = float(bar["high"])
                l = float(bar["low"])
                c = float(bar["close"])
                b_str = str(bar_dt)[:10]

                # Check Target 1 for partial scaling out (sell 50% at target)
                if h >= t["target_price"] and t["remaining_shares"] == t["shares"]:
                    # Scale out 50%
                    sold_shares = math.floor(t["shares"] * 0.5)
                    if sold_shares > 0:
                        realized_t1 = (t["target_price"] - t["entry_price"]) * sold_shares
                        cash_usd += (sold_shares * t["target_price"])
                        t["remaining_shares"] -= sold_shares
                        t["realized_pnl_usd"] += realized_t1
                        # Move stop to breakeven!
                        t["stop_loss"] = t["entry_price"]
                        t["status"] = "PARTIAL_CLOSED"
                        t["notes"] = f"Target 1 hit: sold {sold_shares} sh at ${t['target_price']:.2f}, stop moved to BE"

                # Check Stop Loss
                if l <= t["stop_loss"]:
                    trade_resolved = True
                    exit_p = t["stop_loss"]
                    pnl_rem = (exit_p - t["entry_price"]) * t["remaining_shares"]
                    cash_usd += (t["remaining_shares"] * exit_p)
                    t["realized_pnl_usd"] += pnl_rem
                    t["realized_pnl_pct"] = ((t["realized_pnl_usd"]) / (t["shares"] * t["entry_price"])) * 100.0
                    t["exit_date"] = b_str
                    t["exit_price"] = exit_p
                    t["exit_reason"] = "Stop Loss Hit" if t["remaining_shares"] == t["shares"] else "Trailing Breakeven Stop Hit"
                    t["status"] = "CLOSED"
                    t["remaining_shares"] = 0
                    closed_trades.append(t)
                    break

                # Check Time Expired (4 days for Galaxy, 25 days for Universe)
                max_hold = 4 if t["system"] == "galaxy" else 25
                bars_held = len(df.loc[(df.index > pd.to_datetime(t["entry_date"])) & (df.index <= bar_dt)])
                if bars_held >= max_hold:
                    trade_resolved = True
                    exit_p = c
                    pnl_rem = (exit_p - t["entry_price"]) * t["remaining_shares"]
                    cash_usd += (t["remaining_shares"] * exit_p)
                    t["realized_pnl_usd"] += pnl_rem
                    t["realized_pnl_pct"] = ((t["realized_pnl_usd"]) / (t["shares"] * t["entry_price"])) * 100.0
                    t["exit_date"] = b_str
                    t["exit_price"] = exit_p
                    t["exit_reason"] = f"Time Limit Reached ({max_hold}d)"
                    t["status"] = "CLOSED"
                    t["remaining_shares"] = 0
                    closed_trades.append(t)
                    break

            if not trade_resolved:
                still_open.append(t)

        active_trades = still_open

        # Evaluate new setups on dt_str
        days_signals = signals_by_date[dt_str]
        for s in days_signals:
            if len(active_trades) >= 4:
                break  # Max concurrent positions reached

            # Don't double down if ticker already open
            if any(t["ticker"].upper() == s["ticker"].upper() for t in active_trades):
                continue

            entry_p = float(s["price"])
            stop_p = float(s["stop_loss"])
            target_p = float(s["target_price"])
            rr = float(s.get("rr_ratio") or 0.0)

            # Calculate current total equity
            active_val = sum(t["remaining_shares"] * t["entry_price"] for t in active_trades)
            current_equity = cash_usd + active_val

            is_capitulation = (rr >= 2.5) or (s.get("signal_type") == "RSI Capitulation")
            sizing = compute_position_size(
                equity_usd=current_equity,
                cash_available_usd=cash_usd,
                entry_price=entry_p,
                stop_loss=stop_p,
                risk_pct=risk_pct,
                is_capitulation=is_capitulation,
            )

            sh = sizing["shares"]
            if sh >= 1 and (sh * entry_p) <= cash_usd:
                cost = sh * entry_p
                cash_usd -= cost
                trade_record = {
                    "signal_id": s["id"],
                    "system": s["system"],
                    "ticker": s["ticker"].upper(),
                    "shares": sh,
                    "remaining_shares": sh,
                    "entry_date": dt_str,
                    "entry_price": entry_p,
                    "stop_loss": stop_p,
                    "target_price": target_p,
                    "status": "OPEN",
                    "exit_date": None,
                    "exit_price": None,
                    "exit_reason": None,
                    "realized_pnl_usd": 0.0,
                    "realized_pnl_pct": 0.0,
                    "notes": f"Automated 1.5% Risk Parity ({sh} sh @ ${entry_p:.2f})",
                }
                active_trades.append(trade_record)

    # 5. Persist all simulated trades into auto_trades table
    for t in closed_trades + active_trades:
        insert_sql = """
            INSERT INTO auto_trades (
                signal_id, system, ticker, shares, remaining_shares,
                entry_date, entry_price, stop_loss, target_price, status,
                exit_date, exit_price, exit_reason, realized_pnl_usd, realized_pnl_pct, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        cur.execute(insert_sql, (
            t["signal_id"], t["system"], t["ticker"], t["shares"], t["remaining_shares"],
            t["entry_date"], t["entry_price"], t["stop_loss"], t["target_price"], t["status"],
            t["exit_date"], t["exit_price"], t["exit_reason"],
            round(t["realized_pnl_usd"], 2), round(t["realized_pnl_pct"], 2), t["notes"]
        ))

    # Persist updated auto cash setting
    set_auto_setting("auto_cash_available_usd", str(round(cash_usd, 2)))
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "active_trades_count": len(active_trades),
        "closed_trades_count": len(closed_trades),
        "cash_available_usd": round(cash_usd, 2),
        "cash_available_qar": usd_to_qar(cash_usd),
        "total_deposits_usd": round(total_deposits_usd, 2),
        "total_deposits_qar": round(total_deposits_qar, 2),
    }


def get_auto_portfolio_summary(latest_prices: Optional[dict[str, float]] = None) -> dict[str, Any]:
    """
    Returns complete dashboard status for the Forward Lab:
    Total Equity, Cash, Deployed, Floating & Realized P&L, Active Positions, and Trade Journal.
    """
    db.init_db()
    conn = db.get_connection()
    cur = conn.cursor()

    cur.execute("SELECT * FROM auto_trades ORDER BY entry_date DESC, id DESC")
    all_trades = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT * FROM auto_deposits ORDER BY deposit_date ASC")
    deposits = [dict(r) for r in cur.fetchall()]
    conn.close()

    if not all_trades and not deposits:
        # Auto-replay if never initialized
        replay_history()
        return get_auto_portfolio_summary(latest_prices)

    total_deposited_usd = sum(float(d["amount_usd"]) for d in deposits)
    total_deposited_qar = sum(float(d["amount_qar"]) for d in deposits)

    active_positions = []
    closed_positions = []
    unrealized_pnl_usd = 0.0
    deployed_capital_usd = 0.0
    active_market_val_usd = 0.0

    for t in all_trades:
        rem_sh = float(t["remaining_shares"])
        entry_p = float(t["entry_price"])
        ticker = t["ticker"].upper()
        cur_p = latest_prices.get(ticker, entry_p) if (latest_prices and ticker in latest_prices) else entry_p

        if t["status"] in ("OPEN", "PARTIAL_CLOSED") and rem_sh > 0:
            cost_basis = rem_sh * entry_p
            market_val = rem_sh * cur_p
            pnl_float = market_val - cost_basis
            pnl_float_pct = ((cur_p - entry_p) / entry_p * 100.0) if entry_p > 0 else 0.0

            unrealized_pnl_usd += pnl_float
            deployed_capital_usd += cost_basis
            active_market_val_usd += market_val

            t_enriched = dict(t)
            t_enriched["current_price"] = round(cur_p, 2)
            t_enriched["market_value_usd"] = round(market_val, 2)
            t_enriched["market_value_qar"] = usd_to_qar(market_val)
            t_enriched["cost_basis_usd"] = round(cost_basis, 2)
            t_enriched["cost_basis_qar"] = usd_to_qar(cost_basis)
            t_enriched["unrealized_pnl_usd"] = round(pnl_float, 2)
            t_enriched["unrealized_pnl_qar"] = usd_to_qar(pnl_float)
            t_enriched["unrealized_pnl_pct"] = round(pnl_float_pct, 2)

            # Target Progress %
            stop_p = float(t["stop_loss"])
            tgt_p = float(t["target_price"])
            tot_rng = tgt_p - stop_p
            if tot_rng > 0:
                prog = ((cur_p - stop_p) / tot_rng) * 100.0
                t_enriched["progress_pct"] = max(0.0, min(100.0, round(prog, 1)))
            else:
                t_enriched["progress_pct"] = 50.0

            active_positions.append(t_enriched)
        else:
            closed_positions.append(t)

    unrealized_pnl_qar = usd_to_qar(unrealized_pnl_usd)
    realized_pnl_usd = sum(float(t["realized_pnl_usd"] or 0.0) for t in all_trades)
    realized_pnl_qar = usd_to_qar(realized_pnl_usd)

    cash_usd = total_deposited_usd + realized_pnl_usd - deployed_capital_usd
    cash_qar = usd_to_qar(cash_usd)
    deployed_capital_qar = usd_to_qar(deployed_capital_usd)

    total_equity_usd = cash_usd + active_market_val_usd
    total_equity_qar = usd_to_qar(total_equity_usd)

    net_profit_usd = total_equity_usd - total_deposited_usd
    net_profit_qar = usd_to_qar(net_profit_usd)
    roi_pct = (net_profit_usd / total_deposited_usd * 100.0) if total_deposited_usd > 0 else 0.0

    # Win rate on closed trades
    fully_closed = [t for t in closed_positions if t["status"] == "CLOSED"]
    winning = [t for t in fully_closed if float(t["realized_pnl_usd"] or 0.0) > 0]
    win_rate = (len(winning) / len(fully_closed) * 100.0) if fully_closed else 0.0

    return {
        "summary": {
            "total_equity_usd": round(total_equity_usd, 2),
            "total_equity_qar": round(total_equity_qar, 2),
            "total_deposited_usd": round(total_deposited_usd, 2),
            "total_deposited_qar": round(total_deposited_qar, 2),
            "cash_available_usd": round(cash_usd, 2),
            "cash_available_qar": round(cash_qar, 2),
            "deployed_capital_usd": round(deployed_capital_usd, 2),
            "deployed_capital_qar": round(deployed_capital_qar, 2),
            "unrealized_pnl_usd": round(unrealized_pnl_usd, 2),
            "unrealized_pnl_qar": round(unrealized_pnl_qar, 2),
            "realized_pnl_usd": round(realized_pnl_usd, 2),
            "realized_pnl_qar": round(realized_pnl_qar, 2),
            "net_profit_usd": round(net_profit_usd, 2),
            "net_profit_qar": round(net_profit_qar, 2),
            "roi_pct": round(roi_pct, 2),
            "active_count": len(active_positions),
            "closed_count": len(fully_closed),
            "win_rate": round(win_rate, 1),
            "risk_per_trade_pct": float(get_auto_setting("auto_risk_per_trade_pct", "1.5")),
            "reserve_buffer_pct": 15.0,
        },
        "active_positions": active_positions,
        "trade_journal": all_trades,
        "deposits": deposits,
    }
