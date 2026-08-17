"""Tests for the 13F institutional-positioning layer.

Two themes. The parsing must survive real EDGAR variety — hundreds of filing
agents produce inconsistent namespaces, units and duplicate lines. And the layer
must never be able to produce something stronger than a confidence adjustment,
because 13F cannot support one.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.holdings_13f import (
    CLUSTER_MIN_FILERS,
    MIN_CONVICTION_WEIGHT,
    Action,
    Corroboration,
    FilerQuarter,
    Holding,
    _normalise_name,
    corroborate,
    diff_quarters,
    parse_information_table,
    quarter_end_for,
)

NS = 'xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable"'


def infotable(*rows: str, namespaced: bool = True) -> str:
    header = f"<informationTable {NS}>" if namespaced else "<informationTable>"
    return header + "".join(rows) + "</informationTable>"


def row(
    issuer: str,
    cusip: str,
    value: str,
    shares: str,
    put_call: str | None = None,
) -> str:
    option = f"<putCall>{put_call}</putCall>" if put_call else ""
    return (
        "<infoTable>"
        f"<nameOfIssuer>{issuer}</nameOfIssuer>"
        f"<cusip>{cusip}</cusip>"
        f"<value>{value}</value>"
        f"<shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt></shrsOrPrnAmt>"
        f"{option}"
        "</infoTable>"
    )


def parse(xml: str) -> list[Holding]:
    return parse_information_table(xml, "Test Fund", 1, date(2026, 6, 30), date(2026, 8, 14))


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestParsing:
    def test_reads_a_namespaced_table(self):
        holdings = parse(infotable(row("NVIDIA CORP", "67066G104", "1500000000", "8000000")))
        assert len(holdings) == 1
        assert holdings[0].issuer == "NVIDIA CORP"
        assert holdings[0].cusip == "67066G104"
        assert holdings[0].shares == 8_000_000

    def test_reads_a_table_without_a_namespace(self):
        holdings = parse(
            infotable(row("MICRON TECHNOLOGY INC", "595112103", "1490000000", "1500000"),
                      namespaced=False)
        )
        assert len(holdings) == 1

    def test_values_reported_in_thousands_are_scaled(self):
        """Filings reported values in thousands before 2023 and whole dollars
        after, and both still appear in historical data."""
        thousands = parse(infotable(row("X CORP", "111111111", "1500", "100")))
        dollars = parse(infotable(row("X CORP", "111111111", "1500000000", "100")))
        assert thousands[0].value_usd == pytest.approx(1_500_000)
        assert dollars[0].value_usd == pytest.approx(1_500_000_000)

    def test_options_are_flagged_as_derivatives(self):
        holdings = parse(
            infotable(row("NVIDIA CORP", "67066G104", "500000", "1000", put_call="Put"))
        )
        assert holdings[0].is_derivative is True

    def test_derivatives_are_excluded_from_equity_view(self):
        """A put line is indistinguishable from a hedge on an unreported long
        book, so it cannot be read as a directional position."""
        holdings = parse(
            infotable(
                row("A CORP", "111111111", "1000000", "100"),
                row("B CORP", "222222222", "2000000", "200", put_call="Put"),
            )
        )
        quarter = FilerQuarter("Test Fund", 1, date(2026, 6, 30), None, holdings)
        assert len(quarter.holdings) == 2
        assert len(quarter.equity_only()) == 1

    def test_malformed_xml_returns_nothing_rather_than_raising(self):
        assert parse("<informationTable><infoTable>truncated") == []

    def test_rows_missing_required_fields_are_skipped(self):
        xml = infotable(
            "<infoTable><nameOfIssuer>NO CUSIP</nameOfIssuer><value>100</value></infoTable>",
            row("GOOD CORP", "333333333", "1000000", "100"),
        )
        holdings = parse(xml)
        assert len(holdings) == 1
        assert holdings[0].issuer == "GOOD CORP"

    def test_non_numeric_values_are_skipped(self):
        holdings = parse(infotable(row("BAD CORP", "444444444", "n/a", "100")))
        assert holdings == []


class TestAggregation:
    def test_duplicate_cusip_lines_are_summed(self):
        """Regression: Coatue reports Taiwan Semiconductor on two lines. Keying a
        dict on CUSIP without summing discarded one, understating the position and
        creating a phantom 'trimmed' the next quarter."""
        holdings = parse(
            infotable(
                row("TAIWAN SEMICONDUCTOR", "874039100", "1650000000", "6000000"),
                row("TAIWAN SEMICONDUCTOR", "874039100", "1240000000", "4000000"),
            )
        )
        quarter = FilerQuarter("Test Fund", 1, date(2026, 6, 30), None, holdings)
        merged = quarter.by_cusip()
        assert len(merged) == 1
        assert merged["874039100"].shares == 10_000_000
        assert merged["874039100"].value_usd == pytest.approx(2_890_000_000)


class TestPointInTime:
    def test_quarter_end_respects_the_45_day_deadline(self):
        # On 1 May the March quarter is not yet due, so December is the latest.
        assert quarter_end_for(date(2026, 5, 1)) == date(2025, 12, 31)
        # By mid-May it is.
        assert quarter_end_for(date(2026, 5, 20)) == date(2026, 3, 31)

    def test_august_sees_the_june_quarter(self):
        assert quarter_end_for(date(2026, 8, 17)) == date(2026, 6, 30)


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------


def quarter(
    name: str, positions: dict[str, tuple[float, float]], quarter_end: date
) -> FilerQuarter:
    holdings = [
        Holding(name, 1, quarter_end, None, f"{cusip} CORP", cusip, value, shares)
        for cusip, (value, shares) in positions.items()
    ]
    return FilerQuarter(name, 1, quarter_end, None, holdings)


class TestDiff:
    def test_new_position_detected(self):
        current = quarter("F", {"AAA": (1e9, 1000)}, date(2026, 6, 30))
        previous = quarter("F", {}, date(2026, 3, 31))
        changes = diff_quarters(current, previous)
        assert changes[0].action == Action.NEW

    def test_added_and_trimmed_detected(self):
        current = quarter("F", {"AAA": (2e9, 2000), "BBB": (5e8, 500)}, date(2026, 6, 30))
        previous = quarter("F", {"AAA": (1e9, 1000), "BBB": (1e9, 1000)}, date(2026, 3, 31))
        actions = {c.cusip: c.action for c in diff_quarters(current, previous)}
        assert actions["AAA"] == Action.ADDED
        assert actions["BBB"] == Action.TRIMMED

    def test_exit_detected(self):
        current = quarter("F", {}, date(2026, 6, 30))
        previous = quarter("F", {"AAA": (1e9, 1000)}, date(2026, 3, 31))
        changes = diff_quarters(current, previous)
        assert changes[0].action == Action.EXITED
        assert changes[0].shares_after == 0

    def test_small_drift_is_held_not_a_decision(self):
        """A 2% move is flow or rounding, and treating it as a decision turns
        every filing into a wall of noise."""
        current = quarter("F", {"AAA": (1.02e9, 1020)}, date(2026, 6, 30))
        previous = quarter("F", {"AAA": (1e9, 1000)}, date(2026, 3, 31))
        assert diff_quarters(current, previous)[0].action == Action.HELD

    def test_no_previous_quarter_makes_everything_new(self):
        current = quarter("F", {"AAA": (1e9, 1000), "BBB": (1e9, 1000)}, date(2026, 6, 30))
        assert all(c.action == Action.NEW for c in diff_quarters(current, None))

    def test_weight_is_computed_against_the_filers_own_book(self):
        current = quarter("F", {"AAA": (2e9, 100), "BBB": (8e9, 100)}, date(2026, 6, 30))
        changes = {c.cusip: c for c in diff_quarters(current, None)}
        assert changes["AAA"].weight_after == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# Corroboration
# ---------------------------------------------------------------------------


def change(filer: str, action: str, weight: float, cusip: str = "AAA"):
    from src.holdings_13f import PositionChange

    return PositionChange(filer, cusip, "A CORP", action, 100, 200, 1e9, weight)


class TestCorroboration:
    def test_cluster_requires_enough_conviction_buyers(self):
        changes = {
            f"Fund {i}": [change(f"Fund {i}", Action.ADDED, 0.05)]
            for i in range(CLUSTER_MIN_FILERS)
        }
        result = corroborate("AAA", "AAA", changes, date(2026, 6, 30))
        assert result.is_cluster is True
        assert len(result.conviction_buyers) == CLUSTER_MIN_FILERS

    def test_immaterial_positions_do_not_form_a_cluster(self):
        """Regression: Citadel appears in almost every universe name because it
        runs thousands of positions. Presence is not conviction, and counting it
        inflated every cluster."""
        changes = {
            f"Fund {i}": [change(f"Fund {i}", Action.ADDED, MIN_CONVICTION_WEIGHT / 10)]
            for i in range(CLUSTER_MIN_FILERS + 2)
        }
        result = corroborate("AAA", "AAA", changes, date(2026, 6, 30))
        assert result.is_cluster is False
        assert result.conviction_buyers == []
        assert "immaterial" in result.confidence_adjustment

    def test_one_buyer_is_not_a_cluster(self):
        changes = {"Solo": [change("Solo", Action.NEW, 0.08)]}
        result = corroborate("AAA", "AAA", changes, date(2026, 6, 30))
        assert result.is_cluster is False
        assert "weak support" in result.confidence_adjustment

    def test_consensus_exit_detected(self):
        changes = {
            f"Fund {i}": [change(f"Fund {i}", Action.EXITED, 0.0)]
            for i in range(CLUSTER_MIN_FILERS)
        }
        result = corroborate("AAA", "AAA", changes, date(2026, 6, 30))
        assert result.is_consensus_exit is True
        assert "review" in result.confidence_adjustment

    def test_no_holders_is_reported_as_none(self):
        result = corroborate("AAA", "AAA", {}, date(2026, 6, 30))
        assert result.confidence_adjustment.startswith("none")

    def test_unmapped_cusip_is_refused_not_guessed(self):
        result = corroborate("AAA", None, {}, date(2026, 6, 30))
        assert "cannot be matched" in result.notes[0]

    def test_staleness_is_always_disclosed(self):
        changes = {"Solo": [change("Solo", Action.ADDED, 0.08)]}
        result = corroborate("AAA", "AAA", changes, date(2026, 6, 30))
        assert any("long-only" in n for n in result.notes)

    def test_confidence_adjustment_is_words_not_a_multiplier(self):
        """Deliberately not numeric, so it cannot be silently multiplied into a
        composite and mistaken for a fundamental measurement."""
        result = corroborate("AAA", "AAA", {}, date(2026, 6, 30))
        assert isinstance(result.confidence_adjustment, str)
        assert not hasattr(Corroboration, "score")


class TestNameNormalisation:
    def test_corporate_forms_are_stripped(self):
        assert _normalise_name("NVIDIA CORPORATION") == _normalise_name("Nvidia Corp")

    def test_cik_suffix_is_stripped(self):
        assert _normalise_name("COATUE MANAGEMENT LLC  (CIK 0001135730)") == "coatuemanagement"

    def test_distinct_companies_stay_distinct(self):
        assert _normalise_name("MICRON TECHNOLOGY INC") != _normalise_name("MICROSOFT CORP")
