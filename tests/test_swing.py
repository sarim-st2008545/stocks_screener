"""
Unit tests for technical indicators and swing filters.
"""

import numpy as np
import pandas as pd
import pytest

from src import indicators, swing_filters


def test_sma_and_ema():
    s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    s_sma = indicators.sma(s, 3)
    assert pd.isna(s_sma.iloc[0])
    assert pd.isna(s_sma.iloc[1])
    assert s_sma.iloc[2] == pytest.approx(20.0)
    assert s_sma.iloc[4] == pytest.approx(40.0)

    s_ema = indicators.ema(s, 3)
    assert len(s_ema) == 5
    assert not pd.isna(s_ema.iloc[-1])


def test_rsi_wilder():
    # Constant series should have 0 diff or neutral RSI
    prices = pd.Series([100.0, 102.0, 101.0, 103.0, 105.0, 104.0, 106.0])
    r = indicators.rsi(prices, period=2)
    assert len(r) == len(prices)
    assert 0 <= r.iloc[-1] <= 100


def test_atr_wilder():
    df = pd.DataFrame({
        "high": [105.0, 106.0, 108.0, 107.0, 110.0],
        "low": [95.0, 98.0, 101.0, 100.0, 102.0],
        "close": [100.0, 104.0, 106.0, 103.0, 108.0],
    })
    a = indicators.atr(df["high"], df["low"], df["close"], period=3)
    assert len(a) == 5
    assert a.iloc[-1] > 0


def test_candle_detection():
    # Hammer: open=100, close=101, high=101.5, low=95 -> lower wick=5, body=1 (lower_wick >= 2*body, close>open)
    df = pd.DataFrame({
        "open": [100.0, 100.0],
        "high": [102.0, 101.5],
        "low": [98.0, 95.0],
        "close": [99.0, 101.0],
    })
    patterns = indicators.detect_candles(df)
    assert patterns.iloc[1] == "hammer"


def test_trade_plan_calculation():
    row = pd.Series({
        "close": 200.0,
        "dma_50": 180.0,
        "swing_low_10": 185.0,
        "atr_14": 10.0,
    })
    plan = swing_filters.calculate_trade_plan(row, tier="core")
    # Structural stop = further from entry (min(180, 185)) = 180.0
    # Volatility floor = 200 - 1.5 * 10 = 185.0
    # Chosen stop = min(180, 185) = 180.0
    assert plan.stop_loss == 180.0
    assert plan.stop_distance == 20.0
    assert plan.target_1 == 230.0  # 200 + 3.0 * 10
    assert plan.rr_t1 == pytest.approx(30.0 / 20.0)  # 1.5:1
    assert plan.gate_passed is True
