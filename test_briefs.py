"""
Unit tests for Layer 4: briefs.py
"""

import sys
from pathlib import Path
import json

from briefs import get_market_trends, load_stock_context, generate_fallback_brief, generate_trade_brief

def test_market_trends():
    trends = get_market_trends()
    assert len(trends) >= 3, "Should have at least 3 global market themes"
    mem_chips = next((t for t in trends if "Memory" in t["title"]), None)
    assert mem_chips is not None, "Memory Chips theme must exist"
    assert "MU" in mem_chips["halal_tickers"] or "NVDA" in mem_chips["halal_tickers"], "Memory chips tickers should include MU or NVDA"
    print("  ok  market trends loaded")

def test_load_context():
    ctx = load_stock_context("MU")
    assert ctx["ticker"] == "MU"
    print("  ok  stock context loaded for MU")

def test_fallback_brief():
    ctx = load_stock_context("MU")
    brief = generate_fallback_brief("MU", ctx)
    assert brief["ticker"] == "MU"
    assert brief["is_fallback"] is True
    assert "Executive Summary" in brief["content_markdown"] or "Swing Trade Thesis" in brief["content_markdown"]
    print("  ok  fallback brief generated")

if __name__ == "__main__":
    print("--- briefs tests ---")
    test_market_trends()
    test_load_context()
    test_fallback_brief()
    print("\nALL BRIEFS TESTS PASSED")
