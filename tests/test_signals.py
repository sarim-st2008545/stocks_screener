"""Tests for the decision matrix.

The asymmetries are the point, so they get explicit coverage: a cheap price never
rescues a failing business, a quality-gate failure is disqualifying at any price,
and an unclear reading resolves cautiously rather than toward a buy.
"""

from __future__ import annotations

from datetime import date

import pytest

from src import cycle
from src.signals import (
    BUY_SIDE,
    MATRIX,
    Decision,
    QualityTrend,
    Signal,
    SignalSet,
    ValuationBand,
    quality_trend,
    report,
    valuation_band,
)

AS_OF = date(2026, 8, 17)


# ---------------------------------------------------------------------------
# The matrix itself
# ---------------------------------------------------------------------------


class TestMatrix:
    def test_covers_every_combination(self):
        for trend in (QualityTrend.RISING, QualityTrend.STABLE, QualityTrend.DETERIORATING):
            for band in (
                ValuationBand.UNDERVALUED,
                ValuationBand.FAIR,
                ValuationBand.OVERVALUED,
            ):
                assert (trend, band) in MATRIX

    def test_cheap_and_deteriorating_is_avoid_not_buy(self):
        """The value-trap guard. Cheapness is usually a consequence of
        deterioration, not an opportunity."""
        assert MATRIX[(QualityTrend.DETERIORATING, ValuationBand.UNDERVALUED)] == Decision.AVOID

    def test_best_case_is_rising_and_undervalued(self):
        assert MATRIX[(QualityTrend.RISING, ValuationBand.UNDERVALUED)] == Decision.STRONG_BUY

    def test_worst_case_is_deteriorating_and_overvalued(self):
        assert MATRIX[(QualityTrend.DETERIORATING, ValuationBand.OVERVALUED)] == Decision.EXIT

    def test_no_buy_when_overvalued_whatever_the_quality(self):
        for trend in (QualityTrend.RISING, QualityTrend.STABLE, QualityTrend.DETERIORATING):
            assert MATRIX[(trend, ValuationBand.OVERVALUED)] not in BUY_SIDE

    def test_no_buy_when_quality_is_deteriorating_whatever_the_price(self):
        for band in (
            ValuationBand.UNDERVALUED,
            ValuationBand.FAIR,
            ValuationBand.OVERVALUED,
        ):
            assert MATRIX[(QualityTrend.DETERIORATING, band)] not in BUY_SIDE


# ---------------------------------------------------------------------------
# Valuation band
# ---------------------------------------------------------------------------


class FakeExpectations:
    def __init__(self, implied=None, historical=None, gap=None, note=""):
        self.implied_growth = implied
        self.historical_growth = historical
        self.gap = gap
        self.note = note


class FakeMultiples:
    def __init__(self, verdict="no usable history"):
        self._verdict = verdict

    def versus_own_history(self, name):
        return self._verdict


class FakeDCF:
    def __init__(self, reliability="reasonable"):
        self.reliability = reliability


class FakeValuation:
    def __init__(
        self,
        margin=None,
        reliability="reasonable",
        gap=None,
        implied=None,
        historical=None,
        note="",
        pe_verdict="no usable history",
        required=0.25,
    ):
        self.implied_margin_of_safety = margin
        self.dcf = FakeDCF(reliability)
        self.expectations = FakeExpectations(implied, historical, gap, note)
        self.multiples = FakeMultiples(pe_verdict)
        self.margin_of_safety_required = required


