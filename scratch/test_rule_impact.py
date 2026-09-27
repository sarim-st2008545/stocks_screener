"""
Comprehensive Backtest Comparison:
Investigating the effect of adding:
1. EMA20 Slope Filter (EMA20 >= EMA20[3])
2. EMA20 Daily Non-Negative Filter (EMA20 >= EMA20[1])
3. Candlestick Confirmation Filter (Hammer, Engulfing, Inside Up)
4. Consecutive Down Bars Guard (Down bars <= 4)
on:
- Win Rate (10d and 25d)
- Trade Count (Frequency / Sample Size)
- Average Return & Expectancy (R)
- Profit Factor
- Effect on our 5 Live Trades (AMZN, AAPL, MU, TSM, ANET)
"""

import numpy as np
import pandas as pd
from src import indicators, universe
from pathlib import Path

# Load all data
tickers = [c.ticker for c in universe.candidates()]
benchmarks = ["SOXX", "SMH", "SPY", "QQQ", "XLU"]
ticker_bench = universe.ticker_benchmarks()

histories = {}
for sym in tickers + benchmarks:
    p = Path(f"data/prices/{sym}.csv")
    if p.exists():
        df = pd.read_csv(p, index_col=0, parse_dates=True)
        if len(df) >= 252:
            histories[sym] = df

soxx_df = histories.get("SOXX")
soxx_close = soxx_df["close"]
soxx_sma200 = indicators.sma(soxx_close, 200)

