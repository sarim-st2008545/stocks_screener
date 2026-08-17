"""The decision — BUY / ADD / HOLD / TRIM / EXIT, with its evidence.

Quality-at-a-reasonable-price, the standard long-only discipline: separate *is
this a good business?* from *is it available at a sensible price?*, and let
quality deterioration override price attractiveness in both directions.

Four gates run in order:

1. **Eligibility** — in universe, filings current, liquid enough. Fails here and
   nothing further is computed.
2. **Quality** — a name failing this is **never a buy at any price**. This is the
   rule that prevents value traps: cheapness is usually a consequence of
   deterioration, not an opportunity.
3. **Valuation** — price against fair value, with a required margin of safety.
4. **Corroboration** — institutional positioning, insider activity, cycle
   position. Adjusts confidence *within* a decision; it can never move one across
   the quality gate.

Two deliberate refusals:

**When nothing clears the margin of safety, the answer is no action.** The system
does not fall back to ranking the least-stretched names as though a relative
ordering were an absolute green light, and it does not relax the margin to
manufacture something actionable.

**Every decision carries what would falsify it.** A call without a stated
disproof is a tip, not research.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from src import config, cycle, fundamentals, prices, quality, scoring, universe, valuation
from src.facts import FactSet
from src.sec_client import SECClient


class Decision:
    STRONG_BUY = "STRONG BUY"
    BUY = "BUY"
    ADD = "ADD"
    HOLD = "HOLD"
    TRIM = "TRIM"
    EXIT = "EXIT"
    AVOID = "AVOID"
    NO_DATA = "NO DATA"


class QualityTrend:
    RISING = "rising"
    STABLE = "stable"
    DETERIORATING = "deteriorating"
    UNKNOWN = "unknown"


class ValuationBand:
    UNDERVALUED = "undervalued"
    FAIR = "fair"
    OVERVALUED = "overvalued"
    UNKNOWN = "unknown"


# The matrix. Rows are quality trend, columns are valuation band.
#
# Two asymmetries are deliberate. Deteriorating quality plus a cheap price is
# AVOID, not BUY: the cheapness is a symptom. And a quality-gate failure while
# held is EXIT regardless of how cheap the shares look.
MATRIX: dict[tuple[str, str], str] = {
    (QualityTrend.RISING, ValuationBand.UNDERVALUED): Decision.STRONG_BUY,
    (QualityTrend.RISING, ValuationBand.FAIR): Decision.ADD,
    (QualityTrend.RISING, ValuationBand.OVERVALUED): Decision.HOLD,
    (QualityTrend.STABLE, ValuationBand.UNDERVALUED): Decision.BUY,
    (QualityTrend.STABLE, ValuationBand.FAIR): Decision.HOLD,
    (QualityTrend.STABLE, ValuationBand.OVERVALUED): Decision.HOLD,
    (QualityTrend.DETERIORATING, ValuationBand.UNDERVALUED): Decision.AVOID,
    (QualityTrend.DETERIORATING, ValuationBand.FAIR): Decision.TRIM,
    (QualityTrend.DETERIORATING, ValuationBand.OVERVALUED): Decision.EXIT,
}

BUY_SIDE = (Decision.STRONG_BUY, Decision.BUY, Decision.ADD)


@dataclass
class Gate:
    """One gate's outcome and the checks behind it."""

    name: str
    passed: bool | None
    reasons: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        if self.passed is None:
            return f"{self.name}: not evaluable"
        return f"{self.name}: {'pass' if self.passed else 'FAIL'}"


