"""Tests for valuation.

Valuation is where a model can most easily be confidently wrong, so these tests
concentrate on the guards: refusing to value what cannot be valued, telling a
cycle apart from a growth ramp, and never presenting a single number as a fair
value.
"""

from __future__ import annotations

from datetime import date

import pytest

from src import valuation
from src.fundamentals import build
from src.valuation import (
    DCFResult,
    ImpliedExpectations,
    dcf,
    growth_estimate,
    implied_expectations,
    multiples,
    normalised_free_cash_flow,
)
from tests.test_fundamentals import annual, instant, payload

AS_OF = date(2026, 6, 30)


def fy(concept: str, val: float, year: int, instant_fact: bool = False) -> dict:
    end = f"{year}-03-31"
    filed = f"{year}-05-01"
    if instant_fact:
        return instant(concept, val, end=end, filed=filed)
    return annual(concept, val, start=f"{year - 1}-04-01", end=end, filed=filed)


def company(entries: list[dict]):
    return build(payload(*entries), "TEST", as_of=AS_OF)


def fcf_history(values: dict[int, float]) -> list[dict]:
    """Free cash flow per fiscal year, via operating cash flow less capex."""
    out: list[dict] = []
    for year, value in values.items():
        out.append(fy("NetCashProvidedByUsedInOperatingActivities", value + 10.0, year))
        out.append(fy("PaymentsToAcquirePropertyPlantAndEquipment", 10.0, year))
        out.append(fy("Revenues", 1000.0, year))
    return out


# ---------------------------------------------------------------------------
# Cycle vs growth
# ---------------------------------------------------------------------------


class TestNormalisation:
    def test_secular_growth_keeps_its_latest_year(self):
        """NVIDIA went from ~$4bn to ~$97bn of free cash flow. Averaging that
        produces $39bn, a figure describing no year it will see again."""
        f = company(fcf_history({2026: 97.0, 2025: 61.0, 2024: 27.0, 2023: 4.0, 2022: 8.0}))
        result = normalised_free_cash_flow(f)
        assert result.was_normalised is False
        assert result.value == pytest.approx(97.0)
        assert "secular growth" in result.basis

    def test_one_dip_does_not_reclassify_a_grower(self):
        """A strict monotonic test called NVIDIA cyclical over its FY2023
        inventory correction."""
        f = company(fcf_history({2026: 97.0, 2025: 61.0, 2024: 27.0, 2023: 4.0, 2022: 8.0}))
        assert normalised_free_cash_flow(f).was_normalised is False

    def test_genuine_cycle_is_normalised(self):
        """Micron: free cash flow near a trough while it builds capacity."""
        f = company(fcf_history({2026: 1.7, 2025: 0.1, 2024: -6.1, 2023: 3.1, 2022: 2.4}))
        result = normalised_free_cash_flow(f)
        assert result.was_normalised is True
        assert result.value < 1.7
        assert result.years_used == 5

    def test_stable_company_uses_its_latest_year(self):
        f = company(fcf_history({2026: 100.0, 2025: 98.0, 2024: 102.0, 2023: 99.0}))
        assert normalised_free_cash_flow(f).was_normalised is False

    def test_negative_mean_refuses_to_value(self):
        f = company(fcf_history({2026: -5.0, 2025: -8.0, 2024: -6.0, 2023: -4.0}))
        result = normalised_free_cash_flow(f)
        assert result.value is None
        assert "negative" in result.basis

    def test_thin_history_falls_back_to_latest(self):
        f = company(fcf_history({2026: 50.0}))
        result = normalised_free_cash_flow(f)
        assert result.value == pytest.approx(50.0)
        assert "insufficient history" in result.basis


class TestGrowthEstimate:
    def test_cagr_from_free_cash_flow(self):
        f = company(fcf_history({2026: 200.0, 2025: 150.0, 2024: 100.0}))
        estimate = growth_estimate(f)
        # 100 -> 200 over two years is about 41%, capped to 25%.
        assert estimate.rate == pytest.approx(0.25)
        assert estimate.capped is True

    def test_growth_is_capped_both_ways(self):
        cap = valuation.config.get("rules.valuation.dcf.max_initial_growth")
        floor = valuation.config.get("rules.valuation.dcf.min_initial_growth")
        fast = company(fcf_history({2026: 1000.0, 2025: 300.0, 2024: 10.0}))
        slow = company(fcf_history({2026: 10.0, 2025: 200.0, 2024: 900.0}))
        assert growth_estimate(fast).rate == pytest.approx(cap)
        assert growth_estimate(slow).rate == pytest.approx(floor)

    def test_no_history_is_not_estimable(self):
        assert growth_estimate(company([])).rate is None


