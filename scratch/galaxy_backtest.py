"""
5-Year Dedicated Backtest for Galaxy Halal Active Cash Generator v2
(TradeAlgo Setup #3 Enhanced Rules).

Runs across all 21 verified Shariah-compliant tickers (Zoya/Musaffa approved)
under $50, testing the enhanced rules:
- Dual Trend Gate: Close > 200-SMA AND Close > 50-SMA
- Trigger: RSI(2) < 10 AND Low <= Lower BB(20, 2.0)
- Target: 10-SMA dynamic (mean reversion)
- Stop: 1.5×ATR(14) dynamic (volatility-adaptive)
- RSI Exit: RSI(2) > 70
- Time Limit: Day 4 close
- Islamic Qabd: Min 3 days (strictly enforced)
"""

import sys
import os
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml
import numpy as np
import pandas as pd
import yfinance as yf

from src import galaxy_indicators

# Load config
with open("config/galaxy_universe.yaml") as f:
    univ = yaml.safe_load(f)

tickers = []
for seg in univ.get("segments", {}).values():
    for m in seg.get("members", []):
        tickers.append(m["ticker"])

bench_sym = univ.get("benchmark", {}).get("symbol", "SPY")
all_syms = list(set(tickers + [bench_sym]))

cache_dir = Path("data/galaxy")
cache_dir.mkdir(parents=True, exist_ok=True)

print(f"Loading/Updating 5-year historical data for {len(all_syms)} tickers...")
histories = {}

for sym in all_syms:
    fpath = cache_dir / f"{sym}_5y.csv"
    df = None
    if fpath.exists() and fpath.stat().st_size > 1000:
        try:
            df = pd.read_csv(fpath, index_col=0, parse_dates=True)
            if len(df) < 1000:
                df = None
        except Exception:
            df = None
            
    if df is None:
        try:
            t = yf.Ticker(sym)
            df = t.history(period="5y")
            if not df.empty:
                df.columns = [c.lower() for c in df.columns]
                df.to_csv(fpath)
        except Exception as e:
            pass
            
    if df is not None and len(df) >= 250:
        histories[sym] = df

print(f"Successfully loaded 5-year history for {len(histories)} tickers.")

spy_df = histories.get("SPY")
if spy_df is not None:
    spy_c = spy_df["close"]
    spy_sma200 = spy_c.rolling(200).mean()
else:
    spy_c = None
    spy_sma200 = None

# Enrich each ticker with v2 indicators
enriched = {}
for sym in tickers:
    df = histories.get(sym)
    if df is None or len(df) < 250:
        continue
    d = df.copy()
    c = d["close"]
    h = d["high"]
    l = d["low"]
    v = d["volume"]
    
    d["sma_200"] = galaxy_indicators.sma(c, 200)
    d["sma_50"] = galaxy_indicators.sma(c, 50)
    d["sma_10"] = galaxy_indicators.sma(c, 10)
    d["rsi_2"] = galaxy_indicators.rsi_2(c)
    d["atr_14"] = galaxy_indicators.atr(h, l, c, 14)
    d["bb_lower"] = galaxy_indicators.bollinger_lower(c, 20, 2.0)
    d["vol_sma20"] = v.rolling(20).mean()
    
    if spy_c is not None:
        s_c = spy_c.reindex(d.index).ffill()
        s_sma = spy_sma200.reindex(d.index).ffill()
        d["spy_regime"] = s_c > s_sma
    else:
        d["spy_regime"] = True
        
    enriched[sym] = d

# Run the v2 simulation
trades = []

