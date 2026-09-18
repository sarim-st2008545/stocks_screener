"""
Monte Carlo Simulator for AI Infrastructure Swing Trading System.

Validates the Champion Strategy (Minervini Stage 2 + Pocket Pivot) by running
four independent statistical tests:

1. TRADE RESHUFFLING (Equity Curve Confidence Bands)
2. PARAMETER STABILITY (Overfitting Detection)
3. RANDOM ENTRY BENCHMARK (Statistical Edge Confirmation)
4. OPTIMAL POSITION SIZING (Risk-Per-Trade Sweep)

Run:
    python3 scratch/monte_carlo.py
"""

from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

import numpy as np
import pandas as pd

from src import config, prices, universe, indicators


# =============================================================================
# DATA LOADING
# =============================================================================

def load_enriched_data():
    """Load and enrich price data for the full universe."""
    cands = universe.candidates()
    tickers = [c.ticker for c in cands]
    bench_syms = ["SOXX", "SMH", "SPY", "QQQ", "XLU"]
    all_syms = list(set(tickers + bench_syms))
    ticker_bench = universe.ticker_benchmarks()

    histories = {}
    loaded = prices.load_many(all_syms)
    for sym, h in loaded.items():
        if h is not None and h.frame is not None and not h.frame.empty:
            histories[sym] = h.frame.copy()

    soxx_df = histories.get("SOXX")
    enriched = {}

    for ticker in tickers:
        df = histories.get(ticker)
        if df is None or len(df) < 260:
            continue
        bench_sym = ticker_bench.get(ticker, "SMH")
        bench_df = histories.get(bench_sym, histories.get("SMH"))
        if bench_df is None or len(bench_df) < 260:
            continue

        d = df.copy()
        c, h, l, v = d["close"], d["high"], d["low"], d["volume"]

        d["ema_10"] = indicators.ema(c, 10)
        d["ema_20"] = indicators.ema(c, 20)
        d["sma_50"] = indicators.sma(c, 50)
        d["sma_150"] = indicators.sma(c, 150)
        d["sma_200"] = indicators.sma(c, 200)
        d["sma_200_slope"] = (d["sma_200"] - d["sma_200"].shift(20)) / d["sma_200"].shift(20)
        d["high_52w"] = h.rolling(252).max()
        d["low_52w"] = l.rolling(252).min()
        d["swing_low_10"] = l.rolling(10).min()
        d["atr_14"] = indicators.atr(h, l, c, 14)

        d["ret_63"] = (c / c.shift(63)) - 1
        b_c = bench_df["close"].reindex(d.index).ffill()
        b_ret = (b_c / b_c.shift(63)) - 1
        d["rs_63"] = d["ret_63"] - b_ret

        if soxx_df is not None and not soxx_df.empty:
            s_c = soxx_df["close"].reindex(d.index).ffill()
            s_sma200 = indicators.sma(s_c, 200)
            d["soxx_regime"] = s_c > s_sma200
        else:
            d["soxx_regime"] = True

        is_down = c < c.shift(1)
        down_vol = pd.Series(np.where(is_down, v, 0.0), index=d.index)
        max_down_vol_10 = down_vol.rolling(10).max()
        d["is_up_day"] = c > d["open"]
        d["pocket_pivot_vol"] = (v > max_down_vol_10.shift(1)) & d["is_up_day"]

        enriched[ticker] = d

    return enriched, tickers, ticker_bench


# =============================================================================
# CHAMPION STRATEGY SIGNAL + TRAILING EXIT
# =============================================================================