# ---------------------------------------------------------------------------
# DCF
# ---------------------------------------------------------------------------


class TestDCF:
    def solid(self):
        return company(fcf_history({2026: 100.0, 2025: 95.0, 2024: 92.0, 2023: 90.0}))

    def test_returns_a_band_never_a_single_number(self):
        """A DCF reported as one figure is a false-precision machine."""
        result = dcf(self.solid(), wacc=0.10, shares_outstanding=100.0)
        assert result.base is not None
        assert result.low < result.base < result.high
        assert len(result.grid) > 1

    def test_negative_cash_flow_is_refused(self):
        f = company(fcf_history({2026: -10.0, 2025: -12.0, 2024: -8.0}))
        result = dcf(f, wacc=0.10, shares_outstanding=100.0)
        assert result.base is None

    def test_missing_wacc_is_refused(self):
        assert dcf(self.solid(), wacc=None, shares_outstanding=100.0).base is None

    def test_missing_shares_is_refused(self):
        assert dcf(self.solid(), wacc=0.10, shares_outstanding=None).base is None

    def test_terminal_growth_above_wacc_is_excluded(self):
        """The Gordon formula diverges; those grid points must be dropped."""
        result = dcf(self.solid(), wacc=0.03, shares_outstanding=100.0, terminal_growth=0.025)
        for key in result.grid:
            assert result.grid[key] > 0

    def test_higher_wacc_lowers_value(self):
        cheap = dcf(self.solid(), wacc=0.08, shares_outstanding=100.0)
        dear = dcf(self.solid(), wacc=0.14, shares_outstanding=100.0)
        assert cheap.base > dear.base

    def test_caveats_downgrade_reliability(self):
        capped = company(fcf_history({2026: 1000.0, 2025: 300.0, 2024: 10.0}))
        result = dcf(capped, wacc=0.10, shares_outstanding=100.0)
        assert result.caveats
        assert result.reliability != "reasonable"

    def test_reliability_states_weakness_plainly(self):
        result = DCFResult(base=100.0, low=10.0, high=300.0, grid={"a": 1.0})
        assert "weak" in result.reliability

    def test_not_computable_reliability(self):
        assert DCFResult(None, None, None).reliability == "not computable"


# ---------------------------------------------------------------------------
# Reverse DCF
# ---------------------------------------------------------------------------


class TestImpliedExpectations:
    def solid(self):
        return company(fcf_history({2026: 100.0, 2025: 95.0, 2024: 92.0, 2023: 90.0}))

    def test_solves_for_the_growth_the_price_requires(self):
        """The useful inversion: not 'is this worth $200' but 'the market needs
        X% growth for a decade - has this business ever done that'."""
        result = implied_expectations(self.solid(), 0.10, 100.0, price=30.0)
        assert result.implied_growth is not None
        assert -0.5 < result.implied_growth < 0.6

    def test_a_higher_price_implies_higher_growth(self):
        cheap = implied_expectations(self.solid(), 0.10, 100.0, price=20.0)
        dear = implied_expectations(self.solid(), 0.10, 100.0, price=40.0)
        assert dear.implied_growth > cheap.implied_growth

    def test_gap_against_delivered_growth_is_reported(self):
        result = implied_expectations(self.solid(), 0.10, 100.0, price=35.0)
        assert result.historical_growth is not None
        assert result.gap == pytest.approx(result.implied_growth - result.historical_growth)

    def test_implausible_price_is_named_not_silently_capped(self):
        result = implied_expectations(self.solid(), 0.10, 100.0, price=100_000.0)
        assert result.implied_growth is None
        assert "outside any credible band" in result.note

    def test_verdict_language_distinguishes_demanding_from_modest(self):
        demanding = ImpliedExpectations(0.45, 0.10, 0.35)
        modest = ImpliedExpectations(0.05, 0.20, -0.15)
        assert "needs" in demanding.verdict
        assert "not demanding much" in modest.verdict

    def test_missing_inputs_are_refused(self):
        assert implied_expectations(self.solid(), None, 100.0, 30.0).implied_growth is None
        assert implied_expectations(self.solid(), 0.10, None, 30.0).implied_growth is None
        assert implied_expectations(self.solid(), 0.10, 100.0, None).implied_growth is None


# ---------------------------------------------------------------------------
# Multiples
# ---------------------------------------------------------------------------