for sym, d in enriched.items():
    n = len(d)
    cooldown = -1
    
    for i in range(200, n - 4):
        if i <= cooldown:
            continue
            
        row = d.iloc[i]
        
        # v2 Entry Rules:
        # 1. Macro Regime: SPY > 200-SMA
        # 2. Dual Trend Gate: Close > 200-SMA AND Close > 50-SMA
        # 3. Trigger: RSI(2) < 10.0
        # 4. BB Confirmation: Low <= Lower BB(20, 2.0)
        
        close_val = float(row["close"])
        low_val = float(row["low"])
        sma200_val = float(row["sma_200"])
        sma50_val = float(row["sma_50"])
        rsi2_val = float(row["rsi_2"])
        bb_lower_val = float(row["bb_lower"])
        atr_val = float(row["atr_14"])
        
        # Skip if any indicator is NaN
        if np.isnan(sma200_val) or np.isnan(sma50_val) or np.isnan(bb_lower_val) or np.isnan(atr_val):
            continue
        
        trend_ok = close_val > sma200_val and close_val > sma50_val
        is_oversold = rsi2_val < 10.0
        bb_penetration = low_val <= bb_lower_val
        spy_ok = bool(row["spy_regime"])
        
        if trend_ok and is_oversold and bb_penetration and spy_ok:
            entry_idx = i + 1
            entry_date = d.index[entry_idx]
            entry_price = float(d.iloc[entry_idx]["open"])
            
            # v2 Dynamic levels
            stop_price = entry_price - (1.5 * atr_val)     # 1.5×ATR stop
            sma10_at_entry = float(d.iloc[entry_idx]["sma_10"])
            
            exit_price = entry_price
            exit_date = entry_date
            hold_days = 0
            exit_reason = "Time Limit"
            
            # Simulate forward holding window (min 3 days, max 4 days)
            for k in range(entry_idx, min(entry_idx + 6, n)):
                bar = d.iloc[k]
                k_days = k - entry_idx
                
                # Dynamic 10-SMA target updates each bar
                cur_sma10 = float(bar["sma_10"]) if not np.isnan(float(bar["sma_10"])) else sma10_at_entry
                
                # Strict Islamic Qabd constraint: No exit allowed before day 3!
                if k_days >= 3:
                    # 10-SMA dynamic target hit
                    if float(bar["close"]) >= cur_sma10:
                        exit_price = float(bar["close"])
                        exit_date = d.index[k]
                        hold_days = k_days
                        exit_reason = "10-SMA Target"
                        break
                    # 1.5×ATR stop loss hit
                    if float(bar["low"]) <= stop_price:
                        exit_price = stop_price
                        exit_date = d.index[k]
                        hold_days = k_days
                        exit_reason = "ATR Stop"
                        break
                    # RSI-2 snapback (overbought exit)
                    if float(bar["rsi_2"]) >= 70.0:
                        exit_price = float(bar["close"])
                        exit_date = d.index[k]
                        hold_days = k_days
                        exit_reason = "RSI > 70"
                        break
                    # Reached Day 4 limit
                    if k_days >= 4:
                        exit_price = float(bar["close"])
                        exit_date = d.index[k]
                        hold_days = k_days
                        exit_reason = "Day 4 Close"
                        break
                        
                exit_price = float(bar["close"])
                exit_date = d.index[k]
                hold_days = k_days
                
            pnl_pct = (exit_price - entry_price) / entry_price
            
            trades.append({
                "ticker": sym,
                "entry_date": entry_date,
                "exit_date": exit_date,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "hold_days": max(hold_days, 3),
                "pnl_pct": pnl_pct,
                "exit_reason": exit_reason,
                "win": pnl_pct > 0
            })
            
            cooldown = entry_idx + max(hold_days, 3)

# Performance Metrics
years = 5.0
n_trades = len(trades)
pnls = [t["pnl_pct"] for t in trades]
wins = [p for p in pnls if p > 0]
losses = [-p for p in pnls if p < 0]

wr = len(wins) / n_trades * 100.0 if n_trades > 0 else 0.0
avg_ret = np.mean(pnls) * 100.0 if n_trades > 0 else 0.0
avg_win = np.mean(wins) * 100.0 if wins else 0.0
avg_loss = np.mean(losses) * 100.0 if losses else 0.0
pf = sum(wins) / sum(losses) if sum(losses) > 0 else 99.0
avg_hold = np.mean([t["hold_days"] for t in trades]) if trades else 0.0
trades_per_mo = n_trades / (years * 12)
trades_per_wk = n_trades / (years * 52)