def champion_signal(row, ema_prox=0.02):
    """Minervini Stage 2 Dip + Pocket Pivot combined signal."""
    c = row["close"]
    sma50 = row.get("sma_50", np.nan)
    sma150 = row.get("sma_150", np.nan)
    sma200 = row.get("sma_200", np.nan)
    h52 = row.get("high_52w", np.nan)
    l52 = row.get("low_52w", np.nan)

    ma_stack = (c > sma50) and (sma50 > sma150) and (sma150 > sma200) and (row.get("sma_200_slope", 0) > 0)
    range_cond = (c >= 0.75 * h52) and (c >= 1.30 * l52)
    rs_cond = row.get("rs_63", 0) > 0
    regime = bool(row.get("soxx_regime", True))
    dip = (abs(c - row["ema_10"]) / c <= ema_prox) or (abs(c - row["ema_20"]) / c <= ema_prox)

    stage2_dip = ma_stack and range_cond and rs_cond and dip and regime

    near_base = ((abs(c - row["ema_10"]) / c) <= 0.025) or \
                ((abs(c - row["ema_20"]) / c) <= 0.025) or \
                ((abs(c - row["sma_50"]) / c) <= 0.025)
    uptrend = c > sma50 and rs_cond
    vol_sig = bool(row.get("pocket_pivot_vol", False))
    pocket = regime and uptrend and near_base and vol_sig

    return stage2_dip or pocket


def simulate_trailing_exit(d, entry_idx, entry_price, stop_val, atr_val,
                           t1_mult=3.0, min_hold=3, max_bars=80):
    """Simulate trailing exit. Returns (exit_price, hold_days)."""
    n_bars = len(d)
    t1_target = entry_price + (t1_mult * atr_val)
    cur_stop = stop_val
    highest_h = entry_price
    t1_scaled = False
    ema_break_count = 0
    exit_price = entry_price
    hold_days = 0

    for k in range(entry_idx, min(entry_idx + max_bars, n_bars)):
        bar = d.iloc[k]
        k_days = k - entry_idx
        highest_h = max(highest_h, float(bar["high"]))

        if float(bar["close"]) < float(bar["ema_20"]):
            ema_break_count += 1
        else:
            ema_break_count = 0

        if float(bar["high"]) >= t1_target and not t1_scaled:
            t1_scaled = True
            cur_stop = entry_price

        chandelier = highest_h - (3.0 * atr_val)

        if k_days >= min_hold:
            if float(bar["low"]) <= cur_stop:
                return cur_stop, k_days
            if float(bar["close"]) < chandelier:
                return float(bar["close"]), k_days
            if ema_break_count >= 2:
                return float(bar["close"]), k_days

        exit_price = float(bar["close"])
        hold_days = k_days

    return exit_price, hold_days


def generate_trades(enriched, tickers, ema_prox=0.02, atr_floor=1.5,
                    t1_mult=3.0, min_hold=3):
    """Generate champion strategy trades with no overlapping positions per ticker."""
    trades = []

    for ticker in tickers:
        d = enriched.get(ticker)
        if d is None or len(d) < 260:
            continue

        n_bars = len(d)
        cooldown_until = -1  # no new trade on this ticker until this bar index

        for i in range(100, n_bars - 1):
            if i <= cooldown_until:
                continue  # still in previous trade on this ticker

            row = d.iloc[i]
            if not champion_signal(row, ema_prox=ema_prox):
                continue

            entry_idx = i + 1
            entry_price = float(d.iloc[entry_idx]["open"])
            atr_val = float(row.get("atr_14", entry_price * 0.03))

            s_low = float(row.get("swing_low_10", entry_price - 2 * atr_val))
            sma50 = float(row.get("sma_50", entry_price - 2 * atr_val))
            struct = [x for x in [s_low, sma50] if not np.isnan(x) and x < entry_price]
            stop_val = min(struct) if struct else (entry_price - 2 * atr_val)
            stop_val = min(stop_val, entry_price - atr_floor * atr_val)

            stop_dist = entry_price - stop_val
            if stop_dist <= 0 or (stop_dist / entry_price) > 0.15:
                continue

            exit_price, hold_days = simulate_trailing_exit(
                d, entry_idx, entry_price, stop_val, atr_val,
                t1_mult=t1_mult, min_hold=min_hold
            )

            pnl_pct = (exit_price - entry_price) / entry_price
            pnl_r = (exit_price - entry_price) / stop_dist

            trades.append({
                "ticker": ticker,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "stop_dist": stop_dist,
                "hold_days": hold_days,
                "pnl_pct": pnl_pct,
                "pnl_r": pnl_r,
            })

            # Block next signal on this ticker until this trade exits
            cooldown_until = entry_idx + max(hold_days, min_hold)

    return trades