class TestValuationBand:
    def test_clear_margin_reads_undervalued(self):
        band, _, _ = valuation_band(
            FakeValuation(margin=0.40, gap=-0.05, implied=0.05, historical=0.10,
                          pe_verdict="0.6x its own median - cheap vs history")
        )
        assert band == ValuationBand.UNDERVALUED

    def test_demanding_price_reads_overvalued(self):
        band, _, against = valuation_band(
            FakeValuation(margin=-0.60, gap=0.35, implied=0.50, historical=0.15,
                          pe_verdict="2.4x its own median - expensive vs history")
        )
        assert band == ValuationBand.OVERVALUED
        assert against

    def test_weak_dcf_is_not_weighted(self):
        """A conservative DCF sits below price for nearly every name in this
        sector, so leaning on an unreliable one would label the whole universe
        overvalued and discriminate between nothing."""
        band, supporting, _ = valuation_band(
            FakeValuation(
                margin=-0.90,
                reliability="weak - the sensitivity band is wider than the base case",
                gap=-0.02,
                implied=0.08,
                historical=0.10,
                pe_verdict="0.7x its own median - cheap vs history",
            )
        )
        assert band == ValuationBand.UNDERVALUED
        assert any("not weighted" in s for s in supporting)

    def test_ties_resolve_to_the_cautious_side(self):
        """An unclear valuation must not produce a buy."""
        band, _, _ = valuation_band(
            FakeValuation(
                margin=0.40,
                gap=0.35,
                implied=0.50,
                historical=0.15,
                pe_verdict="no usable history",
            )
        )
        assert band == ValuationBand.OVERVALUED

    def test_no_evidence_is_unknown(self):
        band, _, _ = valuation_band(FakeValuation())
        assert band == ValuationBand.UNKNOWN

    def test_price_beyond_any_credible_growth_reads_overvalued(self):
        band, _, against = valuation_band(
            FakeValuation(note="price requires sustained growth above 60%")
        )
        assert band == ValuationBand.OVERVALUED
        assert any("60%" in a for a in against)

    def test_modest_demand_reads_fair(self):
        band, supporting, _ = valuation_band(
            FakeValuation(gap=0.08, implied=0.18, historical=0.10)
        )
        assert band == ValuationBand.FAIR


# ---------------------------------------------------------------------------
# Quality trend
# ---------------------------------------------------------------------------


class FakeSignal:
    def __init__(self, name, passed):
        self.name = name
        self.passed = passed


class FakePiotroski:
    def __init__(self, signals):
        self.signals = signals


class FakeAssessment:
    def __init__(self, signals):
        self.piotroski = FakePiotroski(signals)


class FakeSeries:
    def __init__(self, direction):
        self.direction = direction


class FakePosition:
    def __init__(self, direction="flat"):
        self.gross_margin = FakeSeries(direction)


def yoy(**flags) -> FakeAssessment:
    names = {
        "roa": "ROA improving",
        "margin": "gross margin rising",
        "turnover": "asset turnover rising",
        "leverage": "leverage falling",
    }
    return FakeAssessment([FakeSignal(names[k], v) for k, v in flags.items()])


class TestQualityTrend:
    def test_broad_improvement_reads_rising(self):
        trend, _ = quality_trend(
            yoy(roa=True, margin=True, turnover=True, leverage=True),
            FakePosition("rising"),
            None,
        )
        assert trend == QualityTrend.RISING

    def test_broad_decline_reads_deteriorating(self):
        trend, _ = quality_trend(
            yoy(roa=False, margin=False, turnover=False, leverage=False),
            FakePosition("falling"),
            None,
        )
        assert trend == QualityTrend.DETERIORATING

    def test_high_quality_softening_is_mean_reversion_not_decay(self):
        """Regression: NVIDIA's ratios all fell because assets grew faster than
        income - 75% to 71% gross margin - and reading that as deteriorating
        issued an EXIT on the highest-quality name in the pool."""
        trend, evidence = quality_trend(
            yoy(roa=False, margin=False, turnover=False, leverage=True),
            FakePosition("falling"),
            None,
            quality_percentile=80.0,
        )
        assert trend == QualityTrend.STABLE
        assert any("mean reversion" in e for e in evidence)

    def test_the_same_pattern_in_a_weak_company_still_deteriorates(self):
        trend, _ = quality_trend(
            yoy(roa=False, margin=False, turnover=False, leverage=True),
            FakePosition("falling"),
            None,
            quality_percentile=20.0,
        )
        assert trend == QualityTrend.DETERIORATING

    def test_no_signals_is_unknown(self):
        trend, _ = quality_trend(yoy(), FakePosition("unknown"), None)
        assert trend == QualityTrend.UNKNOWN

    def test_mixed_signals_read_stable(self):
        trend, _ = quality_trend(
            yoy(roa=True, margin=False, turnover=True, leverage=False),
            FakePosition("flat"),
            None,
        )
        assert trend == QualityTrend.STABLE


