"""
Unit tests for the Signal Recording Database and Portfolio Trade Tracker.
"""

from datetime import date, timedelta
from pathlib import Path
import pytest
import pandas as pd
import numpy as np

from src import records


@pytest.fixture
def test_db(tmp_path):
    """Creates a temporary test database."""
    db_file = tmp_path / "test_records.db"
    records.init_db(db_path=db_file)
    return db_file


def test_init_db_creates_tables_and_settings(test_db):
    conn = records.get_connection(test_db)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {row["name"] for row in cur.fetchall()}
    conn.close()

    assert "settings" in tables
    assert "signals" in tables
    assert "trades" in tables

    # Default capital should be initialized
    cap = records.get_setting("total_capital", db_path=test_db)
    assert float(cap) == 10000.0


def test_portfolio_capital_get_and_set(test_db):
    records.set_setting("total_capital", "25000.0", db_path=test_db)
    val = records.get_setting("total_capital", db_path=test_db)
    assert float(val) == 25000.0


def test_record_scanner_signals_deduplication(test_db):
    sample_setups = [
        {
            "ticker": "PATH",
            "name": "UiPath Inc.",
            "segment": "AI Software",
            "signal": "RSI-2 Panic Capitulation",
            "price": 12.50,
            "stop": 11.80,
            "target": 13.90,
            "stop_pct": 5.6,
            "target_pct": 11.2,
            "rr": 2.0,
            "shariah": "Verified Halal",
        },
        {
            "ticker": "ZETA",
            "name": "Zeta Global",
            "segment": "AI Marketing",
            "signal": "RSI-2 Panic Capitulation",
            "price": 18.00,
            "stop": 17.00,
            "target": 20.00,
            "stop_pct": 5.5,
            "target_pct": 11.1,
            "rr": 2.0,
            "shariah": "Verified Halal",
        }
    ]

    scan_date = "2026-09-18"
    count1 = records.record_scanner_signals("galaxy", sample_setups, scan_date=scan_date, db_path=test_db)
    assert count1 == 2

    # Running the scanner a second time on the same date with updated prices must NOT duplicate
    sample_setups[0]["price"] = 12.55
    count2 = records.record_scanner_signals("galaxy", sample_setups, scan_date=scan_date, db_path=test_db)
    assert count2 == 2

    grouped = records.get_signals_grouped_by_date(system="galaxy", db_path=test_db)
    assert scan_date in grouped
    assert len(grouped[scan_date]) == 2
    # Verify price was updated in place
    path_sig = [s for s in grouped[scan_date] if s["ticker"] == "PATH"][0]
    assert path_sig["price"] == 12.55


def test_open_and_close_trade_flow(test_db):
    # Log a signal first
    records.record_scanner_signals("galaxy", [{
        "ticker": "PATH",
        "price": 12.50,
        "stop": 11.80,
        "target": 13.90,
        "stop_pct": 5.6,
        "target_pct": 11.2,
        "signal": "RSI-2 Capitulation",
    }], scan_date="2026-09-18", db_path=test_db)

    grouped = records.get_signals_grouped_by_date(db_path=test_db)
    sig_id = grouped["2026-09-18"][0]["id"]

    # Open trade linked to this signal
    trade_id = records.open_trade(
        system="galaxy",
        ticker="PATH",
        shares=100,
        entry_price=12.50,
        stop_loss=11.80,
        target_price=13.90,
        entry_date="2026-09-15",  # 3 days ago
        signal_id=sig_id,
        db_path=test_db,
    )
    assert trade_id > 0

    # Check active trades
    latest_prices = {"PATH": 13.20}
    active = records.get_active_trades(latest_prices=latest_prices, db_path=test_db)
    assert len(active) == 1
    t = active[0]
    assert t["ticker"] == "PATH"
    assert t["days_held"] >= 3
    assert t["islamic_qabd_met"] is True
    assert t["unrealized_pnl"] == pytest.approx((13.20 - 12.50) * 100, rel=1e-3)
    assert t["unrealized_pnl_pct"] == pytest.approx(((13.20 - 12.50) / 12.50) * 100, rel=1e-3)

    # Check portfolio summary while open
    summary = records.get_portfolio_summary(latest_prices=latest_prices, db_path=test_db)
    assert summary["active_trades_count"] == 1
    assert summary["deployed_capital"] == 1250.0
    assert summary["unrealized_pnl"] == pytest.approx(70.0, rel=1e-3)

    # Close trade at target
    closed_ok = records.close_trade(
        trade_id=trade_id,
        exit_price=13.90,
        exit_reason="10-SMA Target",
        exit_date="2026-09-18",
        db_path=test_db,
    )
    assert closed_ok is True

    # Check closed trade
    closed = records.get_closed_trades(db_path=test_db)
    assert len(closed) == 1
    c = closed[0]
    assert c["exit_reason"] == "10-SMA Target"
    assert c["pnl_amount"] == pytest.approx((13.90 - 12.50) * 100, rel=1e-3)  # +$140.00
    assert c["pnl_pct"] == pytest.approx(((13.90 - 12.50) / 12.50) * 100, rel=1e-3)

    # Signal status should be updated to WON
    grouped_after = records.get_signals_grouped_by_date(db_path=test_db)
    assert grouped_after["2026-09-18"][0]["status"] == "WON"

    # Check portfolio stats after close
    summary_after = records.get_portfolio_summary(db_path=test_db)
    assert summary_after["active_trades_count"] == 0
    assert summary_after["closed_trades_count"] == 1
    assert summary_after["win_rate"] == 100.0
    assert summary_after["realized_pnl"] == pytest.approx(140.0, rel=1e-3)