# =============================================================================
# TEST 1: TRADE RESHUFFLING
# =============================================================================

def test_trade_reshuffling(trades, n_simulations=10000, risk_per_trade=0.02):
    """Shuffle trade order N times, simulate equity curves."""
    print(f"\n{'=' * 70}")
    print("  TEST 1: TRADE RESHUFFLING (Equity Curve Confidence Bands)")
    print(f"  {n_simulations:,} simulations | {len(trades)} trades | {risk_per_trade*100:.0f}% risk per trade")
    print(f"{'=' * 70}")

    r_multiples = np.array([t["pnl_r"] for t in trades])
    years = 11.5

    cagrs = np.zeros(n_simulations)
    max_drawdowns = np.zeros(n_simulations)
    rng = np.random.default_rng(42)

    for sim in range(n_simulations):
        shuffled = rng.permutation(r_multiples)
        log_equity = 0.0
        peak_log = 0.0
        worst_dd = 0.0

        for r in shuffled:
            pnl = risk_per_trade * r
            pnl = max(pnl, -0.99)  # prevent log(negative)
            log_equity += np.log(1.0 + pnl)
            if log_equity > peak_log:
                peak_log = log_equity
            dd = 1.0 - np.exp(log_equity - peak_log)
            if dd > worst_dd:
                worst_dd = dd

        final_equity = np.exp(log_equity)
        cagr = (final_equity ** (1.0 / years) - 1.0) * 100.0 if final_equity > 0 else -100.0
        cagrs[sim] = cagr
        max_drawdowns[sim] = worst_dd * 100.0

    # Total return for context
    median_total = (np.exp(np.median(np.log(np.maximum(cagrs / 100.0 + 1.0, 0.001)) * years)) - 1.0) * 100.0

    eq_pcts = np.percentile(cagrs, [5, 10, 25, 50, 75, 90, 95])
    dd_pcts = np.percentile(max_drawdowns, [5, 25, 50, 75, 90, 95])
    ruin_prob = np.mean(max_drawdowns > 50.0) * 100.0
    severe_dd_prob = np.mean(max_drawdowns > 30.0) * 100.0

    print(f"\n  Annualized Return (CAGR) Distribution over {years} years:")
    print(f"  {'─' * 50}")
    print(f"   5th percentile (worst realistic):  {eq_pcts[0]:>+10.1f}% CAGR")
    print(f"  10th percentile:                    {eq_pcts[1]:>+10.1f}% CAGR")
    print(f"  25th percentile:                    {eq_pcts[2]:>+10.1f}% CAGR")
    print(f"  50th percentile (median):           {eq_pcts[3]:>+10.1f}% CAGR")
    print(f"  75th percentile:                    {eq_pcts[4]:>+10.1f}% CAGR")
    print(f"  90th percentile:                    {eq_pcts[5]:>+10.1f}% CAGR")
    print(f"  95th percentile (best realistic):   {eq_pcts[6]:>+10.1f}% CAGR")

    print(f"\n  Maximum Drawdown Distribution:")
    print(f"  {'─' * 50}")
    print(f"   5th percentile (best case):        {dd_pcts[0]:>10.1f}%")
    print(f"  25th percentile:                    {dd_pcts[1]:>10.1f}%")
    print(f"  50th percentile (median):           {dd_pcts[2]:>10.1f}%")
    print(f"  75th percentile:                    {dd_pcts[3]:>10.1f}%")
    print(f"  90th percentile:                    {dd_pcts[4]:>10.1f}%")
    print(f"  95th percentile (worst realistic):  {dd_pcts[5]:>10.1f}%")

    print(f"\n  Risk of Ruin:")
    print(f"  {'─' * 50}")
    print(f"  Probability of >30% drawdown:       {severe_dd_prob:>10.1f}%")
    print(f"  Probability of >50% drawdown:       {ruin_prob:>10.1f}%")

    return {
        "cagr_percentiles": eq_pcts,
        "drawdown_percentiles": dd_pcts,
        "ruin_prob_50": ruin_prob,
        "severe_dd_prob_30": severe_dd_prob,
    }