# ---------------------------------------------------------------------------
# Reporting and the no-action policy
# ---------------------------------------------------------------------------


def signal(ticker: str, decision: str, composite: float = 50.0) -> Signal:
    return Signal(ticker=ticker, as_of=AS_OF, decision=decision, composite=composite)


class TestNoActionPolicy:
    def test_says_no_action_when_nothing_qualifies(self):
        """Confirmed behaviour: do not fall back to ranking the least-stretched
        names as though a relative ordering were an absolute green light."""
        text = report(SignalSet(AS_OF, [signal("A", Decision.HOLD), signal("B", Decision.AVOID)]))
        assert "NO ACTION on individual names" in text
        assert "margin of safety" in text

    def test_directs_contributions_to_the_core_sleeve(self):
        text = report(SignalSet(AS_OF, [signal("A", Decision.HOLD)]))
        assert "core_market" in text

    def test_states_that_no_buys_is_a_normal_outcome(self):
        text = report(SignalSet(AS_OF, [signal("A", Decision.HOLD)]))
        assert "expected output" in text

    def test_no_no_action_banner_when_a_buy_exists(self):
        text = report(SignalSet(AS_OF, [signal("A", Decision.BUY)]))
        assert "NO ACTION on individual names" not in text


class TestReporting:
    def test_buys_sort_above_everything_else(self):
        result = SignalSet(
            AS_OF,
            [
                signal("HOLDME", Decision.HOLD),
                signal("BUYME", Decision.STRONG_BUY),
                signal("EXITME", Decision.EXIT),
            ],
        )
        assert [s.ticker for s in result.ordered()][0] == "BUYME"

    def test_holds_are_hidden_unless_verbose(self):
        result = SignalSet(AS_OF, [signal("QUIET", Decision.HOLD), signal("LOUD", Decision.EXIT)])
        assert "QUIET" not in report(result)
        assert "QUIET" in report(result, verbose=True)

    def test_hidden_count_is_disclosed(self):
        result = SignalSet(AS_OF, [signal("A", Decision.HOLD), signal("B", Decision.EXIT)])
        assert "hidden" in report(result)

    def test_report_states_nothing_is_validated_yet(self):
        text = report(SignalSet(AS_OF, [signal("A", Decision.HOLD)]))
        assert "has not run" in text
        assert "no claim about performance" in text

    def test_report_says_every_call_needs_human_approval(self):
        assert "approve or reject" in report(SignalSet(AS_OF, [signal("A", Decision.HOLD)]))

    def test_report_is_ascii_safe(self):
        report(SignalSet(AS_OF, [signal("A", Decision.EXIT)])).encode("cp1252")

    def test_buys_helper_only_returns_buy_side(self):
        result = SignalSet(
            AS_OF,
            [
                signal("A", Decision.STRONG_BUY),
                signal("B", Decision.ADD),
                signal("C", Decision.HOLD),
                signal("D", Decision.TRIM),
            ],
        )
        assert {s.ticker for s in result.buys()} == {"A", "B"}


class TestSignalPayload:
    def test_signal_report_carries_its_evidence(self):
        s = signal("NVDA", Decision.EXIT)
        s.evidence = ["margin still top-decile"]
        s.contradictions = ["price 156% above fair value"]
        s.falsification = ["quality trend turns back up"]
        text = s.report()
        assert "for:" in text and "against:" in text and "would falsify:" in text

    def test_every_decision_can_state_a_disproof(self):
        """A call without a stated disproof is a tip, not research."""
        from src.signals import falsification_for

        for decision in (
            Decision.STRONG_BUY,
            Decision.BUY,
            Decision.HOLD,
            Decision.TRIM,
            Decision.EXIT,
            Decision.AVOID,
        ):
            lines = falsification_for(
                decision,
                None,
                FakeValuation(implied=0.30),
                cycle.CyclePosition(
                    "X", AS_OF, cycle.Position.STABLE,
                    cycle.Series("gross_margin"), cycle.Series("operating_margin"),
                    cycle.Series("inventory_days"), cycle.Series("capex_intensity"),
                    cycle.Series("revenue"),
                ),
            )
            assert lines, decision
