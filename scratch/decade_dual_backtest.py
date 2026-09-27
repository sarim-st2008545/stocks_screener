"""
10-Year Dual-Engine Quantitative Backtest:
Engine 1: Galaxy v2 Halal Mean Reversion (2014-2024 / 10-Year Data)
Engine 2: Universe AI Infrastructure Swing (2014-2024 / 10-Year Data)
"""

import sys
import os
from pathlib import Path
import yaml
import numpy as np
import pandas as pd
import yfinance as yf

# Load Galaxy Universe
with open("config/galaxy_universe.yaml") as f:
    gal_cfg = yaml.safe_load(f)

gal_tickers = []
for seg in gal_cfg.get("segments", {}).values():
    for m in seg.get("members", []):
        gal_tickers.append(m["ticker"])
gal_tickers = sorted(list(set(gal_tickers)))

# Load Universe Tickers
with open("config/universe.yaml") as f:
    uni_cfg = yaml.safe_load(f)

uni_tickers = []
for seg in uni_cfg.get("segments", {}).values():
    for m in seg.get("members", []):
        uni_tickers.append(m["ticker"])
uni_tickers = sorted(list(set(uni_tickers)))

benchmarks = ["SPY", "QQQ", "SOXX", "SMH", "XLU"]
all_symbols = sorted(list(set(gal_tickers + uni_tickers + benchmarks)))

cache_dir = Path("data/prices_10y")
cache_dir.mkdir(parents=True, exist_ok=True)

print(f"Loading 10-year historical prices for {len(all_symbols)} total tickers...")
histories = {}
for sym in all_symbols:
    fpath = cache_dir / f"{sym}.csv"
    df = None
    if fpath.exists() and fpath.stat().st_size > 1000:
        try:
            df = pd.read_csv(fpath, index_col=0, parse_dates=True)
            if len(df) < 1500:
                df = None
        except Exception:
            df = None
    if df is None:
        try:
            t = yf.Ticker(sym)
            df = t.history(period="10y")
            if not df.empty:
                df.columns = [c.lower() for c in df.columns]
                df.to_csv(fpath)
        except Exception:
            pass
    if df is not None and not df.empty:
        histories[sym] = df

print(f"Successfully loaded {len(histories)} / {len(all_symbols)} price histories.")

# ==============================================================================
# 1. GALAXY v2: 10-YEAR MEAN REVERSION SIMULATION
# ==============================================================================
print("\n[Simulating Galaxy v2 10-Year Backtest]...")
spy_df = histories.get("SPY")
if spy_df is not None:
    spy_sma200 = spy_df["close"].rolling(200).mean()
else:
    spy_sma200 = None

def rsi_wilder(series: pd.Series, period: int = 2) -> pd.Series:
    delta = series.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    alpha = 1.0 / period
    ma_up = up.ewm(alpha=alpha, adjust=False).mean()
    ma_down = down.ewm(alpha=alpha, adjust=False).mean()
    rs = ma_up / ma_down
    return 100.0 - (100.0 / (1.0 + rs))

def atr_wilder(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False).mean()

gal_trades = []
for sym in gal_tickers:
    if sym not in histories:
        continue
    df = histories[sym].copy()
    if len(df) < 250:
        continue
    
    close = df["close"]
    low = df["low"]
    high = df["high"]
    
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    sma10 = close.rolling(10).mean()
    r2 = rsi_wilder(close, 2)
    bb_mean = close.rolling(20).mean()
    bb_std = close.rolling(20).std()
    bb_lower = bb_mean - (2.0 * bb_std)
    atr = atr_wilder(df, 14)

    i = 200
    while i < len(df) - 5:
        # Check market regime gate
        cur_date = df.index[i]
        if spy_sma200 is not None:
            try:
                spy_c = spy_df.loc[:cur_date]["close"].iloc[-1]
                spy_ma = spy_sma200.loc[:cur_date].iloc[-1]
                if spy_c < spy_ma:
                    i += 1
                    continue
            except Exception:
                pass
        
        # Dual trend gate
        c_i = close.iloc[i]
        s50 = sma50.iloc[i]
        s200 = sma200.iloc[i]
        if not (c_i > s50 and c_i > s200):
            i += 1
            continue
            
        # Trigger
        rsi_val = r2.iloc[i]
        b_low = bb_lower.iloc[i]
        l_val = low.iloc[i]
        if not (rsi_val < 10.0 and l_val <= b_low):
            i += 1
            continue
            
        # Entry next day open
        entry_idx = i + 1
        entry_price = df["open"].iloc[entry_idx]
        entry_date = df.index[entry_idx]
        cur_atr = atr.iloc[i]
        stop_loss = entry_price - (1.5 * cur_atr)
        
        exit_price = None
        exit_reason = None
        hold_days = 0
        
        for k in range(entry_idx + 1, min(entry_idx + 10, len(df))):
            hold_days = k - entry_idx
            k_high = df["high"].iloc[k]
            k_low = df["low"].iloc[k]
            k_close = df["close"].iloc[k]
            k_sma10 = sma10.iloc[k]
            k_rsi2 = r2.iloc[k]
            
            # Stop loss
            if k_low <= stop_loss:
                exit_price = stop_loss
                exit_reason = "1.5x ATR Stop"
                break
                
            # Islamic Qabd (min 3 days)
            if hold_days < 3:
                continue
                
            # Target Hit (10-SMA)
            if k_high >= k_sma10:
                exit_price = max(entry_price, k_sma10)
                exit_reason = "10-SMA Target"
                break
                
            # RSI Snapback
            if k_rsi2 >= 70.0:
                exit_price = k_close
                exit_reason = "RSI > 70 Snapback"
                break
                
            # Time Limit (Day 4 close)
            if hold_days >= 4:
                exit_price = k_close
                exit_reason = "Day 4 Time Limit"
                break
                
        if exit_price is None and entry_idx + 4 < len(df):
            exit_price = df["close"].iloc[entry_idx + 4]
            exit_reason = "Day 4 Time Limit"
            hold_days = 4
            
        if exit_price is not None:
            pnl = (exit_price - entry_price) / entry_price
            gal_trades.append({
                "ticker": sym,
                "entry_date": str(entry_date.date()),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": pnl * 100.0,
                "hold_days": hold_days,
                "exit_reason": exit_reason,
                "win": pnl > 0
            })
            i = entry_idx + hold_days
        else:
            i += 1

# Output Galaxy Summary
n_gal = len(gal_trades)
gal_wins = [t["pnl_pct"] for t in gal_trades if t["win"]]
gal_losses = [-t["pnl_pct"] for t in gal_trades if not t["win"]]
gal_wr = (len(gal_wins) / n_gal * 100.0) if n_gal > 0 else 0
gal_avg_win = np.mean(gal_wins) if gal_wins else 0
gal_avg_loss = np.mean(gal_losses) if gal_losses else 0
gal_pf = sum(gal_wins) / sum(gal_losses) if sum(gal_losses) > 0 else 99.0
gal_avg_ret = np.mean([t["pnl_pct"] for t in gal_trades]) if n_gal > 0 else 0

print(f"Galaxy 10-Year Results:")
print(f"Trades: {n_gal}, Win Rate: {gal_wr:.1f}%, Profit Factor: {gal_pf:.2f}, Avg Return: {gal_avg_ret:+.2f}%")
print(f"Avg Win: +{gal_avg_win:.2f}%, Avg Loss: -{gal_avg_loss:.2f}%")