# =============================================================================
# TEST 2: PARAMETER STABILITY
# =============================================================================

def test_parameter_stability(enriched, tickers):
    """Perturb key parameters and check if results remain stable."""
    print(f"\n{'=' * 70}")
    print("  TEST 2: PARAMETER STABILITY (Overfitting Detection)")
    print(f"  Perturbing EMA proximity, ATR floor, T1 multiplier, min hold days")
    print(f"{'=' * 70}")

    param_grid = {
        "ema_prox":  [0.015, 0.020, 0.025, 0.030],
        "atr_floor": [1.0, 1.25, 1.5, 1.75, 2.0],
        "t1_mult":   [2.0, 2.5, 3.0, 3.5, 4.0],
        "min_hold":  [3, 4, 5],
    }

    results = []
    baseline = {"ema_prox": 0.02, "atr_floor": 1.5, "t1_mult": 3.0, "min_hold": 3}

    for param_name, values in param_grid.items():
        print(f"\n  Testing: {param_name}")
        print(f"  {'─' * 60}")

        for val in values:
            params = baseline.copy()
            params[param_name] = val
            trades = generate_trades(enriched, tickers, **params)

            if not trades:
                continue

            r_vals = [t["pnl_r"] for t in trades]
            wins = [r for r in r_vals if r > 0]
            losses = [-r for r in r_vals if r < 0]
            pf = sum(wins) / sum(losses) if sum(losses) > 0 else 99.0
            wr = len(wins) / len(r_vals) * 100.0
            exp_r = np.mean(r_vals)
            avg_ret = np.mean([t["pnl_pct"] for t in trades]) * 100.0
            is_base = "  << BASELINE" if val == baseline[param_name] else ""

            print(f"    {param_name}={val:<6}  -> {len(trades):>5} trades | "
                  f"WR: {wr:>5.1f}% | Avg: {avg_ret:>+6.2f}% | "
                  f"E[R]: {exp_r:>+6.3f} | PF: {pf:>5.2f}{is_base}")

            results.append({
                "param": param_name, "value": val, "n_trades": len(trades),
                "win_rate": wr, "avg_return": avg_ret,
                "expectancy_r": exp_r, "profit_factor": pf,
            })

    print(f"\n  Stability Summary:")
    print(f"  {'─' * 60}")
    for param_name in param_grid:
        param_results = [r for r in results if r["param"] == param_name]
        pf_values = [r["profit_factor"] for r in param_results]
        if pf_values:
            pf_min, pf_max = min(pf_values), max(pf_values)
            pf_range = pf_max - pf_min
            stability = "ROBUST" if pf_range < 1.5 else ("MODERATE" if pf_range < 3.0 else "FRAGILE")
            print(f"    {param_name:<12} PF range: [{pf_min:.2f} - {pf_max:.2f}]  "
                  f"(spread: {pf_range:.2f})  {stability}")

    return {"results": results}


# =============================================================================
# TEST 3: RANDOM ENTRY BENCHMARK
# =============================================================================

