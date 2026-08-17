"""Tests for portfolio construction, sizing, and the ledger.

The limits are the point: no single name too large, no segment too concentrated,
nothing too small to be worth holding, and no trade recorded without a human
having approved it. Cost-basis arithmetic gets specific attention because a
partial sale that resets the basis silently corrupts every later P&L figure.
"""

from __future__ import annotations

from datetime import date

import pytest

from src import config, signals as signals_mod
from src.portfolio import (
    Act,
    Ledger,
    Plan,
    Position,
    Proposal,
    Sleeve,
    build_plan,
    report,
    size_positions,
)

AS_OF = date(2026, 8, 17)
PRICES = {"NVDA": 200.0, "MU": 1000.0, "VTI": 400.0, "SOXX": 500.0, "GLD": 400.0}


def signal(ticker: str, decision: str, composite: float = 70.0) -> signals_mod.Signal:
    s = signals_mod.Signal(ticker=ticker, as_of=AS_OF, decision=decision, composite=composite)
    s.evidence = ["quality rising", "cheap vs history"]
    s.falsification = ["quality gate fails"]
    s.contradictions = ["price above fair value"]
    return s


def fresh(size: float = 1000.0) -> Ledger:
    return Ledger(wallet_size=size, cash=size, opened=AS_OF.isoformat())


# ---------------------------------------------------------------------------
# Sizing
# ---------------------------------------------------------------------------


class TestSizing:
    def test_budget_is_distributed_across_names(self):
        allocations = size_positions(
            [("A", 80.0, "s1"), ("B", 60.0, "s2"), ("C", 40.0, "s3")],
            sleeve_budget=300.0,
            portfolio_total=3000.0,
        )
        assert len(allocations) == 3
        assert sum(allocations.values()) == pytest.approx(300.0, rel=0.01)

    def test_higher_score_receives_more(self):
        allocations = size_positions(
            [("HIGH", 90.0, "s1"), ("LOW", 30.0, "s2")],
            sleeve_budget=300.0,
            portfolio_total=3000.0,
        )
        assert allocations["HIGH"] > allocations["LOW"]

    def test_tilt_is_bounded_at_two_to_one(self):
        """Kelly needs a probability this system does not produce, so the tilt is
        deliberately capped rather than derived from an invented edge."""
        allocations = size_positions(
            [("HIGH", 100.0, "s1"), ("LOW", 1.0, "s2")],
            sleeve_budget=300.0,
            portfolio_total=3000.0,
        )
        assert allocations["HIGH"] / allocations["LOW"] == pytest.approx(2.0, rel=0.01)

    def test_equal_scores_split_evenly(self):
        allocations = size_positions(
            [("A", 50.0, "s1"), ("B", 50.0, "s2")],
            sleeve_budget=200.0,
            portfolio_total=2000.0,
        )
        assert allocations["A"] == pytest.approx(allocations["B"])

    def test_single_name_cap_is_enforced_against_the_whole_portfolio(self):
        cap = config.get("portfolio.limits.max_single_name_pct_of_portfolio")
        allocations = size_positions(
            [("SOLO", 90.0, "s1")], sleeve_budget=900.0, portfolio_total=1000.0
        )
        assert allocations["SOLO"] <= cap * 1000.0 + 1e-6

    def test_segment_cap_limits_correlated_names(self):
        """NVDA plus AMD plus TSM is one bet on AI silicon wearing three names."""
        cap = config.get("portfolio.limits.max_segment_pct_of_portfolio")
        allocations = size_positions(
            [("A", 80.0, "silicon"), ("B", 80.0, "silicon"), ("C", 80.0, "silicon")],
            sleeve_budget=900.0,
            portfolio_total=1000.0,
        )
        assert sum(allocations.values()) <= cap * 1000.0 + 1e-6

    def test_positions_below_the_minimum_are_dropped(self):
        minimum = config.get("portfolio.limits.min_position_usd")
        allocations = size_positions(
            [(f"T{i}", 50.0, f"s{i}") for i in range(10)],
            sleeve_budget=minimum,  # spread ten ways, each far too small
            portfolio_total=10_000.0,
        )
        assert allocations == {}

    def test_no_candidates_gives_no_allocations(self):
        assert size_positions([], 500.0, 1000.0) == {}

    def test_zero_budget_gives_no_allocations(self):
        assert size_positions([("A", 50.0, "s")], 0.0, 1000.0) == {}


