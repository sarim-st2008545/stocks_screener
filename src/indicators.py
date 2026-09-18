"""
Technical indicators library for the swing trading engine.
Provides vectorized implementations of common indicators and candle patterns.
"""

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """
    Calculate the Simple Moving Average (SMA).
    
    Args:
        series: Price series (e.g., close prices).
        period: Number of periods for the moving average.
        
    Returns:
        pd.Series containing the SMA.
    """
    return series.rolling(period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """
    Calculate the Exponential Moving Average (EMA).
    
    Args:
        series: Price series (e.g., close prices).
        period: Span for the EMA.
        
    Returns:
        pd.Series containing the EMA.
    """
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 2) -> pd.Series:
    """
    Calculate the Relative Strength Index (RSI) using Wilder smoothing.
    
    Args:
        series: Price series (e.g., close prices).
        period: Number of periods for the RSI (default is 2).
        
    Returns:
        pd.Series containing the RSI.
    """
    ch = series.diff()
    up = ch.clip(lower=0)
    dn = -ch.clip(upper=0)
    
    ag = up.ewm(alpha=1 / period, adjust=False).mean()
    al = dn.ewm(alpha=1 / period, adjust=False).mean()
    
    return 100 - (100 / (1 + ag / al))


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculate the Average True Range (ATR) using Wilder smoothing.
    Matches TradingView's ta.atr() implementation.
    
    Args:
        high: High price series.
        low: Low price series.
        close: Close price series.
        period: Number of periods for the ATR (default is 14).
        
    Returns:
        pd.Series containing the ATR.
    """
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def swing_low(low: pd.Series, window: int = 10) -> pd.Series:
    """
    Calculate the rolling minimum of lows (Swing Low).
    
    Args:
        low: Low price series.
        window: Number of periods to look back.
        
    Returns:
        pd.Series containing the swing lows.
    """
    return low.rolling(window).min()


def swing_high(high: pd.Series, window: int = 10) -> pd.Series:
    """
    Calculate the rolling maximum of highs (Swing High).
    
    Args:
        high: High price series.
        window: Number of periods to look back.
        
    Returns:
        pd.Series containing the swing highs.
    """
    return high.rolling(window).max()


def detect_candles(df: pd.DataFrame) -> pd.Series:
    """
    Detect specific candlestick patterns: hammer, bullish engulfing, and inside day + higher close.
    
    Args:
        df: DataFrame containing 'open', 'high', 'low', 'close' columns.
        
    Returns:
        pd.Series of strings ('hammer', 'engulfing', 'inside_up', or None).
    """
    o, h, l, c = df['open'], df['high'], df['low'], df['close']
    po, ph, pl, pc = o.shift(1), h.shift(1), l.shift(1), c.shift(1)
    
    body = (c - o).abs()
    
    # Calculate wicks
    lower_wick = pd.concat([o, c], axis=1).min(axis=1) - l
    upper_wick = h - pd.concat([o, c], axis=1).max(axis=1)
    
    # Hammer: lower_wick >= 2*body, close > open, body in upper portion of range
    is_hammer = (lower_wick >= 2 * body) & (upper_wick <= body * 0.6) & (c > o)
    
    # Bullish engulfing: previous candle was red, current is green, current engulfs previous body
    is_engulf = (pc < po) & (c > o) & (c >= po) & (o <= pc)
    
    # Inside day + higher close: today's range inside yesterday's range, close > previous close
    is_inside_up = (h <= ph) & (l >= pl) & (c > pc)
    
    # Priority: hammer > engulfing > inside_up
    result = pd.Series(None, index=df.index, dtype=object)
    result[is_inside_up] = 'inside_up'
    result[is_engulf] = 'engulfing'
    result[is_hammer] = 'hammer'
    
    return result


def relative_strength(stock_close: pd.Series, bench_close: pd.Series, lookback: int = 63) -> pd.Series:
    """
    Calculate the relative strength of a stock vs a benchmark over a lookback period.
    
    Args:
        stock_close: Close price series of the stock.
        bench_close: Close price series of the benchmark.
        lookback: Number of periods for the return calculation.
        
    Returns:
        pd.Series containing the relative strength (difference in returns).
    """
    stock_ret = (stock_close / stock_close.shift(lookback)) - 1
    bench_ret = (bench_close / bench_close.shift(lookback)) - 1
    return stock_ret - bench_ret


def ema_proximity(close: pd.Series, ema_values: pd.Series, threshold: float = 0.01) -> pd.Series:
    """
    Check if the close price is within a certain percentage threshold of the EMA.
    
    Args:
        close: Close price series.
        ema_values: EMA series.
        threshold: Proximity threshold (default 1%).
        
    Returns:
        Boolean pd.Series. True when close is within threshold of EMA.
    """
    return (close - ema_values).abs() / close <= threshold
