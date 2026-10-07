"""
Comparative Research Backtest:
Embedding Relative Strength (RS) Directly as an Entry Gate Filter vs. Setup Post-Prioritization
=============================================================================================
Question:
"What happens if we mandate a strict minimum RS threshold (e.g., RS_63 >= +10% or Top 20% RS)
directly into the strategy entry rules before signals are generated?"

We compare 5 Configurations across 10 Years of Historical Data:
1. Current Strategy Baseline (RS_63 > 0, FIFO entry)
2. Priority Only (RS_63 > 0, post-scan prioritization into highest RS first)
3. Hard Gate: RS_63 >= +5% (Only generate signals if outperforming benchmark by at least 5%)
4. Hard Gate: RS_63 >= +10% (Strong Outperformance Gate)
5. Hard Gate: RS_63 >= +15% (Elite Momentum Outperformance Gate)

We evaluate each against our 3-Tier Quantitative Scorecard:
- Total Signals Generated
- Total Trades Executed (Max 4 concurrent slots, 1.5% Risk Parity)
- Win Rate (%)
- Profit Factor (PF)
- Mathematical Expectancy E[R]
- 10-Year Compounded Ending Capital (on $10,000)
- Maximum Drawdown (%)
- Calmar Ratio
- Average Holding Days
"""

import sys
import os
from pathlib import Path
import yaml
import numpy as np
import pandas as pd

# Load Universe Candidates
with open("config/universe.yaml") as f:
    uni_cfg = yaml.safe_load(f)

uni_tickers = []
for seg in uni_cfg.get("segments", {}).values():
    for m in seg.get("members", []):
        uni_tickers.append(m["ticker"])
uni_tickers = sorted(list(set(uni_tickers)))

benchmarks = ["SOXX", "SMH", "SPY", "QQQ", "XLU"]
all_symbols = sorted(list(set(uni_tickers + benchmarks)))

histories = {}
for sym in all_symbols:
    p = Path(f"data/prices/{sym}.csv")
    if p.exists():
        try:
            df = pd.read_csv(p, index_col=0, parse_dates=True)
            df.columns = [c.lower() for c in df.columns]
            if len(df) >= 200:
                histories[sym] = df
        except Exception:
            pass

def calc_sma(series, period):
    return series.rolling(period).mean()

def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_atr(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/period, adjust=False).mean()

soxx_df = histories.get("SOXX")
soxx_sma200 = calc_sma(soxx_df["close"], 200) if soxx_df is not None else None

def generate_universe_signals(min_rs_pct=0.0):
    signals = []
    for ticker in uni_tickers:
        if ticker not in histories:
            continue
        df = histories[ticker].copy()
        if len(df) < 252:
            continue

        bench_df = histories.get("SMH", histories.get("SPY"))
        if bench_df is None or len(bench_df) < 100:
            continue

        close = df["close"]
        high = df["high"]
        low = df["low"]
        open_p = df["open"]
        vol = df["volume"]

        ema10 = calc_ema(close, 10)
        ema20 = calc_ema(close, 20)
        sma50 = calc_sma(close, 50)
        sma150 = calc_sma(close, 150)
        sma200 = calc_sma(close, 200)
        sma200_prev20 = sma200.shift(20)
        atr14 = calc_atr(df, 14)
        vol_sma50 = vol.rolling(50).mean()
        s_low10 = low.rolling(10).min()
        h52 = high.rolling(252).max()
        l52 = low.rolling(252).min()

        bench_c = bench_df["close"].reindex(df.index).ffill()
        ret_63 = (close / close.shift(63)) - 1.0
        bench_ret_63 = (bench_c / bench_c.shift(63)) - 1.0
        rs_63 = (ret_63 - bench_ret_63) * 100.0

        ret_21 = (close / close.shift(21)) - 1.0
        bench_ret_21 = (bench_c / bench_c.shift(21)) - 1.0
        rs_21 = (ret_21 - bench_ret_21) * 100.0

        for i in range(252, len(df) - 30):
            c_i = close.iloc[i]
            o_i = open_p.iloc[i]
            v_i = vol.iloc[i]
            rs_val = rs_63.iloc[i]

            # Direct Strategy Gate: Check min RS threshold
            if rs_val < min_rs_pct:
                continue

            stage2 = (
                (c_i > sma50.iloc[i])
                and (sma50.iloc[i] > sma150.iloc[i])
                and (sma150.iloc[i] > sma200.iloc[i])
                and (sma200.iloc[i] > sma200_prev20.iloc[i])
                and (c_i >= 0.75 * h52.iloc[i])
                and (c_i >= 1.30 * l52.iloc[i])
            )

            soxx_ok = True
            if soxx_sma200 is not None:
                cur_date = df.index[i]
                if cur_date in soxx_df.index:
                    soxx_ok = soxx_df.loc[cur_date, "close"] > soxx_sma200.loc[cur_date]

            ema_dip = (abs(c_i - ema10.iloc[i]) / c_i <= 0.02) or (abs(c_i - ema20.iloc[i]) / c_i <= 0.02)
            trigger_dip = stage2 and ema_dip and soxx_ok

            is_up = c_i > o_i
            down_mask = close.iloc[i-10:i] < close.iloc[i-11:i-1].values
            down_vols = vol.iloc[i-10:i][down_mask]
            down_vol_10 = down_vols.max() if len(down_vols) > 0 else 0
            pocket_pivot_vol = is_up and (v_i > down_vol_10) and (down_vol_10 > 0)
            near_base = (
                (abs(c_i - ema10.iloc[i]) / c_i <= 0.025)
                or (abs(c_i - ema20.iloc[i]) / c_i <= 0.025)
                or (abs(c_i - sma50.iloc[i]) / c_i <= 0.025)
            )
            trigger_pivot = pocket_pivot_vol and near_base and (c_i > sma50.iloc[i]) and soxx_ok

            if not (trigger_dip or trigger_pivot):
                continue

            atr_val = atr14.iloc[i]
            s_low = s_low10.iloc[i]
            s50 = sma50.iloc[i]
            valid_struct = [x for x in [s_low, s50] if not np.isnan(x) and x < c_i]
            chosen_struct = min(valid_struct) if valid_struct else (c_i - 2.0 * atr_val)
            vol_floor_stop = c_i - (1.5 * atr_val)
            stop_loss = min(chosen_struct, vol_floor_stop)
            stop_dist = c_i - stop_loss
            stop_pct = (stop_dist / c_i) * 100.0

            t1_target = c_i + (3.0 * atr_val)
            rr = (t1_target - c_i) / stop_dist if stop_dist > 0 else 0

            if rr >= 1.5 and stop_pct <= 10.0:
                entry_idx = i + 1
                rs_composite = 0.4 * rs_21.iloc[i] + 0.6 * rs_val
                signals.append({
                    "ticker": ticker,
                    "signal_date": df.index[i],
                    "entry_date": df.index[entry_idx],
                    "entry_price": open_p.iloc[entry_idx],
                    "stop_loss": stop_loss,
                    "target_price": t1_target,
                    "rs_score": rs_composite,
                    "rs_63": rs_val
                })
    return signals

