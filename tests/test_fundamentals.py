"""Tests for statement construction and ratios.

Two themes dominate. First, derivations must be correct *and* labelled — a
figure computed from an accounting identity is legitimate, but it must not
masquerade as a filed number. Second, degenerate inputs must yield no value
rather than a flattering one: negative equity, negative EBITDA, and loss-making
years all produce ratios that look fine and mean nothing.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.fundamentals import Fundamentals, build

AS_OF = date(2026, 6, 30)
END = "2026-03-31"
FILED = "2026-05-01"


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def instant(concept: str, val: float, end: str = END, filed: str = FILED) -> dict:
    return {"concept": concept, "val": val, "end": end, "filed": filed, "form": "10-Q"}


def annual(
    concept: str,
    val: float,
    start: str = "2025-04-01",
    end: str = END,
    filed: str = FILED,
) -> dict:
    return {
        "concept": concept,
        "val": val,
        "start": start,
        "end": end,
        "filed": filed,
        "form": "10-K",
    }


def payload(*entries: dict) -> dict:
    facts: dict = {}
    for entry in entries:
        entry = dict(entry)
        concept = entry.pop("concept")
        facts.setdefault(concept, {"units": {"USD": []}})
        facts[concept]["units"]["USD"].append(entry)
    return {"facts": {"us-gaap": facts}}


def fundamentals(*entries: dict) -> Fundamentals:
    return build(payload(*entries), "TEST", as_of=AS_OF)


# ---------------------------------------------------------------------------
# Direct resolution
# ---------------------------------------------------------------------------


class TestDirectResolution:
    def test_reads_filed_figures(self):
        f = fundamentals(instant("Assets", 100e9), annual("Revenues", 50e9))
        assert f.assets.value == 100e9
        assert f.assets.source == "Assets"
        assert f.assets.derived is False
        assert f.revenue.value == 50e9

    def test_missing_item_is_none_with_a_reason(self):
        f = fundamentals(instant("Assets", 100e9))
        assert f.revenue.present is False
        assert f.revenue.value is None
        assert "not tagged" in f.revenue.source

    def test_equity_falls_back_to_noncontrolling_variant(self):
        """Two of 41 universe names tag equity only under this variant."""
        f = fundamentals(
            instant("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", 60e9)
        )
        assert f.equity.value == 60e9

    def test_point_in_time_gate_propagates(self):
        f = build(payload(instant("Assets", 100e9, filed="2026-08-01")), "TEST", as_of=AS_OF)
        assert f.assets.present is False


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


class TestDerivation:
    def test_liabilities_derived_from_the_accounting_identity(self):
        """Six of 41 universe names never tag `Liabilities`."""
        f = fundamentals(
            instant("Assets", 100e9),
            instant("StockholdersEquity", 60e9),
        )
        assert f.liabilities.value == pytest.approx(40e9)
        assert f.liabilities.derived is True
        assert "Assets - equity" in f.liabilities.source

    def test_direct_liabilities_preferred_over_derivation(self):
        f = fundamentals(
            instant("Liabilities", 41e9),
            instant("Assets", 100e9),
            instant("StockholdersEquity", 60e9),
        )
        assert f.liabilities.value == 41e9
        assert f.liabilities.derived is False

    def test_derivation_refuses_mismatched_balance_sheets(self):
        """Components from different filing periods must not be combined."""
        f = fundamentals(
            instant("Assets", 100e9, end="2026-03-31"),
            instant("StockholdersEquity", 60e9, end="2025-03-31"),
        )
        assert f.liabilities.present is False

    def test_gross_profit_derived_from_revenue_less_cost(self):
        """Only ~60% of filers tag GrossProfit; a pillar that vanishes for four
        names in ten is worse than one that is derived and says so."""
        f = fundamentals(
            annual("Revenues", 100e9),
            annual("CostOfRevenue", 30e9),
        )
        assert f.gross_profit.value == pytest.approx(70e9)
        assert f.gross_profit.derived is True
        assert f.gross_margin == pytest.approx(0.7)

    def test_reported_gross_profit_wins(self):
        f = fundamentals(
            annual("GrossProfit", 71e9),
            annual("Revenues", 100e9),
            annual("CostOfRevenue", 30e9),
        )
        assert f.gross_profit.value == 71e9
        assert f.gross_profit.derived is False

    def test_free_cash_flow_subtracts_capex_as_an_outflow(self):
        """Capex is filed as a positive number in the cash-flow statement."""
        f = fundamentals(
            annual("NetCashProvidedByUsedInOperatingActivities", 100e9),
            annual("PaymentsToAcquirePropertyPlantAndEquipment", 20e9),
        )
        assert f.free_cash_flow.value == pytest.approx(80e9)

    def test_free_cash_flow_handles_negative_capex_sign(self):
        f = fundamentals(
            annual("NetCashProvidedByUsedInOperatingActivities", 100e9),
            annual("PaymentsToAcquirePropertyPlantAndEquipment", -20e9),
        )
        assert f.free_cash_flow.value == pytest.approx(80e9)

    def test_no_capex_means_no_free_cash_flow(self):
        f = fundamentals(annual("NetCashProvidedByUsedInOperatingActivities", 100e9))
        assert f.free_cash_flow.present is False
        assert "capex" in f.free_cash_flow.source

    def test_ebit_falls_back_to_pretax_plus_interest(self):
        """KLA and Eaton do not tag OperatingIncomeLoss at all."""
        f = fundamentals(
            annual(
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                90e9,
            ),
            annual("InterestExpense", 2e9),
        )
        assert f.ebit.value == pytest.approx(92e9)
        assert f.ebit.derived is True

    def test_ebitda_adds_back_depreciation(self):
        f = fundamentals(
            annual("OperatingIncomeLoss", 100e9),
            annual("DepreciationDepletionAndAmortization", 5e9),
        )
        assert f.ebitda.value == pytest.approx(105e9)

    def test_ebitda_requires_aligned_periods(self):
        f = fundamentals(
            annual("OperatingIncomeLoss", 100e9, start="2025-04-01", end="2026-03-31"),
            annual(
                "DepreciationDepletionAndAmortization",
                5e9,
                start="2023-01-01",
                end="2023-12-31",
            ),
        )
        assert f.ebitda.present is False

    def test_cash_and_investments_sums_only_same_period(self):
        f = fundamentals(
            instant("CashAndCashEquivalentsAtCarryingValue", 10e9),
            instant("ShortTermInvestments", 5e9),
        )
        assert f.cash_and_investments.value == pytest.approx(15e9)

    def test_cash_alone_when_investments_are_from_another_period(self):
        f = fundamentals(
            instant("CashAndCashEquivalentsAtCarryingValue", 10e9, end="2026-03-31"),
            instant("ShortTermInvestments", 5e9, end="2025-03-31"),
        )
        assert f.cash_and_investments.value == pytest.approx(10e9)


# ---------------------------------------------------------------------------
# Debt
# ---------------------------------------------------------------------------


class TestDebt:
    def test_largest_candidate_wins(self):
        """A filer can maintain a narrow tag beside the real total under another
        name. Understating debt flatters leverage; overstating surfaces it."""
        f = fundamentals(
            instant("LongTermDebt", 23e6),
            instant("DebtAndCapitalLeaseObligations", 16.5e9),
        )
        estimate = f.debt()
        assert estimate.value == pytest.approx(16.5e9)
        assert len(estimate.candidates) >= 2

    def test_untagged_debt_is_unknown_not_zero(self):
        """Treating absence as zero would promote levered companies into the
        safe bucket. ANET, ALAB, CRDO and PLTR tag nothing at all."""
        f = fundamentals(instant("Assets", 100e9), instant("StockholdersEquity", 60e9))
        assert f.total_debt.present is False
        assert f.debt_to_equity is None
        assert f.net_debt.present is False

    def test_finance_leases_count_as_debt(self):
        """ARM tags no conventional borrowing, only finance leases. Excluding
        them cost it leverage scoring despite being genuinely unlevered."""
        f = fundamentals(instant("FinanceLeaseLiability", 59e6))
        assert f.total_debt.value == pytest.approx(59e6)
        assert "finance leases" in f.total_debt.source

    def test_split_components_are_summed(self):
        f = fundamentals(
            instant("LongTermDebtNoncurrent", 8e9),
            instant("LongTermDebtCurrent", 1e9),
        )
        assert f.debt().value == pytest.approx(9e9)

    def test_components_from_different_periods_are_rejected(self):
        f = fundamentals(
            instant("LongTermDebtNoncurrent", 8e9, end="2026-03-31"),
            instant("LongTermDebtCurrent", 1e9, end="2025-03-31"),
        )
        assert f.debt().value != pytest.approx(9e9)

    def test_net_debt_can_be_negative_for_a_net_cash_company(self):
        f = fundamentals(
            instant("LongTermDebt", 8e9),
            instant("CashAndCashEquivalentsAtCarryingValue", 13e9),
        )
        assert f.net_debt.value == pytest.approx(-5e9)

    def test_all_candidates_reported_for_audit(self):
        f = fundamentals(
            instant("LongTermDebt", 5e9),
            instant("SeniorNotes", 3e9),
        )
        assert set(f.debt().candidates) >= {"long-term debt", "senior notes"}


# ---------------------------------------------------------------------------
# Ratio guards
# ---------------------------------------------------------------------------


class TestRatioGuards:
    def test_negative_equity_yields_no_roe(self):
        """Two negatives would produce a flattering positive return."""
        f = fundamentals(
            annual("NetIncomeLoss", -5e9),
            instant("StockholdersEquity", -10e9),
        )
        assert f.return_on_equity is None
        assert f.debt_to_equity is None

    def test_negative_ebitda_yields_no_leverage_multiple(self):
        f = fundamentals(
            annual("OperatingIncomeLoss", -10e9),
            annual("DepreciationDepletionAndAmortization", 1e9),
            instant("LongTermDebt", 5e9),
            instant("CashAndCashEquivalentsAtCarryingValue", 1e9),
        )
        assert f.net_debt_to_ebitda is None

    def test_loss_making_year_yields_no_fcf_conversion(self):
        f = fundamentals(
            annual("NetIncomeLoss", -5e9),
            annual("NetCashProvidedByUsedInOperatingActivities", 10e9),
            annual("PaymentsToAcquirePropertyPlantAndEquipment", 2e9),
        )
        assert f.fcf_conversion is None

    def test_zero_denominator_yields_none(self):
        f = fundamentals(annual("Revenues", 0.0), annual("NetIncomeLoss", 5e9))
        assert f.net_margin is None

    def test_negative_margins_are_reported_not_suppressed(self):
        """A loss-making company has a real negative margin; that is a finding."""
        f = fundamentals(annual("Revenues", 100e9), annual("NetIncomeLoss", -10e9))
        assert f.net_margin == pytest.approx(-0.1)

    def test_trivial_interest_expense_yields_no_coverage(self):
        f = fundamentals(annual("OperatingIncomeLoss", 100e9), annual("InterestExpense", 0.0))
        assert f.interest_coverage is None

    def test_quick_ratio_excludes_inventory(self):
        f = fundamentals(
            instant("AssetsCurrent", 100e9),
            instant("InventoryNet", 40e9),
            instant("LiabilitiesCurrent", 30e9),
        )
        assert f.quick_ratio == pytest.approx(2.0)
        assert f.current_ratio == pytest.approx(100 / 30)

    def test_effective_tax_rate_rejects_implausible_values(self):
        f = fundamentals(
            annual("IncomeTaxExpenseBenefit", 90e9),
            annual(
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                100e9,
            ),
        )
        assert f.effective_tax_rate is None  # 90% is not a real rate

    def test_effective_tax_rate_computed_when_sane(self):
        f = fundamentals(
            annual("IncomeTaxExpenseBenefit", 15e9),
            annual(
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                100e9,
            ),
        )
        assert f.effective_tax_rate == pytest.approx(0.15)


class TestROIC:
    def test_computed_from_nopat_over_invested_capital(self):
        f = fundamentals(
            annual("OperatingIncomeLoss", 100e9),
            annual("IncomeTaxExpenseBenefit", 20e9),
            annual(
                "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                100e9,
            ),
            instant("StockholdersEquity", 200e9),
            instant("LongTermDebt", 50e9),
            instant("CashAndCashEquivalentsAtCarryingValue", 50e9),
        )
        # NOPAT 80bn over invested capital 200bn.
        assert f.roic == pytest.approx(0.4, rel=0.01)

    def test_statutory_rate_fallback_is_recorded(self):
        f = fundamentals(
            annual("OperatingIncomeLoss", 100e9),
            instant("StockholdersEquity", 200e9),
        )
        assert f.roic is not None
        assert any("statutory" in n for n in f.coverage()["notes"])

    def test_non_positive_invested_capital_yields_no_roic(self):
        f = fundamentals(
            annual("OperatingIncomeLoss", 100e9),
            instant("StockholdersEquity", 10e9),
            instant("CashAndCashEquivalentsAtCarryingValue", 50e9),
        )
        assert f.invested_capital.present is False
        assert f.roic is None


# ---------------------------------------------------------------------------
# Coverage reporting
# ---------------------------------------------------------------------------


class TestCoverage:
    def test_reports_present_derived_and_missing(self):
        f = fundamentals(
            instant("Assets", 100e9),
            instant("StockholdersEquity", 60e9),
            annual("Revenues", 50e9),
        )
        cov = f.coverage()
        assert cov["line_items_present"] >= 3
        assert "liabilities" in cov["derived"]
        assert "total_debt" in cov["missing_line_items"]
        assert cov["currency"] == "USD"

    def test_empty_payload_reports_everything_missing(self):
        f = build(None, "TEST", as_of=AS_OF)
        cov = f.coverage()
        assert cov["line_items_present"] == 0
        assert cov["ratios_present"] == 0
        assert len(cov["missing_line_items"]) == cov["line_items_total"]

    def test_report_renders_and_is_ascii_safe(self):
        f = fundamentals(instant("Assets", 100e9), annual("Revenues", 50e9))
        text = f.report()
        assert "assets" in text
        text.encode("cp1252")  # must survive a Windows console

    def test_line_item_label_states_provenance(self):
        f = fundamentals(
            instant("Assets", 100e9),
            instant("StockholdersEquity", 60e9),
        )
        assert "derived" in f.liabilities.label()
        assert "unavailable" in f.revenue.label()
