"""Tests for the dashboard.

Rendering only, so the tests are about honesty and safety rather than arithmetic:
the page must not imply validation that has not happened, must not lose a recorded
thesis, and must escape whatever it is handed.
"""

from __future__ import annotations

from datetime import date

import pytest

from src import portfolio as portfolio_mod, signals as signals_mod
from src.dashboard import Snapshot, gather, render, write

AS_OF = date(2026, 8, 17)
PRICES = {"NVDA": 200.0, "VTI": 400.0, "SOXX": 500.0, "GLD": 400.0}


def ledger_with_position() -> portfolio_mod.Ledger:
    ledger = portfolio_mod.Ledger(wallet_size=1000.0, cash=800.0, opened=AS_OF.isoformat())
    ledger.positions.append(
        portfolio_mod.Position(
            "NVDA",
            portfolio_mod.Sleeve.SATELLITE,
            1.0,
            200.0,
            AS_OF.isoformat(),
            thesis="HBM demand and CUDA moat",
            falsification="gross margin falls two years running",
        )
    )
    return ledger


def snapshot(
    signals: list[dict] | None = None,
    ledger: portfolio_mod.Ledger | None = None,
    plan: portfolio_mod.Plan | None = None,
    backtest: str | None = None,
) -> Snapshot:
    return Snapshot(
        as_of=AS_OF,
        signals=signals or [],
        scores={},
        ledger=ledger or portfolio_mod.Ledger(wallet_size=1000.0, cash=1000.0),
        plan=plan,
        prices_by_ticker=PRICES,
        backtest_text=backtest,
    )


def row(ticker: str, decision: str, **kwargs) -> dict:
    base = {
        "ticker": ticker,
        "decision": decision,
        "quality": "stable",
        "valuation": "overvalued",
        "cycle": "PEAK",
        "composite": 70.0,
        "price": 200.0,
        "implied_growth": 0.35,
        "confidence": "moderate",
        "evidence": ["margins strong"],
        "contradictions": ["price demands 35% growth"],
        "falsification": ["quality gate fails"],
    }
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Honesty
# ---------------------------------------------------------------------------


class TestHonesty:
    def test_says_nothing_is_validated_when_no_backtest_exists(self):
        text = render(snapshot())
        assert "Nothing validated yet" in text
        assert "no claim about performance" in text

    def test_labels_a_backtest_as_hypothetical(self):
        text = render(snapshot(backtest="CAGR 30%"))
        assert "not actual trading results" in text.lower()
        assert "upper bound" in text

    def test_no_qualifying_buys_is_stated_plainly(self):
        text = render(snapshot(signals=[row("NVDA", "HOLD")]))
        assert "No qualifying buys" in text
        assert "expected output" in text

    def test_no_banner_when_a_buy_exists(self):
        text = render(snapshot(signals=[row("NVDA", "BUY")]))
        assert "No qualifying buys" not in text

    def test_states_a_high_score_is_not_a_buy(self):
        text = render(snapshot(signals=[row("NVDA", "HOLD")]))
        assert "high score is not a buy" in text

    def test_footer_disclaims_advice(self):
        assert "Not investment advice" in render(snapshot())

    def test_says_it_only_formats_saved_data(self):
        assert "cannot drift" in render(snapshot())


class TestHoldings:
    def test_recorded_thesis_is_shown(self):
        """The reason for holding is the whole point of the ledger, so it must
        survive to the page."""
        text = render(snapshot(ledger=ledger_with_position()))
        assert "HBM demand and CUDA moat" in text
        assert "gross margin falls two years running" in text

    def test_no_positions_says_so(self):
        assert "Nothing has been bought" in render(snapshot())

    def test_sleeve_drift_is_flagged_outside_the_band(self):
        text = render(snapshot())
        assert "outside band" in text

    def test_values_and_pnl_render(self):
        text = render(snapshot(ledger=ledger_with_position()))
        assert "$200.00" in text  # cost basis


class TestProposals:
    def plan(self) -> portfolio_mod.Plan:
        signal_set = signals_mod.SignalSet(AS_OF, [])
        return portfolio_mod.build_plan(
            signal_set,
            portfolio_mod.Ledger(wallet_size=1000.0, cash=1000.0),
            PRICES,
            as_of=AS_OF,
        )

    def test_proposals_are_numbered_for_the_approval_command(self):
        text = render(snapshot(plan=self.plan()))
        assert "--approve" in text

    def test_explains_that_rejections_are_logged(self):
        text = render(snapshot(plan=self.plan()))
        assert "weaker link" in text

    def test_thesis_breaches_are_surfaced(self):
        plan = self.plan()
        plan.breaches = ["NVDA: decision is EXIT - recorded thesis was 'margins expanding'"]
        text = render(snapshot(plan=plan))
        assert "Thesis breaches" in text
        assert "margins expanding" in text


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


class TestSafety:
    def test_content_is_escaped(self):
        ledger = portfolio_mod.Ledger(wallet_size=1000.0, cash=1000.0)
        ledger.positions.append(
            portfolio_mod.Position(
                "X", portfolio_mod.Sleeve.CORE, 1.0, 10.0, AS_OF.isoformat(),
                thesis="<script>alert('x')</script>",
            )
        )
        text = render(snapshot(ledger=ledger))
        assert "<script>alert" not in text
        assert "&lt;script&gt;" in text

    def test_missing_values_render_as_a_dash_not_none(self):
        text = render(snapshot(signals=[row("X", "HOLD", composite=None, implied_growth=None)]))
        assert ">None<" not in text

    def test_both_themes_are_defined(self):
        text = render(snapshot())
        assert "prefers-color-scheme" in text
        assert '[data-theme="dark"]' in text

    def test_page_has_a_title(self):
        assert "<title>" in render(snapshot())

    def test_writes_a_file(self, tmp_path):
        path = write(tmp_path / "d.html", as_of=AS_OF, signal_set=signals_mod.SignalSet(AS_OF, []))
        assert path.exists()
        assert path.read_text(encoding="utf-8").startswith("<title>")


class TestGather:
    def test_gathers_without_signals(self, tmp_path, monkeypatch):
        result = gather(as_of=AS_OF, signal_set=signals_mod.SignalSet(AS_OF, []))
        assert result.as_of == AS_OF
        assert result.plan is None  # no signals means no plan to build
