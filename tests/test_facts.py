"""Tests for point-in-time XBRL fact resolution.

The point-in-time tests are the ones that matter most. If the `as_of` gate
leaks a fact filed after the as-of date, every backtest the project produces is
measuring a strategy that could see the future — and it will look excellent
while being worthless. These tests exist to make that failure loud.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.facts import Fact, FactSet, Window, align_windows, ratio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def payload(*entries: dict) -> dict:
    """Build a companyfacts-shaped payload from loose entries.

    Each entry needs at least `concept`, `val`, `end`; `start` and `filed` are
    optional so tests can exercise the malformed-data paths.
    """
    facts: dict = {}
    for entry in entries:
        concept = entry.pop("concept")
        facts.setdefault(concept, {"units": {"USD": []}})
        facts[concept]["units"]["USD"].append(entry)
    return {"facts": {"us-gaap": facts}}


def q(concept: str, val: float, start: str, end: str, filed: str, form: str = "10-Q") -> dict:
    return {
        "concept": concept,
        "val": val,
        "start": start,
        "end": end,
        "filed": filed,
        "form": form,
    }


def annual(concept: str, val: float, start: str, end: str, filed: str) -> dict:
    return q(concept, val, start, end, filed, form="10-K")


def instant(concept: str, val: float, end: str, filed: str) -> dict:
    return {"concept": concept, "val": val, "end": end, "filed": filed, "form": "10-Q"}


# ---------------------------------------------------------------------------
# The point-in-time gate
# ---------------------------------------------------------------------------


class TestPointInTimeGate:
    def test_fact_filed_after_as_of_is_invisible(self):
        """The core guarantee. A later filing must not be visible earlier."""
        data = payload(
            instant("Assets", 100.0, end="2022-03-31", filed="2022-05-05"),
            instant("Assets", 200.0, end="2022-06-30", filed="2022-08-04"),
        )
        # Standing on 1 July 2022, the June quarter has not been filed yet.
        view = FactSet(data, as_of=date(2022, 7, 1))
        assert view.instant("Assets").value == 100.0
        assert view.excluded_future_facts == 1

    def test_same_facts_visible_once_filed(self):
        data = payload(
            instant("Assets", 100.0, end="2022-03-31", filed="2022-05-05"),
            instant("Assets", 200.0, end="2022-06-30", filed="2022-08-04"),
        )
        view = FactSet(data, as_of=date(2022, 9, 1))
        assert view.instant("Assets").value == 200.0
        assert view.excluded_future_facts == 0

    def test_restatement_collapses_to_what_was_known_then(self):
        """A later restatement must not rewrite what an earlier view could see.

        This is the subtle one. Reading Q1 2024 from June gives the original
        figure; reading it from October gives the restated one.
        """
        data = payload(
            instant("Assets", 100.0, end="2024-03-31", filed="2024-05-01"),
            instant("Assets", 85.0, end="2024-03-31", filed="2024-09-15"),  # restated
        )
        as_known_then = FactSet(data, as_of=date(2024, 6, 1))
        assert as_known_then.instant("Assets").value == 100.0

        as_known_later = FactSet(data, as_of=date(2024, 10, 1))
        assert as_known_later.instant("Assets").value == 85.0

    def test_staleness_applies_before_restatement_choice(self):
        """A restatement of a long-past balance sheet does not revive it.

        Filing a correction in 2024 to a Q1 2022 balance sheet does not make
        that balance sheet a current description of the company. The staleness
        cutoff governs, and the fact stays absent.
        """
        data = payload(
            instant("Assets", 100.0, end="2022-03-31", filed="2022-05-05"),
            instant("Assets", 85.0, end="2022-03-31", filed="2024-02-15"),
        )
        assert FactSet(data, as_of=date(2025, 1, 1)).instant("Assets") is None

    def test_settle_margin_applied(self):
        """A fact filed today is not yet in the API; the settle margin covers it."""
        data = payload(instant("Assets", 100.0, end="2022-03-31", filed="2022-05-05"))
        assert FactSet(data, as_of=date(2022, 5, 5)).instant("Assets") is None
        assert FactSet(data, as_of=date(2022, 5, 6)).instant("Assets") is None
        assert FactSet(data, as_of=date(2022, 5, 7)).instant("Assets").value == 100.0

    def test_missing_filing_date_falls_back_to_statutory_lag(self):
        """No filing date means the conservative period-plus-lag rule applies."""
        data = payload({"concept": "Assets", "val": 100.0, "end": "2022-03-31"})
        # Instant facts use the annual (105-day) buffer: 2022-03-31 + 105d.
        assert FactSet(data, as_of=date(2022, 6, 30)).instant("Assets") is None
        assert FactSet(data, as_of=date(2022, 7, 20)).instant("Assets").value == 100.0

    def test_filed_before_period_end_is_treated_as_malformed(self):
        """A filing date preceding its own period would leak the future."""
        data = payload(instant("Assets", 100.0, end="2022-06-30", filed="2022-01-15"))
        # The bogus filing date is ignored, so the conservative lag governs.
        assert FactSet(data, as_of=date(2022, 2, 1)).instant("Assets") is None
        assert FactSet(data, as_of=date(2022, 10, 20)).instant("Assets").value == 100.0

    def test_quarterly_fallback_lag_is_shorter_than_annual(self):
        quarterly = payload(
            {"concept": "Revenues", "val": 50.0, "start": "2022-01-01", "end": "2022-03-31"}
        )
        # 90-day quarterly buffer: visible from 2022-06-29.
        assert not FactSet(quarterly, as_of=date(2022, 6, 1)).has("Revenues")
        assert FactSet(quarterly, as_of=date(2022, 7, 1)).has("Revenues")

    def test_as_of_defaults_to_today(self):
        data = payload(instant("Assets", 100.0, end="2020-03-31", filed="2020-05-05"))
        assert FactSet(data).as_of == date.today()

    def test_ttm_chain_known_only_when_last_quarter_filed(self):
        """A stitched TTM is knowable only once its final link is public."""
        data = payload(
            q("Revenues", 10, "2021-07-01", "2021-09-30", "2021-11-01"),
            q("Revenues", 11, "2021-10-01", "2021-12-31", "2022-02-01"),
            q("Revenues", 12, "2022-01-01", "2022-03-31", "2022-05-02"),
            q("Revenues", 13, "2022-04-01", "2022-06-30", "2022-08-01"),
        )
        # Before the June quarter is filed, only a 3-quarter partial exists —
        # which is not a TTM, so no window should be produced.
        assert FactSet(data, as_of=date(2022, 7, 1)).ttm("Revenues") is None

        after = FactSet(data, as_of=date(2022, 8, 15)).ttm("Revenues")
        assert after is not None
        assert after.value == 46
        assert after.filed == date(2022, 8, 1)


# ---------------------------------------------------------------------------
# Fact classification
# ---------------------------------------------------------------------------


class TestFactClassification:
    def test_instant_has_no_duration(self):
        f = Fact("Assets", 1.0, date(2022, 3, 31), None, "10-Q", date(2022, 5, 5))
        assert f.is_instant and f.days is None
        assert not f.is_annual and not f.is_quarterly

    def test_annual_and_quarterly_windows(self):
        year = Fact("Revenues", 1.0, date(2022, 12, 31), date(2022, 1, 1), "10-K", None)
        quarter = Fact("Revenues", 1.0, date(2022, 3, 31), date(2022, 1, 1), "10-Q", None)
        assert year.is_annual and not year.is_quarterly
        assert quarter.is_quarterly and not quarter.is_annual

    def test_odd_duration_is_neither(self):
        """A six-month window is not a quarter and not a year."""
        half = Fact("Revenues", 1.0, date(2022, 6, 30), date(2022, 1, 1), "10-Q", None)
        assert not half.is_annual and not half.is_quarterly


# ---------------------------------------------------------------------------
# Staleness — abandoned tags must not resurface
# ---------------------------------------------------------------------------


class TestStaleness:
    def test_abandoned_tag_drops_out(self):
        """Companies stop maintaining tags without deleting the history.

        A LongTermDebt value last reported in 2011 must not be presented as the
        current figure just because nothing newer carries that tag.
        """
        data = payload(instant("LongTermDebt", 5_000.0, end="2011-12-31", filed="2012-02-15"))
        assert FactSet(data, as_of=date(2025, 1, 1)).instant("LongTermDebt") is None

    def test_income_facts_get_a_longer_horizon(self):
        """A TTM chain's oldest link is already a year behind the window end,
        so the tighter instant cutoff would sever valid chains."""
        data = payload(annual("Revenues", 100.0, "2023-01-01", "2023-12-31", "2024-02-15"))
        view = FactSet(data, as_of=date(2025, 6, 1))  # ~520 days after period end
        assert view.ttm("Revenues") is not None


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------


class TestSelection:
    def test_instant_max_avoids_double_counting(self):
        """Overlapping tags for the same pool: take the largest, never the sum."""
        data = payload(
            instant("ShortTermInvestments", 300.0, end="2024-03-31", filed="2024-05-01"),
            instant("MarketableSecuritiesCurrent", 500.0, end="2024-03-31", filed="2024-05-01"),
        )
        view = FactSet(data, as_of=date(2024, 6, 1))
        best = view.instant_max(["ShortTermInvestments", "MarketableSecuritiesCurrent"])
        assert best.value == 500.0

    def test_ttm_candidates_do_not_merge_across_concepts(self):
        """Revenues and RevenueFromContractWithCustomer overlap; mixing them
        double-counts, so exactly one concept wins outright."""
        data = payload(
            annual("Revenues", 100.0, "2023-01-01", "2023-12-31", "2024-02-15"),
            annual(
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                95.0,
                "2023-01-01",
                "2023-12-31",
                "2024-02-15",
            ),
        )
        view = FactSet(data, as_of=date(2024, 6, 1))
        windows = view.ttm_candidates_best(
            ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]
        )
        # Equally fresh, so the caller's priority order decides.
        assert len(windows) == 1
        assert windows[0].value == 100.0

    def test_abandoned_concept_loses_to_a_maintained_one(self):
        """Regression: NVIDIA stopped tagging `Revenues` in 2018 and moved to
        `RevenueFromContractWithCustomerExcludingAssessedTax`. The stale tag
        still carries windows inside the 900-day duration horizon, so choosing
        purely by priority order reported FY2019 revenue in a 2020 view — a
        wrong number that looked entirely plausible.
        """
        data = payload(
            annual("Revenues", 12.4, "2017-10-30", "2018-10-28", "2018-11-20"),
            annual(
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                10.9,
                "2019-01-28",
                "2020-01-26",
                "2020-02-20",
            ),
        )
        view = FactSet(data, as_of=date(2020, 6, 30))
        best = view.ttm(
            ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]
        )
        assert best.value == 10.9
        assert best.end == date(2020, 1, 26)

    def test_abandoned_instant_concept_loses_to_a_maintained_one(self):
        """Same rule for balance-sheet tags, which are abandoned just as often."""
        data = payload(
            instant("LongTermDebt", 5_000.0, end="2023-06-30", filed="2023-08-01"),
            instant("LongTermDebtNoncurrent", 9_000.0, end="2024-06-30", filed="2024-08-01"),
        )
        view = FactSet(data, as_of=date(2024, 10, 1))
        best = view.instant_first(["LongTermDebt", "LongTermDebtNoncurrent"])
        assert best.value == 9_000.0

    def test_priority_order_still_breaks_ties(self):
        """When both concepts are equally current, the caller's order governs."""
        data = payload(
            instant("Assets", 100.0, end="2024-03-31", filed="2024-05-01"),
            instant("AssetsNet", 90.0, end="2024-03-31", filed="2024-05-01"),
        )
        view = FactSet(data, as_of=date(2024, 6, 1))
        assert view.instant_first(["AssetsNet", "Assets"]).value == 90.0
        assert view.instant_first(["Assets", "AssetsNet"]).value == 100.0

    def test_ttm_prefers_annual_over_stitched_quarters(self):
        data = payload(
            annual("Revenues", 46.0, "2021-07-01", "2022-06-30", "2022-08-01"),
            q("Revenues", 10, "2021-07-01", "2021-09-30", "2021-11-01"),
            q("Revenues", 11, "2021-10-01", "2021-12-31", "2022-02-01"),
            q("Revenues", 12, "2022-01-01", "2022-03-31", "2022-05-02"),
            q("Revenues", 13, "2022-04-01", "2022-06-30", "2022-08-01"),
        )
        best = FactSet(data, as_of=date(2022, 9, 1)).ttm("Revenues")
        assert best.basis == "annual"

    def test_coverage_reports_gaps_without_filling_them(self):
        data = payload(instant("Assets", 100.0, end="2024-03-31", filed="2024-05-01"))
        view = FactSet(data, as_of=date(2024, 6, 1))
        assert view.coverage(["Assets", "Liabilities"]) == {
            "Assets": True,
            "Liabilities": False,
        }

    def test_empty_payload_is_survivable(self):
        for empty in (None, {}, {"facts": {}}, {"facts": {"us-gaap": {}}}):
            view = FactSet(empty, as_of=date(2024, 1, 1))
            assert view.instant("Assets") is None
            assert view.ttm("Revenues") is None
            assert view.has("Assets") is False

    def test_currency_is_detected_from_the_filer(self):
        """Foreign private issuers report in their functional currency."""
        data = {
            "facts": {
                "ifrs-full": {
                    "Assets": {
                        "units": {
                            "TWD": [
                                {"val": 1e12, "end": "2024-12-31", "filed": "2025-04-17"}
                            ],
                            "USD": [
                                {"val": 2e11, "end": "2024-12-31", "filed": "2025-04-17"}
                            ],
                        }
                    },
                    "Revenue": {
                        "units": {
                            "TWD": [
                                {
                                    "val": 3e12,
                                    "start": "2024-01-01",
                                    "end": "2024-12-31",
                                    "filed": "2025-04-17",
                                }
                            ]
                        }
                    },
                }
            }
        }
        view = FactSet(data, as_of=date(2025, 6, 1))
        assert view.reporting_currency == "TWD"
        assert view.is_usd is False
        # The TWD figure is the reported one; the USD line must not shadow it.
        assert view.instant("Assets").value == 1e12

    def test_currency_can_be_forced(self):
        data = payload(instant("Assets", 100.0, end="2024-03-31", filed="2024-05-01"))
        assert FactSet(data, as_of=date(2024, 6, 1), currency="EUR").instant("Assets") is None

    def test_usd_filer_detected_as_usd(self):
        data = payload(instant("Assets", 100.0, end="2024-03-31", filed="2024-05-01"))
        view = FactSet(data, as_of=date(2024, 6, 1))
        assert view.reporting_currency == "USD" and view.is_usd

    def test_data_quality_flags_an_unanalysable_filer(self):
        """Regression: TSMC's 20-F lands in EDGAR with only a cover-page share
        count in XBRL. An empty fact set must read as 'cannot analyse', never
        as a company with no debt and no revenue.
        """
        data = payload(
            instant("EntityCommonStockSharesOutstanding", 5e9, "2025-12-31", "2026-04-16")
        )
        view = FactSet(data, as_of=date(2026, 8, 1))
        report = view.data_quality(required=["Assets", "Revenues", "NetIncomeLoss"])
        assert report["analysable"] is False
        assert report["missing_concepts"] == ["Assets", "Revenues", "NetIncomeLoss"]
        assert report["latest_filing"] == date(2026, 4, 16)

    def test_data_quality_passes_a_complete_filer(self):
        data = payload(
            instant("Assets", 100.0, end="2024-03-31", filed="2024-05-01"),
            annual("Revenues", 500.0, "2023-01-01", "2023-12-31", "2024-02-15"),
        )
        view = FactSet(data, as_of=date(2024, 6, 1))
        report = view.data_quality(required=["Assets", "Revenues"])
        assert report["analysable"] is True
        assert report["missing_concepts"] == []
        assert report["staleness_days"] == (date(2024, 6, 1) - date(2024, 5, 1)).days

    def test_latest_filing_ignores_the_future(self):
        data = payload(
            instant("Assets", 100.0, end="2024-03-31", filed="2024-05-01"),
            instant("Assets", 120.0, end="2024-06-30", filed="2024-08-01"),
        )
        view = FactSet(data, as_of=date(2024, 7, 1))
        assert view.latest_filing_date() == date(2024, 5, 1)

    def test_junk_entries_are_skipped_not_fatal(self):
        data = payload(
            {"concept": "Assets", "val": None, "end": "2024-03-31", "filed": "2024-05-01"},
            {"concept": "Assets", "val": 100.0, "end": "not-a-date", "filed": "2024-05-01"},
            instant("Assets", 250.0, end="2024-03-31", filed="2024-05-01"),
        )
        assert FactSet(data, as_of=date(2024, 6, 1)).instant("Assets").value == 250.0


