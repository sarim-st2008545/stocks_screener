"""
Unit tests for the Portal Server handlers and REST API endpoints.
"""

import json
from io import BytesIO
from unittest.mock import MagicMock
import pytest

from src import records
from src.portal_server import PortalRequestHandler, get_latest_cached_prices


@pytest.fixture(autouse=True)
def use_test_db(tmp_path, monkeypatch):
    test_db = tmp_path / "portal_test.db"
    records.init_db(test_db)
    monkeypatch.setattr(records, "DB_PATH", test_db)


class MockPortalServer(PortalRequestHandler):
    """Subclass of PortalRequestHandler that mocks network sockets for testing."""
    def __init__(self, method: str, path: str, body: dict = None):
        self.rfile = BytesIO(json.dumps(body).encode("utf-8") if body else b"")
        self.wfile = BytesIO()
        self.command = method
        self.path = path
        self.request_version = "HTTP/1.1"
        self.headers = {"Content-Length": str(len(self.rfile.getvalue()))}
        self.client_address = ("127.0.0.1", 8080)
        self.server = MagicMock()
        self.close_connection = True

        if method == "GET":
            self.do_GET()
        elif method == "POST":
            self.do_POST()

    def send_response(self, code, message=None):
        self.response_code = code

    def send_header(self, keyword, value):
        pass

    def end_headers(self):
        pass

    def get_response_json(self):
        data = self.wfile.getvalue().decode("utf-8")
        return json.loads(data) if data else {}


def test_portal_api_portfolio():
    handler = MockPortalServer("GET", "/api/portfolio")
    assert handler.response_code == 200
    res = handler.get_response_json()
    assert "total_capital" in res
    assert "cash_available" in res
    assert "unrealized_pnl" in res


def test_portal_api_signals():
    handler = MockPortalServer("GET", "/api/signals")
    assert handler.response_code == 200
    res = handler.get_response_json()
    assert isinstance(res, dict)


def test_portal_api_trades_active():
    handler = MockPortalServer("GET", "/api/trades/active")
    assert handler.response_code == 200
    res = handler.get_response_json()
    assert isinstance(res, list)


def test_portal_api_update_capital():
    handler = MockPortalServer("POST", "/api/portfolio", body={"total_capital": 30000.0})
    assert handler.response_code == 200
    res = handler.get_response_json()
    assert res["status"] == "ok"
    assert res["total_capital"] == 30000.0

    # Verify updated
    get_handler = MockPortalServer("GET", "/api/portfolio")
    assert get_handler.get_response_json()["total_capital"] == 30000.0


def test_portal_api_open_and_close_trade():
    open_handler = MockPortalServer("POST", "/api/trades/open", body={
        "system": "galaxy",
        "ticker": "ZETA",
        "shares": 50,
        "entry_price": 30.0,
        "stop_loss": 28.0,
        "target_price": 34.0,
        "entry_date": "2026-09-17"
    })
    assert open_handler.response_code == 200
    res = open_handler.get_response_json()
    assert "trade_id" in res
    trade_id = res["trade_id"]

    # Close trade
    close_handler = MockPortalServer("POST", "/api/trades/close", body={
        "trade_id": trade_id,
        "exit_price": 33.5,
        "exit_reason": "10-SMA Target",
        "exit_date": "2026-09-18"
    })
    assert close_handler.response_code == 200
    assert close_handler.get_response_json()["status"] == "ok"


def test_portal_serves_index_html():
    handler = MockPortalServer("GET", "/")
    assert handler.response_code == 200
    html_output = handler.wfile.getvalue().decode("utf-8")
    assert "<title>Aura Quant Terminal" in html_output
    assert "My Active Trades" in html_output
    assert "Setups Stream" in html_output
    assert "Forecast & Analytics" in html_output


def test_portal_api_analytics():
    handler = MockPortalServer("GET", "/api/analytics")
    assert handler.response_code == 200
    res = handler.get_response_json()
    assert "summary" in res
    assert "forecast" in res
    assert "weekly" in res
    assert "monthly" in res


def test_portal_api_scan():
    handler = MockPortalServer("POST", "/api/scan", body={"target": "none"})
    assert handler.response_code == 200
    res = handler.get_response_json()
    assert res["status"] == "ok"

