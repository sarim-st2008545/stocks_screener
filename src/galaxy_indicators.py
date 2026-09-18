"""
Vectorized Quantitative Indicators for the Galaxy Active Cash System (v2 — TradeAlgo Enhanced).

Provides high-speed indicators for 3-4 day swing scalping:
1. 2-Period Relative Strength Index (RSI-2) using Wilder's Exponential Smoothing
2. Dual Trend Gate: SMA-200 + SMA-50 (confirmed uptrend only)
3. 20-Period Bollinger Bands Lower Band (entry confirmation filter)
4. 10-Period SMA (dynamic exit target — rides the mean)
5. 1.5×ATR-14 Dynamic Volatility Stop
6. Thomas Cover 3-Day Log Price Relatives
"""

from __future__ import annotations
import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average over specified period."""
    return series.rolling(window=period).mean()


def rsi_2(close: pd.Series) -> pd.Series:
    """
    Connors 2-Period RSI matching TradingView's ta.rsi(close, 2).
    Uses Wilder's smoothing: alpha = 1 / period.
    Handles zero loss (RSI=100) and zero gain (RSI=0) correctly.
    """
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    
    avg_gain = gain.ewm(alpha=1.0 / 2.0, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / 2.0, adjust=False).mean()
    
    # Where loss is 0 (only gains), RSI is 100
    # Where gain is 0 (only losses), RSI is 0
    rs = np.where(avg_loss == 0.0, np.inf, avg_gain / avg_loss)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    
    # If both gain and loss are 0 (flat price), RSI is 50
    flat_mask = (avg_gain == 0.0) & (avg_loss == 0.0)
    rsi = np.where(flat_mask, 50.0, rsi)
    
    return pd.Series(rsi, index=close.index).fillna(50.0)


def bollinger_lower(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
    """Lower Bollinger Band: SMA - (num_std * rolling standard deviation)."""
    rolling_mean = close.rolling(window=window).mean()
    rolling_std = close.rolling(window=window).std()
    return rolling_mean - (num_std * rolling_std)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's Average True Range."""
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, adjust=False).mean()


def cover_log_relative(close: pd.Series, lookback: int = 3) -> pd.Series:
    """
    Thomas Cover's Log Price Relative: log(P_t / P_{t - lookback}).
    Measures compressed kinetic energy across the holding window.
    """
    return np.log(close / close.shift(lookback))