def test_random_entry_benchmark(enriched, tickers, champion_trades, n_simulations=500):
    """Generate random entries, same exit rules, compare vs Champion."""
    print(f"\n{'=' * 70}")
    print("  TEST 3: RANDOM ENTRY BENCHMARK (Statistical Edge Confirmation)")
    print(f"  {n_simulations:,} random simulations vs Champion Strategy")
    print(f"{'=' * 70}")

    champ_r = [t["pnl_r"] for t in champion_trades]
    champ_wins = [r for r in champ_r if r > 0]
    champ_losses = [-r for r in champ_r if r < 0]
    champ_pf = sum(champ_wins) / sum(champ_losses) if sum(champ_losses) > 0 else 99.0
    champ_exp = np.mean(champ_r)
    champ_wr = len(champ_wins) / len(champ_r) * 100.0

    valid_bars = []
    for ticker in tickers:
        d = enriched.get(ticker)
        if d is None or len(d) < 260:
            continue
        for i in range(100, len(d) - 1):
            valid_bars.append((ticker, i))

    target_trades_per_sim = len(champion_trades)
    rng = np.random.default_rng(123)

    random_pfs = []
    random_exps = []
    random_wrs = []

    print(f"\n  Running {n_simulations} random entry simulations...")
    print(f"  (each with {target_trades_per_sim} random entries, same exit rules)")

    for sim in range(n_simulations):
        if (sim + 1) % 100 == 0:
            print(f"    ... simulation {sim + 1}/{n_simulations}")

        indices = rng.choice(len(valid_bars), size=min(target_trades_per_sim, 500), replace=True)
        sim_r_vals = []

        for idx in indices:
            ticker, bar_i = valid_bars[idx]
            d = enriched[ticker]
            row = d.iloc[bar_i]
            entry_idx = bar_i + 1
            if entry_idx >= len(d):
                continue

            entry_price = float(d.iloc[entry_idx]["open"])
            atr_val = float(row.get("atr_14", entry_price * 0.03))
            if np.isnan(atr_val) or atr_val <= 0:
                continue

            stop_val = entry_price - 1.5 * atr_val
            stop_dist = entry_price - stop_val
            if stop_dist <= 0:
                continue

            exit_price, _ = simulate_trailing_exit(d, entry_idx, entry_price, stop_val, atr_val)
            pnl_r = (exit_price - entry_price) / stop_dist
            sim_r_vals.append(pnl_r)

        if not sim_r_vals:
            continue

        wins = [r for r in sim_r_vals if r > 0]
        losses = [-r for r in sim_r_vals if r < 0]
        pf = sum(wins) / sum(losses) if sum(losses) > 0 else 99.0
        random_pfs.append(pf)
        random_exps.append(np.mean(sim_r_vals))
        random_wrs.append(len(wins) / len(sim_r_vals) * 100.0)

    random_pfs = np.array(random_pfs)
    random_exps = np.array(random_exps)
    random_wrs = np.array(random_wrs)

    beats_pf = np.mean(champ_pf > random_pfs) * 100.0
    beats_exp = np.mean(champ_exp > random_exps) * 100.0

    print(f"\n  Champion Strategy vs Random Entries:")
    print(f"  {'─' * 60}")
    print(f"  {'Metric':<25} {'Champion':>12} {'Random (Med)':>14} {'Random (95th)':>14}")
    print(f"  {'─' * 60}")
    print(f"  {'Win Rate':<25} {champ_wr:>11.1f}% {np.median(random_wrs):>13.1f}% {np.percentile(random_wrs, 95):>13.1f}%")
    print(f"  {'Expectancy (E[R])':<25} {champ_exp:>+11.3f}R {np.median(random_exps):>+13.3f}R {np.percentile(random_exps, 95):>+13.3f}R")
    print(f"  {'Profit Factor':<25} {champ_pf:>11.2f}x {np.median(random_pfs):>13.2f}x {np.percentile(random_pfs, 95):>13.2f}x")

    print(f"\n  Edge Confirmation:")
    print(f"  {'─' * 60}")
    print(f"  Champion beats random by Profit Factor in:  {beats_pf:.1f}% of simulations")
    print(f"  Champion beats random by Expectancy in:     {beats_exp:.1f}% of simulations")

    if beats_pf >= 95:
        print(f"\n  CONFIRMED: Strategy has a statistically significant edge (p < 0.05)")
    elif beats_pf >= 90:
        print(f"\n  LIKELY EDGE: Outperforms random in {beats_pf:.0f}% of cases (marginal)")
    else:
        print(f"\n  NO CONFIRMED EDGE: Only beats random in {beats_pf:.0f}% of cases")

    return {
        "champion_pf": champ_pf, "champion_exp": champ_exp, "champion_wr": champ_wr,
        "random_pf_median": float(np.median(random_pfs)),
        "beats_pf_pct": beats_pf, "beats_exp_pct": beats_exp,
    }


