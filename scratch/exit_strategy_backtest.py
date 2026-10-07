"""
Comprehensive Exit Strategy Optimization & Backtest Engine
============================================================
Quantitatively tests the core research question:
"What generates the most profit:
 1. Holding 100% full position to final target/runner?
 2. Selling 50% at Target 1 and trailing remaining 50%?
 3. Selling 100% full position at Target 1 (quick profit taking)?
 4. Trailing stop only (no target cap)?"

Simulates across historical data for both:
- Part 1: Universe AI Swing Strategy (Stage 2 Dips / Pocket Pivots, 10-Year Data)
- Part 2: Galaxy v2 Halal Mean Reversion Strategy (RSI-2 < 10 + BB Penetration, 5-10 Year Data)

Metrics computed for each exit scenario:
- Total Trades
- Win Rate (%)
- Profit Factor (PF)
- Average Return per Trade (%)
- Expectancy in R multiples (E[R])
- Compound Annual Growth Rate (CAGR) / Cumulative Growth on $10,000
- Maximum Drawdown (Max DD %)
- Sharpe / Calmar Ratio
- Average Holding Period (Days)
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

# Load price histories from data/prices
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

print(f"Loaded {len(histories)} price histories for Universe Swing Backtest.")

# Indicator calculation helpers
def calc_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def calc_sma(series, period):
    return series.rolling(period).mean()

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
# Part 1: Universe AI Swing Signals Identification
# -----------------------------------------------------------------------------
print("\n--- Identifying Universe Swing Signals (Stage 2 Dips & Pivots) ---")
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
    s_low10 = low.rolling(10).min()
    h52 = high.rolling(252).max()
    l52 = low.rolling(252).min()

    # Relative strength vs benchmark
    bench_c = bench_df["close"].reindex(df.index).ffill()
    ret_63 = (close / close.shift(63)) - 1.0
    bench_ret_63 = (bench_c / bench_c.shift(63)) - 1.0
    rs_63 = ret_63 - bench_ret_63

    # Scan loop
    for i in range(252, len(df) - 30):
        c_i = close.iloc[i]
        o_i = open_p.iloc[i]
        v_i = vol.iloc[i]
        h_i = high.iloc[i]
        l_i = low.iloc[i]

        # Stage 2 check
        stage2 = (
            (c_i > sma50.iloc[i])
            and (sma50.iloc[i] > sma150.iloc[i])
            and (sma150.iloc[i] > sma200.iloc[i])
            and (sma200.iloc[i] > sma200_prev20.iloc[i])
            and (c_i >= 0.75 * h52.iloc[i])
            and (c_i >= 1.30 * l52.iloc[i])
        )

        # SOXX Regime
        soxx_ok = True
        if soxx_sma200 is not None:
            cur_date = df.index[i]
            if cur_date in soxx_df.index:
                soxx_ok = soxx_df.loc[cur_date, "close"] > soxx_sma200.loc[cur_date]

        # EMA Dip check
        ema_dip = (abs(c_i - ema10.iloc[i]) / c_i <= 0.02) or (abs(c_i - ema20.iloc[i]) / c_i <= 0.02)
        trigger_dip = stage2 and ema_dip and (rs_63.iloc[i] > 0) and soxx_ok

        # Pocket Pivot check
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

        t1_target = c_i + (3.0 * atr_val) # Target 1 (R:R approx 2:1)
        t2_target = c_i + (5.0 * atr_val) # Target 2 / Final extension
        rr = (t1_target - c_i) / stop_dist if stop_dist > 0 else 0

        if rr >= 1.5 and stop_pct <= 10.0:
            entry_idx = i + 1
            universe_signals.append({
                "ticker": ticker,
                "signal_idx": i,
                "entry_idx": entry_idx,
                "signal_date": df.index[i],
                "entry_date": df.index[entry_idx],
                "entry_price": open_p.iloc[entry_idx],
                "initial_stop": stop_loss,
                "t1_target": t1_target,
                "t2_target": t2_target,
                "atr": atr_val,
                "rr": rr,
                "stop_dist": stop_dist
            })

print(f"Total Universe Swing signals generated: {len(universe_signals)}")

# -----------------------------------------------------------------------------
# Simulation of 4 Exit Scenarios on Universe Swing
# -----------------------------------------------------------------------------
# Scenario 1: FULL RUNNER TO T1 (100% position sold at Target 1, full stop loss)
# Scenario 2: FULL RUNNER TO T2 (100% position held past T1 trying to reach T2 / 5x ATR)
# Scenario 3: 50% AT T1 + TRAIL RUNNER TO BE (Automated system rule: Bank 50% at T1, move stop to BE, trail remainder with 20-EMA)
# Scenario 4: 50% AT T1 + KEEP ORIGINAL STOP (Bank 50% at T1, keep original stop, trail remainder)
# Scenario 5: NO TARGET - PURE 20-EMA TRAILING STOP (Ride full trend until close < 20-EMA)

def simulate_universe_exits(signals, histories):
    results = {
        "Scenario 1 (100% Exit at Target 1)": [],
        "Scenario 2 (100% Run to Extended Target 2)": [],
        "Scenario 3 (50% at T1 + Breakeven Stop on Runner)": [],
        "Scenario 4 (50% at T1 + Original Stop on Runner)": [],
        "Scenario 5 (Pure 20-EMA Trailing Stop - No Target Cap)": [],
    }

    max_hold_bars = 30 # Max swing horizon (~6 weeks)

    for sig in signals:
        ticker = sig["ticker"]
        df = histories[ticker]
        entry_idx = sig["entry_idx"]
        entry_p = sig["entry_price"]
        init_stop = sig["initial_stop"]
        t1 = sig["t1_target"]
        t2 = sig["t2_target"]
        atr = sig["atr"]

        if entry_idx >= len(df) - 1:
            continue

        end_idx = min(entry_idx + max_hold_bars, len(df))
        forward = df.iloc[entry_idx:end_idx]

        # Tracking variables
        t1_hit = False
        t1_hit_bar = -1
        t2_hit = False

        # --- SCENARIO 1: 100% at T1 ---
        s1_exit_p = None
        s1_bars = 0
        for b_i, (dt, row) in enumerate(forward.iterrows()):
            s1_bars = b_i + 1
            if row["low"] <= init_stop:
                s1_exit_p = init_stop
                break
            if row["high"] >= t1:
                s1_exit_p = t1
                break
            if b_i == len(forward) - 1:
                s1_exit_p = row["close"]
        s1_pnl = (s1_exit_p - entry_p) / entry_p
        results["Scenario 1 (100% Exit at Target 1)"].append({
            "pnl": s1_pnl, "bars": s1_bars, "win": s1_pnl > 0, "ticker": ticker
        })

        # --- SCENARIO 2: 100% Run to T2 ---
        s2_exit_p = None
        s2_bars = 0
        for b_i, (dt, row) in enumerate(forward.iterrows()):
            s2_bars = b_i + 1
            if row["low"] <= init_stop:
                s2_exit_p = init_stop
                break
            if row["high"] >= t2:
                s2_exit_p = t2
                break
            if b_i == len(forward) - 1:
                s2_exit_p = row["close"]
        s2_pnl = (s2_exit_p - entry_p) / entry_p
        results["Scenario 2 (100% Run to Extended Target 2)"].append({
            "pnl": s2_pnl, "bars": s2_bars, "win": s2_pnl > 0, "ticker": ticker
        })

        # --- SCENARIO 3: 50% at T1, Breakeven Stop on 50% Runner, Trail remainder ---
        s3_pnl = 0.0
        s3_bars = 0
        half1_sold = False
        half1_pnl = 0.0
        half2_exit_p = None
        cur_stop_s3 = init_stop

        for b_i, (dt, row) in enumerate(forward.iterrows()):
            s3_bars = b_i + 1
            # Check T1 trigger for first half
            if not half1_sold:
                if row["high"] >= t1:
                    half1_sold = True
                    half1_pnl = (t1 - entry_p) / entry_p
                    cur_stop_s3 = entry_p # Move stop to Breakeven
                elif row["low"] <= cur_stop_s3:
                    # Stopped out full before T1
                    half1_sold = True
                    half1_pnl = (cur_stop_s3 - entry_p) / entry_p
                    half2_exit_p = cur_stop_s3
                    break
            else:
                # First half was sold at T1. Now managing runner with Breakeven or trailing 20-EMA
                ema20_val = calc_ema(df["close"].iloc[:entry_idx + b_i + 1], 20).iloc[-1]
                trailing_stop = max(cur_stop_s3, ema20_val)
                if row["low"] <= trailing_stop:
                    half2_exit_p = trailing_stop
                    break
                if row["high"] >= t2:
                    half2_exit_p = t2
                    break
            if b_i == len(forward) - 1 and half2_exit_p is None:
                half2_exit_p = row["close"]

        if half2_exit_p is None:
            half2_exit_p = entry_p
        half2_pnl = (half2_exit_p - entry_p) / entry_p
        s3_total_pnl = 0.5 * half1_pnl + 0.5 * half2_pnl
        results["Scenario 3 (50% at T1 + Breakeven Stop on Runner)"].append({
            "pnl": s3_total_pnl, "bars": s3_bars, "win": s3_total_pnl > 0, "ticker": ticker
        })

        # --- SCENARIO 4: 50% at T1 + Keep Original Stop on Runner ---
        s4_bars = 0
        half1_sold_s4 = False
        half1_pnl_s4 = 0.0
        half2_exit_p_s4 = None

        for b_i, (dt, row) in enumerate(forward.iterrows()):
            s4_bars = b_i + 1
            if not half1_sold_s4:
                if row["high"] >= t1:
                    half1_sold_s4 = True
                    half1_pnl_s4 = (t1 - entry_p) / entry_p
                elif row["low"] <= init_stop:
                    half1_sold_s4 = True
                    half1_pnl_s4 = (init_stop - entry_p) / entry_p
                    half2_exit_p_s4 = init_stop
                    break
            else:
                ema20_val = calc_ema(df["close"].iloc[:entry_idx + b_i + 1], 20).iloc[-1]
                trailing_stop = max(init_stop, ema20_val)
                if row["low"] <= trailing_stop:
                    half2_exit_p_s4 = trailing_stop
                    break
                if row["high"] >= t2:
                    half2_exit_p_s4 = t2
                    break
            if b_i == len(forward) - 1 and half2_exit_p_s4 is None:
                half2_exit_p_s4 = row["close"]

        if half2_exit_p_s4 is None:
            half2_exit_p_s4 = entry_p
        half2_pnl_s4 = (half2_exit_p_s4 - entry_p) / entry_p
        s4_total_pnl = 0.5 * half1_pnl_s4 + 0.5 * half2_pnl_s4
        results["Scenario 4 (50% at T1 + Original Stop on Runner)"].append({
            "pnl": s4_total_pnl, "bars": s4_bars, "win": s4_total_pnl > 0, "ticker": ticker
        })

        # --- SCENARIO 5: Pure Trailing Stop (No Target Cap) ---
        s5_exit_p = None
        s5_bars = 0
        for b_i, (dt, row) in enumerate(forward.iterrows()):
            s5_bars = b_i + 1
            # Check 1.5x ATR stop initially
            if row["low"] <= init_stop:
                s5_exit_p = init_stop
                break
            # Trailing stop: 20-EMA close cross
            ema20_val = calc_ema(df["close"].iloc[:entry_idx + b_i + 1], 20).iloc[-1]
            if b_i >= 3 and row["close"] < ema20_val:
                s5_exit_p = row["close"]
                break
            if b_i == len(forward) - 1:
                s5_exit_p = row["close"]
        s5_pnl = (s5_exit_p - entry_p) / entry_p
        results["Scenario 5 (Pure 20-EMA Trailing Stop - No Target Cap)"].append({
            "pnl": s5_pnl, "bars": s5_bars, "win": s5_pnl > 0, "ticker": ticker
        })

    return results

universe_results = simulate_universe_exits(universe_signals, histories)

def print_metrics_table(results_dict, title):
    print("\n" + "=" * 95)
    print(f"  {title.upper()}")
    print("=" * 95)
    header = f"{'Exit Scenario':<42} | {'Trades':<6} | {'Win %':<6} | {'PF':<5} | {'Avg Ret':<8} | {'Avg Win':<8} | {'Avg Loss':<8} | {'Avg Days':<8}"
    print(header)
    print("-" * 95)

    compounding_table = {}

    for name, trade_list in results_dict.items():
        n = len(trade_list)
        if n == 0:
            continue
        pnls = [t["pnl"] * 100.0 for t in trade_list]
        wins = [p for p in pnls if p > 0]
        losses = [-p for p in pnls if p <= 0]
        bars = [t["bars"] for t in trade_list]

        wr = len(wins) / n * 100.0
        avg_ret = np.mean(pnls)
        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0
        pf = sum(wins) / sum(losses) if sum(losses) > 0 else 99.0
        avg_bars = np.mean(bars)

        # 10-year compound wealth simulation with $10,000 initial, 10% capital per trade
        cap = 10000.0
        peak = cap
        max_dd = 0.0
        for p in pnls:
            # 10% position sizing
            trade_dollar = (cap * 0.10) * (p / 100.0)
            cap += trade_dollar
            if cap > peak:
                peak = cap
            dd = (peak - cap) / peak * 100.0
            if dd > max_dd:
                max_dd = dd

        compounding_table[name] = {"ending_capital": cap, "max_dd": max_dd}

        print(f"{name:<42} | {n:<6} | {wr:5.1f}% | {pf:5.2f} | {avg_ret:+7.2f}% | {avg_win:+7.2f}% | {avg_loss:7.2f}% | {avg_bars:5.1f} d")

    print("=" * 95)
    print("\n  COMPOUND GROWTH SIMULATION ($10,000 STARTING CAPITAL, 10% EQUITY PER TRADE):")
    print("-" * 95)
    for name, data in compounding_table.items():
        ret_mult = data["ending_capital"] / 10000.0
        print(f"  {name:<42}:  ${data['ending_capital']:11,.2f} ({ret_mult:5.2f}x) | Max Drawdown: {data['max_dd']:4.1f}%")
    print("=" * 95)

print_metrics_table(universe_results, "Universe AI Swing Exit Optimization (10-Year Test)")

# -----------------------------------------------------------------------------
# Part 2: Galaxy v2 Halal Mean Reversion Exit Optimization
# -----------------------------------------------------------------------------
print("\n--- Simulating Galaxy v2 Halal Mean Reversion Exit Optimization ---")
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

    s50 = calc_sma(close, 50)
    s200 = calc_sma(close, 200)
    s10 = calc_sma(close, 10)
    r2 = calc_rsi(close, 2)
    bb_m = calc_sma(close, 20)
    bb_s = close.rolling(20).std()
    bb_low = bb_m - (2.0 * bb_s)
    atr = calc_atr(df, 14)

    for i in range(200, len(df) - 6):
        cur_date = df.index[i]
        # Macro check
        if spy_sma200 is not None and cur_date in spy_df.index:
            if spy_df.loc[cur_date, "close"] < spy_sma200.loc[cur_date]:
                continue
        # Trend gate
        c_i = close.iloc[i]
        if not (c_i > s50.iloc[i] and c_i > s200.iloc[i]):
            continue
        # Trigger
        if not (r2.iloc[i] < 10.0 and low.iloc[i] <= bb_low.iloc[i]):
            continue

        entry_idx = i + 1
        atr_v = atr.iloc[i]
        stop_p = open_p.iloc[entry_idx] - (1.5 * atr_v)
        t1_target = s10.iloc[i]
        t2_target = open_p.iloc[entry_idx] + (3.0 * atr_v)

        galaxy_signals.append({
            "ticker": sym,
            "entry_idx": entry_idx,
            "entry_price": open_p.iloc[entry_idx],
            "initial_stop": stop_p,
            "t1_target": t1_target,
            "t2_target": t2_target,
            "atr": atr_v
        })

print(f"Total Galaxy v2 signals generated: {len(galaxy_signals)}")

def simulate_galaxy_exits(signals, histories):
    results = {
        "Scenario 1 (100% Exit at 10-SMA Target)": [],
        "Scenario 2 (100% Run to Extended 3x ATR Target)": [],
        "Scenario 3 (50% at 10-SMA + Breakeven Stop on Remainder)": [],
        "Scenario 4 (50% at 10-SMA + Original Stop on Remainder)": [],
        "Scenario 5 (Fixed 4-Day Time Limit Exit)": [],
    }

    for sig in signals:
        sym = sig["ticker"]
        df = histories[sym]
        entry_idx = sig["entry_idx"]
        entry_p = sig["entry_price"]
        init_stop = sig["initial_stop"]
        t1 = sig["t1_target"]
        t2 = sig["t2_target"]

        forward = df.iloc[entry_idx:min(entry_idx + 6, len(df))]
        close = df["close"]
        sma10 = calc_sma(close, 10)
        rsi2 = calc_rsi(close, 2)

        # Scenario 1: 100% Exit at 10-SMA
        s1_pnl = None
        s1_bars = 0
        for b_i, (dt, row) in enumerate(forward.iterrows()):
            s1_bars = b_i
            cur_sma10 = sma10.iloc[entry_idx + b_i]
            if b_i < 3: # Islamic Qabd min 3 days
                continue
            if row["low"] <= init_stop:
                s1_pnl = (init_stop - entry_p) / entry_p
                break
            if row["high"] >= cur_sma10:
                s1_pnl = (cur_sma10 - entry_p) / entry_p
                break
            if b_i >= 4:
                s1_pnl = (row["close"] - entry_p) / entry_p
                break
        if s1_pnl is None:
            s1_pnl = (forward["close"].iloc[-1] - entry_p) / entry_p
            s1_bars = len(forward)
        results["Scenario 1 (100% Exit at 10-SMA Target)"].append({"pnl": s1_pnl, "bars": s1_bars})

        # Scenario 2: 100% Run to T2 (3x ATR)
        s2_pnl = None
        s2_bars = 0
        for b_i, (dt, row) in enumerate(forward.iterrows()):
            s2_bars = b_i
            if b_i < 3:
                continue
            if row["low"] <= init_stop:
                s2_pnl = (init_stop - entry_p) / entry_p
                break
            if row["high"] >= t2:
                s2_pnl = (t2 - entry_p) / entry_p
                break
            if b_i >= 4:
                s2_pnl = (row["close"] - entry_p) / entry_p
                break
        if s2_pnl is None:
            s2_pnl = (forward["close"].iloc[-1] - entry_p) / entry_p
            s2_bars = len(forward)
        results["Scenario 2 (100% Run to Extended 3x ATR Target)"].append({"pnl": s2_pnl, "bars": s2_bars})

        # Scenario 3: 50% at 10-SMA + Breakeven Stop on Remainder
        h1_sold = False
        h1_pnl = 0.0
        h2_pnl = None
        cur_stop = init_stop
        s3_bars = 0
        for b_i, (dt, row) in enumerate(forward.iterrows()):
            s3_bars = b_i
            cur_sma10 = sma10.iloc[entry_idx + b_i]
            if b_i < 3:
                continue
            if not h1_sold:
                if row["high"] >= cur_sma10:
                    h1_sold = True
                    h1_pnl = (cur_sma10 - entry_p) / entry_p
                    cur_stop = entry_p
                elif row["low"] <= cur_stop:
                    h1_sold = True
                    h1_pnl = (cur_stop - entry_p) / entry_p
                    h2_pnl = h1_pnl
                    break
            else:
                if row["low"] <= cur_stop:
                    h2_pnl = (cur_stop - entry_p) / entry_p
                    break
                if b_i >= 4:
                    h2_pnl = (row["close"] - entry_p) / entry_p
                    break
        if h2_pnl is None:
            h2_pnl = (forward["close"].iloc[-1] - entry_p) / entry_p
        tot_s3 = 0.5 * h1_pnl + 0.5 * h2_pnl
        results["Scenario 3 (50% at 10-SMA + Breakeven Stop on Remainder)"].append({"pnl": tot_s3, "bars": s3_bars})

        # Scenario 4: 50% at 10-SMA + Original Stop on Remainder
        h1_sold4 = False
        h1_pnl4 = 0.0
        h2_pnl4 = None
        s4_bars = 0
        for b_i, (dt, row) in enumerate(forward.iterrows()):
            s4_bars = b_i
            cur_sma10 = sma10.iloc[entry_idx + b_i]
            if b_i < 3:
                continue
            if not h1_sold4:
                if row["high"] >= cur_sma10:
                    h1_sold4 = True
                    h1_pnl4 = (cur_sma10 - entry_p) / entry_p
                elif row["low"] <= init_stop:
                    h1_sold4 = True
                    h1_pnl4 = (init_stop - entry_p) / entry_p
                    h2_pnl4 = h1_pnl4
                    break
            else:
                if row["low"] <= init_stop:
                    h2_pnl4 = (init_stop - entry_p) / entry_p
                    break
                if b_i >= 4:
                    h2_pnl4 = (row["close"] - entry_p) / entry_p
                    break
        if h2_pnl4 is None:
            h2_pnl4 = (forward["close"].iloc[-1] - entry_p) / entry_p
        tot_s4 = 0.5 * h1_pnl4 + 0.5 * h2_pnl4
        results["Scenario 4 (50% at 10-SMA + Original Stop on Remainder)"].append({"pnl": tot_s4, "bars": s4_bars})

        # Scenario 5: Fixed Day 4 Close
        bar4_idx = min(entry_idx + 4, len(df) - 1)
        s5_exit_p = df["close"].iloc[bar4_idx]
        s5_pnl = (s5_exit_p - entry_p) / entry_p
        results["Scenario 5 (Fixed 4-Day Time Limit Exit)"].append({"pnl": s5_pnl, "bars": 4})

    return results

galaxy_results = simulate_galaxy_exits(galaxy_signals, gal_histories)
print_metrics_table(galaxy_results, "Galaxy v2 Halal Mean Reversion Exit Optimization (5-Year Test)")
