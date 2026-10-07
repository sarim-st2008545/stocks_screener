"""
Forward Test Comparison on Exact DB Signals:
Compare Baseline (Default Order) vs Option 1 (RS Priority Order)
Using the exact signals saved in DB from 2026-09-17 through 2026-10-07.
"""
import os, json, math
os.environ['DATABASE_URL'] = 'postgresql://neondb_owner:npg_SkMPE1iI4xHd@ep-proud-dust-b24lkcj7-pooler.c-6.eu-central-1.aws.neon.tech/neondb?sslmode=require'
from src.db import get_connection
from src import records
import pandas as pd

def run_simulation(priority_mode="baseline"):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT * FROM signals ORDER BY signal_date ASC, id ASC")
    raw_signals = [dict(r) for r in cur.fetchall()]
    conn.close()

    # Parse RS_63 for each signal
    for s in raw_signals:
        meta = {}
        if s.get("metadata"):
            try: meta = json.loads(s["metadata"])
            except: pass
        s["rs_score"] = float(meta.get("rs_63", 0.0))

    # Group by signal_date
    signals_by_date = {}
    for s in raw_signals:
        d = s["signal_date"]
        if d not in signals_by_date:
            signals_by_date[d] = []
        signals_by_date[d].append(s)

    # Sort candidates on each day
    ranked_signals_by_date = {}
    for d, sigs in signals_by_date.items():
        if priority_mode == "rs":
            # Sort by highest RS score descending
            ranked = sorted(sigs, key=lambda x: x["rs_score"], reverse=True)
        else:
            # Baseline: preserve natural generation order
            ranked = sigs
        ranked_signals_by_date[d] = ranked

    cached_prices = records.load_all_cached_price_histories()

    init_capital_usd = 20000.0 / 3.64 # $5,494.51
    cash_usd = init_capital_usd
    active_trades = []
    closed_trades = []

    sorted_dates = sorted(signals_by_date.keys())

    for dt_str in sorted_dates:
        # 1. Update active trades against price bars up to dt_str
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

                # Check Target 1 for partial scaling (50%)
                if h >= t["target_price"] and t["remaining_shares"] == t["shares"]:
                    sold_sh = math.floor(t["shares"] * 0.5)
                    if sold_sh > 0:
                        realized_t1 = (t["target_price"] - t["entry_price"]) * sold_sh
                        cash_usd += (sold_sh * t["target_price"])
                        t["remaining_shares"] -= sold_sh
                        t["realized_pnl_usd"] += realized_t1
                        t["stop_loss"] = t["entry_price"]  # Move stop to breakeven
                        t["status"] = "PARTIAL_CLOSED"

                # Check Stop Loss
                if l <= t["stop_loss"]:
                    trade_resolved = True
                    exit_p = t["stop_loss"]
                    pnl_rem = (exit_p - t["entry_price"]) * t["remaining_shares"]
                    cash_usd += (t["remaining_shares"] * exit_p)
                    t["realized_pnl_usd"] += pnl_rem
                    t["exit_date"] = b_str
                    t["exit_price"] = exit_p
                    t["exit_reason"] = "Stop"
                    t["status"] = "CLOSED"
                    closed_trades.append(t)
                    break

            if not trade_resolved:
                still_open.append(t)

        active_trades = still_open

        # 2. Check candidate setups on dt_str
        days_signals = ranked_signals_by_date.get(dt_str, [])
        for s in days_signals:
            if len(active_trades) >= 4:
                break
            if any(t["ticker"].upper() == s["ticker"].upper() for t in active_trades):
                continue

            entry_p = float(s["price"])
            stop_p = float(s["stop_loss"])
            target_p = float(s["target_price"])

            active_val = sum(t["remaining_shares"] * t["entry_price"] for t in active_trades)
            tot_eq = cash_usd + active_val

            # Risk 1.5% parity
            risk_amt = tot_eq * 0.015
            risk_per_sh = abs(entry_p - stop_p)
            if risk_per_sh <= 0:
                continue

            sh = math.floor(risk_amt / risk_per_sh)
            # Max 25% allocation cap
            if (sh * entry_p) > (tot_eq * 0.25):
                sh = math.floor((tot_eq * 0.25) / entry_p)

            if sh >= 1 and (sh * entry_p) <= cash_usd:
                cost = sh * entry_p
                cash_usd -= cost
                trade_rec = {
                    "ticker": s["ticker"].upper(),
                    "shares": sh,
                    "remaining_shares": sh,
                    "entry_date": dt_str,
                    "entry_price": entry_p,
                    "stop_loss": stop_p,
                    "target_price": target_p,
                    "status": "OPEN",
                    "realized_pnl_usd": 0.0,
                    "rs_score": s["rs_score"]
                }
                active_trades.append(trade_rec)

    # Compute live equity against latest bar for open trades
    total_floating_pnl_usd = 0.0
    for t in active_trades:
        df = cached_prices.get(t["ticker"])
        live_p = float(df.iloc[-1]["close"]) if (df is not None and not df.empty) else t["entry_price"]
        float_pnl = (live_p - t["entry_price"]) * t["remaining_shares"]
        t["live_price"] = live_p
        t["unrealized_pnl_usd"] = float_pnl
        total_floating_pnl_usd += float_pnl

    deployed_val = sum(t["remaining_shares"] * t["entry_price"] for t in active_trades)
    current_equity_usd = cash_usd + deployed_val + total_floating_pnl_usd
    total_realized_usd = sum(t["realized_pnl_usd"] for t in closed_trades)
    net_profit_usd = (current_equity_usd - init_capital_usd)

    return {
        "mode": priority_mode,
        "cash_usd": cash_usd,
        "cash_qar": cash_usd * 3.64,
        "equity_usd": current_equity_usd,
        "equity_qar": current_equity_usd * 3.64,
        "realized_usd": total_realized_usd,
        "realized_qar": total_realized_usd * 3.64,
        "floating_usd": total_floating_pnl_usd,
        "floating_qar": total_floating_pnl_usd * 3.64,
        "net_profit_usd": net_profit_usd,
        "net_profit_qar": net_profit_usd * 3.64,
        "active_trades": active_trades,
        "closed_trades": closed_trades
    }

