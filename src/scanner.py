"""
Daily AI Infrastructure & Semiconductor Swing Scanner (Champion Strategy).

Scans all 41 universe stocks at market close, evaluating:
1. Minervini Stage 2 Trend Template & 63d Relative Strength vs Segment Benchmark.
2. Dual Entry Triggers:
   - Trigger A: Pullback to 10-EMA or 20-EMA in Stage 2 trend.
   - Trigger B: Morales-Kacher Pocket Pivot (Institutional volume accumulation inside base).
3. Risk:Reward Trade Plan calculation with ATR floor & structural stop.
4. Islamic 3-day minimum holding advisory.

Run daily after market close:
    python3 -m src.scanner
"""

from __future__ import annotations

import argparse
from datetime import date
import pandas as pd
import numpy as np

from src import config, prices, universe, indicators, records


def run_daily_scan(refresh: bool = False, min_rr: float = 1.5):
    candidates = universe.candidates()
    benchmarks = ["SOXX", "SMH", "SPY", "QQQ", "XLU"]
    all_syms = list(set([c.ticker for c in candidates] + benchmarks))
    ticker_bench = universe.ticker_benchmarks()
    
    print(f"\n{'=' * 78}")
    print("  AI INFRASTRUCTURE & SEMI DAILY SCANNER (Champion Strategy)")
    print(f"  Scanning {len(candidates)} Universe Tickers across 9 Segments")
    print(f"{'=' * 78}")
    
    # Load prices
    histories = {}
    loaded = prices.load_many(all_syms, refresh=refresh)
    for sym, h in loaded.items():
        if h is not None and h.frame is not None and not h.frame.empty:
            histories[sym] = h.frame.copy()
            
    soxx_df = histories.get("SOXX")
    soxx_regime_ok = True

    # Determine actual data freshness from cached prices
    latest_dates = []
    for sym, df in histories.items():
        if not df.empty:
            latest_dates.append(df.index[-1].date())
    data_date = date.today()
    if latest_dates:
        data_date = max(latest_dates)
        stale = data_date < date.today()
        freshness = f"  [Data] Latest close: {data_date}"
        if stale:
            freshness += f"  ⚠️  STALE — run with --refresh to update"
        else:
            freshness += "  ✅ Up-to-date"
        print(freshness)

    if soxx_df is not None and len(soxx_df) >= 200:
        soxx_c = soxx_df["close"].iloc[-1]
        soxx_sma200 = indicators.sma(soxx_df["close"], 200).iloc[-1]
        soxx_regime_ok = soxx_c > soxx_sma200
        print(f"  [Regime Check] SOXX: ${soxx_c:.2f} | 200-DMA: ${soxx_sma200:.2f} -> {'✅ BULLISH REGIME' if soxx_regime_ok else '⚠️ BEARISH REGIME'}")
    
    setups = []
    watch_candidates = []

    for c in candidates:
        ticker = c.ticker
        df = histories.get(ticker)
        if df is None or len(df) < 252:
            continue
            
        bench_sym = ticker_bench.get(ticker, "SMH")
        bench_df = histories.get(bench_sym, histories.get("SMH"))
        if bench_df is None or len(bench_df) < 63:
            continue

        # Indicators
        close = df["close"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"]
        
        cur_c = close.iloc[-1]
        cur_o = df["open"].iloc[-1]
        cur_v = vol.iloc[-1]
        
        ema10 = indicators.ema(close, 10).iloc[-1]
        ema20 = indicators.ema(close, 20).iloc[-1]
        sma50 = indicators.sma(close, 50).iloc[-1]
        sma150 = indicators.sma(close, 150).iloc[-1]
        sma200 = indicators.sma(close, 200).iloc[-1]
        sma200_prev20 = indicators.sma(close, 200).iloc[-20] if len(df) >= 220 else sma200
        
        atr14 = indicators.atr(high, low, close, 14).iloc[-1]
        s_low10 = indicators.swing_low(low, 10).iloc[-1]
        h52 = high.rolling(252).max().iloc[-1]
        l52 = low.rolling(252).min().iloc[-1]
        
        # 63d Returns & Relative Strength
        ret_63 = (cur_c / close.iloc[-63]) - 1 if len(close) >= 63 else 0.0
        bench_ret_63 = (bench_df["close"].iloc[-1] / bench_df["close"].iloc[-63]) - 1 if len(bench_df) >= 63 else 0.0
        rs_63 = ret_63 - bench_ret_63
        
        # Minervini Stage 2 check
        stage2 = (
            (cur_c > sma50)
            and (sma50 > sma150)
            and (sma150 > sma200)
            and (sma200 > sma200_prev20)
            and (cur_c >= 0.75 * h52)
            and (cur_c >= 1.30 * l52)
        )
        
        # Pocket Pivot check
        is_up = cur_c > cur_o
        is_down = close < close.shift(1)
        down_vol_10 = pd.Series(np.where(is_down, vol, 0.0), index=df.index).iloc[-11:-1].max()
        pocket_pivot_vol = is_up and (cur_v > down_vol_10) and (down_vol_10 > 0)
        
        near_base = (
            (abs(cur_c - ema10) / cur_c <= 0.025)
            or (abs(cur_c - ema20) / cur_c <= 0.025)
            or (abs(cur_c - sma50) / cur_c <= 0.025)
        )
        trigger_pivot = pocket_pivot_vol and near_base and (cur_c > sma50) and (rs_63 > 0) and soxx_regime_ok
        
        # VCP / EMA Dip check
        ema_dip = (abs(cur_c - ema10) / cur_c <= 0.02) or (abs(cur_c - ema20) / cur_c <= 0.02)
        trigger_dip = stage2 and ema_dip and (rs_63 > 0) and soxx_regime_ok
        
        # Structural Stop Calculation
        valid_struct = [x for x in [s_low10, sma50] if not np.isnan(x) and x < cur_c]
        chosen_struct = min(valid_struct) if valid_struct else (cur_c - 2.0 * atr14)
        vol_floor_stop = cur_c - (1.5 * atr14)
        stop_loss = min(chosen_struct, vol_floor_stop)
        stop_dist = cur_c - stop_loss
        stop_pct = (stop_dist / cur_c) * 100.0
        
        t1_target = cur_c + (3.0 * atr14)
        t1_dist = t1_target - cur_c
        t1_pct = (t1_dist / cur_c) * 100.0
        rr_t1 = t1_dist / stop_dist if stop_dist > 0 else 0.0
        
        tier = "Flagged/High-Beta" if c.stability_flag else "Core Tier"
        max_stop = 15.0 if c.stability_flag else 10.0
        
        if (trigger_pivot or trigger_dip) and (rr_t1 >= min_rr) and (stop_pct <= max_stop):
            sig_name = "DUAL (Pivot+Dip)" if (trigger_pivot and trigger_dip) else ("Pocket Pivot" if trigger_pivot else "Stage 2 Dip")
            setups.append({
                "ticker": ticker,
                "segment": c.segment_label,
                "tier": tier,
                "price": cur_c,
                "signal": sig_name,
                "stop": stop_loss,
                "stop_pct": stop_pct,
                "t1": t1_target,
                "t1_pct": t1_pct,
                "rr": rr_t1,
                "rs_63": rs_63 * 100.0,
                "bench": bench_sym,
            })
        elif stage2 and (rs_63 > 0):
            dist_to_ema20 = (cur_c - ema20) / cur_c * 100.0
            watch_candidates.append({
                "ticker": ticker,
                "segment": c.segment_label,
                "price": cur_c,
                "dist_ema20": dist_to_ema20,
                "rs_63": rs_63 * 100.0,
                "bench": bench_sym,
            })

    # Record setups to database
    if setups:
        rec_count = records.record_scanner_signals("universe", setups, scan_date=str(data_date))
        print(f"  💾 Registered {rec_count} Universe setup(s) in persistent database for {data_date}")

    # Print results
    print(f"\n{'=' * 78}")
    if setups:
        print(f"  🎯 ACTIVE TRADE SETUPS TRIGGERED TODAY ({len(setups)})")
        print(f"{'=' * 78}")
        for s in sorted(setups, key=lambda x: x['rr'], reverse=True):
            print(f"\n  🚀 ${s['ticker']}  [{s['segment']} | {s['tier']}]")
            print(f"     Trigger:      {s['signal']}")
            print(f"     Market Close: ${s['price']:.2f} (Entry: Next Day Open)")
            print(f"     Stop Loss:    ${s['stop']:.2f}  (-{s['stop_pct']:.1f}%)  [1.5x ATR Volatility Floor / Structural Support]")
            print(f"     Target 1:     ${s['t1']:.2f}  (+{s['t1_pct']:.1f}%)  [R:R = {s['rr']:.2f}:1] -> Bank 50% & Move Stop to Breakeven")
            print(f"     Runner:       Hold remainder >= 3 Days; trail with Chandelier / 20-EMA")
            print(f"     Leadership:   63d RS vs {s['bench']}: +{s['rs_63']:.1f}%")
    else:
        print("  ⚪ NO NEW ACTIVE SIGNALS TRIGGERED TODAY")
        print("     (Patience is edge — the system waits for optimal risk:reward entry)")
        print(f"{'=' * 78}")
        
    if watch_candidates:
        print(f"\n{'=' * 78}")
        print(f"  👀 STAGE 2 LEADERS ON WATCHLIST ({len(watch_candidates)} candidates building bases)")
        print(f"{'=' * 78}")
        print(f"  {'Ticker':<8} {'Segment':<24} {'Price':>10}  {'Dist 20-EMA':>12}  {'63d RS vs Bench':>16}")
        print(f"  {'-'*74}")
        for w in sorted(watch_candidates, key=lambda x: x['dist_ema20'])[:10]:
            dist_str = f"{w['dist_ema20']:+.1f}%"
            rs_str = f"{w['rs_63']:+.1f}% vs {w['bench']}"
            print(f"  {w['ticker']:<8} {w['segment'][:22]:<24} ${w['price']:>8.2f}  {dist_str:>12}  {rs_str:>16}")
            
    print(f"\n{'=' * 78}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Infra Daily Swing Scanner")
    parser.add_argument("--refresh", action="store_true", help="Download latest daily prices")
    args = parser.parse_args()
    run_daily_scan(refresh=args.refresh)