@dataclass
class Signal:
    """A decision, everything that produced it, and what would disprove it."""

    ticker: str
    as_of: date
    decision: str
    quality_trend: str = QualityTrend.UNKNOWN
    valuation_band: ValuationBand | str = ValuationBand.UNKNOWN
    confidence: str = "moderate"
    gates: list[Gate] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    falsification: list[str] = field(default_factory=list)
    composite: float | None = None
    cycle_position: str = cycle.Position.UNKNOWN
    price: float | None = None
    fair_value: float | None = None
    margin_of_safety: float | None = None
    implied_growth: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_buy(self) -> bool:
        return self.decision in BUY_SIDE

    @property
    def actionable(self) -> bool:
        return self.decision in BUY_SIDE + (Decision.TRIM, Decision.EXIT)

    def report(self) -> str:
        out = [f"{self.ticker}  ->  {self.decision}   ({self.confidence} confidence)"]
        out.append(
            f"    quality {self.quality_trend} / valuation {self.valuation_band}"
            f"   cycle {self.cycle_position}"
            + (f"   composite {self.composite:.1f}" if self.composite is not None else "")
        )
        for gate in self.gates:
            out.append(f"    {gate.label}")
            for failure in gate.failures:
                out.append(f"        fails: {failure}")
        if self.price is not None and self.fair_value is not None:
            margin = "n/a" if self.margin_of_safety is None else f"{self.margin_of_safety:+.0%}"
            out.append(
                f"    price {self.price:,.2f}   fair value {self.fair_value:,.2f}"
                f"   margin {margin}"
            )
        for line in self.evidence:
            out.append(f"    for:     {line}")
        for line in self.contradictions:
            out.append(f"    against: {line}")
        for line in self.falsification:
            out.append(f"    would falsify: {line}")
        for line in self.notes:
            out.append(f"    note: {line}")
        return "\n".join(out)


# ---------------------------------------------------------------------------
# Gate 2 — quality
# ---------------------------------------------------------------------------


def quality_gate(assessment: quality.QualityAssessment, f: fundamentals.Fundamentals) -> Gate:
    """Whether the business is sound enough to own at any price."""
    gate = Gate("quality", True)

    if assessment.altman.value is not None:
        if assessment.altman.zone == "distress":
            gate.failures.append(
                f"Altman Z'' {assessment.altman.value:.2f} in the distress zone"
            )
        else:
            gate.reasons.append(f"Altman Z'' {assessment.altman.value:.2f} ({assessment.altman.zone})")

    coverage = f.interest_coverage
    floor = config.get("rules.balance_sheet.interest_coverage.warning_below")
    if coverage is not None:
        if coverage < floor:
            gate.failures.append(f"interest coverage {coverage:.1f}x below {floor}x")
        else:
            gate.reasons.append(f"interest coverage {coverage:.1f}x")

    spread = assessment.roic_wacc_spread
    if spread is not None:
        # A hard gate on ROIC < WACC is too sharp for an estimated WACC. Beta comes
        # from a regression and the equity risk premium is a 5% assumption, so a
        # 50bp shortfall sits inside the error of the estimate. Only a material
        # shortfall disqualifies; a marginal one is recorded as a concern.
        tolerance = config.get("rules.quality.roic_wacc_spread.gate_tolerance_bps") / 10_000
        if spread < -tolerance:
            gate.failures.append(
                f"ROIC {assessment.roic:.1%} falls {-spread:.1%} short of its "
                f"{assessment.wacc:.1%} cost of capital"
            )
        elif spread < 0:
            gate.reasons.append(
                f"ROIC {spread:+.1%} against cost of capital - marginal, inside the "
                "error of an estimated WACC"
            )
        else:
            gate.reasons.append(f"ROIC exceeds cost of capital by {spread:+.1%}")

    # An earnings-quality red flag is disqualifying, but a low conversion caused
    # by heavy capital investment is not the same thing and must not be treated
    # as one.
    if "red flag" in assessment.fcf_assessment:
        gate.failures.append(f"free cash flow conversion: {assessment.fcf_assessment}")

    if not gate.reasons and not gate.failures:
        gate.passed = None
        return gate
    gate.passed = not gate.failures
    return gate


