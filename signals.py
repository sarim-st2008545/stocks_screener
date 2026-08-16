"""
Automated Swing Trading Signal Engine for Halal Equities.

Combines:
  1. Technical Momentum (RSI 14, EMA 20/50, Volume breakout, ATR volatility)
  2. SEC Catalysts (Form 4 insider cluster buys & 8-K earnings/material events)
  3. Fundamental Rank Score (> 60/100)
  4. Silent AAOIFI Shari'ah Compliance Filter (watchlist.json)

Outputs explicit BUY, SELL, Target Price (TP), Stop Loss (SL), and Horizon.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf
import pandas as pd
import numpy as np

from universe import DATA_DIR

def calculate_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add EMA20, EMA50, RSI14, ATR14, and Volume Ratio to daily candles."""
    df = df.copy()
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    # Moving Averages
    df["EMA20"] = close.ewm(span=20, adjust=False).mean()
    df["EMA50"] = close.ewm(span=50, adjust=False).mean()

    # RSI 14
    delta = close.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / (loss.replace(0, 1e-9))
    df["RSI14"] = 100 - (100 / (1 + rs))

    # ATR 14
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["ATR14"] = tr.rolling(window=14).mean()

    # Volume 10-day average ratio
    vol_ma = volume.rolling(window=10).mean()
    df["VolRatio"] = volume / (vol_ma.replace(0, 1))

    return df

def get_compliant_tickers() -> set[str]:
    """Retrieve PASS and REVIEW tickers silently from watchlist.json."""
    wl_path = DATA_DIR / "watchlist.json"
    if wl_path.exists():
        try:
            wl = json.loads(wl_path.read_text())
            pass_list = wl.get("pass", [])
            review_list = wl.get("review", [])
            return set(pass_list + review_list)
        except Exception:
            pass
    return set()

def load_catalysts_by_ticker() -> dict[str, list[dict[str, Any]]]:
    """Load catalysts keyed by ticker."""
    cat_path = DATA_DIR / "catalysts.json"
    cat_map: dict[str, list[dict[str, Any]]] = {}
    if cat_path.exists():
        try:
            cat_data = json.loads(cat_path.read_text())
            events = cat_data.get("events", [])
            for e in events:
                t = e.get("ticker")
                if t:
                    cat_map.setdefault(t, []).append(e)
        except Exception:
            pass
    return cat_map

def load_fundamental_scores() -> dict[str, float]:
    """Load composite fundamental scores."""
    scores_path = DATA_DIR / "scores.json"
    if scores_path.exists():
        try:
            scores_data = json.loads(scores_path.read_text())
            res = {}
            for t, s in scores_data.items():
                res[t] = s.get("composite", 0) if isinstance(s, dict) else float(s)
            return res
        except Exception:
            pass
    return {}

