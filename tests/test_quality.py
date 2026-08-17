"""Tests for quality and financial-strength assessment.

The recurring theme: a signal that could not be computed must never be counted
as a failed signal. Scoring absence as failure penalises companies for their
filers' tagging habits, and it is the easiest way for a screen to look rigorous
while ranking on data availability instead of quality.
"""

from __future__ import annotations

from datetime import date

import pytest

from src import quality
from src.fundamentals import build
from src.quality import PiotroskiScore, Signal, altman, assess, piotroski
from tests.test_fundamentals import annual, instant, payload

AS_OF = date(2026, 6, 30)


def company(entries: list[dict], as_of: date = AS_OF):
    return build(payload(*entries), "TEST", as_of=as_of)


def two_years(current: list[dict], prior: list[dict]):
    """A payload holding two fiscal years, both filed before as_of."""
    return company(current + prior)


# Fiscal-year scaffolding: FY2026 ends 2026-03-31, FY2025 ends 2025-03-31.
def fy(concept: str, val: float, year: int, instant_fact: bool = False) -> dict:
    end = f"{year}-03-31"
    filed = f"{year}-05-01"
    if instant_fact:
        return instant(concept, val, end=end, filed=filed)
    return annual(concept, val, start=f"{year - 1}-04-01", end=end, filed=filed)


# ---------------------------------------------------------------------------
# Score bookkeeping
# ---------------------------------------------------------------------------


class TestScoreBookkeeping:
    def test_unevaluable_signals_are_not_failures(self):
        score = PiotroskiScore(
            [
                Signal("a", True),
                Signal("b", False),
                Signal("c", None),
            ]
        )
        assert score.score == 1
        assert score.evaluable == 2
        assert score.normalised == pytest.approx(4.5)

    def test_normalisation_keeps_names_comparable(self):
        """Reporting a bare '6' is ambiguous when two signals were skipped."""
        full = PiotroskiScore([Signal(str(i), i < 6) for i in range(9)])
        partial = PiotroskiScore([Signal(str(i), i < 4) for i in range(6)])
        assert full.score == 6 and partial.score == 4
        assert full.normalised == pytest.approx(6.0)
        assert partial.normalised == pytest.approx(6.0)

    def test_nothing_evaluable_yields_no_score(self):
        score = PiotroskiScore([Signal("a", None), Signal("b", None)])
        assert score.normalised is None
        assert score.assessment == "not evaluable"
        assert "not evaluable" in score.label()

    def test_assessment_bands(self):
        strong = PiotroskiScore([Signal(str(i), True) for i in range(9)])
        weak = PiotroskiScore([Signal(str(i), False) for i in range(9)])
        assert strong.assessment == "high quality"
        assert weak.assessment == "weak"


# ---------------------------------------------------------------------------
# Piotroski signals
# ---------------------------------------------------------------------------


