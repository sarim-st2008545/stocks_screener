"""
Comprehensive Setup Prioritization Backtest Engine
===================================================
Tests 4 setup ranking/prioritization models when competing for limited portfolio slots:
Portfolio Constraint: Maximum 4 concurrent active positions, 1.5% Risk Parity.
When multiple setups trigger on the same day (or when a slot becomes available):

Option 0: Baseline (FIFO / Random / Default order)
Option 1: Relative Strength (RS) Rank vs. Benchmark (Performance over 21d + 63d)
Option 2: Normalized Volatility / ATR% Rank (Highest velocity movers first)
Option 3: Institutional Volume Surge / Expansion Rank (Day Volume / 50d SMA Volume)
Option 4: Composite Alpha Velocity Rank (Weighted: 40% RS + 35% ATR% + 25% Volume Surge)

Simulates across historical signals for:
- Part 1: Universe AI Swing Strategy (10-Year historical data)
- Part 2: Galaxy v2 Halal Mean Reversion Strategy (5-Year historical data)
"""

import sys
import os
from pathlib import Path
import yaml
import numpy as np
import pandas as pd
from datetime import datetime

# 1. Load Universe Candidates
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

print(f"Loaded {len(histories)} price histories for Universe Swing.")

# Helper indicators
def calc_sma(series, period):
    return series.rolling(period).mean()

def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def calc_atr(df, period=14):
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/period, adjust=False).mean()

def calc_rsi(series, period=2):
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    alpha = 1.0 / period
    ma_up = up.ewm(alpha=alpha, adjust=False).mean()
    ma_down = down.ewm(alpha=alpha, adjust=False).mean()
    rs = ma_up / ma_down
    return 100.0 - (100.0 / (1.0 + rs))

# -----------------------------------------------------------------------------
# Part 1: Identify Universe Swing Signals with Ranking Metrics
# -----------------------------------------------------------------------------
print("\n--- Identifying Universe Swing Signals with Velocity Metrics ---")
soxx_df = histories.get("SOXX")
soxx_sma200 = calc_sma(soxx_df["close"], 200) if soxx_df is not None else None

universe_signals = []

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
    rs_63 = ret_63 - bench_ret_63

    ret_21 = (close / close.shift(21)) - 1.0
    bench_ret_21 = (bench_c / bench_c.shift(21)) - 1.0
    rs_21 = ret_21 - bench_ret_21

    for i in range(252, len(df) - 30):
        c_i = close.iloc[i]
        o_i = open_p.iloc[i]
        v_i = vol.iloc[i]

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
        trigger_dip = stage2 and ema_dip and (rs_63.iloc[i] > 0) and soxx_ok

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
        trigger_pivot = pocket_pivot_vol and near_base and (c_i > sma50.iloc[i]) and (rs_63.iloc[i] > 0) and soxx_ok

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
            # Prioritization Metrics:
            # 1. Relative Strength Composite (weighted 40% 1-mo, 60% 3-mo)
            rs_score = (0.4 * rs_21.iloc[i] + 0.6 * rs_63.iloc[i]) * 100.0
            # 2. Normalized ATR % (Daily volatility)
            atr_pct = (atr_val / c_i) * 100.0
            # 3. Volume Surge Ratio
            v_sma = vol_sma50.iloc[i]
            vol_surge = (v_i / v_sma) if (v_sma and v_sma > 0) else 1.0

            universe_signals.append({
                "system": "universe",
                "ticker": ticker,
                "signal_date": df.index[i],
                "entry_date": df.index[entry_idx],
                "entry_price": open_p.iloc[entry_idx],
                "stop_loss": stop_loss,
                "target_price": t1_target,
                "atr": atr_val,
                "rs_score": rs_score,
                "atr_pct": atr_pct,
                "vol_surge": vol_surge,
            })

print(f"Total Universe signals identified: {len(universe_signals)}")

# -----------------------------------------------------------------------------
# Part 2: Identify Galaxy v2 Signals with Ranking Metrics
# -----------------------------------------------------------------------------
print("\n--- Identifying Galaxy v2 Signals with Velocity Metrics ---")
galaxy_files = list(Path("data/galaxy").glob("*_5y.csv"))
gal_histories = {}
for f in galaxy_files:
    sym = f.stem.replace("_5y", "")
    if sym != "SPY":
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        df.columns = [c.lower() for c in df.columns]
        if len(df) >= 200:
            gal_histories[sym] = df

