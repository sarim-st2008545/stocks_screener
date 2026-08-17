"""Tests for composite scoring.

Percentile direction is tested in both polarities, deliberately. The prior
codebase shipped an inverted sort that put a loss-making company top of
profitability and a 56%-margin company near the bottom: every figure was
plausible, the table sorted, and the ranking was upside down. That failure mode
is silent, so it gets explicit coverage.
"""

from __future__ import annotations

from datetime import date

import pytest

from src import cycle
from src.scoring import (
    METRICS,
    PILLARS,
    CompanyInputs,
    ScoreBoard,
    percentile_rank,
    report,
    score_pool,
)

AS_OF = date(2026, 6, 30)


def company(ticker: str, segment: str = "seg", **values) -> CompanyInputs:
    full = {m.key: None for m in METRICS}
    full.update(values)
    return CompanyInputs(ticker=ticker, segment=segment, status="INVESTABLE", values=full)


# ---------------------------------------------------------------------------
# Direction
# ---------------------------------------------------------------------------


class TestPercentileDirection:
    POOL = [0.10, 0.20, 0.30, 0.40, 0.50]

    def test_higher_is_better_ranks_the_largest_top(self):
        assert percentile_rank(0.50, self.POOL, higher_is_better=True) == 100.0
        assert percentile_rank(0.10, self.POOL, higher_is_better=True) == 20.0

    def test_lower_is_better_ranks_the_smallest_top(self):
        """The inverted case. Net debt / EBITDA and the growth the price demands
        are both better when small."""
        assert percentile_rank(0.10, self.POOL, higher_is_better=False) == 80.0
        assert percentile_rank(0.50, self.POOL, higher_is_better=False) == 0.0

    def test_the_two_directions_are_mirror_images(self):
        for value in self.POOL:
            up = percentile_rank(value, self.POOL, True)
            down = percentile_rank(value, self.POOL, False)
            assert up + down == pytest.approx(100.0)

    def test_negative_values_rank_correctly(self):
        """A loss-making company must not outrank a profitable one."""
        pool = [-0.40, -0.10, 0.20, 0.56]
        loss = percentile_rank(-0.40, pool, higher_is_better=True)
        profit = percentile_rank(0.56, pool, higher_is_better=True)
        assert profit > loss
        assert profit == 100.0

    def test_missing_value_has_no_rank(self):
        assert percentile_rank(None, self.POOL, True) is None

    def test_tiny_population_has_no_rank(self):
        assert percentile_rank(0.5, [0.5], True) is None
        assert percentile_rank(0.5, [], True) is None

    def test_every_metric_declares_a_direction(self):
        for metric in METRICS:
            assert isinstance(metric.higher_is_better, bool)
            assert metric.pillar in PILLARS

    def test_lower_is_better_metrics_are_the_expected_ones(self):
        inverted = {m.key for m in METRICS if not m.higher_is_better}
        assert inverted == {
            "net_debt_to_ebitda",
            "implied_growth_gap",
            "pe_vs_own_history",
        }


# ---------------------------------------------------------------------------
# Pillars and composites
# ---------------------------------------------------------------------------


