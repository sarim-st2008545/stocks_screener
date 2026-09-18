"""Tests for Telegram notification formatting and dispatch."""

from unittest.mock import patch, MagicMock
from src.notifier import format_signal_alert, notify_scanner_results, send_telegram_message


def test_format_signal_alert_galaxy():
    sig = {
        "ticker": "PATH",
        "price": 12.50,
        "stop": 11.80,
        "target": 13.90,
        "stop_pct": 5.6,
        "target_pct": 11.2,
        "signal": "RSI-2 Capitulation",
        "segment": "AI Automation",
        "rr": 2.0
    }
    card = format_signal_alert(sig, system="galaxy")
    assert "PATH" in card
    assert "GALAXY v2" in card
    assert "$12.50" in card
    assert "$13.90" in card
    assert "$11.80" in card
    assert "1 : 2.0" in card


def test_format_signal_alert_universe():
    sig = {
        "ticker": "NVDA",
        "price": 200.0,
        "stop": 190.0,
        "t1": 220.0,
        "stop_pct": 5.0,
        "t1_pct": 10.0,
        "signal": "Stage 2 10-EMA Dip",
        "segment": "AI Accelerators",
        "rr": 2.0
    }
    card = format_signal_alert(sig, system="universe")
    assert "NVDA" in card
    assert "UNIVERSE SWING" in card
    assert "$200.00" in card
    assert "$220.00" in card


@patch("src.notifier.requests.post")
def test_send_telegram_message_mock(mock_post):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True}
    mock_post.return_value = mock_resp

    ok = send_telegram_message("Hello Test", bot_token="dummy_token", chat_id="12345")
    assert ok is True
    assert mock_post.called


@patch("src.notifier.send_telegram_message")
def test_notify_scanner_results(mock_send):
    mock_send.return_value = True
    g_setups = [{
        "ticker": "INOD", "price": 15.0, "stop": 14.0, "target": 17.0,
        "stop_pct": 6.7, "target_pct": 13.3, "signal": "RSI-2 Capitulation", "rr": 2.0
    }]
    u_setups = []

    sent = notify_scanner_results(g_setups, u_setups, scan_date="2026-09-18")
    assert sent == 1
    assert mock_send.call_count == 2  # 1 header + 1 setup card