def quality_trend(
    assessment: quality.QualityAssessment,
    position: cycle.CyclePosition,
    f: fundamentals.Fundamentals,
    quality_percentile: float | None = None,
) -> tuple[str, list[str]]:
    """Whether the business is getting better, holding, or slipping.

    Read from the year-over-year signals plus the margin trend, because a
    single-period snapshot cannot distinguish a good business from an improving
    one and the matrix treats those differently.

    Direction alone is not enough. NVIDIA's ratios all fell — gross margin 75% to
    71%, return on assets 65% to 46% — because its assets grew faster than its
    income, and reading that as "deteriorating" issued an EXIT on the
    highest-quality name in the pool. Falling from exceptional toward excellent is
    mean reversion; deterioration means falling toward or below acceptable. So a
    company still ranked highly on quality needs a severe decline, not a mild one,
    before the trend turns negative.
    """
    evidence: list[str] = []
    improving = 0
    worsening = 0

    yoy = {
        s.name: s.passed
        for s in assessment.piotroski.signals
        if s.name
        in ("ROA improving", "gross margin rising", "asset turnover rising", "leverage falling")
    }
    for name, passed in yoy.items():
        if passed is True:
            improving += 1
            evidence.append(f"{name}")
        elif passed is False:
            worsening += 1

    trend = position.gross_margin.direction
    if trend == "rising":
        improving += 1
        evidence.append("gross margin rising year over year")
    elif trend == "falling":
        worsening += 1

    if improving == 0 and worsening == 0:
        return QualityTrend.UNKNOWN, evidence
    if improving >= worsening + 2:
        return QualityTrend.RISING, evidence

    high_quality_floor = config.get("rules.scoring.high_quality_percentile")
    still_high = quality_percentile is not None and quality_percentile >= high_quality_floor
    margin = 4 if still_high else 2
    if worsening >= improving + margin:
        return QualityTrend.DETERIORATING, evidence
    if still_high and worsening > improving:
        evidence.append(
            f"ratios softening but quality still ranks at the {quality_percentile:.0f}th "
            "percentile of the pool - mean reversion from a peak, not decay"
        )
    return QualityTrend.STABLE, evidence


# ---------------------------------------------------------------------------
# Gate 3 — valuation
# ---------------------------------------------------------------------------


def valuation_band(valued: valuation.Valuation) -> tuple[str, list[str], list[str]]:
    """Place the price in a band, using whichever evidence is strongest.

    A conservative forward DCF sits below the market price for nearly every name
    in this sector, so leaning on it alone would label the whole universe
    overvalued and discriminate between nothing. When the DCF's own reliability
    grade is weak, the reverse DCF and the company's own multiple history carry
    the reading instead.
    """
    required = valued.margin_of_safety_required
    supporting: list[str] = []
    against: list[str] = []
    votes: list[str] = []

    margin = valued.implied_margin_of_safety
    if margin is not None and valued.dcf.reliability.startswith("reasonable"):
        if margin >= required:
            votes.append(ValuationBand.UNDERVALUED)
            supporting.append(f"price {margin:.0%} below base-case fair value")
        elif margin > -0.15:
            votes.append(ValuationBand.FAIR)
        else:
            votes.append(ValuationBand.OVERVALUED)
            against.append(f"price {-margin:.0%} above base-case fair value")
    elif margin is not None:
        supporting.append(
            f"DCF margin {margin:+.0%} but reliability is {valued.dcf.reliability}"
            " - not weighted"
        )

    gap = valued.expectations.gap
    if gap is not None:
        if gap <= 0:
            votes.append(ValuationBand.UNDERVALUED)
            supporting.append(
                f"price implies {valued.expectations.implied_growth:.0%} growth, at or below"
                f" the {valued.expectations.historical_growth:.0%} delivered"
            )
        elif gap <= 0.15:
            votes.append(ValuationBand.FAIR)
            supporting.append(f"price demands only {gap:.0%} more growth than delivered")
        else:
            votes.append(ValuationBand.OVERVALUED)
            against.append(
                f"price demands {gap:.0%} more annual growth than the company has delivered"
            )
    elif valued.expectations.note:
        votes.append(ValuationBand.OVERVALUED)
        against.append(f"reverse DCF: {valued.expectations.note}")

    own = valued.multiples.versus_own_history("pe")
    if "cheap vs history" in own:
        votes.append(ValuationBand.UNDERVALUED)
        supporting.append(f"P/E {own}")
    elif "expensive vs history" in own:
        votes.append(ValuationBand.OVERVALUED)
        against.append(f"P/E {own}")
    elif "in line" in own:
        votes.append(ValuationBand.FAIR)
        supporting.append(f"P/E {own}")

    if not votes:
        return ValuationBand.UNKNOWN, supporting, against

    # Majority of the available readings, with ties resolving to the more
    # cautious side: an unclear valuation should not produce a buy.
    counts = {band: votes.count(band) for band in set(votes)}
    best = max(counts.values())
    leaders = [band for band, count in counts.items() if count == best]
    if len(leaders) == 1:
        return leaders[0], supporting, against
    for band in (ValuationBand.OVERVALUED, ValuationBand.FAIR, ValuationBand.UNDERVALUED):
        if band in leaders:
            return band, supporting, against
    return ValuationBand.UNKNOWN, supporting, against


