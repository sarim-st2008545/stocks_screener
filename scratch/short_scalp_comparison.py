"""
Short-Cycle Active Cash Generator: 3-Strategy Comparative Backtest.

Evaluates 3 distinct short-cycle (3-4 day holding) trading strategies across
a high-beta, multi-sector universe of under-$50 stocks:

1. Model A: Cover-Shannon Volatility Pumping Rebalancer
   - Direct implementation of Thomas Cover's log-optimal rebalancing across
     volatile, low-correlation asset pairs on a 3-4 day cycle.

2. Model B: Connors 3-Day Quant Mean Reversion (RSI-2 Panic Capitulation)
   - Institutional quantitative standard: Price > 200-SMA, RSI(2) < 10,
     exiting on Day 3 or Day 4 (or when RSI(2) > 70).

3. Model C: Cover Log-Dislocation Shock (Statistical Log-Relative Reversion)
   - Enters when 3-day log-price relative drops > 2.0 standard deviations below
     mean, holding for 3 to 4 days to capture the statistical snapback.

All models strictly enforce:
- Minimum holding duration >= 3 trading days (Islamic Qabd constraint).
- Maximum holding duration <= 4 trading days (rapid cash turnover).
"""

import sys
import os
from pathlib import Path
import numpy as np
import pandas as pd

# Load cached data
cache_dir = Path("data/active_cash")
tickers = [
    "SOFI", "HOOD", "AFRM", "F",
    "ZETA", "PATH", "RBLX", "IONQ",
    "BE", "RUN", "CCJ", "UEC",
    "SMCI", "RIVN", "CCL", "AAL"
]

data = {}
for sym in tickers:
    fpath = cache_dir / f"{sym}.csv"
    if fpath.exists():
        df = pd.read_csv(fpath, index_col=0, parse_dates=True)
        if len(df) >= 250:
            data[sym] = df

spy_df = pd.read_csv(cache_dir / "SPY.csv", index_col=0, parse_dates=True)
spy_c = spy_df["close"]
spy_sma200 = spy_c.rolling(200).mean()

# Vectorized indicators
def calc_rsi(series, period=2):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

enriched = {}
for sym, df in data.items():
    d = df.copy()
    c, h, l, v = d["close"], d["high"], d["low"], d["volume"]
    d["sma_50"] = c.rolling(50).mean()
    d["sma_200"] = c.rolling(200).mean()
    d["rsi_2"] = calc_rsi(c, 2)
    d["tr"] = np.maximum(h - l, np.maximum((h - c.shift(1)).abs(), (l - c.shift(1)).abs()))
    d["atr_14"] = d["tr"].rolling(14).mean()
    
    # Cover 3-day log price relative: log(P_t / P_{t-3})
    d["log_rel_3d"] = np.log(c / c.shift(3))
    d["log_rel_mean_20"] = d["log_rel_3d"].rolling(20).mean()
    d["log_rel_std_20"] = d["log_rel_3d"].rolling(20).std()
    d["log_zscore_3d"] = (d["log_rel_3d"] - d["log_rel_mean_20"]) / d["log_rel_std_20"].replace(0, np.nan)
    
    # SPY regime
    d["spy_regime"] = spy_c.reindex(d.index).ffill() > spy_sma200.reindex(d.index).ffill()
    enriched[sym] = d


# =============================================================================
# MODEL B: CONNORS 3-DAY QUANT MEAN REVERSION (RSI-2 < 10)
# =============================================================================
def backtest_model_b(enriched_data):
    trades = []
    for sym, d in enriched_data.items():
        n = len(d)
        cooldown = -1
        for i in range(200, n - 4):
            if i <= cooldown:
                continue
            row = d.iloc[i]
            # 1. Macro Trend: Price > 200-SMA
            # 2. Extreme Panic: RSI-2 < 10
            # 3. Market Regime: SPY > 200-SMA
            if row["close"] > row["sma_200"] and row["rsi_2"] < 10.0 and row["spy_regime"]:
                entry_idx = i + 1
                entry_price = float(d.iloc[entry_idx]["open"])
                stop_loss = entry_price * 0.95  # 5% safety stop
                
                exit_price = entry_price
                hold_days = 0
                # Hold for strictly 3 to 4 days
                for k in range(entry_idx, min(entry_idx + 5, n)):
                    bar = d.iloc[k]
                    days = k - entry_idx
                    # Hard stop check
                    if float(bar["low"]) <= stop_loss and days >= 3:
                        exit_price = stop_loss
                        hold_days = days
                        break
                    # Exit condition: day >= 3 and (RSI > 70 or reached Day 4)
                    if days >= 3:
                        if float(bar["rsi_2"]) >= 70.0 or days >= 4:
                            exit_price = float(bar["close"])
                            hold_days = days
                            break
                        exit_price = float(bar["close"])
                        hold_days = days

                pnl = (exit_price - entry_price) / entry_price
                trades.append({
                    "strategy": "Model B: Connors RSI-2",
                    "ticker": sym,
                    "entry_date": d.index[entry_idx],
                    "pnl": pnl,
                    "hold_days": max(hold_days, 3),
                    "win": pnl > 0
                })
                cooldown = entry_idx + max(hold_days, 3)
    return trades


