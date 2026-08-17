"""Tests for historical validation.

The metric tests are deliberately arithmetic-heavy, because a backtest that
computes its statistics wrongly is worse than no backtest: it produces a confident
number nobody can check. The drawdown test in particular pins a real flaw — the
first version measured drawdown on quarterly snapshots and reported 1.5% for a
portfolio that had actually fallen 17%.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from src.backtest import (
    REBALANCE_MONTHS,
    BacktestRun,
    Metrics,
    compute_metrics,
    evaluate_gates,
    rebalance_dates,
    regress,
    report,
    walk_forward,
)


def curve(values: list[float], start: date = date(2020, 1, 1), step_days: int = 1):
    return [(start + timedelta(days=i * step_days), v) for i, v in enumerate(values)]


def daily_curve(values: list[float]):
    return curve(values, step_days=1)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_flat_curve_has_no_return_and_no_drawdown(self):
        m = compute_metrics(curve([100.0] * 300))
        assert m.total_return == pytest.approx(0.0)
        assert m.max_drawdown == pytest.approx(0.0)
        assert m.volatility == pytest.approx(0.0)

    def test_total_return_and_cagr(self):
        # Doubling over roughly two years.
        m = compute_metrics(curve([100.0 + i * 0.137 for i in range(731)]))
        assert m.total_return == pytest.approx(1.0, rel=0.02)
        assert m.cagr == pytest.approx(0.414, rel=0.05)

    def test_drawdown_measures_peak_to_trough(self):
        m = compute_metrics(curve([100.0, 120.0, 60.0, 90.0]))
        assert m.max_drawdown == pytest.approx(0.5)

    def test_drawdown_needs_a_dense_curve_to_be_real(self):
        """Regression: sampling quarterly hid a crash that recovered inside the
        quarter, and the first version reported 1.5% for a 17% fall."""
        crash = [100.0, 95.0, 83.0, 91.0, 100.0, 104.0]
        dense = compute_metrics(curve(crash))
        sparse = compute_metrics(curve([crash[0], crash[-1]] + [crash[-1]]))
        assert dense.max_drawdown == pytest.approx(0.17)
        assert sparse.max_drawdown == pytest.approx(0.0)
        assert dense.max_drawdown > sparse.max_drawdown

    def test_volatility_rises_with_dispersion(self):
        calm = compute_metrics(curve([100.0 + (i % 2) for i in range(300)]))
        wild = compute_metrics(curve([100.0 + (i % 2) * 20 for i in range(300)]))
        assert wild.volatility > calm.volatility

    def test_sharpe_falls_as_volatility_rises_for_equal_return(self):
        steady = curve([100.0 * (1.0005 ** i) for i in range(500)])
        jumpy = [
            (d, v * (1.05 if i % 2 else 0.96)) for i, (d, v) in enumerate(steady)
        ]
        assert compute_metrics(steady).sharpe > compute_metrics(jumpy).sharpe

    def test_losing_strategy_has_negative_sharpe(self):
        m = compute_metrics(curve([100.0 - i * 0.05 for i in range(500)]))
        assert m.cagr < 0
        assert m.sharpe < 0

    def test_calmar_is_return_over_worst_drawdown(self):
        m = compute_metrics(curve([100.0, 110.0, 80.0, 130.0] * 100))
        if m.cagr is not None and m.max_drawdown:
            assert m.calmar == pytest.approx(m.cagr / m.max_drawdown)

    def test_too_short_a_curve_yields_nothing_rather_than_noise(self):
        m = compute_metrics(curve([100.0, 105.0]))
        assert m.cagr is None
        assert m.sharpe is None

    def test_empty_curve_is_survivable(self):
        assert compute_metrics([]).periods == 0

    def test_metrics_label_handles_missing_values(self):
        assert "n/a" in Metrics().label()


class TestRegression:
    def test_beta_of_one_for_an_identical_series(self):
        returns = [0.01, -0.02, 0.03, 0.00, 0.015]
        alpha, beta = regress(returns, returns)
        assert beta == pytest.approx(1.0)
        assert alpha == pytest.approx(0.0)

    def test_beta_of_a_half_for_a_damped_series(self):
        market = [0.02, -0.04, 0.06, 0.00]
        strategy = [r / 2 for r in market]
        _, beta = regress(strategy, market)
        assert beta == pytest.approx(0.5)

    def test_constant_outperformance_shows_as_alpha(self):
        market = [0.01, -0.01, 0.02, 0.00, 0.01]
        strategy = [r + 0.005 for r in market]
        alpha, beta = regress(strategy, market)
        assert beta == pytest.approx(1.0)
        assert alpha == pytest.approx(0.005)

    def test_too_few_points_yields_nothing(self):
        assert regress([0.01], [0.01]) == (None, None)

    def test_zero_variance_benchmark_yields_nothing(self):
        assert regress([0.01, 0.02, 0.03, 0.04], [0.0] * 4) == (None, None)


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------


class TestSchedule:
    def test_four_rebalances_a_year(self):
        dates = rebalance_dates(date(2020, 1, 1), date(2020, 12, 31))
        assert len(dates) == 4
        assert {d.month for d in dates} == set(REBALANCE_MONTHS)

    def test_rebalances_follow_filing_deadlines(self):
        """February, May, August and November - a fortnight after each quarter's
        deadline, so the figures are genuinely readable when acted on."""
        assert REBALANCE_MONTHS == (2, 5, 8, 11)

    def test_range_is_respected(self):
        dates = rebalance_dates(date(2020, 6, 1), date(2021, 3, 1))
        assert all(date(2020, 6, 1) <= d <= date(2021, 3, 1) for d in dates)

    def test_dates_are_ordered(self):
        dates = rebalance_dates(date(2018, 1, 1), date(2022, 1, 1))
        assert dates == sorted(dates)


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------


class TestWalkForward:
    def long_curve(self, years: int = 12):
        days = years * 365
        return curve([100.0 * (1.0003 ** i) for i in range(days)])

    def test_produces_multiple_windows_over_a_long_history(self):
        wf = walk_forward(self.long_curve())
        assert len(wf.windows) >= 2

    def test_windows_do_not_overlap_train_and_test(self):
        for window in walk_forward(self.long_curve()).windows:
            assert window["train"][1] <= window["test"][0]

    def test_retention_compares_out_of_sample_to_in_sample(self):
        wf = walk_forward(self.long_curve())
        if wf.retention is not None:
            assert wf.retention > 0

    def test_short_history_yields_no_windows(self):
        wf = walk_forward(curve([100.0] * 200))
        assert wf.windows == []
        assert wf.retention is None

    def test_empty_curve_is_survivable(self):
        assert walk_forward([]).windows == []


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------


def run_with(equity, benchmarks=None, name_periods=5, total_periods=10) -> BacktestRun:
    run = BacktestRun(start=equity[0][0], end=equity[-1][0])
    run.equity = equity
    run.rebalance_equity = equity[::60] or equity
    run.benchmarks = benchmarks or {}
    run.name_periods = name_periods
    run.total_periods = total_periods
    return run


class TestGates:
    def strong(self):
        return curve([100.0 * (1.0004 ** i) for i in range(1500)])

    def test_suspiciously_high_sharpe_is_flagged(self):
        """The S&P 500's own long-run Sharpe is roughly 0.4-0.5, so anything far
        above that should be treated as overfitting until proven otherwise."""
        equity = self.strong()
        metrics = compute_metrics(equity)
        gates = evaluate_gates(run_with(equity), metrics, walk_forward(equity), "SOXX")
        if metrics.sharpe and metrics.sharpe > 1.5:
            assert any("suspicious" in g.name for g in gates)

    def test_drawdown_gate_fails_on_a_deep_fall(self):
        equity = curve([100.0] * 200 + [40.0] * 200 + [110.0] * 200)
        gates = evaluate_gates(
            run_with(equity), compute_metrics(equity), walk_forward(equity), "SOXX"
        )
        drawdown = next(g for g in gates if "drawdown" in g.name)
        assert drawdown.passed is False

    def test_benchmark_gate_is_unevaluable_without_a_benchmark(self):
        equity = self.strong()
        gates = evaluate_gates(
            run_with(equity), compute_metrics(equity), walk_forward(equity), "SOXX"
        )
        bench = next(g for g in gates if "SOXX" in g.name)
        assert bench.passed is None

    def test_beating_the_benchmark_passes(self):
        equity = curve([100.0 * (1.0004 ** i) for i in range(1200)])
        weak = curve([100.0 * (1.00005 ** i) for i in range(1200)])
        gates = evaluate_gates(
            run_with(equity, {"SOXX": weak}),
            compute_metrics(equity),
            walk_forward(equity),
            "SOXX",
        )
        bench = next(g for g in gates if "SOXX" in g.name)
        assert bench.passed is True

    def test_rare_name_selection_fails_its_gate(self):
        """A strategy that almost never holds a single name is really just its
        sleeve allocation, and says nothing about the analysis above it."""
        equity = self.strong()
        gates = evaluate_gates(
            run_with(equity, name_periods=1, total_periods=40),
            compute_metrics(equity),
            walk_forward(equity),
            "SOXX",
        )
        participation = next(g for g in gates if "selection" in g.name)
        assert participation.passed is False


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class TestReport:
    def test_labels_results_as_hypothetical(self):
        equity = curve([100.0 * (1.0002 ** i) for i in range(600)])
        text = report(run_with(equity))
        assert "HYPOTHETICAL" in text
        assert "NOT ACTUAL TRADING RESULTS" in text

    def test_discloses_survivorship_bias(self):
        equity = curve([100.0 * (1.0002 ** i) for i in range(600)])
        text = report(run_with(equity))
        assert "SURVIVORSHIP" in text
        assert "upper bound" in text

    def test_states_the_gate_outcome_plainly(self):
        equity = curve([100.0] * 200 + [40.0] * 400)
        text = report(run_with(equity))
        assert "gate NOT cleared" in text
        assert "back for revision" in text

    def test_warns_when_name_selection_is_rare(self):
        equity = curve([100.0 * (1.0002 ** i) for i in range(600)])
        text = report(run_with(equity, name_periods=1, total_periods=40))
        assert "mostly the sleeve allocation" in text

    def test_reports_costs(self):
        equity = curve([100.0 * (1.0002 ** i) for i in range(600)])
        run = run_with(equity)
        run.trades = 12
        run.costs_paid = 3.45
        text = report(run)
        assert "12 trade(s)" in text
        assert "3.45" in text

    def test_distinguishes_rebalances_from_daily_marks(self):
        """The first version labelled 565 daily marks as 565 rebalances."""
        equity = curve([100.0 * (1.0002 ** i) for i in range(600)])
        text = report(run_with(equity))
        assert "daily marks" in text

    def test_empty_run_is_survivable(self):
        run = BacktestRun(start=date(2020, 1, 1), end=date(2021, 1, 1))
        assert "no equity curve" in report(run)

    def test_report_is_ascii_safe(self):
        equity = curve([100.0 * (1.0002 ** i) for i in range(600)])
        report(run_with(equity)).encode("cp1252")