# ---------------------------------------------------------------------------
# Signal assembly
# ---------------------------------------------------------------------------


def falsification_for(
    decision: str,
    f: fundamentals.Fundamentals,
    valued: valuation.Valuation,
    position: cycle.CyclePosition,
) -> list[str]:
    """What would disprove this call — recorded so it can be checked later."""
    out: list[str] = []
    if decision in BUY_SIDE:
        out.append("quality gate fails at any future review (Altman, coverage, or ROIC spread)")
        if valued.expectations.implied_growth is not None:
            out.append(
                f"delivered growth falls durably below the "
                f"{valued.expectations.implied_growth:.0%} the price requires"
            )
        out.append("gross margin falls for two consecutive years")
    elif decision in (Decision.TRIM, Decision.EXIT):
        out.append("quality trend turns back up: margins and returns rising year over year")
        out.append("price falls far enough to restore the margin of safety")
    elif decision == Decision.HOLD:
        out.append(
            "price falls to a margin of safety, which would move this to a buy"
        )
        out.append("quality gate fails, which would move this to an exit")
    elif decision == Decision.AVOID:
        out.append("the quality failure is repaired and sustained for a full year")
    if position.position in (cycle.Position.PEAK, cycle.Position.LATE):
        out.append("cycle rolls over: margins fall from peak toward the middle of their range")
    return out