def test_evaluate_signals_outcomes(test_db):
    records.record_scanner_signals("galaxy", [{
        "ticker": "CRDO",
        "price": 20.00,
        "stop": 18.00,
        "target": 22.00,
        "stop_pct": 10.0,
        "target_pct": 10.0,
        "signal": "RSI-2 Capitulation",
    }], scan_date="2026-09-10", db_path=test_db)

    # Mock price history after 2026-09-10
    dates = pd.date_range("2026-09-10", periods=6, freq="D")
    df_hist = pd.DataFrame({
        "open": [20.0, 20.2, 20.5, 21.0, 22.5, 23.0],
        "high": [20.3, 20.7, 21.2, 21.8, 22.8, 23.2],
        "low":  [19.8, 20.0, 20.1, 20.6, 21.5, 22.0],
        "close": [20.1, 20.5, 21.0, 21.7, 22.6, 23.0],
    }, index=dates)

    updated = records.evaluate_signals_outcomes({"CRDO": df_hist}, db_path=test_db)
    assert updated == 1

    grouped = records.get_signals_grouped_by_date(db_path=test_db)
    sig = grouped["2026-09-10"][0]
    assert sig["status"] == "HIT_TARGET"
    assert sig["outcome_pnl_pct"] == pytest.approx(10.0, rel=1e-3)


def test_get_analytics_and_forecast(test_db):
    # Seed signals across different dates
    records.record_scanner_signals("galaxy", [{
        "ticker": "PATH",
        "price": 12.00,
        "stop": 11.00,
        "target": 13.50,
        "stop_pct": 8.3,
        "target_pct": 12.5,
        "signal": "RSI-2 Capitulation",
    }], scan_date="2026-09-10", db_path=test_db)

    records.record_scanner_signals("universe", [{
        "ticker": "NVDA",
        "price": 200.00,
        "stop": 190.00,
        "target": 220.00,
        "stop_pct": 5.0,
        "target_pct": 10.0,
        "signal": "Stage 2 Dip",
    }], scan_date="2026-09-17", db_path=test_db)

    # Open a trade
    records.open_trade(
        system="galaxy",
        ticker="PATH",
        shares=100,
        entry_price=12.00,
        stop_loss=11.00,
        target_price=13.50,
        entry_date="2026-09-10",
        db_path=test_db
    )

    analytics = records.get_analytics_and_forecast(db_path=test_db)
    assert "summary" in analytics
    assert "forecast" in analytics
    assert "weekly" in analytics
    assert "monthly" in analytics
    assert analytics["summary"]["system_total_signals"] == 2
    assert analytics["summary"]["actual_trades_count"] == 1
    assert "forecast_30d_pnl" in analytics["forecast"]


def test_edit_delete_and_clear_trades(test_db):
    # Open trade with typo (e.g. 10 shares instead of 1)
    trade_id = records.open_trade(
        system="galaxy",
        ticker="INOD",
        shares=10,
        entry_price=15.00,
        stop_loss=14.00,
        target_price=17.00,
        entry_date="2026-09-17",
        db_path=test_db,
    )
    assert trade_id > 0

    active = records.get_active_trades(db_path=test_db)
    assert len(active) == 1
    assert active[0]["shares"] == 10
    assert active[0]["entry_price"] == 15.00

    # Edit trade to 1 share and updated target
    edited = records.edit_trade(
        trade_id=trade_id,
        shares=1,
        entry_price=15.25,
        stop_loss=14.20,
        target_price=17.50,
        notes="Corrected shares to 1",
        db_path=test_db,
    )
    assert edited is True

    active_after_edit = records.get_active_trades(db_path=test_db)
    assert len(active_after_edit) == 1
    t = active_after_edit[0]
    assert t["shares"] == 1
    assert t["entry_price"] == 15.25
    assert t["stop_loss"] == 14.20
    assert t["target_price"] == 17.50
    assert t["notes"] == "Corrected shares to 1"

    # Delete trade
    deleted = records.delete_trade(trade_id=trade_id, db_path=test_db)
    assert deleted is True
    assert len(records.get_active_trades(db_path=test_db)) == 0

    # Open another trade and test clear_all_trades
    records.open_trade(
        system="galaxy",
        ticker="PATH",
        shares=5,
        entry_price=12.00,
        stop_loss=11.00,
        target_price=13.50,
        db_path=test_db,
    )
    assert len(records.get_active_trades(db_path=test_db)) == 1
    cleared_count = records.clear_all_trades(db_path=test_db)
    assert cleared_count >= 1
    assert len(records.get_active_trades(db_path=test_db)) == 0