# ---------------------------------------------------------------------------
# Ledger arithmetic
# ---------------------------------------------------------------------------


class TestLedger:
    def buy(self, ticker="NVDA", amount=100.0, price=200.0, sleeve=Sleeve.SATELLITE):
        return Proposal(
            action=Act.BUY,
            ticker=ticker,
            sleeve=sleeve,
            amount=amount,
            shares=amount / price,
            price=price,
            thesis="quality rising",
            falsification="quality gate fails",
        )

    def test_buy_reduces_cash_and_opens_a_position(self):
        ledger = fresh()
        ledger.apply(self.buy(), price=200.0, when=AS_OF)
        assert ledger.cash == pytest.approx(900.0)
        assert ledger.position("NVDA").shares == pytest.approx(0.5)

    def test_thesis_and_falsification_are_recorded_at_entry(self):
        """The part no commercial screener can offer."""
        ledger = fresh()
        ledger.apply(self.buy(), price=200.0, when=AS_OF)
        held = ledger.position("NVDA")
        assert held.thesis
        assert held.falsification

    def test_buying_more_averages_into_the_position(self):
        ledger = fresh()
        ledger.apply(self.buy(amount=100.0, price=200.0), price=200.0, when=AS_OF)
        ledger.apply(self.buy(amount=100.0, price=400.0), price=400.0, when=AS_OF)
        held = ledger.position("NVDA")
        assert held.cost_basis == pytest.approx(200.0)
        assert held.shares == pytest.approx(0.75)
        assert held.average_price == pytest.approx(200.0 / 0.75)

    def test_buying_beyond_cash_is_refused(self):
        ledger = fresh(size=50.0)
        with pytest.raises(ValueError, match="insufficient cash"):
            ledger.apply(self.buy(amount=100.0), price=200.0, when=AS_OF)

    def test_partial_sale_reduces_basis_proportionally(self):
        """Resetting the basis on a partial sale silently corrupts every later
        profit-and-loss figure."""
        ledger = fresh()
        ledger.apply(self.buy(amount=200.0, price=200.0), price=200.0, when=AS_OF)
        held = ledger.position("NVDA")
        assert held.shares == pytest.approx(1.0)

        ledger.apply(
            Proposal(Act.SELL, "NVDA", Sleeve.SATELLITE, amount=100.0, shares=0.5, price=200.0),
            price=200.0,
            when=AS_OF,
        )
        held = ledger.position("NVDA")
        assert held.shares == pytest.approx(0.5)
        assert held.cost_basis == pytest.approx(100.0)

    def test_full_sale_closes_the_position(self):
        ledger = fresh()
        ledger.apply(self.buy(amount=200.0, price=200.0), price=200.0, when=AS_OF)
        ledger.apply(
            Proposal(Act.SELL, "NVDA", Sleeve.SATELLITE, amount=200.0, shares=1.0, price=200.0),
            price=200.0,
            when=AS_OF,
        )
        assert ledger.position("NVDA") is None
        assert ledger.cash == pytest.approx(1000.0)

    def test_selling_what_is_not_held_is_refused(self):
        with pytest.raises(ValueError, match="not held"):
            fresh().apply(
                Proposal(Act.SELL, "NVDA", Sleeve.SATELLITE, 100.0, 0.5, 200.0),
                price=200.0,
            )

    def test_unrealised_pnl_tracks_price(self):
        ledger = fresh()
        ledger.apply(self.buy(amount=200.0, price=200.0), price=200.0, when=AS_OF)
        held = ledger.position("NVDA")
        assert held.unrealised(300.0) == pytest.approx(100.0)
        assert held.unrealised(100.0) == pytest.approx(-100.0)
        assert held.unrealised(None) is None

    def test_rejections_are_logged(self):
        """A record of overrides is how you learn whether the system or the human
        is the weaker link."""
        ledger = fresh()
        ledger.record_rejection(self.buy(), "too concentrated for me", when=AS_OF)
        assert len(ledger.transactions) == 1
        assert ledger.transactions[0].accepted is False
        assert ledger.transactions[0].rejection_reason

    def test_round_trips_to_disk(self, tmp_path):
        ledger = fresh()
        ledger.apply(self.buy(), price=200.0, when=AS_OF)
        path = ledger.save(tmp_path / "ledger.json")
        reloaded = Ledger.load(path)
        assert reloaded.cash == pytest.approx(ledger.cash)
        assert reloaded.position("NVDA").thesis == "quality rising"
        assert len(reloaded.transactions) == 1

    def test_missing_ledger_starts_from_the_configured_wallet(self, tmp_path):
        ledger = Ledger.load(tmp_path / "absent.json")
        assert ledger.cash == config.get("portfolio.wallet.size")
        assert ledger.positions == []


