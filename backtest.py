"""
Historical Backtester Engine for Swing Trading Strategy.

Simulates entry/exit execution over past market data (6m, 1y, 2y)
to calculate Win Rate %, Total Return %, Profit Factor, Max Drawdown %,
and complete trade log.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import yfinance as yf
import pandas as pd
import numpy as np

from signals import calculate_technical_indicators, get_compliant_tickers

def run_backtest_simulation(
    tickers: list[str] | None = None,
    period: str = "1y",
    initial_capital: float = 10000.0,
    max_position_size_pct: float = 0.25
) -> dict[str, Any]:
    """
    Run historical backtest over specified tickers and period.
    Returns strategy performance metrics and complete trade log.
    """
    if not tickers:
        compliant = get_compliant_tickers()
        if compliant:
            tickers = list(compliant)[:20]
        else:
            tickers = ["MU", "NVDA", "AMAT", "LRCX", "KLAC", "AVGO", "CRWD", "FTNT", "MSFT", "DELL", "AAPL", "FSLR"]

    tickers_str = " ".join(tickers)
    try:
        data = yf.download(tickers_str, period=period, interval="1d", group_by="ticker", progress=False)
    except Exception as e:
        return {"error": f"Failed to download historical data: {e}"}

    capital = initial_capital
    peak_capital = initial_capital
    max_drawdown_pct = 0.0

    open_positions: list[dict[str, Any]] = []
    closed_trades: list[dict[str, Any]] = []

    equity_curve: list[dict[str, Any]] = []

    # Extract all trading dates
    if len(tickers) == 1:
        dates = data.index
    else:
        dates = data.index

    # Need at least 50 bars for indicator calculation
    if len(dates) < 50:
        return {"error": "Insufficient historical candle data"}

    # Process each ticker's indicator table
    stock_dfs = {}
    for t in tickers:
        try:
            df = data if len(tickers) == 1 else data[t]
            df = df.dropna(how="all").copy()
            if len(df) >= 50:
                stock_dfs[t] = calculate_technical_indicators(df)
        except Exception:
            continue

    # Step through daily bars starting after initial 40 warm-up bars
    for i in range(40, len(dates)):
        current_date = dates[i]
        date_str = str(current_date)[:10]

        # 1. Check open positions for TP / SL exit
        still_open = []
        for pos in open_positions:
            t = pos["ticker"]
            if t not in stock_dfs:
                continue

            df = stock_dfs[t]
            if current_date not in df.index:
                still_open.append(pos)
                continue

            row = df.loc[current_date]
            high = float(row["High"])
            low = float(row["Low"])
            close = float(row["Close"])

            entry_p = pos["entry_price"]
            tp_p = pos["tp_price"]
            sl_p = pos["sl_price"]
            shares = pos["shares"]

            exit_reason = None
            exit_price = close

            # Take profit hit
            if high >= tp_p:
                exit_reason = "TAKE PROFIT (Target Hit)"
                exit_price = tp_p
            # Stop loss hit
            elif low <= sl_p:
                exit_reason = "STOP LOSS (Risk Hit)"
                exit_price = sl_p
            # RSI overbought sell
            elif float(row["RSI14"]) > 75 or (close < float(row["EMA20"]) and df.iloc[df.index.get_loc(current_date)-1]["Close"] > df.iloc[df.index.get_loc(current_date)-1]["EMA20"]):
                exit_reason = "MOMENTUM SELL"
                exit_price = close

            if exit_reason:
                proceeds = shares * exit_price
                capital += proceeds
                profit_loss = proceeds - (shares * entry_p)
                pnl_pct = ((exit_price - entry_p) / entry_p) * 100

                closed_trades.append({
                    "ticker": t,
                    "entry_date": pos["entry_date"],
                    "exit_date": date_str,
                    "entry_price": round(entry_p, 2),
                    "exit_price": round(exit_price, 2),
                    "shares": shares,
                    "pnl_usd": round(profit_loss, 2),
                    "pnl_pct": round(pnl_pct, 2),
                    "result": "WIN" if profit_loss > 0 else "LOSS",
                    "reason": exit_reason
                })
            else:
                still_open.append(pos)

        open_positions = still_open

        # 2. Check for new BUY entries across tickers
        # Calculate current equity value
        open_val = sum(p["shares"] * float(stock_dfs[p["ticker"]].loc[current_date]["Close"]) for p in open_positions if current_date in stock_dfs[p["ticker"]].index)
        total_equity = capital + open_val

        # Update drawdown
        if total_equity > peak_capital:
            peak_capital = total_equity
        dd = ((peak_capital - total_equity) / peak_capital) * 100
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd

        # Look for new setups if capital available
        if capital > 500 and len(open_positions) < 5:
            for t, df in stock_dfs.items():
                # Avoid duplicate position
                if any(p["ticker"] == t for p in open_positions):
                    continue
                if current_date not in df.index:
                    continue

                idx_loc = df.index.get_loc(current_date)
                if idx_loc < 10:
                    continue

                row = df.iloc[idx_loc]
                close_p = float(row["Close"])
                ema20 = float(row["EMA20"])
                ema50 = float(row["EMA50"])
                rsi = float(row["RSI14"])
                atr = float(row["ATR14"])
                vol_ratio = float(row["VolRatio"])

                # Entry signal conditions: Uptrend + RSI pullback/breakout (45-68) + Volume
                if (close_p > ema20 > ema50) and (45 <= rsi <= 68) and (vol_ratio >= 1.2):
                    position_amt = min(capital * max_position_size_pct, capital)
                    if position_amt >= 250:
                        shares = int(position_amt // close_p)
                        if shares > 0:
                            cost = shares * close_p
                            capital -= cost
                            sl = max(0.01, round(close_p - (1.5 * atr), 2))
                            tp = round(close_p + (3.0 * atr), 2)

                            open_positions.append({
                                "ticker": t,
                                "entry_date": date_str,
                                "entry_price": close_p,
                                "tp_price": tp,
                                "sl_price": sl,
                                "shares": shares
                            })

        equity_curve.append({
            "date": date_str,
            "equity": round(total_equity, 2)
        })

    # Close remaining open positions at latest price for final accounting
    final_equity = capital
    for pos in open_positions:
        t = pos["ticker"]
        latest_price = float(stock_dfs[t].iloc[-1]["Close"])
        proceeds = pos["shares"] * latest_price
        final_equity += proceeds
        profit_loss = proceeds - (pos["shares"] * pos["entry_price"])
        pnl_pct = ((latest_price - pos["entry_price"]) / pos["entry_price"]) * 100

        closed_trades.append({
            "ticker": t,
            "entry_date": pos["entry_date"],
            "exit_date": str(dates[-1])[:10],
            "entry_price": round(pos["entry_price"], 2),
            "exit_price": round(latest_price, 2),
            "shares": pos["shares"],
            "pnl_usd": round(profit_loss, 2),
            "pnl_pct": round(pnl_pct, 2),
            "result": "WIN" if profit_loss > 0 else "LOSS",
            "reason": "OPEN POSITION (Mark-to-Market)"
        })

    total_trades = len(closed_trades)
    winning_trades = [tr for tr in closed_trades if tr["result"] == "WIN"]
    losing_trades = [tr for tr in closed_trades if tr["result"] == "LOSS"]

    win_rate_pct = round((len(winning_trades) / total_trades * 100), 1) if total_trades > 0 else 0.0
    net_profit_usd = round(final_equity - initial_capital, 2)
    net_profit_pct = round(((final_equity - initial_capital) / initial_capital) * 100, 2)

    total_win_dollars = sum(tr["pnl_usd"] for tr in winning_trades)
    total_loss_dollars = abs(sum(tr["pnl_usd"] for tr in losing_trades))
    profit_factor = round(total_win_dollars / (total_loss_dollars if total_loss_dollars > 0 else 1), 2)

    return {
        "summary": {
            "period": period,
            "initial_capital": initial_capital,
            "final_capital": round(final_equity, 2),
            "net_profit_usd": net_profit_usd,
            "net_profit_pct": net_profit_pct,
            "total_trades": total_trades,
            "wins": len(winning_trades),
            "losses": len(losing_trades),
            "win_rate_pct": win_rate_pct,
            "profit_factor": profit_factor,
            "max_drawdown_pct": round(max_drawdown_pct, 2)
        },
        "trades": closed_trades,
        "equity_curve": equity_curve[::5] # Sample every 5 days for light chart rendering
    }

if __name__ == "__main__":
    print("--- Running Backtest Simulation (1 Year) ---")
    res = run_backtest_simulation(period="1y", initial_capital=10000.0)
    summ = res.get("summary", {})
    print(f"Initial: ${summ.get('initial_capital')} -> Final: ${summ.get('final_capital')} | Net Profit: {summ.get('net_profit_pct')}%")
    print(f"Win Rate: {summ.get('win_rate_pct')}% ({summ.get('wins')}/{summ.get('total_trades')}) | Max Drawdown: {summ.get('max_drawdown_pct')}% | Profit Factor: {summ.get('profit_factor')}")
    print("\nRecent 3 Closed Trades:")
    for tr in res.get("trades", [])[-3:]:
        print(f"  {tr['ticker']} | Entry: ${tr['entry_price']} ({tr['entry_date']}) -> Exit: ${tr['exit_price']} ({tr['exit_date']}) | PnL: ${tr['pnl_usd']} ({tr['pnl_pct']}%) [{tr['result']}]")