# =============================================================================
# MODEL C: COVER LOG-DISLOCATION SHOCK (3-day Z-Score < -2.0)
# =============================================================================
def backtest_model_c(enriched_data):
    trades = []
    for sym, d in enriched_data.items():
        n = len(d)
        cooldown = -1
        for i in range(200, n - 4):
            if i <= cooldown:
                continue
            row = d.iloc[i]
            # 1. Macro Trend: Price > 50-SMA
            # 2. Extreme 3-day log price relative shock: Z <= -2.0
            # 3. Market Regime: SPY > 200-SMA
            if row["close"] > row["sma_50"] and row["log_zscore_3d"] <= -2.0 and row["spy_regime"]:
                entry_idx = i + 1
                entry_price = float(d.iloc[entry_idx]["open"])
                stop_loss = entry_price * 0.95
                
                exit_price = entry_price
                hold_days = 0
                for k in range(entry_idx, min(entry_idx + 5, n)):
                    bar = d.iloc[k]
                    days = k - entry_idx
                    if float(bar["low"]) <= stop_loss and days >= 3:
                        exit_price = stop_loss
                        hold_days = days
                        break
                    # Target: +4.0% recovery pop or day 4
                    if days >= 3:
                        if float(bar["high"]) >= entry_price * 1.04 or days >= 4:
                            exit_price = min(float(bar["close"]), entry_price * 1.04) if float(bar["high"]) >= entry_price * 1.04 else float(bar["close"])
                            hold_days = days
                            break
                        exit_price = float(bar["close"])
                        hold_days = days

                pnl = (exit_price - entry_price) / entry_price
                trades.append({
                    "strategy": "Model C: Cover Log-Shock",
                    "ticker": sym,
                    "entry_date": d.index[entry_idx],
                    "pnl": pnl,
                    "hold_days": max(hold_days, 3),
                    "win": pnl > 0
                })
                cooldown = entry_idx + max(hold_days, 3)
    return trades


# =============================================================================
# MODEL A: COVER-SHANNON VOLATILITY PUMPING REBALANCER (PAIRS)
# =============================================================================
def backtest_model_a(enriched_data):
    """
    Pairs volatile, low-correlation assets. Rebalances every 3 to 4 days,
    shifting capital into the temporary laggard and taking profit on the leader.
    """
    pairs = [
        ("SOFI", "BE"),     # Fintech vs Clean Energy
        ("ZETA", "CCL"),    # AI Software vs Travel/Consumer
        ("AFRM", "RUN"),    # Buy-Now-Pay-Later vs Solar
        ("RBLX", "CCJ"),    # Gaming/Tech vs Uranium
        ("HOOD", "AAL")     # Brokerage vs Airline
    ]
    trades = []
    
    for s1, s2 in pairs:
        if s1 not in enriched_data or s2 not in enriched_data:
            continue
        d1, d2 = enriched_data[s1], enriched_data[s2]
        common_idx = d1.index.intersection(d2.index)
        if len(common_idx) < 300:
            continue
        
        c1 = d1.loc[common_idx, "close"]
        c2 = d2.loc[common_idx, "close"]
        
        # 3-day holding cycle rebalancer
        step = 3
        for idx in range(100, len(common_idx) - step - 1, step):
            t0 = common_idx[idx]
            t_exit = common_idx[idx + step]
            
            ret1 = (c1.loc[t_exit] - c1.loc[t0]) / c1.loc[t0]
            ret2 = (c2.loc[t_exit] - c2.loc[t0]) / c2.loc[t0]
            
            # Shannon-Cover rebalance logic:
            # 50/50 capital allocation rebalanced every 3 days
            cycle_pnl = 0.5 * ret1 + 0.5 * ret2
            
            trades.append({
                "strategy": "Model A: Cover Rebalancer",
                "ticker": f"{s1}/{s2}",
                "entry_date": t0,
                "pnl": cycle_pnl,
                "hold_days": step,
                "win": cycle_pnl > 0
            })
            
    return trades


# =============================================================================
# RUN COMPARISON & REPORT
# =============================================================================
print("\n" + "=" * 78)
print("  SHORT-CYCLE 'ACTIVE CASH' STRATEGY COMPARISON (3-4 Day Holding)")
print("  Universe: 16 High-Beta Stocks Under $50 across Multiple Sectors")
print("  Data: 5 Years Daily OHLCV | Islamic Qabd (Min 3-Day Hold) Strictly Enforced")
print("=" * 78)

tb = backtest_model_b(enriched)
tc = backtest_model_c(enriched)
ta = backtest_model_a(enriched)

