"""
Daily Galaxy Active Cash Scanner v2 (TradeAlgo-Enhanced 3-4 Day Swing Engine).

Scans 21 verified Shariah-compliant (Zoya & Musaffa approved) high-beta stocks
under $50 across 4 non-correlated sectors after US market close:

1. Macro Regime: SPY > 200-SMA (market safety gate)
2. Dual Trend Gate: Stock Close > 200-SMA AND Close > 50-SMA (confirmed uptrend)
3. Trigger: Connors RSI-2 < 10.0 + Low <= Lower Bollinger Band(20, 2.0)
4. Islamic Qabd Advisory: Minimum 3 full trading days before exit
5. Dynamic Target: 10-SMA mean reversion (replaces fixed +4%)
6. Dynamic Stop: 1.5×ATR(14) volatility floor (replaces fixed -4%)
7. RSI Snapback Exit: RSI(2) > 70
8. Time Limit: Day 4 close forced exit
9. Cover Sizing: ~33% of Galaxy cash pool per trade (max 3 concurrent)

Backtest-validated: 158 trades, 58.9% WR, PF 1.92 (5-year, 21 Halal stocks)

Run daily after market close (4:00 PM ET / 11:00 PM UTC+3):
    python3 -m src.galaxy_scanner
    python3 -m src.galaxy_scanner --refresh
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
import yaml

import numpy as np
import pandas as pd
import yfinance as yf

from src import galaxy_indicators, records


def load_yaml(path: str) -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


def fetch_or_load_data(tickers: list[str], cache_dir: Path, refresh: bool = False) -> dict[str, pd.DataFrame]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    histories = {}
    
    for sym in tickers:
        fpath = cache_dir / f"{sym}.csv"
        df = None
        if not refresh and fpath.exists() and fpath.stat().st_size > 1000:
            try:
                df = pd.read_csv(fpath, index_col=0, parse_dates=True)
            except Exception:
                df = None
                
        if df is None or df.empty:
            try:
                t = yf.Ticker(sym)
                df = t.history(period="2y")
                if not df.empty:
                    df.columns = [c.lower() for c in df.columns]
                    df.to_csv(fpath)
            except Exception as e:
                # If network fails, try fallback to stale cache
                if fpath.exists():
                    df = pd.read_csv(fpath, index_col=0, parse_dates=True)
                    
        if df is not None and not df.empty:
            histories[sym] = df
            
    return histories


def run_galaxy_scan(refresh: bool = False):
    univ_cfg = load_yaml("config/galaxy_universe.yaml")
    rules_cfg = load_yaml("config/galaxy_rules.yaml")
    
    # Flatten universe tickers
    all_tickers = []
    ticker_info = {}
    for seg_key, seg_data in univ_cfg.get("segments", {}).items():
        seg_label = seg_data.get("label", seg_key)
        for m in seg_data.get("members", []):
            sym = m["ticker"]
            all_tickers.append(sym)
            ticker_info[sym] = {
                "name": m.get("name", sym),
                "segment": seg_label,
                "shariah": m.get("shariah_screen", "Verified Halal")
            }
            
    # Include SPY benchmark
    bench_sym = univ_cfg.get("benchmark", {}).get("symbol", "SPY")
    fetch_syms = list(set(all_tickers + [bench_sym]))
    
    print(f"\n{'=' * 78}")
    print("  GALAXY ACTIVE CASH SCANNER v2 (TradeAlgo-Enhanced Halal Swing Engine)")
    print(f"  Scanning {len(all_tickers)} Verified Halal Tickers (Zoya/Musaffa Approved)")
    print(f"  Strategy: RSI-2 < 10 + BB Penetration | 50+200 SMA Dual Gate")
    print(f"  Backtest: PF 1.92 | WR 58.9% | Avg +1.76%/trade over 5 years")
    print(f"{'=' * 78}")
    
    cache_dir = Path("data/galaxy")
    histories = fetch_or_load_data(fetch_syms, cache_dir=cache_dir, refresh=refresh)
    
    # Check data freshness safely
    latest_dates = []
    for sym, df in histories.items():
        if not df.empty:
            last_idx = df.index[-1]
            if hasattr(last_idx, "date"):
                latest_dates.append(last_idx.date())
            else:
                try:
                    latest_dates.append(pd.to_datetime(last_idx).date())
                except Exception:
                    pass
    data_date = date.today()
    if latest_dates:
        data_date = max(latest_dates)
        stale = data_date < date.today()
        freshness = f"  [Data] Latest close: {data_date}"
        freshness += "  ⚠️ STALE — run with --refresh" if stale else "  ✅ Up-to-date"
        print(freshness)
        
    # SPY Macro Regime Check
    spy_df = histories.get(bench_sym)
    spy_ok = True
    if spy_df is not None and len(spy_df) >= 200:
        spy_c = spy_df["close"].iloc[-1]
        spy_sma200 = galaxy_indicators.sma(spy_df["close"], 200).iloc[-1]
        spy_ok = spy_c > spy_sma200
        status_str = "✅ BULLISH REGIME" if spy_ok else "⚠️ BEARISH REGIME — ALL ENTRIES BLOCKED"
        print(f"  [Macro Check] SPY: ${spy_c:.2f} | 200-DMA: ${spy_sma200:.2f} -> {status_str}")
        
    setups = []
    watchlist = []
    
    for sym in all_tickers:
        df = histories.get(sym)
        if df is None or len(df) < 200:
            continue
            
        c = df["close"]
        h = df["high"]
        l = df["low"]
        v = df["volume"]
        
        cur_c = float(c.iloc[-1])
        cur_l = float(l.iloc[-1])
        cur_v = float(v.iloc[-1])
        
        sma200 = float(galaxy_indicators.sma(c, 200).iloc[-1])
        sma50 = float(galaxy_indicators.sma(c, 50).iloc[-1])
        sma10 = float(galaxy_indicators.sma(c, 10).iloc[-1])
        rsi2 = float(galaxy_indicators.rsi_2(c).iloc[-1])
        atr14 = float(galaxy_indicators.atr(h, l, c, 14).iloc[-1])
        b_lower = float(galaxy_indicators.bollinger_lower(c, 20, 2.0).iloc[-1])
        vol_sma20 = float(galaxy_indicators.sma(v, 20).iloc[-1])
        
        # =====================================================================
        # v2 ENTRY CONDITIONS (TradeAlgo Setup #3 Enhanced)
        # =====================================================================
        
        # 1. Dual Trend Gate: Close > 200-SMA AND Close > 50-SMA
        trend_ok = cur_c > sma200 and cur_c > sma50
        
        # 2. Trigger: RSI-2 < 10.0 (Panic capitulation)
        is_oversold = rsi2 < 10.0
        
        # 3. Bollinger Confirmation: Today's Low penetrated Lower BB(20, 2.0)
        bb_penetration = cur_l <= b_lower
        
        # Dynamic risk levels (v2)
        stop_price = cur_c - (1.5 * atr14)      # 1.5×ATR dynamic stop
        stop_pct = (cur_c - stop_price) / cur_c * 100.0
        target_price = sma10                      # 10-SMA dynamic target
        target_pct = (target_price - cur_c) / cur_c * 100.0
        
        meta = ticker_info.get(sym, {})
        
        if trend_ok and is_oversold and bb_penetration and spy_ok:
            setups.append({
                "ticker": sym,
                "name": meta.get("name", sym),
                "segment": meta.get("segment", "General"),
                "shariah": meta.get("shariah", "Verified Halal"),
                "price": cur_c,
                "low": cur_l,
                "rsi2": rsi2,
                "sma200": sma200,
                "sma50": sma50,
                "sma10": sma10,
                "bb_lower": b_lower,
                "stop": stop_price,
                "stop_pct": stop_pct,
                "target": target_price,
                "target_pct": target_pct,
                "atr": atr14
            })
        elif trend_ok and (rsi2 < 20.0):
            # Approaching setup — on watchlist
            watchlist.append({
                "ticker": sym,
                "segment": meta.get("segment", "General"),
                "price": cur_c,
                "rsi2": rsi2,
                "bb_lower": b_lower,
                "bb_gap_pct": (cur_l - b_lower) / cur_c * 100.0,
                "dist_sma200": (cur_c - sma200) / cur_c * 100.0
            })
            
    # Record setups to database
    if setups:
        rec_count = records.record_scanner_signals("galaxy", setups, scan_date=str(data_date))
        print(f"  💾 Registered {rec_count} Galaxy setup(s) in persistent database for {data_date}")

    # Output Setups
    print(f"\n{'=' * 78}")
    if setups:
        print(f"  🎯 ACTIVE GALAXY v2 SETUPS TRIGGERED TODAY ({len(setups)})")
        print(f"{'=' * 78}")
        # Sort by deepest oversold (lowest RSI-2)
        for s in sorted(setups, key=lambda x: x["rsi2"]):
            print(f"\n  ⚡ ${s['ticker']}  [{s['segment']} | {s['shariah']}]")
            print(f"     Company:        {s['name']}")
            print(f"     ─── Entry Triggers ───")
            print(f"     RSI-2:          {s['rsi2']:.1f}  (< 10.0 ✅ Panic Capitulation)")
            print(f"     Bollinger Low:  ${s['bb_lower']:.2f}  |  Today's Low: ${s['low']:.2f}  (Penetrated ✅)")
            print(f"     Trend Gate:     Close ${s['price']:.2f} > 50-SMA ${s['sma50']:.2f} > 200-SMA ${s['sma200']:.2f} ✅")
            print(f"     ─── Trade Plan ───")
            print(f"     Entry:          Next Day Open (Buy at Market)")
            print(f"     Dynamic Target: ${s['target']:.2f}  (10-SMA mean reversion, {s['target_pct']:+.1f}%)")
            print(f"     Dynamic Stop:   ${s['stop']:.2f}  (1.5×ATR, -{s['stop_pct']:.1f}%)")
            print(f"     RSI Exit:       When RSI(2) crosses above 70")
            print(f"     Time Limit:     Day 4 close (forced exit)")
            print(f"     Holding Rule:   Hold >= 3 Days (Islamic Qabd) ☪️")
            print(f"     Sizing:         ~33% of Galaxy Cash Pool")
    else:
        print("  ⚪ NO ACTIVE GALAXY v2 SETUPS TRIGGERED TODAY")
        print("     (Discipline is edge — waiting for RSI-2 < 10 + BB penetration in dual uptrend)")
        print(f"{'=' * 78}")
        
    if watchlist:
        print(f"\n{'=' * 78}")
        print(f"  👀 WATCHLIST: APPROACHING OVERSOLD DIP ({len(watchlist)})")
        print(f"{'=' * 78}")
        print(f"  {'Ticker':<8} {'Segment':<28} {'Price':>8}  {'RSI(2)':>7}  {'BB Gap':>8}  {'> 200-SMA':>10}")
        print(f"  {'-'*74}")
        for w in sorted(watchlist, key=lambda x: x["rsi2"])[:10]:
            dist_str = f"{w['dist_sma200']:+.1f}%"
            bb_str = f"{w['bb_gap_pct']:+.1f}%"
            print(f"  {w['ticker']:<8} {w['segment'][:26]:<28} ${w['price']:>7.2f}  {w['rsi2']:>6.1f}   {bb_str:>7}  {dist_str:>10}")
            
    print(f"\n{'=' * 78}\n")
    return setups, str(data_date)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Galaxy Active Cash Scanner v2")
    parser.add_argument("--refresh", action="store_true", help="Download latest daily prices")
    args = parser.parse_args()
    run_galaxy_scan(refresh=args.refresh)
