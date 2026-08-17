"""Composite scoring — percentile ranks within the investable pool.

Everything before this measured companies one at a time. Scoring is the first
cross-sectional layer, and it answers a different question: not "is a 58% return
on capital good?" but "how does it compare to the other names you could actually
buy?" A percentile is far more robust than a hand-picked threshold, and it is the
comparison that matters when the portfolio can only hold a handful of positions.

Four rules govern it:

**Direction is asserted per metric and tested in both polarities.** The prior
codebase shipped an inverted sort that put a loss-making company top of
profitability and a 56%-margin company near the bottom. Every number looked
plausible and the ranking was upside down, so `higher_is_better` is explicit for
every metric and both directions are covered by tests.

**A missing metric is renormalised away, never scored zero.** Otherwise a company
is punished twice — once for the disclosure gap and again by a diluted total.

**Pillar weights are a hypothesis, not a law.** Every individual metric traces to
a published framework; the weights combining them do not. They are recorded in
config as `basis: hypothesis` and are to be validated by walk-forward backtest,
not tuned until the equity curve looks good.

**Cycle position is reported beside the score, not folded into it.** A company at
peak margins scores well on quality *because* margins are peaking. Hiding that
inside a composite would launder a warning into a recommendation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Iterable

from src import config, cycle, fundamentals, prices, quality, universe, valuation
from src.facts import FactSet
from src.sec_client import SECClient

BOARD_DIR = config.DATA_DIR / "pit" / "scores"


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Metric:
    """One ranked measure, and which way is better."""

    key: str
    pillar: str
    higher_is_better: bool
    label: str


METRICS: tuple[Metric, ...] = (
    # Quality and moat — Koller et al.'s value-creation test plus earnings quality.
    Metric("roic", "quality", True, "return on invested capital"),
    Metric("roic_wacc_spread", "quality", True, "ROIC less cost of capital"),
    Metric("fcf_conversion", "quality", True, "free cash flow / net income"),
    Metric("gross_margin", "quality", True, "gross margin"),
    Metric("operating_margin", "quality", True, "operating margin"),
    # Financial strength — Piotroski, Altman, and rating-agency leverage bands.
    Metric("piotroski", "financial_strength", True, "Piotroski F-Score"),
    Metric("altman_z", "financial_strength", True, "Altman Z''"),
    Metric("interest_coverage", "financial_strength", True, "interest coverage"),
    Metric("net_debt_to_ebitda", "financial_strength", False, "net debt / EBITDA"),
    Metric("current_ratio", "financial_strength", True, "current ratio"),
    # Valuation — yields rather than multiples, so loss-makers stay comparable,
    # plus the reverse-DCF gap, which is the most discriminating measure available.
    Metric("implied_growth_gap", "valuation", False, "growth the price demands, less delivered"),
    Metric("fcf_yield", "valuation", True, "free cash flow yield"),
    Metric("earnings_yield", "valuation", True, "earnings yield"),
    Metric("pe_vs_own_history", "valuation", False, "P/E against its own median"),
    # Growth — trailing, from filed history.
    Metric("revenue_growth", "growth", True, "revenue growth"),
    Metric("fcf_growth", "growth", True, "free cash flow growth"),
)

PILLARS: tuple[str, ...] = ("quality", "financial_strength", "valuation", "growth")


# ---------------------------------------------------------------------------
# Per-company inputs
# ---------------------------------------------------------------------------


@dataclass
class CompanyInputs:
    """Raw metric values for one company, before any ranking."""

    ticker: str
    segment: str
    status: str
    values: dict[str, float | None] = field(default_factory=dict)
    cycle_position: str = cycle.Position.UNKNOWN
    earnings_repeatable: bool | None = None
    stability_flag: str | None = None
    notes: list[str] = field(default_factory=list)


def gather(
    constituent: universe.Constituent,
    facts: dict[str, Any] | None,
    history: prices.PriceHistory | None,
    as_of: date,
) -> CompanyInputs:
    """Compute every scored metric for one company."""
    f = fundamentals.Fundamentals(FactSet(facts, as_of=as_of), constituent.ticker)
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

    # P/E against its own median, rather than against the pool: a semiconductor
    # multiple that looks rich against the market may be cheap against itself.
    pe_ratio: float | None = None
    own_pe = [v for v in valued.multiples.history.get("pe", []) if v and v > 0]
    if valued.multiples.pe and len(own_pe) >= 3:
        median = sorted(own_pe)[len(own_pe) // 2]
        if median > 0:
            pe_ratio = valued.multiples.pe / median

    revenue_growth: float | None = None
    revenue_series = position.revenue
    if revenue_series.observations >= 2 and revenue_series.values[1] > 0:
        revenue_growth = (revenue_series.values[0] / revenue_series.values[1]) - 1

    values: dict[str, float | None] = {
        "roic": f.roic,
        "roic_wacc_spread": assessment.roic_wacc_spread,
        "fcf_conversion": f.fcf_conversion,
        "gross_margin": f.gross_margin,
        "operating_margin": f.operating_margin,
        "piotroski": assessment.piotroski.normalised,
        "altman_z": assessment.altman.value,
        "interest_coverage": f.interest_coverage,
        "net_debt_to_ebitda": f.net_debt_to_ebitda,
        "current_ratio": f.current_ratio,
        "implied_growth_gap": valued.expectations.gap,
        "fcf_yield": valued.multiples.fcf_yield,
        "earnings_yield": valued.multiples.earnings_yield,
        "pe_vs_own_history": pe_ratio,
        "revenue_growth": revenue_growth,
        "fcf_growth": valued.growth.rate,
    }

    notes = list(assessment.notes)
    if valued.expectations.implied_growth is None and valued.expectations.note:
        # A price demanding growth beyond any credible band is a valuation
        # finding, and leaving the gap blank would quietly drop it from the rank.
        notes.append(f"reverse DCF: {valued.expectations.note}")

    return CompanyInputs(
        ticker=constituent.ticker,
        segment=constituent.segment,
        status=constituent.status,
        values=values,
        cycle_position=position.position,
        earnings_repeatable=position.earnings_repeatable,
        stability_flag=constituent.stability_flag,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def percentile_rank(
    value: float | None,
    population: Iterable[float],
    higher_is_better: bool,
) -> float | None:
    """Where `value` sits in `population`, 0 (worst) to 100 (best).

    Direction is a required argument rather than inferred, because inferring it
    is exactly how a ranking ends up inverted while every number still looks
    plausible.
    """
    if value is None:
        return None
    pool = [v for v in population if v is not None]
    if len(pool) < 2:
        return None
    at_or_below = sum(1 for v in pool if v <= value)
    percentile = (at_or_below / len(pool)) * 100
    return percentile if higher_is_better else 100 - percentile


@dataclass
class PillarScore:
    name: str
    score: float | None
    metric_scores: dict[str, float] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> str:
        total = len(self.metric_scores) + len(self.missing)
        return f"{len(self.metric_scores)}/{total}"


@dataclass
class CompanyScore:
    ticker: str
    segment: str
    composite: float | None
    pillars: dict[str, PillarScore] = field(default_factory=dict)
    missing_pillars: list[str] = field(default_factory=list)
    cycle_position: str = cycle.Position.UNKNOWN
    earnings_repeatable: bool | None = None
    stability_flag: str | None = None
    status: str = ""
    rank: int | None = None
    segment_rank: int | None = None
    notes: list[str] = field(default_factory=list)

    def pillar(self, name: str) -> float | None:
        entry = self.pillars.get(name)
        return entry.score if entry else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "segment": self.segment,
            "composite": self.composite,
            "rank": self.rank,
            "segment_rank": self.segment_rank,
            "pillars": {k: v.score for k, v in self.pillars.items()},
            "missing_pillars": self.missing_pillars,
            "cycle_position": self.cycle_position,
            "earnings_repeatable": self.earnings_repeatable,
            "stability_flag": self.stability_flag,
            "status": self.status,
        }


@dataclass
class ScoreBoard:
    as_of: date
    scores: list[CompanyScore]
    pool_size: int = 0

    def ranked(self) -> list[CompanyScore]:
        return sorted(
            [s for s in self.scores if s.composite is not None],
            key=lambda s: s.composite,
            reverse=True,
        )

    def unscored(self) -> list[CompanyScore]:
        return [s for s in self.scores if s.composite is None]

    def by_ticker(self, ticker: str) -> CompanyScore | None:
        return next((s for s in self.scores if s.ticker == ticker.upper()), None)

    def save(self, directory: Path = BOARD_DIR) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.as_of.isoformat()}.json"
        path.write_text(
            json.dumps(
                {
                    "as_of": self.as_of.isoformat(),
                    "pool_size": self.pool_size,
                    "weights": config.get("rules.scoring.weights"),
                    "scores": [s.to_dict() for s in self.scores],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path


def score_pool(inputs: list[CompanyInputs], as_of: date) -> ScoreBoard:
    """Rank every company against the pool, pillar by pillar."""
    weights = config.get("rules.scoring.weights")

    # The population for each metric is the pool itself, so a percentile means
    # "against the names you could actually buy" rather than against a fixed bar.
    populations: dict[str, list[float]] = {
        metric.key: [
            c.values[metric.key] for c in inputs if c.values.get(metric.key) is not None
        ]
        for metric in METRICS
    }

    scores: list[CompanyScore] = []
    for company in inputs:
        pillars: dict[str, PillarScore] = {}
        for pillar in PILLARS:
            metric_scores: dict[str, float] = {}
            missing: list[str] = []
            for metric in (m for m in METRICS if m.pillar == pillar):
                rank = percentile_rank(
                    company.values.get(metric.key),
                    populations[metric.key],
                    metric.higher_is_better,
                )
                if rank is None:
                    missing.append(metric.key)
                else:
                    metric_scores[metric.key] = rank
            # Renormalise across the metrics that were available rather than
            # treating an untagged figure as a zero score.
            pillar_score = (
                sum(metric_scores.values()) / len(metric_scores) if metric_scores else None
            )
            pillars[pillar] = PillarScore(pillar, pillar_score, metric_scores, missing)

        available = {name: p for name, p in pillars.items() if p.score is not None}
        missing_pillars = [name for name in PILLARS if pillars[name].score is None]
        if available:
            # Reweight over present pillars so a disclosure gap does not drag the
            # composite down as though the company had scored badly.
            total_weight = sum(weights[name] for name in available)
            composite = (
                sum(p.score * weights[name] for name, p in available.items()) / total_weight
            )
        else:
            composite = None

        scores.append(
            CompanyScore(
                ticker=company.ticker,
                segment=company.segment,
                composite=composite,
                pillars=pillars,
                missing_pillars=missing_pillars,
                cycle_position=company.cycle_position,
                earnings_repeatable=company.earnings_repeatable,
                stability_flag=company.stability_flag,
                status=company.status,
                notes=company.notes,
            )
        )

    board = ScoreBoard(as_of=as_of, scores=scores, pool_size=len(inputs))

    for position, entry in enumerate(board.ranked(), start=1):
        entry.rank = position

    for segment in {s.segment for s in scores}:
        members = [s for s in board.ranked() if s.segment == segment]
        for position, entry in enumerate(members, start=1):
            entry.segment_rank = position

    return board


def build(
    as_of: date | None = None,
    tickers: Iterable[str] | None = None,
    client: SECClient | None = None,
) -> ScoreBoard:
    """Score the investable universe as it stood on `as_of`."""
    as_of = as_of or date.today()
    client = client or SECClient()

    snapshot = universe.build(as_of=as_of, client=client, tickers=tickers)
    inputs: list[CompanyInputs] = []
    for constituent in snapshot.constituents:
        # Names that cannot be analysed at all are carried through unscored
        # rather than dropped, so the report shows what was excluded and why.
        if constituent.status == universe.Status.INSUFFICIENT_DATA:
            inputs.append(
                CompanyInputs(
                    ticker=constituent.ticker,
                    segment=constituent.segment,
                    status=constituent.status,
                    notes=list(constituent.failures),
                )
            )
            continue
        facts = client.company_facts(constituent.cik) if constituent.cik else None
        history = prices.load(constituent.ticker)
        inputs.append(gather(constituent, facts, history, as_of))

    return score_pool(inputs, as_of)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report(board: ScoreBoard, top: int | None = None) -> str:
    out = [
        f"AI-infrastructure scoreboard as of {board.as_of}   "
        f"(percentile ranks within {board.pool_size} names)",
        "=" * 100,
    ]
    weights = config.get("rules.scoring.weights")
    out.append(
        "  weights: "
        + ", ".join(f"{name} {weight:.0%}" for name, weight in weights.items())
        + "   [a hypothesis to be validated by backtest, not a law]"
    )
    out.append("")
    header = (
        f"  {'#':>3} {'tick':6} {'comp':>6} {'qual':>6} {'fin':>6} {'val':>6} {'grow':>6}"
        f"  {'cycle':15} {'segment':22} flags"
    )
    out.append(header)
    out.append("-" * 100)

    entries = board.ranked()
    if top:
        entries = entries[:top]
    for entry in entries:
        def cell(name: str) -> str:
            value = entry.pillar(name)
            return "  --  " if value is None else f"{value:6.1f}"

        flags = []
        if entry.earnings_repeatable is False:
            # Trough earnings are no fairer a basis than peak ones, so the label
            # has to follow the position rather than assume a peak.
            if entry.cycle_position in (cycle.Position.TROUGH, cycle.Position.EARLY):
                flags.append("trough earnings")
            elif entry.cycle_position == cycle.Position.LATE:
                flags.append("late-cycle earnings")
            else:
                flags.append("peak earnings")
        if entry.stability_flag:
            flags.append("stability flag")
        if entry.missing_pillars:
            flags.append(f"no {'/'.join(entry.missing_pillars)}")
        out.append(
            f"  {entry.rank:>3} {entry.ticker:6} {entry.composite:6.1f} "
            f"{cell('quality')} {cell('financial_strength')} {cell('valuation')} "
            f"{cell('growth')}  {entry.cycle_position:15} {entry.segment:22} "
            f"{'; '.join(flags)}"
        )

    unscored = board.unscored()
    if unscored:
        out.append("")
        out.append("  unscored:")
        for entry in unscored:
            reason = entry.notes[0] if entry.notes else "no metrics available"
            out.append(f"    {entry.ticker:6} {entry.status:20} {reason}")

    out.append("")
    out.append(
        "  A high score is not a buy. It ranks quality and price within the pool;"
    )
    out.append(
        "  the decision matrix (Phase 9) can still veto on quality or cycle position."
    )
    return "\n".join(out)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Score the AI-infrastructure universe")
    parser.add_argument("--as-of", help="ISO date to score as of (default today)")
    parser.add_argument("--top", type=int, help="show only the top N")
    parser.add_argument("--save", action="store_true", help="persist a dated scoreboard")
    parser.add_argument("--tickers", nargs="*", help="limit to these tickers")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    board = build(as_of=as_of, tickers=args.tickers)
    print(report(board, top=args.top))
    if args.save:
        print(f"\nscoreboard written to {board.save()}")


if __name__ == "__main__":
    main()