class TestSleeveWeights:
    def test_all_cash_reads_one_hundred_percent_cash(self):
        weights = fresh().sleeve_weights(PRICES)
        assert weights[Sleeve.CASH] == pytest.approx(1.0)
        assert weights[Sleeve.CORE] == pytest.approx(0.0)

    def test_weights_reflect_holdings(self):
        ledger = fresh()
        ledger.positions.append(
            Position("VTI", Sleeve.CORE, 1.0, 400.0, AS_OF.isoformat())
        )
        ledger.cash = 600.0
        weights = ledger.sleeve_weights(PRICES)
        assert weights[Sleeve.CORE] == pytest.approx(0.4)
        assert weights[Sleeve.CASH] == pytest.approx(0.6)

    def test_missing_price_falls_back_to_cost_basis(self):
        ledger = fresh()
        ledger.positions.append(
            Position("UNKNOWN", Sleeve.CORE, 1.0, 250.0, AS_OF.isoformat())
        )
        ledger.cash = 750.0
        assert ledger.total_value(PRICES) == pytest.approx(1000.0)


# ---------------------------------------------------------------------------
# Plan building
# ---------------------------------------------------------------------------


class TestPlan:
    def test_fresh_wallet_fills_every_sleeve(self):
        plan = build_plan(
            signals_mod.SignalSet(AS_OF, [signal("NVDA", signals_mod.Decision.HOLD)]),
            fresh(),
            PRICES,
            as_of=AS_OF,
        )
        bought = {p.ticker for p in plan.proposals if p.action == Act.BUY}
        assert {"VTI", "SOXX", "GLD"} <= bought

    def test_no_qualifying_buy_produces_a_no_action_proposal(self):
        plan = build_plan(
            signals_mod.SignalSet(AS_OF, [signal("NVDA", signals_mod.Decision.HOLD)]),
            fresh(),
            PRICES,
            as_of=AS_OF,
        )
        assert any(p.action == Act.NO_ACTION for p in plan.proposals)

    def test_sector_etf_still_fills_the_sleeve_with_no_name_buys(self):
        """Sleeve size is a strategic allocation; the margin of safety governs
        single-stock selection. Without saying so the output looks contradictory."""
        plan = build_plan(
            signals_mod.SignalSet(AS_OF, [signal("NVDA", signals_mod.Decision.HOLD)]),
            fresh(),
            PRICES,
            as_of=AS_OF,
        )
        assert any(p.ticker == "SOXX" for p in plan.proposals)
        assert any("strategic allocation" in n for n in plan.notes)

    def test_buy_signals_produce_sized_name_proposals(self):
        plan = build_plan(
            signals_mod.SignalSet(
                AS_OF,
                [signal("NVDA", signals_mod.Decision.BUY, 80.0)],
            ),
            fresh(10_000.0),
            PRICES,
            segments={"NVDA": "ai_accelerators"},
            as_of=AS_OF,
        )
        buys = [p for p in plan.proposals if p.ticker == "NVDA"]
        assert buys and buys[0].action == Act.BUY
        assert buys[0].thesis and buys[0].falsification

    def test_expensive_share_warns_about_fractional_need(self):
        plan = build_plan(
            signals_mod.SignalSet(AS_OF, [signal("MU", signals_mod.Decision.BUY, 80.0)]),
            fresh(1000.0),
            PRICES,
            segments={"MU": "memory_storage"},
            as_of=AS_OF,
        )
        proposals = [p for p in plan.proposals if p.ticker == "MU"]
        if proposals:
            assert any("fractional" in w for w in proposals[0].warnings)

    def test_exit_decision_on_a_holding_flags_a_thesis_breach(self):
        ledger = fresh()
        ledger.positions.append(
            Position(
                "NVDA", Sleeve.SATELLITE, 1.0, 200.0, AS_OF.isoformat(),
                thesis="margins expanding",
            )
        )
        ledger.cash = 800.0
        plan = build_plan(
            signals_mod.SignalSet(AS_OF, [signal("NVDA", signals_mod.Decision.EXIT)]),
            ledger,
            PRICES,
            as_of=AS_OF,
        )
        assert plan.breaches
        assert "margins expanding" in plan.breaches[0]
        assert any(p.action == Act.SELL and p.ticker == "NVDA" for p in plan.proposals)

    def test_trim_sells_half_the_position(self):
        ledger = fresh()
        ledger.positions.append(
            Position("NVDA", Sleeve.SATELLITE, 2.0, 400.0, AS_OF.isoformat())
        )
        ledger.cash = 600.0
        plan = build_plan(
            signals_mod.SignalSet(AS_OF, [signal("NVDA", signals_mod.Decision.TRIM)]),
            ledger,
            PRICES,
            as_of=AS_OF,
        )
        sells = [p for p in plan.proposals if p.action == Act.SELL]
        assert sells and sells[0].shares == pytest.approx(1.0)

    def test_sleeves_inside_their_band_are_left_alone(self):
        ledger = fresh()
        ledger.positions.extend(
            [
                Position("VTI", Sleeve.CORE, 1.375, 550.0, AS_OF.isoformat()),
                Position("SOXX", Sleeve.SATELLITE, 0.4, 200.0, AS_OF.isoformat()),
                Position("GLD", Sleeve.GOLD, 0.375, 150.0, AS_OF.isoformat()),
            ]
        )
        ledger.cash = 100.0
        plan = build_plan(
            signals_mod.SignalSet(AS_OF, [signal("NVDA", signals_mod.Decision.HOLD)]),
            ledger,
            PRICES,
            as_of=AS_OF,
        )
        rebalances = [
            p for p in plan.proposals if p.ticker in ("VTI", "SOXX", "GLD")
        ]
        assert rebalances == []