def assemble(
    constituent: universe.Constituent,
    facts: dict[str, Any] | None,
    history: prices.PriceHistory | None,
    as_of: date,
    composite: float | None = None,
    corroboration: Any | None = None,
    quality_percentile: float | None = None,
) -> Signal:
    """Run the gates and produce a decision for one company."""
    f = fundamentals.Fundamentals(FactSet(facts, as_of=as_of), constituent.ticker)

    eligibility = Gate("eligibility", True)
    if constituent.status == universe.Status.INSUFFICIENT_DATA:
        eligibility.passed = False
        eligibility.failures.extend(constituent.failures)
        return Signal(
            ticker=constituent.ticker,
            as_of=as_of,
            decision=Decision.NO_DATA,
            gates=[eligibility],
            notes=["cannot be analysed; hold sector exposure through the ETF sleeve instead"],
        )
    if constituent.status == universe.Status.SCREENED_OUT:
        eligibility.passed = False
        eligibility.failures.extend(constituent.failures)
    else:
        eligibility.reasons.append(f"{constituent.status} in {constituent.segment_label}")

    shares = f.shares_outstanding
    market_cap = (
        prices.market_cap(history, shares.value, as_of)
        if history is not None and shares.present
        else None
    )
    price = history.raw_close(as_of) if history is not None else None

    valued = valuation.value(f, market_cap=market_cap, price=price, as_of=as_of)
    assessment = quality.assess(f, market_cap=market_cap, wacc=valued.cost_of_capital.wacc)
    position = cycle.assess(f, cyclical_segment=cycle.is_cyclical(constituent.ticker))

    gate2 = quality_gate(assessment, f)
    trend, trend_evidence = quality_trend(
        assessment, position, f, quality_percentile=quality_percentile
    )
    band, supporting, against = valuation_band(valued)

    gate3 = Gate("valuation", band != ValuationBand.UNKNOWN)
    gate3.reasons.extend(supporting)
    gate3.failures.extend(against)

    gate4 = Gate("corroboration", None)
    evidence = list(trend_evidence) + supporting
    contradictions = list(against)

    if corroboration is not None:
        adjustment = corroboration.confidence_adjustment
        gate4.passed = True
        gate4.reasons.append(f"institutional positioning: {adjustment}")
        if corroboration.is_cluster:
            evidence.append(
                f"13F cluster: {len(corroboration.conviction_buyers)} managers bought with conviction"
            )
        if corroboration.is_consensus_exit:
            contradictions.append(
                f"several tracked managers reduced or exited ({len(corroboration.exited + corroboration.trimmed)})"
            )

    if position.valuation_caveat:
        contradictions.append(position.valuation_caveat)

    # -- decide -------------------------------------------------------------

    if eligibility.passed is False:
        decision = Decision.AVOID
    elif gate2.passed is False:
        # Never a buy at any price. This is the value-trap guard.
        decision = Decision.AVOID
    elif gate2.passed is None or trend == QualityTrend.UNKNOWN or band == ValuationBand.UNKNOWN:
        decision = Decision.HOLD
    else:
        decision = MATRIX[(trend, band)]

    # A peak-cycle name whose trailing earnings are not a fair basis cannot be a
    # STRONG BUY on those earnings. Downgrade rather than veto, and say why.
    notes: list[str] = []
    if decision == Decision.STRONG_BUY and position.earnings_repeatable is False:
        decision = Decision.BUY
        notes.append(
            "downgraded from STRONG BUY: trailing earnings are not a fair basis at this "
            "point in the cycle"
        )

    if decision == Decision.EXIT:
        # EXIT is an instruction to a holder. For a name not owned, the same
        # evidence means do not start, so both readings are stated rather than
        # implying a position exists.
        notes.append("EXIT applies if held; if not held this reads as AVOID")

    if decision in BUY_SIDE and constituent.stability_flag:
        notes.append(f"stability flag stands regardless of the score: {constituent.stability_flag}")

    confidence = "moderate"
    if corroboration is not None and corroboration.is_cluster and decision in BUY_SIDE:
        confidence = "higher"
    if contradictions and decision in BUY_SIDE:
        confidence = "lower"
    if gate2.passed is None or band == ValuationBand.UNKNOWN:
        confidence = "low - incomplete evidence"

    return Signal(
        ticker=constituent.ticker,
        as_of=as_of,
        decision=decision,
        quality_trend=trend,
        valuation_band=band,
        confidence=confidence,
        gates=[eligibility, gate2, gate3, gate4],
        evidence=evidence,
        contradictions=contradictions,
        falsification=falsification_for(decision, f, valued, position),
        composite=composite,
        cycle_position=position.position,
        price=price,
        fair_value=valued.dcf.base,
        margin_of_safety=valued.implied_margin_of_safety,
        implied_growth=valued.expectations.implied_growth,
        notes=notes + list(assessment.notes[:1]),
    )


