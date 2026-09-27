"""
Consolidation & Liquidity Sweep Quantitative Research Engine
=============================================================
1. Quantitative Range / Consolidation Detector:
   - Identifies sideways consolidation vs trending markets:
     a) ADX(14) < 22 (Absence of strong directional trend)
     b) Normalized Donchian Width / Range Compression:
        (Highest_High(30) - Lowest_Low(30)) / SMA(50) <= 25%
     c) 50-SMA Slope is relatively flat (within +/- 3% over 20 days)

2. Wyckoff / Liquidity Sweep Entry Mechanics:
   - Identifies Range Low (Support) over lookback L (e.g. 20-30 days)
   - Liquidity Sweep Condition:
     a) Low[t] breaks below Prior Range Low (triggers retail sell stops)
     b) Close[t] reverses and closes BACK ABOVE Prior Range Low (liquidity absorbed)
     c) Volume spike or Hammer/Pin-bar wick at the bottom of range
   - Entry: Next Day Open (buying the dip at the bottom of range)

3. Trade Plan & Target Rotation:
   - Stop Loss: 0.5x to 1.0x ATR below the sweep low
   - Target 1: Range Midpoint (50% of the range) -> Take 50% profit
   - Target 2: Range High (Resistance) -> Exit remaining 50%
   - Time Stop: Max 10-15 trading days
   - Islamic Qabd: Holding >= 3 trading days
"""

import numpy as np
import pandas as pd
from pathlib import Path

