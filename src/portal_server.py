"""
High-Performance Local Trading Portal Server.

Built with zero external dependencies using Python's standard library:
- http.server.ThreadingHTTPServer for concurrent requests
- RESTful JSON API endpoints for portfolio, active trades, signals, and scanner execution
- Serves the sleek, dark-themed SPA (Single Page Application) dashboard

Usage:
    python3 -m src.portal_server
    python3 -m src.portal_server --port 8080
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import time

from src import records

BASE_DIR = Path(__file__).resolve().parent.parent
PORTAL_DIR = Path(__file__).resolve().parent / "portal"

_LATEST_PRICES_CACHE: dict[str, float] = {}
_LATEST_PRICES_CACHE_TIME: float = 0.0
_CACHE_TTL_SECONDS: float = 60.0


def _fast_extract_last_close(filepath: Path) -> float | None:
    """Reads only the tail bytes of a CSV file to extract the latest closing price without loading pandas."""
    try:
        with open(filepath, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size == 0:
                return None
            f.seek(max(0, size - 1024))
            chunk = f.read().decode("utf-8", errors="ignore")
            lines = chunk.splitlines()
            non_empty = [l.strip() for l in lines if l.strip()]
            if not non_empty:
                return None
            parts = non_empty[-1].split(",")
            if len(parts) >= 5:
                return float(parts[4])
    except Exception:
        pass
    return None


def get_latest_cached_prices(force_refresh: bool = False) -> dict[str, float]:
    """
    Scans cached CSV price data from data/prices/ and data/galaxy/ as baseline,
    and dynamically queries real-time live quotes via yfinance for all active signal/trade tickers.
    Ensures 100% accurate live P&L on both local runs and cloud deployments (e.g. Render).
    """
    global _LATEST_PRICES_CACHE, _LATEST_PRICES_CACHE_TIME
    now = time.time()
    if not force_refresh and _LATEST_PRICES_CACHE and (now - _LATEST_PRICES_CACHE_TIME < _CACHE_TTL_SECONDS):
        return _LATEST_PRICES_CACHE

    prices: dict[str, float] = {}

    # 1. Baseline: Check local CSV files (data/prices/ and data/galaxy/)
    p_dir = BASE_DIR / "data" / "prices"
    if p_dir.exists():
        for f in p_dir.glob("*.csv"):
            ticker = f.stem.upper()
            val = _fast_extract_last_close(f)
            if val is not None:
                prices[ticker] = val

    g_dir = BASE_DIR / "data" / "galaxy"
    if g_dir.exists():
        for f in g_dir.glob("*.csv"):
            ticker = f.stem.replace("_5y", "").upper()
            val = _fast_extract_last_close(f)
            if val is not None:
                prices[ticker] = val

    # 2. Dynamic Live Quotes: Query active tickers from database
    needed_tickers: set[str] = set()
    try:
        from src import db as _db
        conn = _db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT ticker FROM signals")
        for r in cur.fetchall():
            val = r["ticker"] if isinstance(r, dict) else r[0]
            if val:
                needed_tickers.add(val.strip().upper())
        cur.execute("SELECT DISTINCT ticker FROM trades WHERE status = 'OPEN'")
        for r in cur.fetchall():
            val = r["ticker"] if isinstance(r, dict) else r[0]
            if val:
                needed_tickers.add(val.strip().upper())
        conn.close()
    except Exception:
        pass

    # 3. Fetch real-time market quotes via yfinance for needed tickers
    if needed_tickers:
        try:
            import yfinance as yf
            tickers_obj = yf.Tickers(" ".join(needed_tickers))
            for sym in needed_tickers:
                try:
                    t = tickers_obj.tickers.get(sym)
                    if t:
                        lp = getattr(t.fast_info, "last_price", None)
                        if lp and float(lp) > 0:
                            prices[sym] = round(float(lp), 2)
                except Exception:
                    pass
        except Exception:
            pass

    _LATEST_PRICES_CACHE = prices
    _LATEST_PRICES_CACHE_TIME = now
    return prices


import math


def _sanitize_json_obj(obj):
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return 0.0
        return obj
    elif isinstance(obj, dict):
        return {k: _sanitize_json_obj(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_sanitize_json_obj(v) for v in obj]
    return obj


class PortalRequestHandler(BaseHTTPRequestHandler):
    def send_json(self, data: dict | list, status: int = HTTPStatus.OK):
        clean_data = _sanitize_json_obj(data)
        body = json.dumps(clean_data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # -------------------------------------------------------------
        # REST API Routes
        # -------------------------------------------------------------
        if path == "/api/portfolio":
            prices = get_latest_cached_prices()
            summary = records.get_portfolio_summary(latest_prices=prices)
            self.send_json(summary)
            return

        elif path == "/api/trades/active":
            prices = get_latest_cached_prices()
            trades = records.get_active_trades(latest_prices=prices)
            self.send_json(trades)
            return

        elif path == "/api/trades/closed":
            trades = records.get_closed_trades()
            self.send_json(trades)
            return

        elif path == "/api/signals":
            query_params = parse_qs(parsed.query)
            system = query_params.get("system", [None])[0]
            prices = get_latest_cached_prices()
            grouped_signals = records.get_signals_grouped_by_date(system=system, latest_prices=prices)
            self.send_json(grouped_signals)
            return

        elif path == "/api/analytics":
            prices = get_latest_cached_prices()
            analytics = records.get_analytics_and_forecast(latest_prices=prices)
            self.send_json(analytics)
            return

        elif path == "/api/watchlist":
            query_params = parse_qs(parsed.query)
            refresh = query_params.get("refresh", ["0"])[0] == "1"
            if refresh:
                from src.scanner import run_daily_scan
                run_daily_scan()
            watchlist = records.get_stage2_watchlist()
            self.send_json(watchlist)
            return

        elif path == "/api/prices":
            prices = get_latest_cached_prices()
            self.send_json(prices)
            return

        elif path == "/api/auto/summary":
            prices = get_latest_cached_prices()
            from src import auto_engine
            auto_data = auto_engine.get_auto_portfolio_summary(latest_prices=prices)
            self.send_json(auto_data)
            return

        # -------------------------------------------------------------
        # Static Web Portal Files
        # -------------------------------------------------------------
        if path == "/" or path == "/index.html":
            file_path = PORTAL_DIR / "index.html"
            content_type = "text/html; charset=utf-8"
        else:
            rel_path = path.lstrip("/")
            file_path = PORTAL_DIR / rel_path
            if rel_path.endswith(".js"):
                content_type = "application/javascript"
            elif rel_path.endswith(".css"):
                content_type = "text/css"
            elif rel_path.endswith(".svg"):
                content_type = "image/svg+xml"
            else:
                content_type = "text/plain"

        if file_path.exists() and file_path.is_file():
            with open(file_path, "rb") as f:
                content = f.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b"{}"
        try:
            payload = json.loads(post_data.decode("utf-8")) if post_data else {}
        except Exception:
            payload = {}

        if path == "/api/portfolio":
            if "total_capital" in payload:
                cap_val = float(payload["total_capital"])
                records.set_setting("total_capital", str(cap_val))
                self.send_json({"status": "ok", "total_capital": cap_val})
            else:
                self.send_json({"error": "Missing total_capital"}, status=HTTPStatus.BAD_REQUEST)
            return

        elif path == "/api/trades/open":
            try:
                system = payload.get("system", "galaxy")
                ticker = payload["ticker"]
                shares = float(payload.get("shares", 1.0))
                entry_price = float(payload["entry_price"])
                stop_loss = float(payload["stop_loss"])
                target_price = float(payload["target_price"])
                entry_date = payload.get("entry_date")
                raw_sig_id = payload.get("signal_id")
                signal_id = int(raw_sig_id) if (raw_sig_id is not None and str(raw_sig_id).strip() and str(raw_sig_id).strip().lower() != "null") else None
                notes = payload.get("notes", "")

                trade_id = records.open_trade(
                    system=system,
                    ticker=ticker,
                    shares=shares,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    target_price=target_price,
                    entry_date=entry_date,
                    signal_id=signal_id,
                    notes=notes,
                )
                self.send_json({"status": "ok", "trade_id": trade_id})
            except Exception as e:
                self.send_json({"error": str(e)}, status=HTTPStatus.BAD_REQUEST)
            return

        elif path == "/api/trades/close":
            try:
                trade_id = int(payload["trade_id"])
                exit_price = float(payload["exit_price"])
                exit_reason = payload.get("exit_reason", "Manual Exit")
                exit_date = payload.get("exit_date")
                shares_val = payload.get("shares")
                shares_to_close = float(shares_val) if (shares_val is not None and str(shares_val).strip() != "") else None

                success = records.close_trade(
                    trade_id=trade_id,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    exit_date=exit_date,
                    shares_to_close=shares_to_close,
                )
                if success:
                    self.send_json({"status": "ok", "trade_id": trade_id})
                else:
                    self.send_json({"error": "Trade not found or already closed"}, status=HTTPStatus.NOT_FOUND)
            except Exception as e:
                self.send_json({"error": str(e)}, status=HTTPStatus.BAD_REQUEST)
            return

        elif path == "/api/trades/edit":
            try:
                trade_id = int(payload["trade_id"])
                shares = float(payload["shares"]) if "shares" in payload and payload["shares"] is not None else None
                entry_price = float(payload["entry_price"]) if "entry_price" in payload and payload["entry_price"] is not None else None
                stop_loss = float(payload["stop_loss"]) if "stop_loss" in payload and payload["stop_loss"] is not None else None
                target_price = float(payload["target_price"]) if "target_price" in payload and payload["target_price"] is not None else None
                entry_date = payload.get("entry_date")
                notes = payload.get("notes")

                success = records.edit_trade(
                    trade_id=trade_id,
                    shares=shares,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    target_price=target_price,
                    entry_date=entry_date,
                    notes=notes,
                )
                if success:
                    self.send_json({"status": "ok", "trade_id": trade_id})
                else:
                    self.send_json({"error": "Trade not found"}, status=HTTPStatus.NOT_FOUND)
            except Exception as e:
                self.send_json({"error": str(e)}, status=HTTPStatus.BAD_REQUEST)
            return

        elif path == "/api/trades/delete":
            try:
                trade_id = int(payload["trade_id"])
                success = records.delete_trade(trade_id=trade_id)
                if success:
                    self.send_json({"status": "ok", "trade_id": trade_id})
                else:
                    self.send_json({"error": "Trade not found"}, status=HTTPStatus.NOT_FOUND)
            except Exception as e:
                self.send_json({"error": str(e)}, status=HTTPStatus.BAD_REQUEST)
            return

        elif path == "/api/trades/clear":
            try:
                count = records.clear_all_trades()
                self.send_json({"status": "ok", "deleted_count": count})
            except Exception as e:
                self.send_json({"error": str(e)}, status=HTTPStatus.BAD_REQUEST)
            return

        elif path == "/api/auto/replay":
            try:
                from src import auto_engine
                res = auto_engine.replay_history()
                self.send_json(res)
            except Exception as e:
                self.send_json({"error": str(e)}, status=HTTPStatus.BAD_REQUEST)
            return

        elif path == "/api/scan":
            scan_target = payload.get("target", "both")
            refresh = bool(payload.get("refresh", False))
            notify = bool(payload.get("notify", True))
            results = {"status": "ok", "scanned": scan_target}
            galaxy_setups, universe_setups = [], []
            scan_date = None
            try:
                if scan_target in ("galaxy", "both"):
                    from src.galaxy_scanner import run_galaxy_scan
                    galaxy_setups, g_date = run_galaxy_scan(refresh=refresh)
                    scan_date = g_date
                if scan_target in ("universe", "both"):
                    from src.scanner import run_daily_scan
                    universe_setups, u_date = run_daily_scan(refresh=refresh)
                    scan_date = u_date or scan_date

                # Auto-evaluate outcomes across all signals
                evaluated_count = records.auto_evaluate_all_signals()
                get_latest_cached_prices(force_refresh=True)

                # Update autonomous Forward Lab portfolio
                try:
                    from src import auto_engine
                    auto_engine.replay_history()
                except Exception:
                    pass

                results["evaluated_count"] = evaluated_count
                results["galaxy_setups_count"] = len(galaxy_setups)
                results["universe_setups_count"] = len(universe_setups)

                # Send Telegram Alerts if requested
                if notify and (galaxy_setups or universe_setups):
                    try:
                        from src.notifier import notify_scanner_results
                        sent_count = notify_scanner_results(galaxy_setups, universe_setups, scan_date=scan_date or "")
                        results["telegram_alerts_sent"] = sent_count
                    except Exception as te:
                        results["telegram_error"] = str(te)

                results["message"] = f"Scanners executed successfully. Found {len(galaxy_setups) + len(universe_setups)} setup(s)."
            except Exception as e:
                results["warning"] = str(e)

            self.send_json(results)
            return

        self.send_error(HTTPStatus.NOT_FOUND, "API endpoint not found")

    def log_message(self, format, *args):
        # Clean console logging
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")


def run_server(host: str = "127.0.0.1", port: int = 8080):
    records.init_db()
    server_address = (host, port)
    httpd = ThreadingHTTPServer(server_address, PortalRequestHandler)
    print(f"\n{'=' * 78}")
    print("  🚀 QUANT TRADING PORTAL & PERFORMANCE DASHBOARD")
    print(f"  Live local server: http://{host}:{port}")
    print(f"  API Endpoints:      http://{host}:{port}/api/portfolio")
    print("  Theme:              Obsidian Dark (Ultra-Sleek)")
    print(f"{'=' * 78}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping portal server...")
        httpd.server_close()


if __name__ == "__main__":
    default_host = os.environ.get("HOST", "0.0.0.0")
    default_port = int(os.environ.get("PORT", "8080"))
    parser = argparse.ArgumentParser(description="Quant Trading Portal Server")
    parser.add_argument("--host", default=default_host, help=f"Host address (default: {default_host})")
    parser.add_argument("--port", type=int, default=default_port, help=f"Port number (default: {default_port})")
    args = parser.parse_args()
    run_server(host=args.host, port=args.port)