def simulate_engine(rule_variant="base"):
    all_trades = []
    
    for sym in tickers:
        df = histories.get(sym)
        if df is None or len(df) < 300:
            continue
            
        bench_sym = ticker_bench.get(sym, "SMH")
        bench_df = histories.get(bench_sym, histories.get("SMH"))
        if bench_df is None or len(bench_df) < 100:
            continue
            
        close = df["close"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"]
        open_p = df["open"]
        
        # Precompute indicators
        ema10 = indicators.ema(close, 10)
        ema20 = indicators.ema(close, 20)
        sma50 = indicators.sma(close, 50)
        sma150 = indicators.sma(close, 150)
        sma200 = indicators.sma(close, 200)
        sma200_prev20 = sma200.shift(20)
        atr14 = indicators.atr(high, low, close, 14)
        s_low10 = indicators.swing_low(low, 10)
        h52 = high.rolling(252).max()
        l52 = low.rolling(252).min()
        candles = indicators.detect_candles(df)
        
        bench_c = bench_df["close"].reindex(df.index).ffill()
        ret_63 = (close / close.shift(63)) - 1
        bench_ret_63 = (bench_c / bench_c.shift(63)) - 1
        rs_63 = ret_63 - bench_ret_63
        
        # SOXX regime aligned
        soxx_c_aligned = soxx_close.reindex(df.index).ffill()
        soxx_sma_aligned = soxx_sma200.reindex(df.index).ffill()
        regime_ok = soxx_c_aligned > soxx_sma_aligned
        
        # Down volume for pocket pivot
        is_down = close < close.shift(1)
        down_vol = pd.Series(np.where(is_down, vol, 0.0), index=df.index)
        max_down_vol_10 = down_vol.rolling(10).max().shift(1)
        
        # Slope calculations
        ema20_slope_3 = ema20 - ema20.shift(3)
        ema20_slope_1 = ema20 - ema20.shift(1)
        
        # Consecutive down bars
        down_bar = close < open_p
        
        # Loop across 10 years (2015 to 2026)
        start_idx = max(252, df.index.get_indexer([pd.Timestamp("2015-01-01")], method="bfill")[0])
        end_idx = len(df) - 30
        
        i = start_idx
        while i < end_idx:
            c_val = close.iloc[i]
            o_val = open_p.iloc[i]
            v_val = vol.iloc[i]
            s50 = sma50.iloc[i]
            s150 = sma150.iloc[i]
            s200 = sma200.iloc[i]
            s200_p = sma200_prev20.iloc[i]
            h52_v = h52.iloc[i]
            l52_v = l52.iloc[i]
            e10 = ema10.iloc[i]
            e20 = ema20.iloc[i]
            rs_v = rs_63.iloc[i]
            reg_v = regime_ok.iloc[i]
            atr_v = atr14.iloc[i]
            slow_v = s_low10.iloc[i]
            
            # Check Minervini Stage 2
            stage2 = (
                (c_val > s50)
                and (s50 > s150)
                and (s150 > s200)
                and (s200 > s200_p)
                and (c_val >= 0.75 * h52_v)
                and (c_val >= 1.30 * l52_v)
            )
            
            # Pocket Pivot
            is_up = c_val > o_val
            pv_thresh = max_down_vol_10.iloc[i]
            pocket_pivot_vol = is_up and (v_val > pv_thresh) and (pv_thresh > 0)
            near_base = (
                (abs(c_val - e10) / c_val <= 0.025)
                or (abs(c_val - e20) / c_val <= 0.025)
                or (abs(c_val - s50) / c_val <= 0.025)
            )
            trigger_pivot = pocket_pivot_vol and near_base and (c_val > s50) and (rs_v > 0) and reg_v
            
            # Stage 2 Dip
            ema_dip = (abs(c_val - e10) / c_val <= 0.02) or (abs(c_val - e20) / c_val <= 0.02)
            trigger_dip = stage2 and ema_dip and (rs_v > 0) and reg_v
            
            # Apply Variants
            if rule_variant == "base":
                signal = trigger_pivot or trigger_dip
            elif rule_variant == "ema20_slope_3d":
                slope_ok = ema20_slope_3.iloc[i] >= 0
                signal = (trigger_pivot or trigger_dip) and slope_ok
            elif rule_variant == "ema20_slope_1d":
                slope_ok = ema20_slope_1.iloc[i] >= 0
                signal = (trigger_pivot or trigger_dip) and slope_ok
            elif rule_variant == "candle_confirmation":
                c_ok = not pd.isna(candles.iloc[i])
                signal = (trigger_pivot or trigger_dip) and c_ok
            elif rule_variant == "slope3d_and_candle":
                slope_ok = ema20_slope_3.iloc[i] >= 0
                c_ok = not pd.isna(candles.iloc[i])
                signal = (trigger_pivot or trigger_dip) and slope_ok and c_ok
            else:
                signal = False
                
            if not signal:
                i += 1
                continue
                
            # Trade Plan
            valid_struct = [x for x in [slow_v, s50] if not np.isnan(x) and x < c_val]
            chosen_struct = min(valid_struct) if valid_struct else (c_val - 2.0 * atr_v)
            vol_floor_stop = c_val - (1.5 * atr_v)
            stop_loss = min(chosen_struct, vol_floor_stop)
            stop_dist = c_val - stop_loss
            stop_pct = (stop_dist / c_val) * 100.0
            
            t1 = c_val + (3.0 * atr_v)
            rr_t1 = (t1 - c_val) / stop_dist if stop_dist > 0 else 0.0
            
            if rr_t1 < 1.5 or stop_pct > 10.0:
                i += 1
                continue
                
            # Execute Next Day Open
            entry_idx = i + 1
            if entry_idx >= len(df):
                break
            entry_p = open_p.iloc[entry_idx]
            entry_date = df.index[entry_idx]
            
            # Track outcomes at 10-day and 25-day horizon
            idx_10 = min(entry_idx + 10, len(df) - 1)
            idx_25 = min(entry_idx + 25, len(df) - 1)
            
            ret_10 = (close.iloc[idx_10] - entry_p) / entry_p
            ret_25 = (close.iloc[idx_25] - entry_p) / entry_p
            r_10 = (close.iloc[idx_10] - entry_p) / stop_dist
            r_25 = (close.iloc[idx_25] - entry_p) / stop_dist
            
            all_trades.append({
                "ticker": sym,
                "entry_date": entry_date,
                "ret_10": ret_10,
                "ret_25": ret_25,
                "r_10": r_10,
                "r_25": r_25,
                "win_10": ret_10 > 0,
                "win_25": ret_25 > 0,
            })
            
            # Step forward to prevent immediate overlapping signals on same stock
            i = entry_idx + 5
            
    return all_trades

variants = [
    ("1. Baseline (Current Scanner)", "base"),
    ("2. + EMA20 Slope >= 0 (3-day)", "ema20_slope_3d"),
    ("3. + EMA20 Slope >= 0 (1-day)", "ema20_slope_1d"),
    ("4. + Candlestick Pattern", "candle_confirmation"),
    ("5. + EMA20 Slope (3d) + Candle", "slope3d_and_candle"),
]

print("\n" + "=" * 110)
print(f"{'Variant':35s} | {'Signals':7s} | {'10d WR':7s} | {'10d Ret':8s} | {'25d WR':7s} | {'25d Ret':8s} | {'25d PF':7s} | {'25d Exp [R]':11s}")
print("=" * 110)

results = {}
for name, var_key in variants:
    trades = simulate_engine(var_key)
    n = len(trades)
    if n == 0:
        continue
    w10 = np.mean([t["win_10"] for t in trades]) * 100
    r10 = np.mean([t["ret_10"] for t in trades]) * 100
    w25 = np.mean([t["win_25"] for t in trades]) * 100
    r25 = np.mean([t["ret_25"] for t in trades]) * 100
    
    g25 = [t["r_25"] for t in trades if t["r_25"] > 0]
    l25 = [-t["r_25"] for t in trades if t["r_25"] < 0]
    pf25 = sum(g25) / sum(l25) if sum(l25) > 0 else 99.0
    exp_r25 = np.mean([t["r_25"] for t in trades])
    
    results[var_key] = trades
    print(f"{name:35s} | {n:7d} | {w10:6.1f}% | {r10:+7.2f}% | {w25:6.1f}% | {r25:+7.2f}% | {pf25:7.2f} | {exp_r25:+10.3f}R")
print("=" * 110)