class TestMultiples:
    def base(self):
        return company(
            [
                fy("Revenues", 1000.0, 2026),
                fy("NetIncomeLoss", 100.0, 2026),
                fy("OperatingIncomeLoss", 150.0, 2026),
                fy("DepreciationDepletionAndAmortization", 20.0, 2026),
                fy("NetCashProvidedByUsedInOperatingActivities", 180.0, 2026),
                fy("PaymentsToAcquirePropertyPlantAndEquipment", 30.0, 2026),
                fy("LongTermDebt", 200.0, 2026, instant_fact=True),
                fy("CashAndCashEquivalentsAtCarryingValue", 50.0, 2026, instant_fact=True),
            ]
        )

    def test_computes_the_standard_set(self):
        result = multiples(self.base(), market_cap=2000.0)
        assert result.pe == pytest.approx(20.0)
        assert result.ev_ebitda is not None
        assert result.ev_sales is not None
        assert result.fcf_yield == pytest.approx(150.0 / 2000.0)

    def test_enterprise_value_includes_net_debt(self):
        result = multiples(self.base(), market_cap=2000.0)
        # EV = 2000 + (200 - 50) = 2150 over sales of 1000.
        assert result.ev_sales == pytest.approx(2.15)

    def test_peg_uses_growth(self):
        result = multiples(self.base(), market_cap=2000.0, growth=0.20)
        assert result.peg == pytest.approx(1.0)

    def test_loss_making_company_has_no_pe(self):
        f = company([fy("Revenues", 1000.0, 2026), fy("NetIncomeLoss", -50.0, 2026)])
        result = multiples(f, market_cap=2000.0)
        assert result.pe is None
        assert result.earnings_yield is not None  # a negative yield is meaningful

    def test_non_usd_filer_is_refused_not_fudged(self):
        """A dollar market cap over euro earnings is a meaningless ratio."""
        data = {
            "facts": {
                "us-gaap": {
                    "Revenues": {
                        "units": {
                            "EUR": [
                                {
                                    "val": 1000.0,
                                    "start": "2025-04-01",
                                    "end": "2026-03-31",
                                    "filed": "2026-05-01",
                                }
                            ]
                        }
                    }
                }
            }
        }
        f = build(data, "ASML", as_of=AS_OF)
        result = multiples(f, market_cap=2000.0)
        assert result.pe is None
        assert any("withheld" in n for n in result.notes)

    def test_missing_market_cap_is_refused(self):
        result = multiples(self.base(), market_cap=None)
        assert result.pe is None
        assert any("market cap" in n for n in result.notes)

    def test_history_comparison_needs_enough_observations(self):
        result = multiples(self.base(), market_cap=2000.0)
        assert "no usable history" in result.versus_own_history("pe")

    def test_history_comparison_labels_expensive_and_cheap(self):
        result = multiples(self.base(), market_cap=2000.0)
        result.history["pe"] = [10.0, 10.0, 10.0]
        assert "expensive vs history" in result.versus_own_history("pe")
        result.history["pe"] = [40.0, 40.0, 40.0]
        assert "cheap vs history" in result.versus_own_history("pe")


# ---------------------------------------------------------------------------
# Cost of capital
# ---------------------------------------------------------------------------


class TestCostOfCapital:
    def test_missing_inputs_mean_no_wacc_and_a_stated_reason(self):
        """A synthetic ticker has no price history, so beta is unestimable and
        the refusal happens there. What matters is that it refuses and says why
        rather than substituting a sector-average beta as if it were measured."""
        f = company([fy("Revenues", 1000.0, 2026)])
        result = valuation.cost_of_capital(f, market_cap=None, as_of=AS_OF)
        assert result.wacc is None
        assert result.notes, "a refusal must carry its reason"

    def test_label_survives_missing_wacc(self):
        f = company([])
        assert "not computable" in valuation.cost_of_capital(f, None, AS_OF).label()


class TestReport:
    def test_report_is_ascii_safe(self):
        f = company(fcf_history({2026: 100.0, 2025: 95.0, 2024: 92.0}))
        v = valuation.value(f, market_cap=2000.0, price=25.0, as_of=AS_OF)
        v.report().encode("cp1252")

    def test_verdict_avoids_implying_a_recommendation(self):
        f = company(fcf_history({2026: 100.0, 2025: 95.0, 2024: 92.0}))
        v = valuation.value(f, market_cap=2000.0, price=25.0, as_of=AS_OF)
        assert "buy" not in v.verdict.lower()
        assert "sell" not in v.verdict.lower()