class TestReport:
    def test_report_states_nothing_was_executed(self):
        ledger = fresh()
        plan = build_plan(
            signals_mod.SignalSet(AS_OF, [signal("NVDA", signals_mod.Decision.HOLD)]),
            ledger,
            PRICES,
            as_of=AS_OF,
        )
        text = report(plan, ledger, PRICES)
        assert "Nothing above has been executed" in text
        assert "needs your approval" in text

    def test_report_shows_drift_and_flags_breaches_of_the_band(self):
        ledger = fresh()
        plan = build_plan(
            signals_mod.SignalSet(AS_OF, [signal("NVDA", signals_mod.Decision.HOLD)]),
            ledger,
            PRICES,
            as_of=AS_OF,
        )
        text = report(plan, ledger, PRICES)
        assert "outside band" in text

    def test_report_shows_recorded_thesis_for_holdings(self):
        ledger = fresh()
        ledger.positions.append(
            Position(
                "NVDA", Sleeve.SATELLITE, 1.0, 200.0, AS_OF.isoformat(),
                thesis="HBM demand", falsification="margins fall two years",
            )
        )
        ledger.cash = 800.0
        plan = build_plan(
            signals_mod.SignalSet(AS_OF, [signal("NVDA", signals_mod.Decision.HOLD)]),
            ledger,
            PRICES,
            as_of=AS_OF,
        )
        text = report(plan, ledger, PRICES)
        assert "HBM demand" in text
        assert "falsifies if" in text

    def test_report_is_ascii_safe(self):
        ledger = fresh()
        plan = build_plan(
            signals_mod.SignalSet(AS_OF, [signal("NVDA", signals_mod.Decision.HOLD)]),
            ledger,
            PRICES,
            as_of=AS_OF,
        )
        report(plan, ledger, PRICES).encode("cp1252")

    def test_no_broker_configured_is_disclosed(self):
        ledger = fresh()
        plan = build_plan(
            signals_mod.SignalSet(AS_OF, [signal("NVDA", signals_mod.Decision.HOLD)]),
            ledger,
            PRICES,
            as_of=AS_OF,
        )
        assert "fractional shares are assumed" in report(plan, ledger, PRICES)