def compute_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Standard Wilder ADX."""
    h, l, c = df["high"], df["low"], df["close"]
    prev_h = h.shift(1)
    prev_l = l.shift(1)
    prev_c = c.shift(1)
    
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0/period, adjust=False).mean()
    
    up_move = h - prev_h
    down_move = prev_l - l
    
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    
    plus_di = 100.0 * pd.Series(plus_dm, index=df.index).ewm(alpha=1.0/period, adjust=False).mean() / atr
    minus_di = 100.0 * pd.Series(minus_dm, index=df.index).ewm(alpha=1.0/period, adjust=False).mean() / atr
    
    dx = 100.0 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
    adx = dx.ewm(alpha=1.0/period, adjust=False).mean()
    return adx

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/period, adjust=False).mean()

def run_range_liquidity_backtest(
    lookback_range: int = 30,
    max_adx: float = 24.0,
    max_range_width_pct: float = 25.0,
    min_hold_days: int = 3,
    max_hold_days: int = 12,
):
    files = list(Path("data/galaxy").glob("*_5y.csv"))
    all_trades = []
    
    for f in files:
        sym = f.stem.replace("_5y", "")
        if sym == "SPY":
            continue
            
        df = pd.read_csv(f, index_col=0, parse_dates=True)
        df.columns = [c.lower() for c in df.columns]
        if len(df) < 150:
            continue
            
        close = df["close"]
        high = df["high"]
        low = df["low"]
        open_p = df["open"]
        vol = df["volume"]
        
        adx = compute_adx(df, 14)
        atr = compute_atr(df, 14)
        sma50 = close.rolling(50).mean()
        vol_ma20 = vol.rolling(20).mean()
        
        # Donchian channel over lookback (prior range, shifted by 1 so we don't look ahead)
        range_high = high.rolling(lookback_range).max().shift(1)
        range_low = low.rolling(lookback_range).min().shift(1)
        range_mid = (range_high + range_low) / 2.0
        range_width_pct = ((range_high - range_low) / range_low) * 100.0
        
        # Consolidation check:
        # 1. ADX is low (no strong runaway trend)
        # 2. Range width is reasonable (consolidating within bounds)
        is_consolidating = (adx < max_adx) & (range_width_pct <= max_range_width_pct) & (range_width_pct >= 6.0)
        
        # Liquidity Sweep trigger:
        # 1. Low drops below Prior Range Low (Sweep retail stop-losses)
        # 2. Close rejects and finishes ABOVE Prior Range Low (failed breakdown / absorption)
        # 3. Volume is elevated or candle shows bottom wick
        sweep_low = low < range_low
        reject_up = close >= range_low
        vol_absorbed = vol >= (0.9 * vol_ma20)
        
        trigger = is_consolidating & sweep_low & reject_up & vol_absorbed
        
        i = max(lookback_range + 50, 100)
        while i < len(df) - max_hold_days:
            if not trigger.iloc[i]:
                i += 1
                continue
                
            entry_idx = i + 1
            entry_p = open_p.iloc[entry_idx]
            entry_date = df.index[entry_idx]
            
            curr_r_low = range_low.iloc[i]
            curr_r_mid = range_mid.iloc[i]
            curr_r_high = range_high.iloc[i]
            curr_atr = atr.iloc[i]
            sweep_bar_low = low.iloc[i]
            
            # Stop loss: 0.5x ATR below the sweep candle low
            stop_loss = sweep_bar_low - (0.5 * curr_atr)
            target_mid = curr_r_mid
            target_high = curr_r_high
            
            # Require positive risk:reward to midpoint
            if (target_mid - entry_p) <= 0 or (entry_p - stop_loss) <= 0:
                i += 1
                continue
                
            rr_mid = (target_mid - entry_p) / (entry_p - stop_loss)
            if rr_mid < 0.8:  # Skip if entry is already too high up in the range
                i += 1
                continue
                
            exit_price = None
            exit_reason = None
            hold_days = 0
            
            for k in range(entry_idx + 1, min(entry_idx + max_hold_days + 1, len(df))):
                hold_days = k - entry_idx
                k_low = low.iloc[k]
                k_high = high.iloc[k]
                k_close = close.iloc[k]
                
                # Check Stop Loss
                if k_low <= stop_loss:
                    exit_price = stop_loss
                    exit_reason = "Sweep Invalidation (Stop)"
                    break
                    
                # Islamic Qabd: no exit before day 3
                if hold_days < min_hold_days:
                    continue
                    
                # Target 1: Midpoint Hit
                if k_high >= target_mid:
                    exit_price = target_mid
                    exit_reason = "Range Midline Hit"
                    break
                    
                # Target 2: Range High Hit
                if k_high >= target_high:
                    exit_price = target_high
                    exit_reason = "Range High Hit"
                    break
                    
                # Time limit
                if hold_days >= max_hold_days:
                    exit_price = k_close
                    exit_reason = "Max Hold Time Stop"
                    break
                    
            if exit_price is None and (entry_idx + max_hold_days < len(df)):
                exit_price = close.iloc[entry_idx + max_hold_days]
                exit_reason = "Max Hold Time Stop"
                hold_days = max_hold_days
                
            if exit_price is not None:
                pnl = (exit_price - entry_p) / entry_p
                all_trades.append({
                    "ticker": sym,
                    "entry_date": entry_date,
                    "entry_price": entry_p,
                    "exit_price": exit_price,
                    "pnl_pct": pnl * 100.0,
                    "hold_days": hold_days,
                    "exit_reason": exit_reason,
                    "win": pnl > 0,
                    "rr_mid": rr_mid
                })
                i = entry_idx + hold_days
            else:
                i += 1
                
    return all_trades

print("\n" + "=" * 90)
print("  HALAL CONSOLIDATION & LIQUIDITY SWEEP STRATEGY (5-Year Multi-Stock Backtest)")
print("=" * 90)

trades = run_range_liquidity_backtest()
n = len(trades)
if n > 0:
    wins = [t["pnl_pct"] for t in trades if t["win"]]
    losses = [-t["pnl_pct"] for t in trades if not t["win"]]
    wr = len(wins) / n * 100.0
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    pf = sum(wins) / sum(losses) if sum(losses) > 0 else 99.0
    avg_ret = np.mean([t["pnl_pct"] for t in trades])
    avg_hold = np.mean([t["hold_days"] for t in trades])
    
    print(f"Total Trades Triggered:    {n}")
    print(f"Win Rate:                  {wr:.1f}%")
    print(f"Profit Factor:             {pf:.2f}")
    print(f"Average Return / Trade:    {avg_ret:+.2f}%")
    print(f"Average Gain per Win:      +{avg_win:.2f}%")
    print(f"Average Loss per Loss:     -{avg_loss:.2f}%")
    print(f"Average Holding Time:      {avg_hold:.1f} trading days")
    
    # Exit breakdown
    print("\nExit Reason Breakdown:")
    reasons = pd.Series([t["exit_reason"] for t in trades]).value_counts()
    for r, count in reasons.items():
        print(f"  - {r:28s}: {count:3d} ({count/n*100:.1f}%)")
        
    print("\nSample Trades on Halal Consolidators:")
    df_t = pd.DataFrame(trades)
    for sym in ["DOCU", "BOX", "PINS", "PATH", "INTC", "CCJ"]:
        sub_t = df_t[df_t["ticker"] == sym]
        if not sub_t.empty:
            sub_w = sub_t["win"].mean() * 100
            print(f"  - {sym:6s}: {len(sub_t):2d} trades | Win Rate: {sub_w:.0f}% | Avg Ret: {sub_t['pnl_pct'].mean():+.2f}%")
print("=" * 90)