@dataclass
class SignalSet:
    as_of: date
    signals: list[Signal] = field(default_factory=list)

    def buys(self) -> list[Signal]:
        return [s for s in self.signals if s.is_buy]

    def by_decision(self, decision: str) -> list[Signal]:
        return [s for s in self.signals if s.decision == decision]

    def ordered(self) -> list[Signal]:
        rank = {
            Decision.STRONG_BUY: 0,
            Decision.BUY: 1,
            Decision.ADD: 2,
            Decision.EXIT: 3,
            Decision.TRIM: 4,
            Decision.HOLD: 5,
            Decision.AVOID: 6,
            Decision.NO_DATA: 7,
        }
        return sorted(
            self.signals,
            key=lambda s: (rank.get(s.decision, 9), -(s.composite or 0)),
        )


def build(
    as_of: date | None = None,
    tickers: Iterable[str] | None = None,
    client: SECClient | None = None,
    with_corroboration: bool = False,
) -> SignalSet:
    as_of = as_of or date.today()
    client = client or SECClient()

    board = scoring.build(as_of=as_of, tickers=tickers, client=client)
    composites = {s.ticker: s.composite for s in board.scores}
    quality_pillars = {s.ticker: s.pillar("quality") for s in board.scores}

    corroborations: dict[str, Any] = {}
    if with_corroboration:
        from src import smart_money

        view = smart_money.build(as_of=as_of, tickers=tickers, client=client)
        corroborations = view.by_ticker

    snapshot = universe.build(as_of=as_of, client=client, tickers=tickers)
    signals: list[Signal] = []
    for constituent in snapshot.constituents:
        facts = client.company_facts(constituent.cik) if constituent.cik else None
        history = prices.load(constituent.ticker)
        signals.append(
            assemble(
                constituent,
                facts,
                history,
                as_of,
                composite=composites.get(constituent.ticker),
                corroboration=corroborations.get(constituent.ticker),
                quality_percentile=quality_pillars.get(constituent.ticker),
            )
        )
    return SignalSet(as_of=as_of, signals=signals)


def report(signal_set: SignalSet, verbose: bool = False) -> str:
    out = [f"Decisions as of {signal_set.as_of}", "=" * 96]
    counts: dict[str, int] = {}
    for signal in signal_set.signals:
        counts[signal.decision] = counts.get(signal.decision, 0) + 1
    out.append("  " + "   ".join(f"{k}: {v}" for k, v in sorted(counts.items())))
    out.append("")

    buys = signal_set.buys()
    if not buys:
        # The configured behaviour: say so rather than ranking the least-stretched
        # names as though a relative ordering were a green light.
        required = config.get("rules.valuation.margin_of_safety")
        target = config.get("portfolio.no_qualifying_buy.direct_contributions_to")
        out.append("  NO ACTION on individual names.")
        out.append(
            f"  Nothing clears the {required:.0%} margin of safety with an acceptable"
            " quality trend."
        )
        out.append(
            f"  Direct any contributions to the {target} sleeve instead. Long stretches"
        )
        out.append("  with no individual-stock buys are an expected output, not a fault.")
        out.append("")

    for signal in signal_set.ordered():
        if not verbose and signal.decision in (Decision.HOLD, Decision.NO_DATA):
            continue
        out.append(signal.report())
        out.append("")

    if not verbose:
        hidden = len(signal_set.by_decision(Decision.HOLD)) + len(
            signal_set.by_decision(Decision.NO_DATA)
        )
        if hidden:
            out.append(f"  ({hidden} HOLD/NO DATA names hidden; use --verbose to see them)")

    out.append("")
    out.append("  Every call above is a proposal for a human to approve or reject.")
    out.append("  Nothing here has been validated on historical data yet: the backtest")
    out.append("  (Phase 11) has not run, so no claim about performance can be made.")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Buy/hold/sell decisions with evidence")
    parser.add_argument("--as-of", help="ISO date (default today)")
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--verbose", action="store_true", help="include HOLD names")
    parser.add_argument(
        "--corroborate", action="store_true", help="include 13F positioning (slow)"
    )
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    result = build(
        as_of=as_of, tickers=args.tickers, with_corroboration=args.corroborate
    )
    print(report(result, verbose=args.verbose))


if __name__ == "__main__":
    main()