class TestScoring:
    def pool(self) -> list[CompanyInputs]:
        return [
            company("STRONG", roic=0.50, gross_margin=0.70, operating_margin=0.40,
                    fcf_conversion=1.2, piotroski=8.0, altman_z=9.0,
                    interest_coverage=100.0, net_debt_to_ebitda=-0.5, current_ratio=3.5,
                    implied_growth_gap=0.05, fcf_yield=0.05, earnings_yield=0.04,
                    pe_vs_own_history=0.8, revenue_growth=0.40, fcf_growth=0.35),
            company("MIDDLING", roic=0.15, gross_margin=0.45, operating_margin=0.20,
                    fcf_conversion=0.9, piotroski=6.0, altman_z=4.0,
                    interest_coverage=12.0, net_debt_to_ebitda=1.5, current_ratio=2.0,
                    implied_growth_gap=0.20, fcf_yield=0.03, earnings_yield=0.025,
                    pe_vs_own_history=1.1, revenue_growth=0.10, fcf_growth=0.08),
            company("WEAK", roic=-0.02, gross_margin=0.20, operating_margin=-0.05,
                    fcf_conversion=0.3, piotroski=2.0, altman_z=1.0,
                    interest_coverage=1.2, net_debt_to_ebitda=4.5, current_ratio=0.9,
                    implied_growth_gap=0.55, fcf_yield=0.005, earnings_yield=-0.01,
                    pe_vs_own_history=2.4, revenue_growth=-0.05, fcf_growth=-0.10),
        ]

    def test_ordering_is_strong_then_middling_then_weak(self):
        board = score_pool(self.pool(), AS_OF)
        assert [s.ticker for s in board.ranked()] == ["STRONG", "MIDDLING", "WEAK"]

    def test_ranks_are_assigned(self):
        board = score_pool(self.pool(), AS_OF)
        assert board.by_ticker("STRONG").rank == 1
        assert board.by_ticker("WEAK").rank == 3

    def test_every_pillar_scored_when_data_present(self):
        board = score_pool(self.pool(), AS_OF)
        strong = board.by_ticker("STRONG")
        for pillar in PILLARS:
            assert strong.pillar(pillar) is not None
        assert strong.missing_pillars == []

    def test_strong_company_tops_each_pillar(self):
        board = score_pool(self.pool(), AS_OF)
        strong, weak = board.by_ticker("STRONG"), board.by_ticker("WEAK")
        for pillar in PILLARS:
            assert strong.pillar(pillar) > weak.pillar(pillar), pillar

    def test_composite_lies_within_the_percentile_range(self):
        board = score_pool(self.pool(), AS_OF)
        for entry in board.ranked():
            assert 0 <= entry.composite <= 100


class TestMissingData:
    def test_missing_pillar_is_renormalised_not_zeroed(self):
        """Scoring an absent pillar as zero would punish a company twice: once
        for the disclosure gap and again through a diluted total."""
        pool = [
            company("FULL", roic=0.30, gross_margin=0.60, fcf_yield=0.04,
                    earnings_yield=0.03, piotroski=7.0, revenue_growth=0.20),
            company("NOVAL", roic=0.30, gross_margin=0.60, piotroski=7.0,
                    revenue_growth=0.20),
            company("OTHER", roic=0.10, gross_margin=0.30, fcf_yield=0.01,
                    earnings_yield=0.01, piotroski=3.0, revenue_growth=0.05),
        ]
        board = score_pool(pool, AS_OF)
        noval = board.by_ticker("NOVAL")
        assert "valuation" in noval.missing_pillars
        assert noval.pillar("valuation") is None
        # Identical on every pillar it does have, so it must not rank below FULL
        # merely for lacking one.
        assert noval.composite == pytest.approx(board.by_ticker("FULL").composite, abs=15)

    def test_metric_gap_within_a_pillar_renormalises(self):
        pool = [
            company("A", roic=0.30, gross_margin=0.60, operating_margin=0.30),
            company("B", roic=0.30),  # same roic, no margins
            company("C", roic=0.05, gross_margin=0.20, operating_margin=0.05),
        ]
        board = score_pool(pool, AS_OF)
        b = board.by_ticker("B")
        assert b.pillar("quality") is not None
        assert set(b.pillars["quality"].missing) >= {"gross_margin", "operating_margin"}

    def test_company_with_nothing_is_unscored_not_ranked_last(self):
        pool = [
            company("A", roic=0.30, gross_margin=0.60),
            company("B", roic=0.10, gross_margin=0.20),
            company("EMPTY"),
        ]
        board = score_pool(pool, AS_OF)
        assert board.by_ticker("EMPTY").composite is None
        assert board.by_ticker("EMPTY") in board.unscored()
        assert [s.ticker for s in board.ranked()] == ["A", "B"]

    def test_pillar_coverage_is_reported(self):
        pool = [
            company("A", roic=0.30, gross_margin=0.60),
            company("B", roic=0.10, gross_margin=0.20),
        ]
        board = score_pool(pool, AS_OF)
        coverage = board.by_ticker("A").pillars["quality"].coverage
        assert coverage.endswith("/5")