spy_df = histories.get("SPY")
spy_sma200 = calc_sma(spy_df["close"], 200) if spy_df is not None else None

galaxy_signals = []

for sym, df in gal_histories.items():
    close = df["close"]
    low = df["low"]
    high = df["high"]
    open_p = df["open"]
    vol = df["volume"]

    s50 = calc_sma(close, 50)
    s200 = calc_sma(close, 200)
    s10 = calc_sma(close, 10)
    r2 = calc_rsi(close, 2)
    bb_m = calc_sma(close, 20)
    bb_s = close.rolling(20).std()
    bb_low = bb_m - (2.0 * bb_s)
    atr = calc_atr(df, 14)
    vol_sma50 = vol.rolling(50).mean()

    bench_c = spy_df["close"].reindex(df.index).ffill() if spy_df is not None else close
    rs_21 = ((close / close.shift(21)) - (bench_c / bench_c.shift(21))) * 100.0
    rs_63 = ((close / close.shift(63)) - (bench_c / bench_c.shift(63))) * 100.0

    for i in range(200, len(df) - 6):
        cur_date = df.index[i]
        if spy_sma200 is not None and cur_date in spy_df.index:
            if spy_df.loc[cur_date, "close"] < spy_sma200.loc[cur_date]:
                continue
        c_i = close.iloc[i]
        if not (c_i > s50.iloc[i] and c_i > s200.iloc[i]):
            continue
        if not (r2.iloc[i] < 10.0 and low.iloc[i] <= bb_low.iloc[i]):
            continue

        entry_idx = i + 1
        atr_v = atr.iloc[i]
        stop_p = open_p.iloc[entry_idx] - (1.5 * atr_v)
        t1_target = s10.iloc[i]

        rs_score = 0.4 * rs_21.iloc[i] + 0.6 * rs_63.iloc[i]
        atr_pct = (atr_v / c_i) * 100.0
        v_sma = vol_sma50.iloc[i]
        vol_surge = (vol.iloc[i] / v_sma) if (v_sma and v_sma > 0) else 1.0

        galaxy_signals.append({
            "system": "galaxy",
            "ticker": sym,
            "signal_date": df.index[i],
            "entry_date": df.index[entry_idx],
            "entry_price": open_p.iloc[entry_idx],
            "stop_loss": stop_p,
            "target_price": t1_target,
            "atr": atr_v,
            "rs_score": rs_score,
            "atr_pct": atr_pct,
            "vol_surge": vol_surge,
        })

print(f"Total Galaxy v2 signals identified: {len(galaxy_signals)}")