def run_portfolio_simulation(signals, price_histories, rank_by_rs=False, max_positions=4):
    signals_by_date = {}
    for s in signals:
        d = str(s["signal_date"])[:10]
        if d not in signals_by_date:
            signals_by_date[d] = []
        signals_by_date[d].append(s)

    sorted_dates = sorted(signals_by_date.keys())
    if not sorted_dates:
        return None

    ranked_by_date = {}
    for d, sigs in signals_by_date.items():
        if rank_by_rs:
            ranked_by_date[d] = sorted(sigs, key=lambda x: x["rs_score"], reverse=True)
        else:
            ranked_by_date[d] = sigs

    capital = 10000.0
    cash = capital
    active_trades = []
    closed_trades = []
    peak_equity = capital
    max_drawdown = 0.0

    all_calendar_dates = sorted(list(set(
        [pd.to_datetime(d).strftime("%Y-%m-%d") for df in price_histories.values() for d in df.index]
    )))
    start_d = sorted_dates[0]
    all_calendar_dates = [d for d in all_calendar_dates if d >= start_d]

    for cur_dt in all_calendar_dates:
        still_open = []
        for t in active_trades:
            df = price_histories.get(t["ticker"])
            if df is None:
                still_open.append(t)
                continue
            try:
                bar = df.loc[cur_dt]
            except KeyError:
                still_open.append(t)
                continue

            h = float(bar["high"])
            l = float(bar["low"])
            c = float(bar["close"])

            resolved = False
            t["bars_held"] += 1

            # 50% scale at Target 1, breakeven stop
            if h >= t["target_price"] and t["remaining_shares"] == t["shares"]:
                sold_sh = t["shares"] * 0.5
                pnl_half = (t["target_price"] - t["entry_price"]) * sold_sh
                cash += (sold_sh * t["target_price"])
                t["remaining_shares"] -= sold_sh
                t["realized_usd"] += pnl_half
                t["stop_loss"] = t["entry_price"]

            if l <= t["stop_loss"]:
                resolved = True
                exit_p = t["stop_loss"]
                pnl_rem = (exit_p - t["entry_price"]) * t["remaining_shares"]
                cash += (t["remaining_shares"] * exit_p)
                t["realized_usd"] += pnl_rem
                t["exit_price"] = exit_p
                t["status"] = "CLOSED"
                closed_trades.append(t)

            if not resolved and t["bars_held"] >= 25:
                resolved = True
                exit_p = c
                pnl_rem = (exit_p - t["entry_price"]) * t["remaining_shares"]
                cash += (t["remaining_shares"] * exit_p)
                t["realized_usd"] += pnl_rem
                t["exit_price"] = exit_p
                t["status"] = "CLOSED"
                closed_trades.append(t)

            if not resolved:
                still_open.append(t)

        active_trades = still_open

        # New entries
        candidate_setups = ranked_by_date.get(cur_dt, [])
        for s in candidate_setups:
            if len(active_trades) >= max_positions:
                break
            if any(t["ticker"] == s["ticker"] for t in active_trades):
                continue

            cur_active_val = sum(t["remaining_shares"] * t["entry_price"] for t in active_trades)
            tot_eq = cash + cur_active_val
            risk_usd = tot_eq * 0.015
            entry_p = float(s["entry_price"])
            stop_p = float(s["stop_loss"])
            risk_per_sh = abs(entry_p - stop_p)
            if risk_per_sh <= 0:
                continue

            sh = risk_usd / risk_per_sh
            max_pos_val = tot_eq * 0.25
            if (sh * entry_p) > max_pos_val:
                sh = max_pos_val / entry_p

            cost = sh * entry_p
            if cost <= cash and sh > 0:
                cash -= cost
                active_trades.append({
                    "ticker": s["ticker"],
                    "shares": sh,
                    "remaining_shares": sh,
                    "entry_price": entry_p,
                    "stop_loss": stop_p,
                    "target_price": float(s["target_price"]),
                    "entry_date": cur_dt,
                    "bars_held": 0,
                    "realized_usd": 0.0,
                    "risk_usd": risk_usd
                })

        cur_active_val = sum(t["remaining_shares"] * t["entry_price"] for t in active_trades)
        daily_equity = cash + cur_active_val
        if daily_equity > peak_equity:
            peak_equity = daily_equity
        dd = (peak_equity - daily_equity) / peak_equity * 100.0
        if dd > max_drawdown:
            max_drawdown = dd

    ending_capital = cash + sum(t["remaining_shares"] * t["entry_price"] for t in active_trades)
    pnls = [t["realized_usd"] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p <= 0]
    wr = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0
    pf = (sum(wins) / sum(losses)) if sum(losses) > 0 else 99.0
    avg_hold = np.mean([t["bars_held"] for t in closed_trades]) if closed_trades else 0.0
    tot_ret = ((ending_capital - 10000.0) / 10000.0) * 100.0
    calmar = tot_ret / max_drawdown if max_drawdown > 0 else 0.0

    # Mathematical Expectancy in R multiples
    r_multiples = [(t["realized_usd"] / t["risk_usd"]) for t in closed_trades if t.get("risk_usd", 0) > 0]
    exp_r = np.mean(r_multiples) if r_multiples else 0.0

    return {
        "signals_count": len(signals),
        "trades_executed": len(closed_trades),
        "win_rate": wr,
        "profit_factor": pf,
        "expectancy_r": exp_r,
        "ending_capital": ending_capital,
        "total_return_pct": tot_ret,
        "max_drawdown": max_drawdown,
        "calmar": calmar,
        "avg_hold": avg_hold
    }