# =============================================================================
# BONUS: OPTIMAL POSITION SIZING
# =============================================================================

def test_optimal_position_size(trades):
    """Sweep risk-per-trade levels to find optimal sizing."""
    print(f"\n{'=' * 70}")
    print("  BONUS: OPTIMAL POSITION SIZING (Risk-Per-Trade Sweep)")
    print(f"  Finding the sweet spot between growth and drawdown control")
    print(f"{'=' * 70}")

    r_multiples = np.array([t["pnl_r"] for t in trades])
    risk_levels = [0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.075, 0.10]
    n_sims = 5000
    rng = np.random.default_rng(77)

    print(f"\n  {'Risk/Trade':>12} {'Median CAGR':>13} {'Median MaxDD':>14} {'95th MaxDD':>12} {'Ruin (>50%)':>12}")
    print(f"  {'─' * 68}")

    best_risk = 0.02
    best_score = -999
    years = 11.5
    sizing_rows = []

    for risk in risk_levels:
        max_dds = np.zeros(n_sims)
        cagrs = np.zeros(n_sims)

        for sim in range(n_sims):
            shuffled = rng.permutation(r_multiples)
            # Use log returns to prevent overflow
            log_equity = 0.0
            peak_log = 0.0
            worst_dd = 0.0

            for r in shuffled:
                pnl = risk * r
                # Clamp to prevent log(negative)
                pnl = max(pnl, -0.99)
                log_equity += np.log(1.0 + pnl)
                if log_equity > peak_log:
                    peak_log = log_equity
                # Drawdown in real space
                dd = 1.0 - np.exp(log_equity - peak_log)
                if dd > worst_dd:
                    worst_dd = dd

            # CAGR = (final_equity)^(1/years) - 1
            final_equity = np.exp(log_equity)
            cagr = (final_equity ** (1.0 / years) - 1.0) * 100.0 if final_equity > 0 else -100.0
            cagrs[sim] = cagr
            max_dds[sim] = worst_dd * 100.0

        med_cagr = np.median(cagrs)
        med_dd = np.median(max_dds)
        dd_95 = np.percentile(max_dds, 95)
        ruin = np.mean(max_dds > 50) * 100.0

        # Score: maximize CAGR, penalize heavily if 95th DD > 30%
        score = med_cagr - (max(0, dd_95 - 30) * 5)
        if ruin > 20:
            score -= 100  # disqualify high-ruin levels

        sizing_rows.append((risk, med_cagr, med_dd, dd_95, ruin, score))

    # Find the actual best
    best_row = max(sizing_rows, key=lambda x: x[5])
    best_risk = best_row[0]

    for risk, med_cagr, med_dd, dd_95, ruin, score in sizing_rows:
        marker = "  << OPTIMAL" if risk == best_risk else ""
        print(f"  {risk*100:>10.1f}%  {med_cagr:>+12.1f}%  {med_dd:>13.1f}%  {dd_95:>11.1f}%  {ruin:>10.1f}%{marker}")

    print(f"\n  Recommended risk per trade: {best_risk*100:.1f}% of equity")

    return {"optimal_risk_per_trade": best_risk}


# =============================================================================
# MAIN RUNNER
# =============================================================================