class TestPiotroski:
    def improving(self):
        return two_years(
            current=[
                fy("Assets", 1000.0, 2026, instant_fact=True),
                fy("NetIncomeLoss", 120.0, 2026),
                fy("NetCashProvidedByUsedInOperatingActivities", 200.0, 2026),
                fy("Revenues", 900.0, 2026),
                fy("CostOfRevenue", 400.0, 2026),
                fy("LongTermDebt", 50.0, 2026, instant_fact=True),
                fy("AssetsCurrent", 400.0, 2026, instant_fact=True),
                fy("LiabilitiesCurrent", 100.0, 2026, instant_fact=True),
            ],
            prior=[
                fy("Assets", 900.0, 2025, instant_fact=True),
                fy("NetIncomeLoss", 50.0, 2025),
                fy("NetCashProvidedByUsedInOperatingActivities", 80.0, 2025),
                fy("Revenues", 700.0, 2025),
                fy("CostOfRevenue", 400.0, 2025),
                fy("LongTermDebt", 100.0, 2025, instant_fact=True),
                fy("AssetsCurrent", 300.0, 2025, instant_fact=True),
                fy("LiabilitiesCurrent", 150.0, 2025, instant_fact=True),
            ],
        )

    def test_improving_company_scores_well(self):
        score = piotroski(self.improving())
        assert score.score >= 7
        assert score.evaluable >= 8

    def test_positive_roa_and_cfo_detected(self):
        score = piotroski(self.improving())
        by_name = {s.name: s for s in score.signals}
        assert by_name["positive ROA"].passed is True
        assert by_name["positive operating cash flow"].passed is True

    def test_loss_making_fails_profitability(self):
        f = company(
            [
                fy("Assets", 1000.0, 2026, instant_fact=True),
                fy("NetIncomeLoss", -50.0, 2026),
                fy("NetCashProvidedByUsedInOperatingActivities", -20.0, 2026),
            ]
        )
        by_name = {s.name: s for s in piotroski(f).signals}
        assert by_name["positive ROA"].passed is False
        assert by_name["positive operating cash flow"].passed is False

    def test_accruals_signal_compares_cash_to_accounting_earnings(self):
        f = company(
            [
                fy("Assets", 1000.0, 2026, instant_fact=True),
                fy("NetIncomeLoss", 100.0, 2026),
                fy("NetCashProvidedByUsedInOperatingActivities", 50.0, 2026),
            ]
        )
        by_name = {s.name: s for s in piotroski(f).signals}
        # CFO/assets 0.05 below ROA 0.10 — earnings ahead of cash.
        assert by_name["cash earnings exceed accruals"].passed is False

    def test_share_issuance_detected(self):
        entries = [
            fy("Assets", 1000.0, 2026, instant_fact=True),
            fy("NetIncomeLoss", 100.0, 2026),
            fy("Assets", 900.0, 2025, instant_fact=True),
            fy("NetIncomeLoss", 80.0, 2025),
        ]
        data = payload(*entries)
        data["facts"]["dei"] = {
            "EntityCommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        {"val": 100.0, "end": "2025-03-31", "filed": "2025-05-01"},
                        {"val": 130.0, "end": "2026-03-31", "filed": "2026-05-01"},
                    ]
                }
            }
        }
        f = build(data, "TEST", as_of=AS_OF)
        by_name = {s.name: s for s in piotroski(f).signals}
        assert by_name["no new share issuance"].passed is False

    def test_small_share_drift_is_not_issuance(self):
        """Buyback and vesting noise should not read as a capital raise."""
        entries = [
            fy("Assets", 1000.0, 2026, instant_fact=True),
            fy("NetIncomeLoss", 100.0, 2026),
            fy("Assets", 900.0, 2025, instant_fact=True),
        ]
        data = payload(*entries)
        data["facts"]["dei"] = {
            "EntityCommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        {"val": 100.0, "end": "2025-03-31", "filed": "2025-05-01"},
                        {"val": 101.0, "end": "2026-03-31", "filed": "2026-05-01"},
                    ]
                }
            }
        }
        f = build(data, "TEST", as_of=AS_OF)
        by_name = {s.name: s for s in piotroski(f).signals}
        assert by_name["no new share issuance"].passed is True

    def test_no_prior_year_leaves_yoy_signals_unevaluable(self):
        """A recent IPO cannot have year-over-year signals; inventing them
        would be worse than reporting fewer."""
        f = company(
            [
                fy("Assets", 1000.0, 2026, instant_fact=True),
                fy("NetIncomeLoss", 100.0, 2026),
                fy("NetCashProvidedByUsedInOperatingActivities", 150.0, 2026),
            ]
        )
        score = piotroski(f)
        by_name = {s.name: s for s in score.signals}
        assert by_name["ROA improving"].passed is None
        assert by_name["gross margin rising"].passed is None
        assert score.evaluable < 9

    def test_always_returns_nine_signals(self):
        f = company([])
        assert len(piotroski(f).signals) == 9

    def test_empty_company_is_wholly_unevaluable(self):
        score = piotroski(company([]))
        assert score.evaluable == 0
        assert score.normalised is None


# ---------------------------------------------------------------------------
# Altman
# ---------------------------------------------------------------------------