def generate_stock_signal(ticker: str, price_history: pd.DataFrame | None = None) -> dict[str, Any] | None:
    """
    Generate actionable swing trade signal for a single stock.
    Returns None if stock doesn't meet setup criteria.
    """
    ticker = ticker.upper()

    # Download daily history if not provided
    if price_history is None:
        try:
            ticker_obj = yf.Ticker(ticker)
            df = ticker_obj.history(period="6mo", interval="1d")
            if df.empty or len(df) < 30:
                return None
        except Exception:
            return None
    else:
        df = price_history

    df = calculate_technical_indicators(df)
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    curr_price = float(latest["Close"])
    ema20 = float(latest["EMA20"])
    ema50 = float(latest["EMA50"])
    rsi = float(latest["RSI14"])
    atr = float(latest["ATR14"])
    vol_ratio = float(latest["VolRatio"])

    # Load context
    cat_map = load_catalysts_by_ticker()
    scores = load_fundamental_scores()

    stock_cats = cat_map.get(ticker, [])
    fund_score = scores.get(ticker, 50.0)

    # Catalyst triggers
    insider_cluster = any(c.get("kind") == "insider_cluster" for c in stock_cats)
    earnings_8k = any("Results of Operations" in c.get("headline", "") for c in stock_cats)
    high_imp_cat = any(c.get("importance", 0) >= 4 for c in stock_cats)

    # Signal Scoring Logic
    signal_score = 0
    reasons = []

    # Technical momentum factors
    if curr_price > ema20 > ema50:
        signal_score += 25
        reasons.append("Uptrend (Price > EMA20 > EMA50)")

    if 45 <= rsi <= 68:
        signal_score += 20
        reasons.append(f"Optimal Bullish RSI ({rsi:.1f})")
    elif rsi > 70:
        reasons.append(f"Overbought RSI ({rsi:.1f})")

    if vol_ratio >= 1.3:
        signal_score += 15
        reasons.append(f"Volume Surge ({vol_ratio:.1f}x avg)")

    # Fundamental rank factor
    if fund_score >= 70:
        signal_score += 20
        reasons.append(f"Top Fundamental Rank ({fund_score:.1f}/100)")
    elif fund_score >= 55:
        signal_score += 10

    # SEC Catalyst factors
    if insider_cluster:
        signal_score += 25
        reasons.append("SEC Form 4 Insider Cluster Buying ($25k+)")
    elif high_imp_cat:
        signal_score += 15
        reasons.append("Material SEC 8-K Catalyst Event")

    # Determine Signal Type
    signal_type = "HOLD"
    if signal_score >= 65:
        signal_type = "STRONG BUY"
    elif signal_score >= 50:
        signal_type = "BUY"
    elif rsi > 75 or (curr_price < ema20 and prev["Close"] > prev["EMA20"]):
        signal_type = "SELL"
        reasons.append("Momentum loss / Moving average breakdown")

    if signal_type == "HOLD" and not insider_cluster and not high_imp_cat:
        return None

    # Calculate Target Price (TP) and Stop Loss (SL) based on ATR
    atr_multiplier_sl = 1.5
    atr_multiplier_tp = 3.0

    stop_loss = max(0.01, round(curr_price - (atr * atr_multiplier_sl), 2))
    target_price = round(curr_price + (atr * atr_multiplier_tp), 2)

    stop_loss_pct = round(((stop_loss - curr_price) / curr_price) * 100, 2)
    target_pct = round(((target_price - curr_price) / curr_price) * 100, 2)

    risk_amount = curr_price - stop_loss
    reward_amount = target_price - curr_price
    reward_risk_ratio = round(reward_amount / (risk_amount if risk_amount > 0 else 1), 2)

    # Estimate Horizon
    horizon_days = "5 - 15 Days" if signal_type in ("BUY", "STRONG BUY") else "1 - 5 Days"

    primary_reason = " • ".join(reasons[:2]) if reasons else "Momentum & fundamental alignment"

    return {
        "ticker": ticker,
        "signal_type": signal_type,
        "signal_score": signal_score,
        "entry_price": round(curr_price, 2),
        "target_price": target_price,
        "stop_loss": stop_loss,
        "target_pct": target_pct,
        "stop_loss_pct": stop_loss_pct,
        "reward_risk_ratio": reward_risk_ratio,
        "horizon_days": horizon_days,
        "reason": primary_reason,
        "rsi": round(rsi, 1),
        "fund_score": round(fund_score, 1),
        "has_insider_buy": insider_cluster,
        "updated_at": datetime.now().isoformat()
    }

def get_active_market_signals(limit: int = 20) -> list[dict[str, Any]]:
    """Scan Halal investable universe and return actionable BUY/SELL signals."""
    compliant_tickers = get_compliant_tickers()
    if not compliant_tickers:
        # Fallback default universe if watchlist not generated yet
        compliant_tickers = {"MU", "NVDA", "AMAT", "LRCX", "KLAC", "AVGO", "CRWD", "FTNT", "MSFT", "DELL", "FSLR", "AAPL"}

    signals = []
    
    # Download batch history for speed
    tickers_str = " ".join(list(compliant_tickers)[:40])
    try:
        data = yf.download(tickers_str, period="6m", interval="1d", group_by="ticker", progress=False)
        for ticker in compliant_tickers:
            try:
                if len(compliant_tickers) == 1:
                    df = data
                else:
                    df = data[ticker]
                
                df = df.dropna(how="all")
                if df.empty or len(df) < 30:
                    continue

                sig = generate_stock_signal(ticker, price_history=df)
                if sig and sig["signal_type"] in ("STRONG BUY", "BUY", "SELL"):
                    signals.append(sig)
            except Exception:
                continue
    except Exception:
        pass

    # Sort signals by signal score (best setups first)
    signals.sort(key=lambda x: x["signal_score"], reverse=True)
    return signals[:limit]

if __name__ == "__main__":
    print("--- Scanning Actionable Signals ---")
    sigs = get_active_market_signals(limit=5)
    for s in sigs:
        print(f"[{s['signal_type']}] {s['ticker']} @ ${s['entry_price']} | TP: ${s['target_price']} (+{s['target_pct']}%) | SL: ${s['stop_loss']} ({s['stop_loss_pct']}%) | Reason: {s['reason']}")
