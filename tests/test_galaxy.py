"""
Unit tests for Galaxy Active Cash Engine.
Tests indicators, configurations, and Islamic Qabd holding constraints.
"""

import pytest
import numpy as np
import pandas as pd
import yaml
from pathlib import Path

from src import galaxy_indicators


def test_rsi_2_bounds_and_reactivity():
    """Verify RSI-2 is bounded between 0 and 100 and reacts quickly to price shifts."""
    # Monotonically increasing prices -> RSI should approach 100
    up_prices = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    rsi_up = galaxy_indicators.rsi_2(up_prices)
    assert 0.0 <= rsi_up.iloc[-1] <= 100.0
    assert rsi_up.iloc[-1] > 80.0

    # Monotonically decreasing prices -> RSI should approach 0 (extreme oversold)
    down_prices = pd.Series([20.0, 18.0, 16.0, 14.0, 12.0, 10.0])
    rsi_down = galaxy_indicators.rsi_2(down_prices)
    assert 0.0 <= rsi_down.iloc[-1] <= 100.0
    assert rsi_down.iloc[-1] < 10.0


def test_sma_calculation():
    """Verify SMA calculation accuracy."""
    prices = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    sma_val = galaxy_indicators.sma(prices, 3)
    assert np.isnan(sma_val.iloc[0])
    assert np.isnan(sma_val.iloc[1])
    assert sma_val.iloc[2] == pytest.approx(20.0)
    assert sma_val.iloc[4] == pytest.approx(40.0)


def test_bollinger_lower():
    """Verify Lower Bollinger Band sits below SMA."""
    prices = pd.Series([10, 12, 11, 13, 15, 14, 16, 18, 17, 19, 21, 20, 22, 24, 23, 25, 27, 26, 28, 30, 29, 31])
    b_low = galaxy_indicators.bollinger_lower(prices, window=20, num_std=2.0)
    sma20 = galaxy_indicators.sma(prices, 20)
    assert b_low.iloc[-1] < sma20.iloc[-1]


def test_cover_log_relative():
    """Verify Thomas Cover log price relative math."""
    prices = pd.Series([10.0, 12.0, 15.0, 20.0])
    log_rel = galaxy_indicators.cover_log_relative(prices, lookback=3)
    expected = np.log(20.0 / 10.0)  # log(2.0) ~= 0.6931
    assert log_rel.iloc[-1] == pytest.approx(expected)


def test_galaxy_universe_config_integrity():
    """Verify galaxy_universe.yaml is valid and every member is documented."""
    cfg_path = Path("config/galaxy_universe.yaml")
    assert cfg_path.exists()
    
    with open(cfg_path, "r") as f:
        data = yaml.safe_load(f)
        
    assert "benchmark" in data
    assert data["benchmark"]["symbol"] == "SPY"
    assert "segments" in data
    assert len(data["segments"]) >= 4
    
    total_members = 0
    for seg_key, seg_val in data["segments"].items():
        assert "members" in seg_val
        for m in seg_val["members"]:
            assert "ticker" in m
            assert "shariah_screen" in m
            assert "Verified Halal" in m["shariah_screen"]
            total_members += 1
            
    assert total_members == 21, f"Expected 21 Halal tickers, found {total_members}"


def test_galaxy_rules_islamic_constraint():
    """Verify rules enforce strict minimum 3-day holding period."""
    rules_path = Path("config/galaxy_rules.yaml")
    assert rules_path.exists()
    
    with open(rules_path, "r") as f:
        rules = yaml.safe_load(f)
        
    min_days = rules["exit_rules"]["holding"]["min_days"]
    assert min_days >= 3, "Islamic Qabd violation: min_days must be >= 3"
