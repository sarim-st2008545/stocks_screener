"""Tests for the research note.

Rendering code, so the tests are about honesty rather than arithmetic: the note
must name what it could not compute, must not imply a recommendation exists, and
must survive a Windows console.
"""

from __future__ import annotations

from datetime import date

from src import quality
from src.fundamentals import Fundamentals, build as build_fundamentals
from src.note import Note, render
from tests.test_fundamentals import annual, instant, payload

AS_OF = date(2026, 6, 30)


def note(entries: list[dict], wacc: float | None = None, market_cap: float | None = None) -> Note:
    f = build_fundamentals(payload(*entries), "TEST", as_of=AS_OF)
    return Note(
        ticker="TEST",
        as_of=AS_OF,
        constituent=None,
        fundamentals=f,
        assessment=quality.assess(f, market_cap=market_cap, wacc=wacc),
        market_cap=market_cap,
        price=100.0,
    )


SOLID = [
    instant("Assets", 1000e6),
    instant("Liabilities", 300e6),
    instant("StockholdersEquity", 700e6),
    instant("RetainedEarningsAccumulatedDeficit", 400e6),
    instant("AssetsCurrent", 500e6),
    instant("LiabilitiesCurrent", 200e6),
    instant("CashAndCashEquivalentsAtCarryingValue", 150e6),
    instant("LongTermDebt", 100e6),
    annual("Revenues", 900e6),
    annual("CostOfRevenue", 400e6),
    annual("OperatingIncomeLoss", 200e6),
    annual("NetIncomeLoss", 150e6),
    annual("NetCashProvidedByUsedInOperatingActivities", 220e6),
    annual("PaymentsToAcquirePropertyPlantAndEquipment", 30e6),
]


class TestRendering:
    def test_renders_every_section(self):
        text = render(note(SOLID))
        for heading in (
            "UNIVERSE ELIGIBILITY",
            "STATEMENTS",
            "RATIOS",
            "QUALITY FRAMEWORKS",
            "DATA QUALITY",
            "NOT YET BUILT",
        ):
            assert heading in text

    def test_is_ascii_safe(self):
        render(note(SOLID)).encode("cp1252")

    def test_marks_derived_figures(self):
        text = render(note(SOLID))
        assert "derived" in text
        assert "AssetsCurrent - LiabilitiesCurrent" in text

    def test_names_unavailable_items_rather_than_hiding_them(self):
        text = render(note([instant("Assets", 1000e6)]))
        assert "unavailable" in text

    def test_states_no_recommendation_exists(self):
        """The note must not read as though a buy/sell call had been withheld."""
        text = render(note(SOLID))
        assert "No buy or sell recommendation exists yet" in text

    def test_lists_unbuilt_phases(self):
        text = render(note(SOLID))
        assert "valuation" in text and "backtest" in text

    def test_reports_the_point_in_time_gate(self):
        text = render(note(SOLID))
        assert "point-in-time gate" in text

    def test_spread_shown_when_wacc_given(self):
        text = render(note(SOLID, wacc=0.09))
        assert "clears its cost of capital" in text

    def test_spread_absent_without_wacc(self):
        text = render(note(SOLID))
        assert "WACC n/a" in text

    def test_variant_disagreement_is_explained(self):
        text = render(note(SOLID, market_cap=500_000e6))
        if "DISAGREES" in text:
            assert "different stories" in text

    def test_error_note_renders_cleanly(self):
        n = Note("XXXX", AS_OF, None, None, None, None, None, error="not an SEC filer")
        assert render(n) == "XXXX: not an SEC filer"

    def test_empty_company_still_renders(self):
        text = render(note([]))
        assert "NOT YET BUILT" in text
