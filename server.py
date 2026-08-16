"""
Flask Web Application & API Server for Halal Automated Swing Trading System.
Serves REST APIs for Signals, Backtesting, Trends, and Telegram Alerts.
"""

from __future__ import annotations

import csv
import json
import os
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from config_loader import get_telegram_config, get_llm_config
from universe import DATA_DIR
from signals import get_active_market_signals, generate_stock_signal
from backtest import run_backtest_simulation
from briefs import get_market_trends, generate_trade_brief, load_stock_context
from telegram_bot import send_telegram_message, poll_telegram_updates

app = Flask(__name__, static_folder="static", static_url_path="")

# Background Telegram listener thread handle
_bot_thread = None

def start_telegram_listener():
    global _bot_thread
    if _bot_thread is None or not _bot_thread.is_alive():
        _bot_thread = threading.Thread(target=poll_telegram_updates, daemon=True)
        _bot_thread.start()
        print("[Server] Started background Telegram listener thread.")

@app.route("/")
def index():
    """Serve the single page application."""
    return send_from_directory(app.static_folder, "index.html")

@app.route("/api/status")
def api_status():
    """Return system status and API configuration flags."""
    telegram_ok = get_telegram_config() is not None
    llm_cfg = get_llm_config()

    return jsonify({
        "status": "ok",
        "telegram_configured": telegram_ok,
        "llm_configured": llm_cfg is not None,
        "llm_provider": llm_cfg[0] if llm_cfg else "none"
    })

@app.route("/api/signals")
def api_signals():
    """Return active BUY/SELL signals for Halal investable universe."""
    limit = int(request.args.get("limit", 20))
    signals = get_active_market_signals(limit=limit)
    return jsonify({"total": len(signals), "data": signals})

@app.route("/api/backtest", methods=["POST"])
def api_backtest():
    """Run strategy backtest on historical market data."""
    body = request.json or {}
    period = body.get("period", "1y")
    capital = float(body.get("capital", 10000.0))
    tickers = body.get("tickers") # Optional list

    results = run_backtest_simulation(tickers=tickers, period=period, initial_capital=capital)
    return jsonify(results)

@app.route("/api/trends")
def api_trends():
    """Return global sector hikes and investment trends."""
    return jsonify({"data": get_market_trends()})

@app.route("/api/brief/<ticker>", methods=["GET", "POST"])
def api_brief(ticker: str):
    """Fetch or generate AI swing trade brief."""
    force_refresh = request.method == "POST" or request.args.get("refresh") == "true"
    brief = generate_trade_brief(ticker, force_refresh=force_refresh)
    return jsonify(brief)

@app.route("/api/telegram/signal", methods=["POST"])
def api_telegram_signal():
    """Send formatted BUY/SELL signal alert to Telegram bot."""
    data = request.json or {}
    ticker = data.get("ticker", "").upper()
    if not ticker:
        return jsonify({"error": "Ticker required"}), 400

    sig = generate_stock_signal(ticker)
    if not sig:
        ctx = load_stock_context(ticker)
        comp = ctx.get("compliance") or {}
        msg = (
            f"ℹ️ *Swing Alert: {ticker}*\n"
            f"Company: {comp.get('company_name', ticker)}\n"
            f"Verdict: Shari'ah Compliant ({comp.get('verdict', 'PASS')})\n"
            f"Use `/brief {ticker}` to generate AI swing analysis!"
        )
    else:
        sig_type = sig["signal_type"]
        icon = "🚀" if "BUY" in sig_type else "🔻"
        msg = (
            f"{icon} *{sig_type} SIGNAL: {sig['ticker']}*\n\n"
            f"• *Entry Price*: `${sig['entry_price']}`\n"
            f"• *Target Price (TP)*: `${sig['target_price']}` (`+{sig['target_pct']}%`)\n"
            f"• *Stop Loss (SL)*: `${sig['stop_loss']}` (`{sig['stop_loss_pct']}%`)\n"
            f"• *Risk / Reward*: `1 : {sig['reward_risk_ratio']}`\n"
            f"• *Expected Horizon*: `{sig['horizon_days']}`\n\n"
            f"💡 *Rationale*: {sig['reason']}\n"
        )

    ok = send_telegram_message(msg)
    if ok:
        return jsonify({"status": "success", "message": f"Signal alert for {ticker} sent to Telegram!"})
    else:
        return jsonify({"status": "error", "message": "Failed to send to Telegram. Check TELEGRAM_BOT_TOKEN in .env"}), 500

if __name__ == "__main__":
    start_telegram_listener()
    port = int(os.environ.get("PORT", 5000))
    print(f"\n[SERVER] Automated Halal Swing Assistant active on http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)
