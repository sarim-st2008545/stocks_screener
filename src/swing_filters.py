"""
Swing trading filters and trade plan calculations.
Implements the 6 entry filters and risk:reward trade plan defined in implementation_plan.md Part 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from src import config, indicators


@dataclass
class TradePlan:
    """Calculated trade plan for a setup."""

    signal_date: date | pd.Timestamp
    signal_close: float
    entry_price: float
    stop_loss: float
    stop_distance: float
    stop_distance_pct: float
    stop_distance_atr: float
    target_1: float
    target_1_distance: float
    target_1_pct: float
    rr_t1: float
    tier: str
    gate_passed: bool
    gate_failure_reasons: list[str] = field(default_factory=list)


@dataclass
class FilterResult:
    """Evaluation of all entry filters for a single bar."""

    as_of: date | pd.Timestamp
    ticker: str
    regime_pass: bool
    earnings_pass: bool
    trend_pass: bool
    rs_pass: bool
    dma50_pass: bool
    dip_pass: bool
    candle_pass: bool
    candle_type: str | None
    all_filters_pass: bool
    trade_plan: TradePlan | None = None
    failures: list[str] = field(default_factory=list)


def compute_indicators(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    soxx_df: pd.DataFrame | None = None,
    rules: dict[str, Any] | None = None,
) -> pd.DataFrame:
    """
    Enrich stock OHLCV with all indicators required for swing trade filtering.
    """
    if rules is None:
        try:
            rules = config.get("rules.swing")
        except Exception:
            rules = {}

    df = stock_df.copy()
    
    formation_days = rules.get("trend", {}).get("formation_lookback_days", 63)
    dma50_period = rules.get("trend", {}).get("dma_period", 50)
    ema20_period = rules.get("dip_trigger", {}).get("ema_period", 20)
    rsi_period = rules.get("dip_trigger", {}).get("rsi_period", 2)
    atr_period = rules.get("trade_plan", {}).get("atr_period", 14)
    swing_low_window = rules.get("trade_plan", {}).get("swing_low_window", 10)
    
    df["dma_50"] = indicators.sma(df["close"], dma50_period)
    df["ema_20"] = indicators.ema(df["close"], ema20_period)
    df["rsi_2"] = indicators.rsi(df["close"], rsi_period)
    df["atr_14"] = indicators.atr(df["high"], df["low"], df["close"], atr_period)
    df["swing_low_10"] = indicators.swing_low(df["low"], swing_low_window)
    df["swing_high_10"] = indicators.swing_high(df["high"], swing_low_window)
    
    df["candle_pattern"] = indicators.detect_candles(df)
    df["return_formation"] = (df["close"] / df["close"].shift(formation_days)) - 1
    
    bench_close_aligned = benchmark_df["close"].reindex(df.index).ffill()
    bench_ret_formation = (bench_close_aligned / bench_close_aligned.shift(formation_days)) - 1
    df["bench_return_formation"] = bench_ret_formation
    df["rs_formation"] = df["return_formation"] - bench_ret_formation
    
    threshold = rules.get("dip_trigger", {}).get("ema_proximity_threshold", 0.01)
    df["ema_20_prox"] = indicators.ema_proximity(df["close"], df["ema_20"], threshold)
    
    if soxx_df is not None and not soxx_df.empty:
        soxx_close = soxx_df["close"].reindex(df.index).ffill()
        soxx_dma200 = indicators.sma(soxx_close, rules.get("regime", {}).get("dma_period", 200))
        df["regime_soxx_above_200"] = soxx_close > soxx_dma200
    else:
        df["regime_soxx_above_200"] = True
        
    return df


def calculate_trade_plan(
    row: pd.Series,
    tier: str = "core",
    entry_price: float | None = None,
    rules: dict[str, Any] | None = None,
) -> TradePlan:
    """
    Calculate entry, structural stop with 1.5x ATR floor, and T1 target.
    """
    if rules is None:
        try:
            rules = config.get("rules.swing")
        except Exception:
            rules = {}

    trade_plan_cfg = rules.get("trade_plan", {})
    gates_cfg = rules.get("gates", {}).get(tier, {"max_stop_pct": 10.0 if tier == "core" else 15.0, "min_rr1": 1.5})
    
    entry = float(entry_price if entry_price is not None else row["close"])
    atr_val = float(row.get("atr_14", entry * 0.03))
    dma_50 = float(row.get("dma_50", np.nan))
    s_low = float(row.get("swing_low_10", np.nan))
    
    valid_structural: list[float] = []
    if not np.isnan(dma_50) and dma_50 < entry:
        valid_structural.append(dma_50)
    if not np.isnan(s_low) and s_low < entry:
        valid_structural.append(s_low)
        
    atr_floor_mult = float(trade_plan_cfg.get("atr_floor_mult", 1.5))
    fallback_mult = float(trade_plan_cfg.get("fallback_stop_atr_mult", 2.0))
    volatility_floor_stop = entry - (atr_floor_mult * atr_val)
    
    if valid_structural:
        chosen_structural = min(valid_structural)
        stop = min(chosen_structural, volatility_floor_stop)
    else:
        stop = entry - (fallback_mult * atr_val)
        
    stop_dist = entry - stop
    stop_dist_pct = (stop_dist / entry) * 100.0
    stop_dist_atr = stop_dist / atr_val if atr_val > 0 else 1.0
    
    t1_mult = float(trade_plan_cfg.get("t1_atr_mult", 3.0))
    t1 = entry + (t1_mult * atr_val)
    t1_dist = t1 - entry
    t1_pct = (t1_dist / entry) * 100.0
    
    rr_t1 = t1_dist / stop_dist if stop_dist > 0 else 0.0
    
    min_rr = float(gates_cfg.get("min_rr1", 1.5))
    max_stop_pct = float(gates_cfg.get("max_stop_pct", 10.0 if tier == "core" else 15.0))
    
    failures: list[str] = []
    if rr_t1 < min_rr:
        failures.append(f"R:R ({rr_t1:.2f}) < min ({min_rr:.2f})")
    if stop_dist_pct > max_stop_pct:
        failures.append(f"Stop width ({stop_dist_pct:.1f}%) > max ({max_stop_pct:.1f}%)")
        
    gate_passed = len(failures) == 0
    
    return TradePlan(
        signal_date=row.name if hasattr(row, "name") else date.today(),
        signal_close=float(row["close"]),
        entry_price=entry,
        stop_loss=stop,
        stop_distance=stop_dist,
        stop_distance_pct=stop_dist_pct,
        stop_distance_atr=stop_dist_atr,
        target_1=t1,
        target_1_distance=t1_dist,
        target_1_pct=t1_pct,
        rr_t1=rr_t1,
        tier=tier,
        gate_passed=gate_passed,
        gate_failure_reasons=failures,
    )


def evaluate_bar(
    row: pd.Series,
    ticker: str,
    tier: str = "core",
    has_upcoming_earnings: bool = False,
    rules: dict[str, Any] | None = None,
) -> FilterResult:
    """
    Evaluate all 6 entry filters on a single bar.
    """
    if rules is None:
        try:
            rules = config.get("rules.swing")
        except Exception:
            rules = {}

    regime_enabled = rules.get("regime", {}).get("enabled", True)
    earnings_enabled = rules.get("earnings", {}).get("enabled", True)
    rsi_threshold = rules.get("dip_trigger", {}).get("rsi_oversold_threshold", 10.0)
    
    failures: list[str] = []
    
    regime_pass = True
    if regime_enabled:
        regime_pass = bool(row.get("regime_soxx_above_200", True))
        if not regime_pass:
            failures.append("Regime: SOXX is below 200-DMA")
            
    earnings_pass = True
    if earnings_enabled:
        earnings_pass = not has_upcoming_earnings
        if not earnings_pass:
            failures.append("Earnings: scheduled release within buffer")
            
    ret_form = row.get("return_formation", np.nan)
    trend_pass = not np.isnan(ret_form) and ret_form > 0
    if not trend_pass:
        failures.append(f"Trend: 63d return ({ret_form:.1%}) <= 0")
        
    rs_form = row.get("rs_formation", np.nan)
    rs_pass = not np.isnan(rs_form) and rs_form > 0
    if not rs_pass:
        failures.append(f"RS: 63d relative strength ({rs_form:.1%}) <= 0")
        
    dma50 = row.get("dma_50", np.nan)
    dma50_pass = not np.isnan(dma50) and row["close"] > dma50
    if not dma50_pass:
        failures.append(f"MA: close ({row['close']:.2f}) <= 50-DMA ({dma50:.2f})")
        
    ema_prox = bool(row.get("ema_20_prox", False))
    rsi_val = row.get("rsi_2", np.nan)
    rsi_oversold = not np.isnan(rsi_val) and rsi_val < rsi_threshold
    dip_cond_a = ema_prox or rsi_oversold
    
    candle_type = row.get("candle_pattern")
    if pd.isna(candle_type):
        candle_type = None
    candle_cond_b = candle_type is not None
    
    dip_pass = dip_cond_a and candle_cond_b
    if not dip_cond_a:
        failures.append("Dip: neither 20-EMA proximity nor RSI(2)<10")
    if not candle_cond_b:
        failures.append("Candle: no bullish reversal candle")
        
    all_filters_pass = (
        regime_pass
        and earnings_pass
        and trend_pass
        and rs_pass
        and dma50_pass
        and dip_pass
    )
    
    plan: TradePlan | None = None
    if all_filters_pass:
        plan = calculate_trade_plan(row, tier=tier, rules=rules)
        if not plan.gate_passed:
            all_filters_pass = False
            failures.extend(plan.gate_failure_reasons)
            
    return FilterResult(
        as_of=row.name if hasattr(row, "name") else date.today(),
        ticker=ticker,
        regime_pass=regime_pass,
        earnings_pass=earnings_pass,
        trend_pass=trend_pass,
        rs_pass=rs_pass,
        dma50_pass=dma50_pass,
        dip_pass=dip_cond_a,
        candle_pass=candle_cond_b,
        candle_type=candle_type,
        all_filters_pass=all_filters_pass,
        trade_plan=plan,
        failures=failures,
    )