# -----------------------------------------------------------------------------
# Portfolio Simulation Engine with Setup Prioritization
# -----------------------------------------------------------------------------
def run_prioritized_portfolio_backtest(all_signals, price_histories, ranking_option="fifo", max_positions=4):
    """
    Simulates portfolio equity curve where candidate setups on any given day
    are ranked according to ranking_option:
    - 'fifo': Default order (first-come first-served)
    - 'rs': Highest Relative Strength score first
    - 'atr_pct': Highest ATR% volatility first (Fast Movers)
    - 'volume': Highest Volume Expansion ratio first
    - 'composite': 40% RS + 35% ATR% + 25% Volume Surge
    """
    # Group signals by signal_date
    signals_by_date = {}
    for s in all_signals:
        d = str(s["signal_date"])[:10]
        if d not in signals_by_date:
            signals_by_date[d] = []
        signals_by_date[d].append(s)

    sorted_dates = sorted(signals_by_date.keys())

    # Pre-rank signals on each day
    ranked_signals_by_date = {}
    for d, sigs in signals_by_date.items():
        if ranking_option == "fifo":
            ranked = sigs
        elif ranking_option == "rs":
            ranked = sorted(sigs, key=lambda x: x["rs_score"], reverse=True)
        elif ranking_option == "atr_pct":
            ranked = sorted(sigs, key=lambda x: x["atr_pct"], reverse=True)
        elif ranking_option == "volume":
            ranked = sorted(sigs, key=lambda x: x["vol_surge"], reverse=True)
        elif ranking_option == "composite":
            # Rank percentiles within the day's candidate batch
            n = len(sigs)
            if n > 1:
                rs_vals = [x["rs_score"] for x in sigs]
                atr_vals = [x["atr_pct"] for x in sigs]
                vol_vals = [x["vol_surge"] for x in sigs]

                # Min-max normalize within batch or global reference
                def norm(vals):
                    mn, mx = min(vals), max(vals)
                    return [(v - mn) / (mx - mn + 1e-6) for v in vals]

                n_rs = norm(rs_vals)
                n_atr = norm(atr_vals)
                n_vol = norm(vol_vals)

                composite_scores = [
                    0.40 * n_rs[idx] + 0.35 * n_atr[idx] + 0.25 * n_vol[idx]
                    for idx in range(n)
                ]
                # Attach score and sort
                for idx, item in enumerate(sigs):
                    item["comp_score"] = composite_scores[idx]
                ranked = sorted(sigs, key=lambda x: x["comp_score"], reverse=True)
            else:
                ranked = sigs
        ranked_signals_by_date[d] = ranked

    # Run discrete daily portfolio walk-forward
    capital = 10000.0
    cash = capital
    active_trades = []
    closed_trades = []
    equity_curve = []
    peak_equity = capital
    max_drawdown = 0.0

    all_calendar_dates = sorted(list(set(
        [pd.to_datetime(d).strftime("%Y-%m-%d") for df in price_histories.values() for d in df.index]
    )))

    # Filter to test range
    start_d = sorted_dates[0]
    all_calendar_dates = [d for d in all_calendar_dates if d >= start_d]

    for cur_dt in all_calendar_dates:
        # 1. Update existing trades
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

            # Exit logic: 50% scale at Target 1, breakeven stop on runner
            if h >= t["target_price"] and t["remaining_shares"] == t["shares"]:
                sold_sh = t["shares"] * 0.5
                pnl_half = (t["target_price"] - t["entry_price"]) * sold_sh
                cash += (sold_sh * t["target_price"])
                t["remaining_shares"] -= sold_sh
                t["realized_usd"] += pnl_half
                t["stop_loss"] = t["entry_price"]  # Breakeven stop

            # Check stop loss
            if l <= t["stop_loss"]:
                resolved = True
                exit_p = t["stop_loss"]
                pnl_rem = (exit_p - t["entry_price"]) * t["remaining_shares"]
                cash += (t["remaining_shares"] * exit_p)
                t["realized_usd"] += pnl_rem
                t["exit_price"] = exit_p
                t["exit_date"] = cur_dt
                t["exit_reason"] = "Stop"
                closed_trades.append(t)

            # Check time limit (4d for galaxy, 25d for universe)
            max_hold = 4 if t["system"] == "galaxy" else 25
            if not resolved and t["bars_held"] >= max_hold:
                resolved = True
                exit_p = c
                pnl_rem = (exit_p - t["entry_price"]) * t["remaining_shares"]
                cash += (t["remaining_shares"] * exit_p)
                t["realized_usd"] += pnl_rem
                t["exit_price"] = exit_p
                t["exit_date"] = cur_dt
                t["exit_reason"] = "Time Limit"
                closed_trades.append(t)

            if not resolved:
                still_open.append(t)

        active_trades = still_open

        # 2. Check for new candidate setups on cur_dt
        candidate_setups = ranked_signals_by_date.get(cur_dt, [])
        for s in candidate_setups:
            if len(active_trades) >= max_positions:
                break
            if any(t["ticker"] == s["ticker"] for t in active_trades):
                continue

            # Position sizing: risk 1.5% of total current equity
            cur_active_val = sum(t["remaining_shares"] * t["entry_price"] for t in active_trades)
            tot_eq = cash + cur_active_val
            risk_usd = tot_eq * 0.015
            entry_p = float(s["entry_price"])
            stop_p = float(s["stop_loss"])
            risk_per_sh = abs(entry_p - stop_p)
            if risk_per_sh <= 0:
                continue

            sh = risk_usd / risk_per_sh
            max_pos_val = tot_eq * 0.25  # Max 25% allocation per position
            if (sh * entry_p) > max_pos_val:
                sh = max_pos_val / entry_p

            cost = sh * entry_p
            if cost <= cash and sh > 0:
                cash -= cost
                active_trades.append({
                    "system": s["system"],
                    "ticker": s["ticker"],
                    "shares": sh,
                    "remaining_shares": sh,
                    "entry_price": entry_p,
                    "stop_loss": stop_p,
                    "target_price": float(s["target_price"]),
                    "entry_date": cur_dt,
                    "bars_held": 0,
                    "realized_usd": 0.0,
                    "initial_cost": cost,
                    "atr_pct": s["atr_pct"],
                    "rs_score": s["rs_score"],
                    "vol_surge": s["vol_surge"]
                })

        # Calculate current equity and peak drawdown
        cur_active_val = sum(t["remaining_shares"] * t["entry_price"] for t in active_trades)
        daily_equity = cash + cur_active_val
        if daily_equity > peak_equity:
            peak_equity = daily_equity
        dd = (peak_equity - daily_equity) / peak_equity * 100.0
        if dd > max_drawdown:
            max_drawdown = dd

    # Final tally
    ending_capital = cash + sum(t["remaining_shares"] * t["entry_price"] for t in active_trades)
    pnls = [t["realized_usd"] for t in closed_trades]
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p <= 0]
    wr = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0
    pf = (sum(wins) / sum(losses)) if sum(losses) > 0 else 99.0
    avg_hold = np.mean([t["bars_held"] for t in closed_trades]) if closed_trades else 0.0
    tot_ret_pct = ((ending_capital - 10000.0) / 10000.0) * 100.0

    return {
        "ranking_option": ranking_option,
        "ending_capital": ending_capital,
        "total_return_pct": tot_ret_pct,
        "mult": ending_capital / 10000.0,
        "trades_executed": len(closed_trades),
        "win_rate": wr,
        "profit_factor": pf,
        "avg_hold_days": avg_hold,
        "max_drawdown": max_drawdown
    }