class TestAltman:
    def healthy(self) -> list[dict]:
        return [
            fy("Assets", 1000.0, 2026, instant_fact=True),
            fy("AssetsCurrent", 500.0, 2026, instant_fact=True),
            fy("LiabilitiesCurrent", 200.0, 2026, instant_fact=True),
            fy("Liabilities", 300.0, 2026, instant_fact=True),
            fy("StockholdersEquity", 700.0, 2026, instant_fact=True),
            fy("RetainedEarningsAccumulatedDeficit", 400.0, 2026, instant_fact=True),
            fy("OperatingIncomeLoss", 200.0, 2026),
            fy("Revenues", 900.0, 2026),
        ]

    def test_healthy_company_lands_in_the_safe_zone(self):
        score = altman(company(self.healthy()))
        assert score.value is not None
        assert score.zone == "safe"
        assert score.variant == "Z''"

    def test_distressed_company_flagged(self):
        entries = [
            fy("Assets", 1000.0, 2026, instant_fact=True),
            fy("AssetsCurrent", 100.0, 2026, instant_fact=True),
            fy("LiabilitiesCurrent", 500.0, 2026, instant_fact=True),
            fy("Liabilities", 950.0, 2026, instant_fact=True),
            fy("StockholdersEquity", 50.0, 2026, instant_fact=True),
            fy("RetainedEarningsAccumulatedDeficit", -300.0, 2026, instant_fact=True),
            fy("OperatingIncomeLoss", -100.0, 2026),
            fy("Revenues", 400.0, 2026),
        ]
        score = altman(company(entries))
        assert score.zone == "distress"

    def test_z_double_prime_is_always_primary(self):
        """Chosen deliberately: capex intensity separates fabless designers from
        integrated manufacturers cleanly, but hyperscalers now run 35% of
        revenue on data-centre capex and would be misclassified. Z'' is the
        variant built for cross-industry comparability."""
        capital_heavy = self.healthy() + [
            fy("PropertyPlantAndEquipmentNet", 600.0, 2026, instant_fact=True)
        ]
        score = altman(company(capital_heavy), market_cap=5000.0)
        assert score.variant == "Z''"
        assert "capital-intensive" in score.reason

    def test_original_z_reported_as_a_cross_check(self):
        score = altman(company(self.healthy()), market_cap=5000.0)
        assert score.cross_check is not None
        assert score.agrees is not None
        assert "Z " in score.label()

    def test_no_market_cap_means_no_cross_check(self):
        score = altman(company(self.healthy()))
        assert score.cross_check is None
        assert score.agrees is None
        assert "not computable without a market cap" in score.reason

    def test_disagreement_between_variants_is_surfaced(self):
        """Intel reads grey on book equity and safe on market equity — the
        market pricing a recovery the balance sheet does not yet show."""
        entries = [
            fy("Assets", 1000.0, 2026, instant_fact=True),
            fy("AssetsCurrent", 300.0, 2026, instant_fact=True),
            fy("LiabilitiesCurrent", 250.0, 2026, instant_fact=True),
            fy("Liabilities", 600.0, 2026, instant_fact=True),
            fy("StockholdersEquity", 400.0, 2026, instant_fact=True),
            fy("RetainedEarningsAccumulatedDeficit", 100.0, 2026, instant_fact=True),
            fy("OperatingIncomeLoss", 10.0, 2026),
            fy("Revenues", 500.0, 2026),
        ]
        score = altman(company(entries), market_cap=100_000.0)
        assert score.agrees is False
        assert "DISAGREES" in score.label()

    def test_missing_inputs_are_named(self):
        score = altman(company([fy("Assets", 1000.0, 2026, instant_fact=True)]))
        assert score.value is None
        assert score.missing
        assert "not evaluable" in score.label()


# ---------------------------------------------------------------------------
# Full assessment
# ---------------------------------------------------------------------------