print("=" * 115)
print("  RESEARCH AUDIT: EMBEDDING RELATIVE STRENGTH (RS) DIRECTLY INTO STRATEGY RULES")
print("  Comparing Hard Filter Gates vs. Setup Post-Prioritization (10-Year Decadal Test)")
print("=" * 115)

configs = [
    ("Config 0: Original Baseline (RS > 0, FIFO Entry)", 0.0, False),
    ("Config 1: Prioritization Only (RS > 0, Rank into Top RS)", 0.0, True),
    ("Config 2: Hard Strategy Gate (RS_63 >= +5%)", 5.0, False),
    ("Config 3: Hard Strategy Gate (RS_63 >= +10%)", 10.0, False),
    ("Config 4: Hard Strategy Gate (RS_63 >= +15%)", 15.0, False),
    ("Config 5: Combined (Hard Gate RS >= +10% AND Priority Rank)", 10.0, True),
]

results = []
for label, rs_threshold, rank_flag in configs:
    print(f"Testing {label}...")
    sigs = generate_universe_signals(min_rs_pct=rs_threshold)
    sim = run_portfolio_simulation(sigs, histories, rank_by_rs=rank_flag)
    sim["label"] = label
    results.append(sim)

print("\n" + "=" * 115)
print(f"{'Strategy Architecture Configuration':<46} | {'Signals':<7} | {'Trades':<6} | {'Win %':<6} | {'PF':<5} | {'E[R]':<6} | {'Max DD':<7} | {'Calmar':<6} | {'Ending Capital ($10k)'}")
print("-" * 115)
for r in results:
    mult = r['ending_capital'] / 10000.0
    print(f"{r['label']:<46} | {r['signals_count']:<7} | {r['trades_executed']:<6} | {r['win_rate']:5.1f}% | {r['profit_factor']:5.2f} | {r['expectancy_r']:+5.2f}R | {r['max_drawdown']:5.1f}% | {r['calmar']:5.1f}  | ${r['ending_capital']:11,.2f} ({mult:4.2f}x)")
print("=" * 115)