# ---------------------------------------------------------------------------
# Period alignment
# ---------------------------------------------------------------------------


class TestAlignment:
    def make(self, value: float, start: str, end: str, basis: str = "annual") -> Window:
        return Window(value, date.fromisoformat(start), date.fromisoformat(end), "X", basis)

    def test_mismatched_periods_are_not_paired(self):
        """Dividing an annual figure into one quarter of revenue was a real bug
        that produced a ratio roughly four times too high."""
        annual_num = [self.make(7.0, "2023-01-01", "2023-12-31")]
        quarterly_den = [self.make(100.0, "2023-10-01", "2023-12-31", "4x quarterly")]
        # End dates match, so these DO align — that is correct behaviour.
        assert align_windows(annual_num, quarterly_den) is not None

        # Now a genuine mismatch: ends nine months apart.
        far = [self.make(100.0, "2023-01-01", "2023-03-31", "4x quarterly")]
        assert align_windows(annual_num, far) is None

    def test_latest_common_period_wins(self):
        nums = [
            self.make(5.0, "2022-01-01", "2022-12-31"),
            self.make(7.0, "2023-01-01", "2023-12-31"),
        ]
        dens = [
            self.make(100.0, "2022-01-01", "2022-12-31"),
            self.make(140.0, "2023-01-01", "2023-12-31"),
        ]
        num, den = align_windows(nums, dens)
        assert (num.value, den.value) == (7.0, 140.0)

    def test_annual_preferred_over_stitched_at_same_date(self):
        nums = [self.make(7.0, "2023-01-01", "2023-12-31")]
        dens = [
            self.make(140.0, "2023-01-01", "2023-12-31", "4x quarterly"),
            self.make(141.0, "2023-01-01", "2023-12-31", "annual"),
        ]
        _, den = align_windows(nums, dens)
        assert den.basis == "annual"

    def test_ratio_computes_and_reports_its_inputs(self):
        nums = [self.make(7.0, "2023-01-01", "2023-12-31")]
        dens = [self.make(140.0, "2023-01-01", "2023-12-31")]
        value, num, den = ratio(nums, dens)
        assert value == pytest.approx(0.05)
        assert num.value == 7.0 and den.value == 140.0

    def test_ratio_refuses_to_divide_by_zero(self):
        nums = [self.make(7.0, "2023-01-01", "2023-12-31")]
        dens = [self.make(0.0, "2023-01-01", "2023-12-31")]
        assert ratio(nums, dens) is None

    def test_ratio_returns_none_when_unpairable(self):
        nums = [self.make(7.0, "2023-01-01", "2023-12-31")]
        dens = [self.make(140.0, "2020-01-01", "2020-12-31")]
        assert ratio(nums, dens) is None