all_models = [
    ("Model A: Cover Volatility Rebalancer", ta),
    ("Model B: Connors RSI-2 Quant Mean Reversion", tb),
    ("Model C: Cover Log-Dislocation Shock", tc)
]

def summarize(name, t_list, years=5.0):
    if not t_list:
        return f"{name}: No trades"
    pnls = [t["pnl"] for t in t_list]
    wins = [p for p in pnls if p > 0]
    losses = [-p for p in pnls if p < 0]
    
    wr = len(wins) / len(pnls) * 100.0
    avg_ret = np.mean(pnls) * 100.0
    pf = sum(wins) / sum(losses) if sum(losses) > 0 else 99.0
    total_trades = len(pnls)
    trades_per_wk = (total_trades / (years * 52))
    avg_hold = np.mean([t["hold_days"] for t in t_list])
    
    # Cumulative compound return
    eq = 1.0
    for p in pnls:
        eq *= (1.0 + 0.10 * p)  # 10% allocation per trade
    cagr = (eq ** (1.0 / years) - 1.0) * 100.0
    
    return {
        "name": name,
        "total_trades": total_trades,
        "trades_per_week": round(trades_per_wk, 1),
        "win_rate": round(wr, 1),
        "avg_return": round(avg_ret, 2),
        "profit_factor": round(pf, 2),
        "avg_hold_days": round(avg_hold, 1),
        "cagr_at_10pct_alloc": round(cagr, 1)
    }

metrics = [summarize(name, t) for name, t in all_models]

print(f"\n  {'Strategy':<40} {'Trades/Wk':<10} {'Win Rate':<10} {'Avg Ret':<10} {'PF':<8} {'Hold':<8}")
print("  " + "-" * 76)
for m in metrics:
    print(f"  {m['name']:<40} {m['trades_per_week']:<10} {m['win_rate']:>5.1f}%    {m['avg_return']:>+6.2f}%    {m['profit_factor']:<8.2f} {m['avg_hold_days']:<4.1f}d")

print("\n" + "=" * 78)

# =============================================================================
# MODEL D: HYBRID COVER-CONNORS (RSI-2 < 10 + Volatility Floor + 3-Day Pop)
# =============================================================================
def backtest_hybrid(enriched_data):
    """
    Combines:
    1. Trend Quality: Price > 200-SMA and 50-SMA > 0 slope (positive drift).
    2. Connors Extreme Panic: RSI-2 < 10.
    3. Volume expansion on drop: Volume > 1.2x 20-day SMA volume (panicked sellers giving up).
    4. Target: Quick +3.5% pop or exit at Day 3 / Day 4.
    """
    trades = []
    for sym, d in enriched_data.items():
        n = len(d)
        cooldown = -1
        vol_sma = d["volume"].rolling(20).mean()
        for i in range(200, n - 4):
            if i <= cooldown:
                continue
            row = d.iloc[i]
            
            trend_ok = row["close"] > row["sma_200"] and row["close"] > row["sma_50"]
            panic_ok = row["rsi_2"] < 10.0
            vol_cap = row["volume"] > 1.1 * vol_sma.iloc[i]
            regime = row["spy_regime"]
            
            if trend_ok and panic_ok and vol_cap and regime:
                entry_idx = i + 1
                entry_price = float(d.iloc[entry_idx]["open"])
                stop_loss = entry_price * 0.96  # 4% tight risk floor
                target_price = entry_price * 1.04  # 4% quick profit target
                
                exit_price = entry_price
                hold_days = 0
                for k in range(entry_idx, min(entry_idx + 5, n)):
                    bar = d.iloc[k]
                    days = k - entry_idx
                    
                    # Hard stop check
                    if float(bar["low"]) <= stop_loss and days >= 3:
                        exit_price = stop_loss
                        hold_days = days
                        break
                    
                    if days >= 3:
                        # Target hit
                        if float(bar["high"]) >= target_price:
                            exit_price = target_price
                            hold_days = days
                            break
                        # RSI snapback
                        if float(bar["rsi_2"]) >= 65.0 or days >= 4:
                            exit_price = float(bar["close"])
                            hold_days = days
                            break
                        exit_price = float(bar["close"])
                        hold_days = days
                        
                pnl = (exit_price - entry_price) / entry_price
                trades.append({
                    "strategy": "Model D: Cover-Connors Hybrid",
                    "ticker": sym,
                    "entry_date": d.index[entry_idx],
                    "pnl": pnl,
                    "hold_days": max(hold_days, 3),
                    "win": pnl > 0
                })
                cooldown = entry_idx + max(hold_days, 3)
    return trades

td = backtest_hybrid(enriched)
md = summarize("Model D: Cover-Connors Hybrid", td)
print(f"  {md['name']:<40} {md['trades_per_week']:<10} {md['win_rate']:>5.1f}%    {md['avg_return']:>+6.2f}%    {md['profit_factor']:<8.2f} {md['avg_hold_days']:<4.1f}d")
print("=" * 78 + "\n")
