"""
Swing Trading Entry Validation and Ablation Testing Engine (Phase 2).

Tests the entry filters with fixed-hold exits (hold N days, N in {5, 10, 15, 20, 25})
to isolate entry predictive power from exit design. Runs full ablation tests
to determine which filters contribute edge and which reduce sample size.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable, Callable

import numpy as np
import pandas as pd

from src import config, prices, universe, indicators, swing_filters


@dataclass
class Trade:
    """Simulated trade with fixed-hold exits."""

    ticker: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price: float
    stop_loss: float
    stop_distance: float
    stop_distance_pct: float
    atr: float
    tier: str
    candle_pattern: str | None
    
    # Outcomes across multiple holding horizons (trading days)
    # Mapping horizon_days -> {exit_date, exit_price, pnl_pct, pnl_r}
    horizons: dict[int, dict[str, Any]] = field(default_factory=dict)


@dataclass
class AblationMetrics:
    """Summary statistics for an ablation experiment."""

    name: str
    signal_count: int
    trades_per_year: float
    
    # Metrics per holding horizon (e.g. 10 days, 25 days)
    win_rate_10d: float = 0.0
    avg_return_10d: float = 0.0
    exp_r_10d: float = 0.0  # Expectancy in R multiples
    profit_factor_10d: float = 0.0
    
    win_rate_25d: float = 0.0
    avg_return_25d: float = 0.0
    exp_r_25d: float = 0.0
    profit_factor_25d: float = 0.0


class SwingBacktester:
    """Backtest runner for swing trade entry qualification and ablations."""

    def __init__(
        self,
        tickers: list[str] | None = None,
        benchmarks: list[str] | None = None,
        refresh: bool = False,
    ):
        if tickers is None:
            self.tickers = [c.ticker for c in universe.candidates()]
        else:
            self.tickers = tickers

        if benchmarks is None:
            self.benchmarks = ["SOXX", "SMH", "SPY", "QQQ", "XLU"]
        else:
            self.benchmarks = benchmarks

        self.ticker_bench = universe.ticker_benchmarks()
        self.flagged = set(universe.UniverseSnapshot(date.today(), universe.candidates()).by_status(universe.Status.SPECULATIVE))
        
        # Load price histories
        print(f"[Backtest] Loading price data for {len(self.tickers)} tickers and {len(self.benchmarks)} benchmarks...")
        all_syms = list(set(self.tickers + self.benchmarks))
        self.histories: dict[str, pd.DataFrame] = {}
        
        loaded = prices.load_many(all_syms, refresh=refresh)
        for sym, h in loaded.items():
            if h is not None and h.frame is not None and not h.frame.empty:
                self.histories[sym] = h.frame.copy()
                
        print(f"[Backtest] Loaded {len(self.histories)} / {len(all_syms)} price series.")

    def run_simulation(
        self,
        filter_override: Callable[[pd.Series, str, dict], bool] | None = None,
        rules_override: dict[str, Any] | None = None,
        horizons: tuple[int, ...] = (5, 10, 15, 20, 25),
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[Trade]:
        """
        Simulate entries across all tickers and compute outcomes across horizons.
        """
        soxx_df = self.histories.get("SOXX")
        rules = rules_override or config.get("rules.swing")
        
        trades: list[Trade] = []

        for ticker in self.tickers:
            df = self.histories.get(ticker)
            if df is None or len(df) < 100:
                continue

            bench_sym = self.ticker_bench.get(ticker, "SMH")
            bench_df = self.histories.get(bench_sym, self.histories.get("SMH"))
            if bench_df is None or len(bench_df) < 100:
                continue

            tier = "high_beta" if ticker in [c.ticker for c in universe.candidates() if c.stability_flag] else "core"

            enriched = swing_filters.compute_indicators(df, bench_df, soxx_df, rules=rules)
            
            # Slice dates if requested
            if start_date:
                enriched = enriched[enriched.index >= start_date]
            if end_date:
                enriched = enriched[enriched.index <= end_date]

            n_bars = len(enriched)
            for i in range(n_bars - 1):  # leave at least 1 bar for t+1 open execution
                row = enriched.iloc[i]
                sig_date = enriched.index[i]
                
                # Check filter
                if filter_override is not None:
                    passes = filter_override(row, tier, rules)
                else:
                    eval_res = swing_filters.evaluate_bar(row, ticker, tier=tier, rules=rules)
                    passes = eval_res.all_filters_pass

                if not passes:
                    continue

                # Signal fired at bar i. Entry is bar i+1 open
                next_row = enriched.iloc[i + 1]
                entry_date = enriched.index[i + 1]
                entry_price = float(next_row["open"])
                
                # Compute trade plan with actual entry price
                plan = swing_filters.calculate_trade_plan(row, tier=tier, entry_price=entry_price, rules=rules)
                if not plan.gate_passed:
                    continue

                t = Trade(
                    ticker=ticker,
                    signal_date=sig_date,
                    entry_date=entry_date,
                    entry_price=entry_price,
                    stop_loss=plan.stop_loss,
                    stop_distance=plan.stop_distance,
                    stop_distance_pct=plan.stop_distance_pct,
                    atr=float(row.get("atr_14", entry_price * 0.03)),
                    tier=tier,
                    candle_pattern=row.get("candle_pattern"),
                )

                # Simulate outcomes at each holding horizon
                for h_days in horizons:
                    exit_idx = i + 1 + h_days
                    if exit_idx < n_bars:
                        exit_row = enriched.iloc[exit_idx]
                        exit_date = enriched.index[exit_idx]
                        exit_price = float(exit_row["close"])
                    else:
                        exit_row = enriched.iloc[-1]
                        exit_date = enriched.index[-1]
                        exit_price = float(exit_row["close"])

                    pnl_pct = (exit_price - entry_price) / entry_price
                    pnl_r = (exit_price - entry_price) / plan.stop_distance if plan.stop_distance > 0 else 0.0

                    t.horizons[h_days] = {
                        "exit_date": exit_date,
                        "exit_price": exit_price,
                        "pnl_pct": pnl_pct,
                        "pnl_r": pnl_r,
                    }

                trades.append(t)

        return trades

    def evaluate_ablation(self, name: str, trades: list[Trade], years: float = 10.0) -> AblationMetrics:
        """Compute summary metrics for an ablation run."""
        count = len(trades)
        trades_yr = count / years if years > 0 else 0.0
        
        if count == 0:
            return AblationMetrics(name=name, signal_count=0, trades_per_year=0.0)

        # Horizon 10d
        r_10 = [t.horizons.get(10, {}).get("pnl_r", 0.0) for t in trades if 10 in t.horizons]
        ret_10 = [t.horizons.get(10, {}).get("pnl_pct", 0.0) for t in trades if 10 in t.horizons]
        
        win_10 = np.mean([r > 0 for r in r_10]) if r_10 else 0.0
        avg_ret_10 = np.mean(ret_10) if ret_10 else 0.0
        exp_r_10 = np.mean(r_10) if r_10 else 0.0
        
        gains_10 = [r for r in r_10 if r > 0]
        losses_10 = [-r for r in r_10 if r < 0]
        pf_10 = sum(gains_10) / sum(losses_10) if sum(losses_10) > 0 else (99.0 if sum(gains_10) > 0 else 0.0)

        # Horizon 25d
        r_25 = [t.horizons.get(25, {}).get("pnl_r", 0.0) for t in trades if 25 in t.horizons]
        ret_25 = [t.horizons.get(25, {}).get("pnl_pct", 0.0) for t in trades if 25 in t.horizons]
        
        win_25 = np.mean([r > 0 for r in r_25]) if r_25 else 0.0
        avg_ret_25 = np.mean(ret_25) if ret_25 else 0.0
        exp_r_25 = np.mean(r_25) if r_25 else 0.0
        
        gains_25 = [r for r in r_25 if r > 0]
        losses_25 = [-r for r in r_25 if r < 0]
        pf_25 = sum(gains_25) / sum(losses_25) if sum(losses_25) > 0 else (99.0 if sum(gains_25) > 0 else 0.0)

        return AblationMetrics(
            name=name,
            signal_count=count,
            trades_per_year=round(trades_yr, 1),
            win_rate_10d=round(win_10 * 100, 1),
            avg_return_10d=round(avg_ret_10 * 100, 2),
            exp_r_10d=round(exp_r_10, 3),
            profit_factor_10d=round(pf_10, 2),
            win_rate_25d=round(win_25 * 100, 1),
            avg_return_25d=round(avg_ret_25 * 100, 2),
            exp_r_25d=round(exp_r_25, 3),
            profit_factor_25d=round(pf_25, 2),
        )

    def run_all_ablations(self, start_date: str = "2015-01-01", end_date: str = "2026-08-31") -> list[AblationMetrics]:
        """
        Execute the full ablation matrix specified in Phase 2.
        """
        # Calculate years spanned
        d1 = pd.Timestamp(start_date)
        d2 = pd.Timestamp(end_date)
        years = max(1.0, (d2 - d1).days / 365.25)
        
        results: list[AblationMetrics] = []

        # 1. Baseline: All 6 filters + R:R gate
        print("[Ablation 1/7] Baseline: All 6 filters...")
        trades_base = self.run_simulation(start_date=start_date, end_date=end_date)
        results.append(self.evaluate_ablation("1. Baseline (All 6 Filters)", trades_base, years))

        # 2. Minus Candlestick requirement
        print("[Ablation 2/7] Minus Candlestick filter...")
        def filter_no_candle(row, tier, rules):
            regime = bool(row.get("regime_soxx_above_200", True))
            trend = row.get("return_formation", np.nan) > 0
            rs = row.get("rs_formation", np.nan) > 0
            dma50 = row["close"] > row.get("dma_50", np.nan)
            dip = bool(row.get("ema_20_prox", False)) or (row.get("rsi_2", np.nan) < 10.0)
            return regime and trend and rs and dma50 and dip

        trades_no_candle = self.run_simulation(filter_override=filter_no_candle, start_date=start_date, end_date=end_date)
        results.append(self.evaluate_ablation("2. No Candle Filter (Dip Only)", trades_no_candle, years))

        # 3. Minus 63-day Return > 0 (Own Trend)
        print("[Ablation 3/7] Minus Own Trend filter...")
        def filter_no_own_trend(row, tier, rules):
            regime = bool(row.get("regime_soxx_above_200", True))
            rs = row.get("rs_formation", np.nan) > 0
            dma50 = row["close"] > row.get("dma_50", np.nan)
            dip = bool(row.get("ema_20_prox", False)) or (row.get("rsi_2", np.nan) < 10.0)
            candle = pd.notna(row.get("candle_pattern"))
            return regime and rs and dma50 and dip and candle

        trades_no_trend = self.run_simulation(filter_override=filter_no_own_trend, start_date=start_date, end_date=end_date)
        results.append(self.evaluate_ablation("3. No Own Trend (RS + MA only)", trades_no_trend, years))

        # 4. Minus RS vs Benchmark
        print("[Ablation 4/7] Minus RS vs Benchmark filter...")
        def filter_no_rs(row, tier, rules):
            regime = bool(row.get("regime_soxx_above_200", True))
            trend = row.get("return_formation", np.nan) > 0
            dma50 = row["close"] > row.get("dma_50", np.nan)
            dip = bool(row.get("ema_20_prox", False)) or (row.get("rsi_2", np.nan) < 10.0)
            candle = pd.notna(row.get("candle_pattern"))
            return regime and trend and dma50 and dip and candle

        trades_no_rs = self.run_simulation(filter_override=filter_no_rs, start_date=start_date, end_date=end_date)
        results.append(self.evaluate_ablation("4. No RS Filter (Trend + Dip)", trades_no_rs, years))

        # 5. 20-EMA Proximity ONLY (no RSI)
        print("[Ablation 5/7] 20-EMA Proximity Only...")
        def filter_ema_only(row, tier, rules):
            regime = bool(row.get("regime_soxx_above_200", True))
            trend = row.get("return_formation", np.nan) > 0
            rs = row.get("rs_formation", np.nan) > 0
            dma50 = row["close"] > row.get("dma_50", np.nan)
            dip = bool(row.get("ema_20_prox", False))
            candle = pd.notna(row.get("candle_pattern"))
            return regime and trend and rs and dma50 and dip and candle

        trades_ema_only = self.run_simulation(filter_override=filter_ema_only, start_date=start_date, end_date=end_date)
        results.append(self.evaluate_ablation("5. 20-EMA Only (No RSI)", trades_ema_only, years))

        # 6. RSI(2) < 10 ONLY (no 20-EMA)
        print("[Ablation 6/7] RSI(2) < 10 Only...")
        def filter_rsi_only(row, tier, rules):
            regime = bool(row.get("regime_soxx_above_200", True))
            trend = row.get("return_formation", np.nan) > 0
            rs = row.get("rs_formation", np.nan) > 0
            dma50 = row["close"] > row.get("dma_50", np.nan)
            dip = row.get("rsi_2", np.nan) < 10.0
            candle = pd.notna(row.get("candle_pattern"))
            return regime and trend and rs and dma50 and dip and candle

        trades_rsi_only = self.run_simulation(filter_override=filter_rsi_only, start_date=start_date, end_date=end_date)
        results.append(self.evaluate_ablation("6. RSI(2)<10 Only (No EMA)", trades_rsi_only, years))

        # 7. Random Entry Baseline (Null Hypothesis)
        print("[Ablation 7/7] Random Entry Baseline (Null)...")
        np.random.seed(42)
        def filter_random(row, tier, rules):
            # ~2% random sample rate on trading days
            return np.random.rand() < 0.02

        trades_rand = self.run_simulation(filter_override=filter_random, start_date=start_date, end_date=end_date)
        results.append(self.evaluate_ablation("7. Random Entry Baseline (Null)", trades_rand, years))

        return results


def format_ablation_table(metrics: list[AblationMetrics]) -> str:
    """Format ablation metrics as a clean markdown table."""
    lines: list[str] = []
    lines.append("# Phase 2 — Swing Entry Ablation & Validation Report")
    lines.append("")
    lines.append("| Experiment | Total Signals | Signals / Yr | 10d Win% | 10d Avg Ret | 10d E[R] | 10d PF | 25d Win% | 25d Avg Ret | 25d E[R] | 25d PF |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    
    for m in metrics:
        lines.append(
            f"| **{m.name}** | {m.signal_count} | {m.trades_per_year} | "
            f"{m.win_rate_10d}% | {m.avg_return_10d:+0.2f}% | {m.exp_r_10d:+0.3f}R | {m.profit_factor_10d:.2f} | "
            f"{m.win_rate_25d}% | {m.avg_return_25d:+0.2f}% | {m.exp_r_25d:+0.3f}R | {m.profit_factor_25d:.2f} |"
        )

    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Swing Entry Validation Backtester")
    parser.add_argument("--refresh", action="store_true", help="Force refresh price caches")
    parser.add_argument("--start", default="2015-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default="2026-08-31", help="End date YYYY-MM-DD")
    args = parser.parse_args()

    tester = SwingBacktester(refresh=args.refresh)
    metrics = tester.run_all_ablations(start_date=args.start, end_date=args.end)
    table = format_ablation_table(metrics)
    print("\n" + table)
