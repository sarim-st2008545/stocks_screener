"""
Scratchpad: Strategy Comparison & Benchmarking Engine.

Tests 5 distinct proven trading strategies for high-growth tech / semiconductor stocks
over 11.5+ years of historical data (2015-2026), enforcing the Islamic 3-day minimum
holding constraint on all trades:

1. Strategy A (Current 2-Speed System): 63d RS + 50-SMA + 20-EMA Dip + Candle + SOXX Regime.
2. Strategy B (Morales-Kacher Pocket Pivot): Institutional Volume Accumulation inside 10/20 EMA base.
3. Strategy C (Minervini Stage 2 Trend Template Pullback): Stage 2 MA Stack (50>150>200) + 10/20 EMA dip.
4. Strategy D (Linda Raschke Holy Grail): Strong ADX(14)>25 Trend + 20-EMA Touch.
5. Strategy E (Turtle / Donchian 20-Day Momentum Breakout): Classic trend-following channel breakout.

All strategies adhere to:
- Shariah Constraint: Minimum holding duration >= 3 trading days.
- Entry at t+1 open (no look-ahead).
- Trailing and fixed horizon tracking.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src import config, prices, universe, indicators


@dataclass
class TradeRecord:
    strategy_name: str
    ticker: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price: float
    stop_loss: float
    stop_distance: float
    atr: float
    
    # Outcomes across horizons (trading days >= 3)
    pnl_3d: float = 0.0
    pnl_10d: float = 0.0
    pnl_25d: float = 0.0
    pnl_r_25d: float = 0.0
    
    # Trailing exit outcome
    trailing_exit_date: pd.Timestamp | None = None
    trailing_exit_price: float = 0.0
    trailing_hold_days: int = 0
    trailing_pnl_pct: float = 0.0
    trailing_pnl_r: float = 0.0


@dataclass
class StrategyMetrics:
    name: str
    description: str
    total_signals: int
    signals_per_year: float
    
    # 3-Day minimum hold metrics
    win_rate_3d: float = 0.0
    avg_return_3d: float = 0.0
    
    # 10-Day hold metrics
    win_rate_10d: float = 0.0
    avg_return_10d: float = 0.0
    
    # 25-Day hold metrics
    win_rate_25d: float = 0.0
    avg_return_25d: float = 0.0
    exp_r_25d: float = 0.0
    profit_factor_25d: float = 0.0
    
    # Full Trailing Exit Metrics (with min 3-day hold)
    trailing_win_rate: float = 0.0
    trailing_avg_return: float = 0.0
    trailing_exp_r: float = 0.0
    trailing_profit_factor: float = 0.0
    avg_hold_days: float = 0.0


class StrategyBenchmark:
    def __init__(self):
        self.candidates = [c.ticker for c in universe.candidates()]
        self.benchmarks = ["SOXX", "SMH", "SPY", "QQQ", "XLU"]
        self.ticker_bench = universe.ticker_benchmarks()
        
        all_syms = list(set(self.candidates + self.benchmarks))
        self.histories: dict[str, pd.DataFrame] = {}
        loaded = prices.load_many(all_syms)
        for sym, h in loaded.items():
            if h is not None and h.frame is not None and not h.frame.empty:
                self.histories[sym] = h.frame.copy()

    def _enrich_data(self, df: pd.DataFrame, bench_df: pd.DataFrame, soxx_df: pd.DataFrame) -> pd.DataFrame:
        d = df.copy()
        c = d["close"]
        h = d["high"]
        l = d["low"]
        v = d["volume"]
        
        # MAs
        d["ema_10"] = indicators.ema(c, 10)
        d["ema_20"] = indicators.ema(c, 20)
        d["sma_50"] = indicators.sma(c, 50)
        d["sma_150"] = indicators.sma(c, 150)
        d["sma_200"] = indicators.sma(c, 200)
        d["sma_200_slope"] = (d["sma_200"] - d["sma_200"].shift(20)) / d["sma_200"].shift(20)
        
        # Highs / Lows (52-week = 252 days)
        d["high_52w"] = h.rolling(252).max()
        d["low_52w"] = l.rolling(252).min()
        d["donchian_20_high"] = h.rolling(20).max()
        d["swing_low_10"] = l.rolling(10).min()
        
        # Oscillators / Volatility
        d["atr_14"] = indicators.atr(h, l, c, 14)
        d["rsi_2"] = indicators.rsi(c, 2)
        
        # ADX(14)
        up_move = h - h.shift(1)
        down_move = l.shift(1) - l
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
        
        atr_adx = tr.ewm(alpha=1/14, adjust=False).mean()
        plus_di = 100 * (pd.Series(plus_dm, index=d.index).ewm(alpha=1/14, adjust=False).mean() / atr_adx)
        minus_di = 100 * (pd.Series(minus_dm, index=d.index).ewm(alpha=1/14, adjust=False).mean() / atr_adx)
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))
        d["adx_14"] = dx.ewm(alpha=1/14, adjust=False).mean()
        
        # Candles
        d["candle_pattern"] = indicators.detect_candles(d)
        
        # Relative strength (63d)
        d["ret_63"] = (c / c.shift(63)) - 1
        b_c = bench_df["close"].reindex(d.index).ffill()
        b_ret = (b_c / b_c.shift(63)) - 1
        d["rs_63"] = d["ret_63"] - b_ret
        
        # Regime (SOXX > 200d)
        if soxx_df is not None and not soxx_df.empty:
            s_c = soxx_df["close"].reindex(d.index).ffill()
            s_sma200 = indicators.sma(s_c, 200)
            d["soxx_regime"] = s_c > s_sma200
        else:
            d["soxx_regime"] = True
            
        # Pocket pivot volume condition:
        # Down day volume in last 10 days
        is_down = c < c.shift(1)
        down_vol = pd.Series(np.where(is_down, v, 0.0), index=d.index)
        max_down_vol_10 = down_vol.rolling(10).max()
        d["is_up_day"] = c > d["open"]
        d["pocket_pivot_vol"] = (v > max_down_vol_10.shift(1)) & d["is_up_day"]
        
        return d

    def simulate_strategy(self, name: str, desc: str, signal_func) -> list[TradeRecord]:
        soxx_df = self.histories.get("SOXX")
        trades: list[TradeRecord] = []
        
        for ticker in self.candidates:
            df = self.histories.get(ticker)
            if df is None or len(df) < 260:
                continue
                
            bench_sym = self.ticker_bench.get(ticker, "SMH")
            bench_df = self.histories.get(bench_sym, self.histories.get("SMH"))
            if bench_df is None or len(bench_df) < 260:
                continue
                
            d = self._enrich_data(df, bench_df, soxx_df)
            n_bars = len(d)
            
            for i in range(100, n_bars - 1):
                row = d.iloc[i]
                if not signal_func(row, d.iloc[:i+1]):
                    continue
                    
                # Signal triggered at bar i. Enter bar i+1 open
                entry_date = d.index[i + 1]
                entry_price = float(d.iloc[i + 1]["open"])
                atr_val = float(row.get("atr_14", entry_price * 0.03))
                
                # Structural Stop (min 1.5 ATR floor)
                s_low = float(row.get("swing_low_10", entry_price - 2*atr_val))
                sma50 = float(row.get("sma_50", entry_price - 2*atr_val))
                struct = [x for x in [s_low, sma50] if not np.isnan(x) and x < entry_price]
                stop_val = min(struct) if struct else (entry_price - 2 * atr_val)
                stop_val = min(stop_val, entry_price - 1.5 * atr_val)
                
                stop_dist = entry_price - stop_val
                if stop_dist <= 0 or (stop_dist / entry_price) > 0.15:
                    continue  # skip invalid or overly wide stops
                    
                tr = TradeRecord(
                    strategy_name=name,
                    ticker=ticker,
                    signal_date=d.index[i],
                    entry_date=entry_date,
                    entry_price=entry_price,
                    stop_loss=stop_val,
                    stop_distance=stop_dist,
                    atr=atr_val,
                )
                
                # 3-Day Hold (Mandatory Islamic constraint)
                if i + 1 + 3 < n_bars:
                    p3 = float(d.iloc[i + 1 + 3]["close"])
                    tr.pnl_3d = (p3 - entry_price) / entry_price
                
                # 10-Day Hold
                if i + 1 + 10 < n_bars:
                    p10 = float(d.iloc[i + 1 + 10]["close"])
                    tr.pnl_10d = (p10 - entry_price) / entry_price
                    
                # 25-Day Hold
                if i + 1 + 25 < n_bars:
                    p25 = float(d.iloc[i + 1 + 25]["close"])
                    tr.pnl_25d = (p25 - entry_price) / entry_price
                    tr.pnl_r_25d = (p25 - entry_price) / stop_dist
                    
                # Full Trailing Simulation (Min 3-day hold strictly enforced!)
                t1_target = entry_price + (3.0 * atr_val)
                cur_stop = stop_val
                highest_h = entry_price
                t1_scaled = False
                ema_break_count = 0
                exit_price = entry_price
                exit_date = entry_date
                hold_days = 0
                
                for k in range(i + 1, min(i + 1 + 80, n_bars)):
                    cur_bar = d.iloc[k]
                    k_days = k - (i + 1)
                    highest_h = max(highest_h, float(cur_bar["high"]))
                    
                    if float(cur_bar["close"]) < float(cur_bar["ema_20"]):
                        ema_break_count += 1
                    else:
                        ema_break_count = 0
                        
                    # Check T1 target
                    if float(cur_bar["high"]) >= t1_target and not t1_scaled:
                        t1_scaled = True
                        cur_stop = entry_price  # move stop to breakeven
                        
                    chandelier = highest_h - (3.0 * atr_val)
                    
                    # Exit conditions ONLY evaluated after minimum 3 days!
                    if k_days >= 3:
                        # Stop hit
                        if float(cur_bar["low"]) <= cur_stop:
                            exit_price = cur_stop
                            exit_date = d.index[k]
                            hold_days = k_days
                            break
                        # Chandelier stop hit
                        if float(cur_bar["close"]) < chandelier:
                            exit_price = float(cur_bar["close"])
                            exit_date = d.index[k]
                            hold_days = k_days
                            break
                        # 2 consecutive closes below 20-EMA
                        if ema_break_count >= 2:
                            exit_price = float(cur_bar["close"])
                            exit_date = d.index[k]
                            hold_days = k_days
                            break
                            
                    exit_price = float(cur_bar["close"])
                    exit_date = d.index[k]
                    hold_days = k_days

                tr.trailing_exit_date = exit_date
                tr.trailing_exit_price = exit_price
                tr.trailing_hold_days = hold_days
                tr.trailing_pnl_pct = (exit_price - entry_price) / entry_price
                tr.trailing_pnl_r = (exit_price - entry_price) / stop_dist
                
                trades.append(tr)
                
        return trades

    def compute_metrics(self, name: str, desc: str, trades: list[TradeRecord], years: float = 11.5) -> StrategyMetrics:
        n = len(trades)
        if n == 0:
            return StrategyMetrics(name=name, description=desc, total_signals=0, signals_per_year=0.0)
            
        trades_yr = n / years
        
        # 3d
        pnl_3 = [t.pnl_3d for t in trades]
        win_3 = np.mean([p > 0 for p in pnl_3]) if pnl_3 else 0.0
        avg_3 = np.mean(pnl_3) if pnl_3 else 0.0
        
        # 10d
        pnl_10 = [t.pnl_10d for t in trades]
        win_10 = np.mean([p > 0 for p in pnl_10]) if pnl_10 else 0.0
        avg_10 = np.mean(pnl_10) if pnl_10 else 0.0
        
        # 25d
        pnl_25 = [t.pnl_25d for t in trades]
        pnl_r_25 = [t.pnl_r_25d for t in trades]
        win_25 = np.mean([p > 0 for p in pnl_25]) if pnl_25 else 0.0
        avg_25 = np.mean(pnl_25) if pnl_25 else 0.0
        exp_r_25 = np.mean(pnl_r_25) if pnl_r_25 else 0.0
        gains_25 = [r for r in pnl_r_25 if r > 0]
        losses_25 = [-r for r in pnl_r_25 if r < 0]
        pf_25 = sum(gains_25) / sum(losses_25) if sum(losses_25) > 0 else 99.0
        
        # Trailing
        pnl_tr = [t.trailing_pnl_pct for t in trades]
        pnl_r_tr = [t.trailing_pnl_r for t in trades]
        hold_days = [t.trailing_hold_days for t in trades]
        win_tr = np.mean([p > 0 for p in pnl_tr]) if pnl_tr else 0.0
        avg_tr = np.mean(pnl_tr) if pnl_tr else 0.0
        exp_r_tr = np.mean(pnl_r_tr) if pnl_r_tr else 0.0
        gains_tr = [r for r in pnl_r_tr if r > 0]
        losses_tr = [-r for r in pnl_r_tr if r < 0]
        pf_tr = sum(gains_tr) / sum(losses_tr) if sum(losses_tr) > 0 else 99.0
        avg_h = np.mean(hold_days) if hold_days else 0.0
        
        return StrategyMetrics(
            name=name,
            description=desc,
            total_signals=n,
            signals_per_year=round(trades_yr, 1),
            win_rate_3d=round(win_3 * 100, 1),
            avg_return_3d=round(avg_3 * 100, 2),
            win_rate_10d=round(win_10 * 100, 1),
            avg_return_10d=round(avg_10 * 100, 2),
            win_rate_25d=round(win_25 * 100, 1),
            avg_return_25d=round(avg_25 * 100, 2),
            exp_r_25d=round(exp_r_25, 3),
            profit_factor_25d=round(pf_25, 2),
            trailing_win_rate=round(win_tr * 100, 1),
            trailing_avg_return=round(avg_tr * 100, 2),
            trailing_exp_r=round(exp_r_tr, 3),
            trailing_profit_factor=round(pf_tr, 2),
            avg_hold_days=round(avg_h, 1),
        )


def run_benchmark_suite():
    bench = StrategyBenchmark()
    
    # -------------------------------------------------------------------------
    # Strategy 1: Two-Speed System (Our Baseline 6-Filter)
    # -------------------------------------------------------------------------
    def sig_two_speed(row, hist):
        regime = bool(row.get("soxx_regime", True))
        trend = row.get("ret_63", np.nan) > 0
        rs = row.get("rs_63", np.nan) > 0
        dma50 = row["close"] > row.get("sma_50", np.nan)
        dip = (abs(row["close"] - row["ema_20"]) / row["close"] <= 0.01) or (row.get("rsi_2", np.nan) < 10.0)
        candle = pd.notna(row.get("candle_pattern"))
        return regime and trend and rs and dma50 and dip and candle

    # -------------------------------------------------------------------------
    # Strategy 2: Morales-Kacher Pocket Pivot (Institutional Base Accumulation)
    # -------------------------------------------------------------------------
    def sig_pocket_pivot(row, hist):
        # 1. Price is constructive near 10-EMA, 20-EMA, or 50-SMA (within 2.5% of any)
        c = row["close"]
        near_10 = abs(c - row["ema_10"]) / c <= 0.025
        near_20 = abs(c - row["ema_20"]) / c <= 0.025
        near_50 = abs(c - row["sma_50"]) / c <= 0.025
        constructive = near_10 or near_20 or near_50
        
        # 2. In Stage 2 / Uptrend (Close > 50-SMA and RS > 0)
        uptrend = c > row["sma_50"] and row.get("rs_63", 0) > 0
        
        # 3. Pocket Pivot Volume Signature
        vol_sig = bool(row.get("pocket_pivot_vol", False))
        regime = bool(row.get("soxx_regime", True))
        
        return regime and uptrend and constructive and vol_sig

    # -------------------------------------------------------------------------
    # Strategy 3: Minervini Stage 2 Trend Template Pullback (VCP Dip)
    # -------------------------------------------------------------------------
    def sig_minervini_pullback(row, hist):
        c = row["close"]
        sma50 = row.get("sma_50", np.nan)
        sma150 = row.get("sma_150", np.nan)
        sma200 = row.get("sma_200", np.nan)
        h52 = row.get("high_52w", np.nan)
        l52 = row.get("low_52w", np.nan)
        
        # 1. Minervini Moving Average Stack
        ma_stack = (c > sma50) and (sma50 > sma150) and (sma150 > sma200) and (row.get("sma_200_slope", 0) > 0)
        # 2. Within 25% of 52w High, and > 30% above 52w Low
        range_cond = (c >= 0.75 * h52) and (c >= 1.30 * l52)
        # 3. RS > 0
        rs_cond = row.get("rs_63", 0) > 0
        # 4. Pullback to 10-EMA or 20-EMA (within 2%)
        dip = (abs(c - row["ema_10"]) / c <= 0.02) or (abs(c - row["ema_20"]) / c <= 0.02)
        
        return ma_stack and range_cond and rs_cond and dip

    # -------------------------------------------------------------------------
    # Strategy 4: Linda Raschke Holy Grail (ADX Trend + 20-EMA Pullback)
    # -------------------------------------------------------------------------
    def sig_holy_grail(row, hist):
        c = row["close"]
        adx = row.get("adx_14", 0)
        strong_trend = adx > 25.0 and c > row.get("sma_50", np.nan)
        # Touching 20-EMA on day
        ema_touch = (row["low"] <= row["ema_20"]) and (row["close"] >= row["ema_20"] * 0.985)
        rs_cond = row.get("rs_63", 0) > 0
        return strong_trend and ema_touch and rs_cond

    # -------------------------------------------------------------------------
    # Strategy 5: Turtle / Donchian 20-Day Momentum Breakout
    # -------------------------------------------------------------------------
    def sig_donchian_breakout(row, hist):
        c = row["close"]
        # Reaching 20-day high in an uptrend (Close > 50-SMA and RS > 0)
        is_breakout = c >= row.get("donchian_20_high", np.nan)
        uptrend = c > row.get("sma_50", np.nan) and row.get("rs_63", 0) > 0
        regime = bool(row.get("soxx_regime", True))
        return regime and uptrend and is_breakout

    # Run simulations
    print("Running Simulation 1: Two-Speed Baseline...")
    t1 = bench.simulate_strategy("1. Two-Speed Dip Entry", "63d RS + 50-SMA + 20-EMA + Candle", sig_two_speed)
    m1 = bench.compute_metrics("1. Two-Speed Dip Entry", "63d RS + 50-SMA + 20-EMA + Candle", t1)
    
    print("Running Simulation 2: Pocket Pivot...")
    t2 = bench.simulate_strategy("2. Morales-Kacher Pocket Pivot", "Institutional Volume Signature inside Base", sig_pocket_pivot)
    m2 = bench.compute_metrics("2. Morales-Kacher Pocket Pivot", "Institutional Volume Signature inside Base", t2)
    
    print("Running Simulation 3: Minervini Trend Template Dip...")
    t3 = bench.simulate_strategy("3. Minervini Stage 2 VCP", "Stage 2 MA Stack (50>150>200) + 10/20 EMA Pullback", sig_minervini_pullback)
    m3 = bench.compute_metrics("3. Minervini Stage 2 VCP", "Stage 2 MA Stack (50>150>200) + 10/20 EMA Pullback", t3)
    
    print("Running Simulation 4: Raschke Holy Grail...")
    t4 = bench.simulate_strategy("4. Raschke Holy Grail (ADX)", "ADX(14)>25 Trend + 20-EMA Touch", sig_holy_grail)
    m4 = bench.compute_metrics("4. Raschke Holy Grail (ADX)", "ADX(14)>25 Trend + 20-EMA Touch", t4)

    print("Running Simulation 5: Donchian 20d Breakout...")
    t5 = bench.simulate_strategy("5. Donchian 20d Breakout", "20-Day New High Breakout + RS Filter", sig_donchian_breakout)
    m5 = bench.compute_metrics("5. Donchian 20d Breakout", "20-Day New High Breakout + RS Filter", t5)

    all_metrics = [m1, m2, m3, m4, m5]
    
    # Format output
    lines = []
    lines.append("# Multi-Strategy Benchmark Comparison (2015-2026)")
    lines.append("**Universe:** 41 AI Infrastructure & Semiconductor stocks | **Data:** 11.5 Years Daily OHLCV")
    lines.append("**Hard Islamic Constraint:** Minimum holding duration >= 3 trading days enforced on all trades")
    lines.append("")
    lines.append("### 1. Fixed Holding Horizons Performance")
    lines.append("| Strategy | Signals/Yr | Total Signals | 3-Day Win% | 3-Day Avg Ret | 10-Day Win% | 10-Day Avg Ret | 25-Day Win% | 25-Day Avg Ret | 25-Day E[R] | 25-Day PF |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for m in all_metrics:
        lines.append(
            f"| **{m.name}** | {m.signals_per_year} | {m.total_signals} | "
            f"{m.win_rate_3d}% | {m.avg_return_3d:+0.2f}% | "
            f"{m.win_rate_10d}% | {m.avg_return_10d:+0.2f}% | "
            f"{m.win_rate_25d}% | {m.avg_return_25d:+0.2f}% | {m.exp_r_25d:+0.3f}R | {m.profit_factor_25d:.2f} |"
        )
        
    lines.append("")
    lines.append("### 2. Full Trailing Exit Strategy Performance (T1 + Chandelier + 20-EMA Break, Min 3 Days)")
    lines.append("| Strategy | Signals/Yr | Win Rate | Avg Return | Expectancy (E[R]) | Profit Factor | Avg Hold (Days) |")
    lines.append("|---|---|---|---|---|---|---|")
    for m in all_metrics:
        lines.append(
            f"| **{m.name}** | {m.signals_per_year} | "
            f"**{m.trailing_win_rate}%** | **{m.trailing_avg_return:+0.2f}%** | **{m.trailing_exp_r:+0.3f}R** | **{m.trailing_profit_factor:.2f}** | {m.avg_hold_days} days |"
        )
        
    res_table = "\n".join(lines)
    Path("scratch/benchmark_results.md").write_text(res_table)
    print("\n" + res_table)


if __name__ == "__main__":
    run_benchmark_suite()