# -----------------------------------------------------------------------------
# Run Backtest Comparisons
# -----------------------------------------------------------------------------
# Combined Universe + Galaxy signal pool sorted chronologically
all_signals_combined = sorted(
    universe_signals + galaxy_signals,
    key=lambda x: str(x["signal_date"])
)

# Combined price histories lookup
combined_histories = {**histories, **gal_histories}

print("\n" + "=" * 105)
print("  COMPREHENSIVE SETUP PRIORITIZATION BACKTEST (UNIVERSE + GALAXY)")
print("  Portfolio Rules: Max 4 Concurrent Positions, 1.5% Risk Parity, 50% Scale at Target 1")
print("=" * 105)

options = [
    ("fifo", "Option 0: Baseline (First-Come First-Served / No Priority)"),
    ("rs", "Option 1: Relative Strength (RS) Rank vs. Benchmark"),
    ("atr_pct", "Option 2: Normalized Volatility / ATR% Rank (Fast Movers)"),
    ("volume", "Option 3: Institutional Volume Surge / Expansion Rank"),
    ("composite", "Option 4: Composite Alpha Velocity Rank (40% RS + 35% ATR% + 25% Vol)")
]

results = []
for code, desc in options:
    print(f"Running simulation for {code}...")
    res = run_prioritized_portfolio_backtest(all_signals_combined, combined_histories, ranking_option=code)
    res["desc"] = desc
    results.append(res)

print("\n" + "=" * 105)
print(f"{'Prioritization Strategy':<42} | {'Trades':<6} | {'Win Rate':<8} | {'PF':<5} | {'Avg Hold':<9} | {'Max DD':<7} | {'Final Equity':<12} | {'Return'}")
print("-" * 105)
for r in results:
    print(f"{r['desc']:<42} | {r['trades_executed']:<6} | {r['win_rate']:6.1f}%  | {r['profit_factor']:5.2f} | {r['avg_hold_days']:5.1f} d   | {r['max_drawdown']:5.1f}% | ${r['ending_capital']:11,.2f} | {r['mult']:5.2f}x (+{r['total_return_pct']:,.0f}%)")
print("=" * 105)
