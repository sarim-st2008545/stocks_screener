"""
Fundamental scoring for the compliant watchlist.

Compliance says what you may own; this says what is worth owning. It does not
predict prices and makes no attempt to - every number here is an accounting
fact from a filing, or an arithmetic combination of them.

Scores are percentile ranks within the investable pool (PASS + REVIEW), not
absolute grades. A profitability score of 80 means "more profitable than 80% of
the names you could actually buy", which is the comparison that matters and is
far more robust than thresholds someone picked by hand.

Five pillars:

  Growth        revenue and free cash flow, year over year
  Profitability operating, net and free-cash-flow margins
  Quality       return on equity and return on invested capital
  Leverage      net debt to EBITDA, debt to equity  (lower is better)
  Valuation     free-cash-flow and earnings yields, EV/EBITDA, ranked against
                sector peers where the sector has enough members

Yields are used rather than P/E and similar multiples: a loss-making company has
a meaningful negative earnings yield but a meaningless negative P/E.

Metric selection was driven by measured XBRL coverage across the watchlist.
Gross margin is deliberately absent - only 60% of filers tag GrossProfit, and a
pillar that silently vanishes for four names in ten is worse than no pillar.

Usage:
    python scoring.py
    python scoring.py --top 30
    python scoring.py --pass-only
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import requests

from aaoifi_screener import (
    CACHE_DIR,
    CASH_CONCEPTS,
    REVENUE_CONCEPTS,
    SHORT_TERM_INVESTMENT_CONCEPTS,
    USER_AGENT,
    FactSelector,
    Window,
    align_windows,
    resolve_debt,
)
from universe import DATA_DIR, BulkFactsArchive

# ---------------------------------------------------------------------------
# Concepts, ordered by priority within each measure
# ---------------------------------------------------------------------------

OPERATING_INCOME_CONCEPTS = ["OperatingIncomeLoss"]
NET_INCOME_CONCEPTS = ["NetIncomeLoss", "ProfitLoss"]
OCF_CONCEPTS = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
]
CAPEX_CONCEPTS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsForCapitalImprovements",
]
TAX_CONCEPTS = ["IncomeTaxExpenseBenefit"]
PRETAX_CONCEPTS = [
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
]
DA_CONCEPTS = [
    "DepreciationDepletionAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "Depreciation",
]
EQUITY_CONCEPTS = [
    "StockholdersEquity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
]

# Used when a company's effective rate cannot be computed (loss years give
# nonsense rates). Roughly the US federal statutory rate.
DEFAULT_TAX_RATE = 0.21

# A sector needs at least this many members before valuation is ranked within
# it; below that the peer group is too small to say anything.
MIN_SECTOR_PEERS = 5

PILLAR_WEIGHTS = {
    "growth": 0.25,
    "profitability": 0.25,
    "quality": 0.20,
    "leverage": 0.15,
    "valuation": 0.15,
}


# ---------------------------------------------------------------------------
# Raw fundamentals
# ---------------------------------------------------------------------------


@dataclass
class Fundamentals:
    ticker: str
    cik: int | None = None
    sic: int | None = None
    status: str = ""
    market_cap: float | None = None
    period: str = ""

    revenue: float | None = None
    revenue_prior: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    ocf: float | None = None
    capex: float | None = None
    fcf: float | None = None
    fcf_prior: float | None = None
    depreciation: float | None = None
    ebitda: float | None = None

    equity: float | None = None
    debt: float | None = None
    cash: float | None = None
    net_debt: float | None = None

    # Derived ratios
    revenue_growth: float | None = None
    fcf_growth: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    fcf_margin: float | None = None
    roe: float | None = None
    roic: float | None = None
    net_debt_to_ebitda: float | None = None
    debt_to_equity: float | None = None
    fcf_yield: float | None = None
    earnings_yield: float | None = None
    ev_to_ebitda: float | None = None

    gaps: list[str] = field(default_factory=list)


def _ttm(selector: FactSelector, concepts: list[str], anchor: date | None) -> Window | None:
    """TTM figure aligned to an anchor period end, or the newest if no anchor."""
    candidates = selector.ttm_candidates_first(concepts)
    if not candidates:
        return None
    if anchor is None:
        return max(candidates, key=lambda w: (w.end, w.basis == "annual"))
    aligned = [c for c in candidates if abs((c.end - anchor).days) <= 45]
    if not aligned:
        return None
    return max(aligned, key=lambda w: (w.end, w.basis == "annual"))


def _prior_year(candidates: list[Window], anchor: date) -> Window | None:
    """The TTM window ending roughly twelve months before the anchor."""
    older = [c for c in candidates if 300 <= (anchor - c.end).days <= 430]
    if not older:
        return None
    return max(older, key=lambda w: (w.end, w.basis == "annual"))


def _safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def extract_fundamentals(
    ticker: str,
    facts: dict[str, Any] | None,
    market_cap: float | None,
    status: str = "",
    sic: int | None = None,
    cik: int | None = None,
    reference_date: date | None = None,
) -> Fundamentals:
    """Pull every metric onto one consistent trailing-twelve-month window."""
    out = Fundamentals(ticker=ticker, cik=cik, sic=sic, status=status, market_cap=market_cap)
    if not facts:
        out.gaps.append("no XBRL facts")
        return out

    selector = FactSelector(facts, reference_date=reference_date)

    revenue_candidates = selector.ttm_candidates_first(REVENUE_CONCEPTS)
    if not revenue_candidates:
        out.gaps.append("no usable revenue period")
        return out

    revenue_window = max(revenue_candidates, key=lambda w: (w.end, w.basis == "annual"))
    anchor = revenue_window.end
    out.revenue = revenue_window.value
    out.period = f"{revenue_window.start}..{revenue_window.end}"

    prior = _prior_year(revenue_candidates, anchor)
    out.revenue_prior = prior.value if prior else None
    if prior is None:
        out.gaps.append("no prior-year revenue")

    # Every flow metric is pinned to the revenue window so ratios between them
    # describe the same twelve months.
    for attr, concepts in (
        ("operating_income", OPERATING_INCOME_CONCEPTS),
        ("net_income", NET_INCOME_CONCEPTS),
        ("ocf", OCF_CONCEPTS),
        ("capex", CAPEX_CONCEPTS),
        ("depreciation", DA_CONCEPTS),
    ):
        window = _ttm(selector, concepts, anchor)
        setattr(out, attr, window.value if window else None)
        if window is None:
            out.gaps.append(attr.replace("_", " "))

    if out.ocf is not None:
        # Capex is reported as a positive outflow in the cash flow statement.
        out.fcf = out.ocf - abs(out.capex or 0.0)

    ocf_candidates = selector.ttm_candidates_first(OCF_CONCEPTS)
    prior_ocf = _prior_year(ocf_candidates, anchor) if ocf_candidates else None
    capex_candidates = selector.ttm_candidates_first(CAPEX_CONCEPTS)
    prior_capex = _prior_year(capex_candidates, anchor) if capex_candidates else None
    if prior_ocf is not None:
        out.fcf_prior = prior_ocf.value - abs(prior_capex.value if prior_capex else 0.0)

    if out.operating_income is not None and out.depreciation is not None:
        out.ebitda = out.operating_income + out.depreciation

    equity = selector.instant_first(EQUITY_CONCEPTS)
    out.equity = equity.value if equity else None

    debt = resolve_debt(selector)
    out.debt = debt.value

    # Cash for net debt is cash plus short-term investments only. The
    # compliance screen's liquid-assets figure also folds in long-term
    # securities, which are not available to repay debt on demand.
    cash_fact = selector.instant_first(CASH_CONCEPTS)
    short_term = selector.instant_max(SHORT_TERM_INVESTMENT_CONCEPTS)
    if cash_fact or short_term:
        out.cash = (cash_fact.value if cash_fact else 0.0) + (
            short_term.value if short_term else 0.0
        )
    if out.debt is not None:
        out.net_debt = out.debt - (out.cash or 0.0)

    _derive(out, selector, anchor)
    return out


def _derive(out: Fundamentals, selector: FactSelector, anchor: date) -> None:
    out.revenue_growth = (
        (out.revenue - out.revenue_prior) / abs(out.revenue_prior)
        if out.revenue is not None and out.revenue_prior
        else None
    )
    out.fcf_growth = (
        (out.fcf - out.fcf_prior) / abs(out.fcf_prior)
        if out.fcf is not None and out.fcf_prior
        else None
    )

    out.operating_margin = _safe_div(out.operating_income, out.revenue)
    out.net_margin = _safe_div(out.net_income, out.revenue)
    out.fcf_margin = _safe_div(out.fcf, out.revenue)

    # Negative equity makes return on equity meaningless rather than excellent.
    out.roe = _safe_div(out.net_income, out.equity) if (out.equity or 0) > 0 else None

    tax_window = _ttm(selector, TAX_CONCEPTS, anchor)
    pretax_window = _ttm(selector, PRETAX_CONCEPTS, anchor)
    tax_rate = DEFAULT_TAX_RATE
    if tax_window and pretax_window and pretax_window.value > 0:
        candidate = tax_window.value / pretax_window.value
        if 0.0 <= candidate <= 0.5:
            tax_rate = candidate

    invested = (out.equity or 0.0) + (out.debt or 0.0) - (out.cash or 0.0)
    if out.operating_income is not None and invested > 0:
        out.roic = out.operating_income * (1 - tax_rate) / invested

    if out.ebitda and out.ebitda > 0:
        out.net_debt_to_ebitda = _safe_div(out.net_debt, out.ebitda)
        if out.market_cap:
            enterprise_value = out.market_cap + (out.debt or 0.0) - (out.cash or 0.0)
            out.ev_to_ebitda = enterprise_value / out.ebitda

    out.debt_to_equity = _safe_div(out.debt, out.equity) if (out.equity or 0) > 0 else None
    out.fcf_yield = _safe_div(out.fcf, out.market_cap)
    out.earnings_yield = _safe_div(out.net_income, out.market_cap)


# ---------------------------------------------------------------------------
# Percentile scoring
# ---------------------------------------------------------------------------


def percentile_ranks(values: dict[str, float], higher_is_better: bool = True) -> dict[str, float]:
    """Map each ticker's value to its 0-100 percentile among those that have one.

    Ties share the average rank, so a metric where half the field reports the
    same number does not hand one of them an arbitrary advantage.
    """
    if not values:
        return {}
    # Best value first, so position 0 scores 100 regardless of direction:
    # descending when larger is better, ascending when smaller is better.
    ordered = sorted(values.items(), key=lambda kv: kv[1], reverse=higher_is_better)
    n = len(ordered)
    if n == 1:
        return {ordered[0][0]: 50.0}

    ranks: dict[str, float] = {}
    index = 0
    while index < n:
        stop = index
        while stop + 1 < n and ordered[stop + 1][1] == ordered[index][1]:
            stop += 1
        average_position = (index + stop) / 2
        score = 100.0 * (n - 1 - average_position) / (n - 1)
        for position in range(index, stop + 1):
            ranks[ordered[position][0]] = score
        index = stop + 1
    return ranks


@dataclass
class Score:
    ticker: str
    status: str = ""
    market_cap: float | None = None
    sector_peers: int = 0
    growth: float | None = None
    profitability: float | None = None
    quality: float | None = None
    leverage: float | None = None
    valuation: float | None = None
    composite: float | None = None
    pillars_missing: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# metric -> (attribute, higher_is_better)
PILLAR_METRICS: dict[str, list[tuple[str, bool]]] = {
    "growth": [("revenue_growth", True), ("fcf_growth", True)],
    "profitability": [
        ("operating_margin", True),
        ("net_margin", True),
        ("fcf_margin", True),
    ],
    "quality": [("roe", True), ("roic", True)],
    "leverage": [("net_debt_to_ebitda", False), ("debt_to_equity", False)],
    "valuation": [
        ("fcf_yield", True),
        ("earnings_yield", True),
        ("ev_to_ebitda", False),
    ],
}

# Valuation only means something against comparable businesses, so these are
# ranked within sector when the peer group is big enough.
SECTOR_RELATIVE_PILLARS = {"valuation"}


def _sector_key(sic: int | None) -> int | None:
    """SIC major group - the first two digits."""
    return sic // 100 if sic is not None else None


def score_universe(
    fundamentals: list[Fundamentals], weights: dict[str, float] | None = None
) -> list[Score]:
    weights = weights or PILLAR_WEIGHTS
    by_ticker = {f.ticker: f for f in fundamentals}

    sectors: dict[int | None, list[str]] = {}
    for item in fundamentals:
        sectors.setdefault(_sector_key(item.sic), []).append(item.ticker)

    pillar_scores: dict[str, dict[str, float]] = {p: {} for p in PILLAR_METRICS}

    for pillar, metrics in PILLAR_METRICS.items():
        metric_ranks: list[dict[str, float]] = []
        for attribute, higher_better in metrics:
            if pillar in SECTOR_RELATIVE_PILLARS:
                ranks: dict[str, float] = {}
                for _, members in sectors.items():
                    pool = {
                        t: getattr(by_ticker[t], attribute)
                        for t in members
                        if getattr(by_ticker[t], attribute) is not None
                    }
                    # Too few peers to compare against; fall back to the
                    # whole pool rather than inventing a sector percentile.
                    if len(pool) < MIN_SECTOR_PEERS:
                        continue
                    ranks.update(percentile_ranks(pool, higher_better))
                remaining = {
                    f.ticker: getattr(f, attribute)
                    for f in fundamentals
                    if f.ticker not in ranks and getattr(f, attribute) is not None
                }
                ranks.update(percentile_ranks(remaining, higher_better))
            else:
                pool = {
                    f.ticker: getattr(f, attribute)
                    for f in fundamentals
                    if getattr(f, attribute) is not None
                }
                ranks = percentile_ranks(pool, higher_better)
            metric_ranks.append(ranks)

        for item in fundamentals:
            present = [r[item.ticker] for r in metric_ranks if item.ticker in r]
            if present:
                pillar_scores[pillar][item.ticker] = statistics.fmean(present)

    scores: list[Score] = []
    for item in fundamentals:
        score = Score(
            ticker=item.ticker,
            status=item.status,
            market_cap=item.market_cap,
            sector_peers=len(sectors.get(_sector_key(item.sic), [])),
        )
        available: dict[str, float] = {}
        for pillar in PILLAR_METRICS:
            value = pillar_scores[pillar].get(item.ticker)
            setattr(score, pillar, round(value, 1) if value is not None else None)
            if value is None:
                score.pillars_missing.append(pillar)
            else:
                available[pillar] = value

        if available:
            # Renormalise so a name missing one pillar is not penalised twice -
            # once by lacking the data and again by a diluted total.
            total_weight = sum(weights[p] for p in available)
            score.composite = round(
                sum(available[p] * weights[p] for p in available) / total_weight, 1
            )
        scores.append(score)

    scores.sort(key=lambda s: (s.composite is None, -(s.composite or 0)))
    return scores


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


def load_screen_rows(pass_only: bool = False) -> list[dict[str, str]]:
    path = DATA_DIR / "universe_screen.csv"
    if not path.exists():
        print(f"  ! {path} not found - run universe.py first")
        return []
    wanted = {"PASS"} if pass_only else {"PASS", "REVIEW"}
    with open(path, encoding="utf-8") as handle:
        return [r for r in csv.DictReader(handle) if r["status"] in wanted]


def _to_float(raw: str | None) -> float | None:
    try:
        return float(raw) if raw else None
    except ValueError:
        return None


def _to_int(raw: str | None) -> int | None:
    try:
        return int(float(raw)) if raw else None
    except ValueError:
        return None


def build(pass_only: bool = False, reference_date: date | None = None) -> list[Fundamentals]:
    rows = load_screen_rows(pass_only)
    if not rows:
        return []

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    archive = BulkFactsArchive(CACHE_DIR / "companyfacts.zip", session)
    if not archive.download():
        return []

    print(f"Extracting fundamentals for {len(rows)} names")
    results: list[Fundamentals] = []
    try:
        for index, row in enumerate(rows, start=1):
            if index % 100 == 0:
                print(f"  {index}/{len(rows)}", flush=True)
            cik = _to_int(row.get("cik"))
            results.append(
                extract_fundamentals(
                    ticker=row["ticker"],
                    facts=archive.facts(cik) if cik else None,
                    market_cap=_to_float(row.get("market_cap")),
                    status=row.get("status", ""),
                    sic=_to_int(row.get("sic")),
                    cik=cik,
                    reference_date=reference_date,
                )
            )
    finally:
        archive.close()
    return results


def write_outputs(
    fundamentals: list[Fundamentals], scores: list[Score], out_dir: Path = DATA_DIR
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    by_ticker = {f.ticker: f for f in fundamentals}

    rows = []
    for score in scores:
        row: dict[str, Any] = {k: v for k, v in score.to_dict().items()}
        row["pillars_missing"] = " | ".join(score.pillars_missing)
        raw = by_ticker[score.ticker]
        for key, value in asdict(raw).items():
            if key in ("ticker", "status", "market_cap", "gaps"):
                continue
            row[key] = value
        row["gaps"] = " | ".join(raw.gaps)
        rows.append(row)

    if rows:
        with open(out_dir / "scores.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    # Compact lookup for the catalyst monitor to rank events by company quality.
    (out_dir / "scores.json").write_text(
        json.dumps(
            {s.ticker: s.composite for s in scores if s.composite is not None},
            indent=2,
            sort_keys=True,
        )
    )


def print_table(scores: list[Score], limit: int) -> None:
    print(f"\n{'#':>3}  {'TICKER':7s} {'ST':6s} {'COMP':>5s} "
          f"{'GRW':>5s} {'PROF':>5s} {'QUAL':>5s} {'LEV':>5s} {'VAL':>5s}  missing")
    print("-" * 78)
    for position, score in enumerate(scores[:limit], start=1):
        def cell(value: float | None) -> str:
            return f"{value:5.1f}" if value is not None else "    -"

        print(
            f"{position:3d}  {score.ticker:7s} {score.status[:6]:6s} "
            f"{cell(score.composite)} {cell(score.growth)} {cell(score.profitability)} "
            f"{cell(score.quality)} {cell(score.leverage)} {cell(score.valuation)}  "
            f"{','.join(p[:4] for p in score.pillars_missing)}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Fundamental scoring for the watchlist")
    parser.add_argument("--top", type=int, default=25, help="Rows to print")
    parser.add_argument("--pass-only", action="store_true", help="Score only PASS names")
    parser.add_argument("--as-of", help="Reference date for fact staleness, YYYY-MM-DD")
    args = parser.parse_args()

    reference_date = date.fromisoformat(args.as_of) if args.as_of else None
    fundamentals = build(pass_only=args.pass_only, reference_date=reference_date)
    if not fundamentals:
        return

    scores = score_universe(fundamentals)
    write_outputs(fundamentals, scores)

    scored = [s for s in scores if s.composite is not None]
    print(f"\nScored {len(scored)} of {len(scores)} names")
    complete = sum(1 for s in scores if not s.pillars_missing)
    print(f"  {complete} have all five pillars")
    print_table(scores, args.top)
    print(f"\nWritten to {DATA_DIR / 'scores.csv'}")


if __name__ == "__main__":
    main()