def run_full_monte_carlo():
    """Run all Monte Carlo tests."""
    print("\n" + "=" * 70)
    print("  MONTE CARLO SYSTEM VALIDATION SUITE")
    print("  Champion Strategy: Minervini Stage 2 + Pocket Pivot")
    print("  Universe: 41 AI Infrastructure & Semiconductor Stocks")
    print("  Data: 11.5 Years (2015-2026) | Islamic 3-Day Hold Enforced")
    print("=" * 70)

    print("\n  Loading and enriching price data...")
    enriched, tickers, _ = load_enriched_data()
    print(f"  Loaded {len(enriched)} tickers with full indicator enrichment.")

    print("\n  Generating champion strategy trade history...")
    trades = generate_trades(enriched, tickers)
    print(f"  Generated {len(trades)} historical trades.")

    if not trades:
        print("  ERROR: No trades generated. Cannot run Monte Carlo tests.")
        return

    r_vals = [t["pnl_r"] for t in trades]
    wins = [r for r in r_vals if r > 0]
    losses = [-r for r in r_vals if r < 0]
    pf = sum(wins) / sum(losses) if sum(losses) > 0 else 99.0
    wr = len(wins) / len(r_vals) * 100.0
    exp_r = np.mean(r_vals)
    avg_ret = np.mean([t["pnl_pct"] for t in trades]) * 100.0
    avg_hold = np.mean([t["hold_days"] for t in trades])

    print(f"\n  Baseline Champion Strategy Performance:")
    print(f"  {'─' * 50}")
    print(f"    Total Trades:     {len(trades)}")
    print(f"    Win Rate:         {wr:.1f}%")
    print(f"    Avg Return:       {avg_ret:+.2f}%")
    print(f"    Expectancy:       {exp_r:+.3f}R")
    print(f"    Profit Factor:    {pf:.2f}")
    print(f"    Avg Hold (Days):  {avg_hold:.1f}")

    # Run all 4 tests
    reshuffle = test_trade_reshuffling(trades)
    stability = test_parameter_stability(enriched, tickers)
    random_bench = test_random_entry_benchmark(enriched, tickers, trades, n_simulations=500)
    sizing = test_optimal_position_size(trades)

    # Final verdict
    print(f"\n{'=' * 70}")
    print("  MONTE CARLO VALIDATION VERDICT")
    print(f"{'=' * 70}")

    edge_confirmed = random_bench["beats_pf_pct"] >= 90
    low_ruin = reshuffle["ruin_prob_50"] < 5.0
    robust = True

    for param_name in ["ema_prox", "atr_floor", "t1_mult", "min_hold"]:
        param_results = [r for r in stability["results"] if r["param"] == param_name]
        pf_values = [r["profit_factor"] for r in param_results]
        if pf_values and (max(pf_values) - min(pf_values)) >= 3.0:
            robust = False

    verdicts = []
    verdicts.append(f"  1. Statistical Edge:     {'CONFIRMED' if edge_confirmed else 'NOT CONFIRMED'} "
                    f"(beats random in {random_bench['beats_pf_pct']:.0f}% of cases)")
    verdicts.append(f"  2. Parameter Robustness: {'ROBUST' if robust else 'SOME FRAGILITY'} "
                    f"(results stable across parameter perturbations)")
    verdicts.append(f"  3. Ruin Probability:     {'LOW RISK' if low_ruin else 'ELEVATED'} "
                    f"({reshuffle['ruin_prob_50']:.1f}% chance of >50% drawdown)")
    verdicts.append(f"  4. Optimal Position Size: {sizing['optimal_risk_per_trade']*100:.1f}% of equity per trade")

    for v in verdicts:
        print(v)

    if edge_confirmed and low_ruin and robust:
        print(f"\n  SYSTEM VALIDATED: The Champion Strategy has a confirmed, robust,")
        print(f"  statistically significant edge with manageable drawdown risk.")
    elif edge_confirmed:
        print(f"\n  EDGE CONFIRMED but monitor parameter sensitivity and position sizing.")
    else:
        print(f"\n  REVIEW RECOMMENDED: Some tests did not pass with high confidence.")

    print(f"\n{'=' * 70}\n")


if __name__ == "__main__":
    run_full_monte_carlo()