class TestAssessment:
    def solid(self) -> list[dict]:
        return [
            fy("Assets", 1000.0, 2026, instant_fact=True),
            fy("AssetsCurrent", 500.0, 2026, instant_fact=True),
            fy("LiabilitiesCurrent", 200.0, 2026, instant_fact=True),
            fy("Liabilities", 300.0, 2026, instant_fact=True),
            fy("StockholdersEquity", 700.0, 2026, instant_fact=True),
            fy("RetainedEarningsAccumulatedDeficit", 400.0, 2026, instant_fact=True),
            fy("LongTermDebt", 100.0, 2026, instant_fact=True),
            fy("CashAndCashEquivalentsAtCarryingValue", 150.0, 2026, instant_fact=True),
            fy("OperatingIncomeLoss", 200.0, 2026),
            fy("NetIncomeLoss", 150.0, 2026),
            fy("Revenues", 900.0, 2026),
            fy("CostOfRevenue", 400.0, 2026),
            fy("NetCashProvidedByUsedInOperatingActivities", 220.0, 2026),
            fy("PaymentsToAcquirePropertyPlantAndEquipment", 30.0, 2026),
            fy("InterestExpense", 10.0, 2026),
            fy("DepreciationDepletionAndAmortization", 40.0, 2026),
        ]

    def test_produces_a_complete_picture(self):
        # One fiscal year only, so the six year-over-year signals cannot be
        # judged and the three single-year ones can.
        a = assess(company(self.solid()), market_cap=5000.0)
        assert a.piotroski.evaluable == 3
        assert a.altman.value is not None
        assert a.roic is not None
        assert a.fcf_conversion is not None
        assert a.balance_sheet["interest coverage"].startswith("safe")

    def test_spread_unavailable_without_a_cost_of_capital(self):
        """The sign of the spread is the whole finding, so a guessed hurdle
        rate would be worse than reporting nothing."""
        a = assess(company(self.solid()))
        assert a.wacc is None
        assert a.roic_wacc_spread is None
        assert a.creates_value is None
        assert any("cost of capital" in n for n in a.notes)

    def test_spread_computed_when_wacc_supplied(self):
        a = assess(company(self.solid()), wacc=0.09)
        assert a.roic_wacc_spread == pytest.approx(a.roic - 0.09)
        assert a.creates_value is True

    def test_negative_spread_fails_the_value_test(self):
        a = assess(company(self.solid()), wacc=0.95)
        assert a.creates_value is False

    def test_heavy_capex_reframes_low_fcf_conversion(self):
        """Micron converts 0.20 while spending 42% of revenue on capacity, yet
        passes the accruals test. That is an investment phase, not accounting
        weakness, and the two must not read the same."""
        entries = [
            fy("Assets", 1000.0, 2026, instant_fact=True),
            fy("NetIncomeLoss", 100.0, 2026),
            fy("Revenues", 500.0, 2026),
            fy("NetCashProvidedByUsedInOperatingActivities", 220.0, 2026),
            fy("PaymentsToAcquirePropertyPlantAndEquipment", 200.0, 2026),
        ]
        a = assess(company(entries))
        assert a.fcf_conversion is not None and a.fcf_conversion < 0.6
        assert "investment phase" in a.fcf_assessment

    def test_low_conversion_without_capex_is_a_red_flag(self):
        entries = [
            fy("Assets", 1000.0, 2026, instant_fact=True),
            fy("NetIncomeLoss", 100.0, 2026),
            fy("Revenues", 500.0, 2026),
            fy("NetCashProvidedByUsedInOperatingActivities", 40.0, 2026),
            fy("PaymentsToAcquirePropertyPlantAndEquipment", 5.0, 2026),
        ]
        a = assess(company(entries))
        assert "red flag" in a.fcf_assessment

    def test_untagged_debt_is_noted_not_scored_as_zero(self):
        entries = [
            fy("Assets", 1000.0, 2026, instant_fact=True),
            fy("StockholdersEquity", 700.0, 2026, instant_fact=True),
            fy("NetIncomeLoss", 100.0, 2026),
        ]
        a = assess(company(entries))
        assert any("unknown, not zero" in n for n in a.notes)

    def test_net_cash_company_does_not_produce_absurd_roic(self):
        """Palantir reported 382% purely because subtracting its cash left a
        near-zero denominator."""
        entries = [
            fy("Assets", 1000.0, 2026, instant_fact=True),
            fy("StockholdersEquity", 500.0, 2026, instant_fact=True),
            fy("CashAndCashEquivalentsAtCarryingValue", 480.0, 2026, instant_fact=True),
            fy("OperatingIncomeLoss", 100.0, 2026),
            fy("Revenues", 900.0, 2026),
        ]
        f = company(entries)
        assert f.roic is not None and f.roic < 0.5
        assert "cash not deducted" in f.invested_capital.source

    def test_report_renders_and_is_ascii_safe(self):
        a = assess(company(self.solid()), market_cap=5000.0, wacc=0.09)
        text = a.report()
        assert "quality assessment" in text
        text.encode("cp1252")

    def test_empty_company_survives_assessment(self):
        a = assess(company([]))
        assert a.piotroski.evaluable == 0
        assert a.altman.value is None
        assert a.distress_risk == "not evaluable"