class TestCycleAndFlags:
    def test_cycle_position_is_carried_beside_the_score(self):
        """Reported, never folded in: a company at peak margins scores well on
        quality because margins are peaking, and hiding that inside a composite
        would launder a warning into a recommendation."""
        entries = [
            company("PEAKY", roic=0.40, gross_margin=0.60),
            company("STEADY", roic=0.20, gross_margin=0.40),
        ]
        entries[0].cycle_position = cycle.Position.PEAK
        entries[0].earnings_repeatable = False
        entries[1].cycle_position = cycle.Position.STABLE
        entries[1].earnings_repeatable = True

        board = score_pool(entries, AS_OF)
        peaky = board.by_ticker("PEAKY")
        assert peaky.rank == 1  # still ranks top on the numbers
        assert peaky.earnings_repeatable is False
        assert peaky.cycle_position == cycle.Position.PEAK

    def test_trough_is_not_labelled_peak_earnings(self):
        """Regression: SMCI and HPE read cycle TROUGH while the flag said 'peak
        earnings'. The logic was right, the label was not."""
        entries = [
            company("LOW", roic=0.05, gross_margin=0.20),
            company("HIGH", roic=0.40, gross_margin=0.60),
        ]
        entries[0].cycle_position = cycle.Position.TROUGH
        entries[0].earnings_repeatable = False
        board = score_pool(entries, AS_OF)
        text = report(board)
        assert "trough earnings" in text
        assert "peak earnings" not in text

    def test_late_cycle_has_its_own_label(self):
        entries = [
            company("A", roic=0.40, gross_margin=0.60),
            company("B", roic=0.10, gross_margin=0.20),
        ]
        entries[0].cycle_position = cycle.Position.LATE
        entries[0].earnings_repeatable = False
        assert "late-cycle earnings" in report(score_pool(entries, AS_OF))

    def test_stability_flag_surfaces_in_the_report(self):
        entries = [
            company("RISKY", roic=0.40, gross_margin=0.60),
            company("SAFE", roic=0.10, gross_margin=0.20),
        ]
        entries[0].stability_flag = "governance risk"
        assert "stability flag" in report(score_pool(entries, AS_OF))


class TestSegmentRanking:
    def test_segment_rank_is_independent_of_overall_rank(self):
        pool = [
            company("A1", segment="alpha", roic=0.50, gross_margin=0.70),
            company("A2", segment="alpha", roic=0.10, gross_margin=0.30),
            company("B1", segment="beta", roic=0.40, gross_margin=0.60),
        ]
        board = score_pool(pool, AS_OF)
        assert board.by_ticker("B1").segment_rank == 1
        assert board.by_ticker("A1").segment_rank == 1
        assert board.by_ticker("A2").segment_rank == 2
        assert board.by_ticker("B1").rank == 2


class TestPersistenceAndReport:
    def pool(self):
        return [
            company("A", roic=0.40, gross_margin=0.60, fcf_yield=0.04,
                    piotroski=8.0, revenue_growth=0.30),
            company("B", roic=0.10, gross_margin=0.20, fcf_yield=0.01,
                    piotroski=3.0, revenue_growth=0.05),
        ]

    def test_board_round_trips_to_disk(self, tmp_path):
        board = score_pool(self.pool(), AS_OF)
        path = board.save(tmp_path)
        import json

        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["as_of"] == "2026-06-30"
        assert loaded["pool_size"] == 2
        assert "weights" in loaded  # the hypothesis under test is recorded with it

    def test_report_is_ascii_safe(self):
        report(score_pool(self.pool(), AS_OF)).encode("cp1252")

    def test_report_states_a_score_is_not_a_buy(self):
        assert "not a buy" in report(score_pool(self.pool(), AS_OF))

    def test_report_records_the_weights_as_a_hypothesis(self):
        assert "hypothesis" in report(score_pool(self.pool(), AS_OF))

    def test_top_limits_the_listing(self):
        board = score_pool(self.pool(), AS_OF)
        assert "  B " not in report(board, top=1)


class TestWeights:
    def test_weights_cover_every_pillar(self):
        from src import config

        weights = config.get("rules.scoring.weights")
        assert set(weights) == set(PILLARS)
        assert sum(weights.values()) == pytest.approx(1.0)