print("Running Baseline Forward Test...")
b = run_simulation("baseline")
print("\nRunning Option 1 (RS Priority) Forward Test...")
r = run_simulation("rs")

print("\n" + "=" * 80)
print("  HEAD-TO-HEAD FORWARD TEST COMPARISON ON EXACT DATABASE SIGNALS")
print("  Date Range: Sept 17, 2026 to Present (Oct 7, 2026)")
print("=" * 80)
print(f"{'Metric':<30} | {'Baseline (Current)':<22} | {'Option 1 (RS Priority)':<22}")
print("-" * 80)
print(f"{'Starting Capital':<30} | 20,000 QAR ($5,495)     | 20,000 QAR ($5,495)")
print(f"{'Current Total Equity':<30} | {b['equity_qar']:,.2f} QAR (${b['equity_usd']:,.2f}) | {r['equity_qar']:,.2f} QAR (${r['equity_usd']:,.2f})")
print(f"{'Realized Banked Cash':<30} | {b['realized_qar']:,.2f} QAR (${b['realized_usd']:,.2f})   | {r['realized_qar']:,.2f} QAR (${r['realized_usd']:,.2f})")
print(f"{'Open Floating Profit':<30} | {b['floating_qar']:,.2f} QAR (${b['floating_usd']:,.2f})   | {r['floating_qar']:,.2f} QAR (${r['floating_usd']:,.2f})")
print(f"{'Net Profit Total':<30} | {b['net_profit_qar']:,.2f} QAR (${b['net_profit_usd']:,.2f})   | {r['net_profit_qar']:,.2f} QAR (${r['net_profit_usd']:,.2f})")
print(f"{'Closed Trades Banked':<30} | {len(b['closed_trades'])} trades               | {len(r['closed_trades'])} trades")
print(f"{'Active Positions Open':<30} | {len(b['active_trades'])} open                 | {len(r['active_trades'])} open")
print("=" * 80)

print("\nActive Positions in Baseline:")
for t in b["active_trades"]:
    print(f"  {t['ticker']:<5} | entered: {t['entry_date']} @ ${t['entry_price']:.2f} | live: ${t['live_price']:.2f} | float: ${t['unrealized_pnl_usd']:+.2f} | RS={t['rs_score']:.1f}%")

print("\nActive Positions in Option 1 (RS Priority):")
for t in r["active_trades"]:
    print(f"  {t['ticker']:<5} | entered: {t['entry_date']} @ ${t['entry_price']:.2f} | live: ${t['live_price']:.2f} | float: ${t['unrealized_pnl_usd']:+.2f} | RS={t['rs_score']:.1f}%")

print("\nClosed Trades in Baseline:")
for t in b["closed_trades"]:
    print(f"  {t['ticker']:<5} | entered: {t['entry_date']} -> exit: {t['exit_date']} | realized: ${t['realized_pnl_usd']:+.2f}")

print("\nClosed Trades in Option 1 (RS Priority):")
for t in r["closed_trades"]:
    print(f"  {t['ticker']:<5} | entered: {t['entry_date']} -> exit: {t['exit_date']} | realized: ${t['realized_pnl_usd']:+.2f}")
