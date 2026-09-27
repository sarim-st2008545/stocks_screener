"""
Optimized Liquidity Sweep Backtest with Macro Filter & ATR Floor
"""

import numpy as np
import pandas as pd
from pathlib import Path
from scratch.backtest_liquidity_range import compute_adx, compute_atr

def test_variants():
    files = list(Path("data/galaxy").glob("*_5y.csv"))
    
    # Load SPY
    spy_df = pd.read_csv("data/galaxy/SPY_5y.csv", index_col=0, parse_dates=True)
    spy_df.columns = [c.lower() for c in spy_df.columns]
    spy_sma200 = spy_df["close"].rolling(200).mean()
    
    results = []
    
    # Test different stop multipliers and trend requirements
    for stop_mult in [0.8, 1.2, 1.5]:
        for req_above_200 in [False, True]:
            for rsi_thresh in [100, 25]: # 100 means no RSI filter, 25 means oversold filter
                trades = []
                for f in files:
                    sym = f.stem.replace("_5y", "")
                    if sym == "SPY":
                        continue
                    df = pd.read_csv(f, index_col=0, parse_dates=True)
                    df.columns = [c.lower() for c in df.columns]
                    if len(df) < 220:
                        continue
                        
                    close = df["close"]
                    high = df["high"]
                    low = df["low"]
                    open_p = df["open"]
                    vol = df["volume"]
                    
                    adx = compute_adx(df, 14)
                    atr = compute_atr(df, 14)
                    sma200 = close.rolling(200).mean()
                    vol_ma20 = vol.rolling(20).mean()
                    
                    # RSI 2
                    delta = close.diff()
                    up = delta.clip(lower=0.0).ewm(alpha=0.5, adjust=False).mean()
                    down = (-delta).clip(lower=0.0).ewm(alpha=0.5, adjust=False).mean()
                    r2 = 100.0 - (100.0 / (1.0 + (up / down.replace(0, np.nan))))
                    
                    lookback_range = 30
                    range_high = high.rolling(lookback_range).max().shift(1)
                    range_low = low.rolling(lookback_range).min().shift(1)
                    range_mid = (range_high + range_low) / 2.0
                    range_width_pct = ((range_high - range_low) / range_low) * 100.0
                    
                    is_consolidating = (adx < 25.0) & (range_width_pct <= 25.0) & (range_width_pct >= 6.0)
                    sweep_low = low < range_low
                    reject_up = close >= range_low
                    vol_absorbed = vol >= (0.8 * vol_ma20)
                    
                    trigger = is_consolidating & sweep_low & reject_up & vol_absorbed
                    if req_above_200:
                        trigger = trigger & (close > sma200)
                    if rsi_thresh < 100:
                        trigger = trigger & (r2 < rsi_thresh)
                        
                    i = 200
                    while i < len(df) - 10:
                        if not trigger.iloc[i]:
                            i += 1
                            continue
                            
                        entry_idx = i + 1
                        entry_p = open_p.iloc[entry_idx]
                        entry_date = df.index[entry_idx]
                        
                        curr_atr = atr.iloc[i]
                        sweep_bar_low = low.iloc[i]
                        stop_loss = sweep_bar_low - (stop_mult * curr_atr)
                        target_mid = range_mid.iloc[i]
                        
                        if (target_mid - entry_p) <= 0 or (entry_p - stop_loss) <= 0:
                            i += 1
                            continue
                            
                        exit_price = None
                        exit_reason = None
                        hold_days = 0
                        
                        for k in range(entry_idx + 1, min(entry_idx + 12, len(df))):
                            hold_days = k - entry_idx
                            k_low = low.iloc[k]
                            k_high = high.iloc[k]
                            k_close = close.iloc[k]
                            
                            if k_low <= stop_loss:
                                exit_price = stop_loss
                                exit_reason = "Stop"
                                break
                            if hold_days < 3:
                                continue
                            if k_high >= target_mid:
                                exit_price = target_mid
                                exit_reason = "Target"
                                break
                            if hold_days >= 8:
                                exit_price = k_close
                                exit_reason = "Time"
                                break
                                
                        if exit_price is not None:
                            pnl = (exit_price - entry_p) / entry_p
                            trades.append({"pnl": pnl * 100, "win": pnl > 0})
                            i = entry_idx + hold_days
                        else:
                            i += 1
                            
                n = len(trades)
                if n >= 15:
                    w = np.mean([t["win"] for t in trades]) * 100
                    wins = [t["pnl"] for t in trades if t["win"]]
                    losses = [-t["pnl"] for t in trades if not t["win"]]
                    pf = sum(wins) / sum(losses) if sum(losses) > 0 else 99.0
                    avg_ret = np.mean([t["pnl"] for t in trades])
                    print(f"Stop: {stop_mult}x ATR | >200SMA: {str(req_above_200):5s} | RSI<{rsi_thresh:2d} | Trades: {n:3d} | WR: {w:5.1f}% | PF: {pf:4.2f} | AvgRet: {avg_ret:+5.2f}%")

test_variants()