# Kelly Criterion calculation
if avg_loss > 0:
    R = avg_win / avg_loss
    W = wr / 100.0
    kelly = W - ((1 - W) / R)
    half_kelly = kelly / 2.0
else:
    kelly = 0.0
    half_kelly = 0.0

# Exit Reason Breakdown
reasons = {}
for t in trades:
    r = t["exit_reason"]
    reasons[r] = reasons.get(r, 0) + 1

print("\n" + "=" * 78)
print("  GALAXY v2 HALAL ENGINE — 5-YEAR BACKTEST (TradeAlgo Setup #3 Enhanced)")
print("  Universe: 21 Verified Halal Stocks (Zoya & Musaffa Approved)")
print("  Rules: RSI(2)<10 + BB Penetration | Dual 50+200 SMA Gate")
print("  Exits: 10-SMA Target | 1.5×ATR Stop | RSI>70 | Day 4")
print("  Hard Constraint: Islamic Qabd (Hold >= 3 Trading Days Strictly Enforced)")
print("=" * 78)

print(f"\n  📊 Core Performance Summary:")
print(f"  {'─' * 55}")
print(f"    Total Trades Taken:       {n_trades} trades across 5 years")
print(f"    Trade Frequency:          {trades_per_wk:.1f} trades/week  (~{trades_per_mo:.1f} trades/month)")
print(f"    Win Rate:                 {wr:.1f}%")
print(f"    Average Gain per Trade:   {avg_ret:+.2f}%  (in {avg_hold:.1f} days average hold)")
print(f"    Average Winning Trade:    +{avg_win:.2f}%")
print(f"    Average Losing Trade:     -{avg_loss:.2f}%")
print(f"    Win/Loss Payoff Ratio:    {(avg_win / avg_loss) if avg_loss > 0 else 0:.2f} : 1")
print(f"    Profit Factor:            {pf:.2f}")
print(f"    Average Holding Duration: {avg_hold:.1f} trading days")

print(f"\n  🧮 Kelly Criterion Sizing:")
print(f"  {'─' * 55}")
print(f"    Full Kelly Fraction:      {kelly*100:.1f}% of equity per trade")
print(f"    Half Kelly (Recommended): {half_kelly*100:.1f}% of equity per trade")
print(f"    Risk per Trade at Half-K: Lose max {half_kelly*avg_loss:.2f}% of equity")

print(f"\n  🎯 Exit Reason Breakdown:")
print(f"  {'─' * 55}")
for r, cnt in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
    pct = (cnt / n_trades) * 100.0
    print(f"    {r:<20} {cnt:>4} trades  ({pct:>5.1f}%)")

# Per-ticker breakdown
print(f"\n  📈 Per-Ticker Performance:")
print(f"  {'─' * 55}")
print(f"  {'Ticker':<8} {'Trades':>7} {'WR':>6} {'Avg Ret':>9} {'PF':>6}")
print(f"  {'-'*40}")
ticker_stats = {}
for t in trades:
    sym = t["ticker"]
    if sym not in ticker_stats:
        ticker_stats[sym] = {"wins": 0, "losses": 0, "pnls": []}
    if t["win"]:
        ticker_stats[sym]["wins"] += 1
    else:
        ticker_stats[sym]["losses"] += 1
    ticker_stats[sym]["pnls"].append(t["pnl_pct"])

for sym, stats in sorted(ticker_stats.items(), key=lambda x: np.mean(x[1]["pnls"]), reverse=True):
    total = stats["wins"] + stats["losses"]
    wr_t = stats["wins"] / total * 100 if total > 0 else 0
    avg_t = np.mean(stats["pnls"]) * 100
    w_arr = [p for p in stats["pnls"] if p > 0]
    l_arr = [-p for p in stats["pnls"] if p < 0]
    pf_t = sum(w_arr) / sum(l_arr) if sum(l_arr) > 0 else 99.0
    print(f"  {sym:<8} {total:>7} {wr_t:>5.0f}% {avg_t:>+8.2f}% {pf_t:>5.2f}")

print("\n" + "=" * 78 + "\n")
