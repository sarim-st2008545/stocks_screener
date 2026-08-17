"""Universe construction — who is in the investable set, and on what date.

Membership is **curated by thesis, screened by rule**. The two halves matter
separately:

*Curated* — which companies belong to an AI-infrastructure thesis is a judgement,
and SIC codes cannot make it. Measured against live EDGAR, Amazon files as
"Retail-Catalog & Mail-Order Houses", Entegris as "Plastics Products", and KLA as
"Optical Instruments & Lenses". A pure classification screen would both admit
irrelevant names and miss real ones, so the candidate list lives in
`config/universe.yaml` with a stated reason per name.

*Screened* — whether a candidate is currently investable is a measurement, and it
is re-derived from filings and prices at the as-of date every time: market cap,
liquidity, and cash generation. Nothing measurable is trusted from config.

**The survivorship limitation, stated plainly.** Because the candidate list was
written in 2026, it contains companies that survived to 2026. Running a backtest
over it inherits that bias: names that were credible AI-infrastructure plays in
2018 and were then acquired or wiped out are absent. This module therefore does
two things rather than pretending the problem away — it records a dated snapshot
on every run so that *forward* history accumulates honestly, and it exposes
`sic_peers()` so the semiconductor core can be reconstructed rule-based from all
SEC filers when the backtester needs a genuinely unbiased universe. See README
§10 for how that bounds what a backtest over this universe can claim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from src import config, prices
from src.facts import FactSet
from src.sec_client import SECClient

SNAPSHOT_DIR = config.DATA_DIR / "pit" / "universe"

# Cash generation, the mandate's "established and cash-generative" test.
OCF_CONCEPTS = [
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
]

# Enough of a core statement set to say a company is analysable at all.
CORE_CONCEPTS = ["Assets", "NetIncomeLoss"] + OCF_CONCEPTS[:1]

# SIC codes whose filers are semiconductor or semiconductor-equipment businesses.
# Used only for rule-based peer discovery, never as the membership definition.
SEMI_SIC_CODES = frozenset({3674, 3672, 3559, 3827, 3825, 3823, 3679, 3576, 3572, 3571})


class Status:
    """Why a candidate is or is not investable today."""

    INVESTABLE = "INVESTABLE"
    SPECULATIVE = "SPECULATIVE"  # passes screens but carries a stability flag
    SCREENED_OUT = "SCREENED_OUT"  # failed a measurable screen
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # cannot be analysed at all


@dataclass
class Constituent:
    """One candidate, its annotations, and what the screens measured."""

    ticker: str
    segment: str
    segment_label: str
    note: str = ""
    stability_flag: str | None = None
    cyclical: bool = False

    cik: int | None = None
    company_name: str | None = None
    sic: int | None = None
    sic_description: str | None = None

    market_cap: float | None = None
    price: float | None = None
    shares_outstanding: float | None = None
    avg_dollar_volume: float | None = None
    operating_cash_flow: float | None = None
    reporting_currency: str = "USD"
    latest_filing: date | None = None
    price_staleness_days: int | None = None

    status: str = Status.INSUFFICIENT_DATA
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def investable(self) -> bool:
        return self.status in (Status.INVESTABLE, Status.SPECULATIVE)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for key in ("latest_filing",):
            if out[key] is not None:
                out[key] = str(out[key])
        return out


@dataclass
class UniverseSnapshot:
    """The universe as it stood on one date."""

    as_of: date
    constituents: list[Constituent]

    def investable(self) -> list[Constituent]:
        return [c for c in self.constituents if c.investable]

    def by_status(self, status: str) -> list[Constituent]:
        return [c for c in self.constituents if c.status == status]

    def by_segment(self) -> dict[str, list[Constituent]]:
        out: dict[str, list[Constituent]] = {}
        for c in self.constituents:
            out.setdefault(c.segment, []).append(c)
        return out

    def tickers(self) -> list[str]:
        return [c.ticker for c in self.investable()]

    # -- persistence --------------------------------------------------------

    def save(self, directory: Path = SNAPSHOT_DIR) -> Path:
        """Write a dated snapshot, so forward membership history accumulates.

        This is the only defence against survivorship bias that a curated
        universe can offer: from today onward, what was in the set on each date
        is recorded rather than reconstructed from hindsight.
        """
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.as_of.isoformat()}.json"
        path.write_text(
            json.dumps(
                {
                    "as_of": self.as_of.isoformat(),
                    "constituents": [c.to_dict() for c in self.constituents],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def load(path: Path) -> dict[str, Any]:
        return json.loads(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Candidate list
# ---------------------------------------------------------------------------


def candidates() -> list[Constituent]:
    """The curated candidate list from config, with annotations attached."""
    out: list[Constituent] = []
    for segment, spec in config.get("universe.segments").items():
        for member in spec["members"]:
            out.append(
                Constituent(
                    ticker=member["ticker"].upper(),
                    segment=segment,
                    segment_label=spec.get("label", segment),
                    note=member.get("note", ""),
                    stability_flag=member.get("stability_flag"),
                    cyclical=bool(spec.get("cyclical", False)),
                )
            )
    return out


# ---------------------------------------------------------------------------
# Screening
# ---------------------------------------------------------------------------


def screen(
    constituent: Constituent,
    facts: dict[str, Any] | None,
    history: prices.PriceHistory | None,
    as_of: date,
) -> Constituent:
    """Measure one candidate against the screens at `as_of`.

    Every threshold comes from `config/universe.yaml`. A screen that cannot be
    evaluated is recorded as unevaluated, never as passed — a missing market cap
    must not slip a name through a market-cap floor.
    """
    min_cap = config.get("universe.screens.min_market_cap_usd")
    min_volume = config.get("universe.screens.min_avg_daily_dollar_volume")
    require_ocf = config.get("universe.screens.require_positive_operating_cash_flow")

    view = FactSet(facts, as_of=as_of) if facts else None
    if view is not None:
        constituent.reporting_currency = view.reporting_currency
        quality = view.data_quality(required=CORE_CONCEPTS)
        constituent.latest_filing = quality["latest_filing"]

        shares = view.shares_outstanding()
        if shares is not None:
            constituent.shares_outstanding = shares.value
        else:
            # companyfacts strips class dimensions, so multi-class filers report
            # a breakdown with no labels. Refusing to guess costs a market cap.
            constituent.notes.append("share count unresolved (multi-class filer)")

        ocf = view.ttm(OCF_CONCEPTS)
        if ocf is not None:
            constituent.operating_cash_flow = ocf.value

        if not quality["analysable"]:
            constituent.status = Status.INSUFFICIENT_DATA
            missing = ", ".join(quality["missing_concepts"][:3])
            constituent.failures.append(f"core statements unavailable ({missing})")
            return constituent
    else:
        constituent.status = Status.INSUFFICIENT_DATA
        constituent.failures.append("no SEC filings available")
        return constituent

    if history is not None:
        constituent.price = history.raw_close(as_of)
        constituent.avg_dollar_volume = history.avg_dollar_volume(as_of)
        constituent.price_staleness_days = history.staleness_days(as_of)
        if constituent.shares_outstanding:
            constituent.market_cap = prices.market_cap(
                history, constituent.shares_outstanding, as_of
            )
        elif as_of >= date.today():
            # Multi-class filers leave no usable share count, so a market-cap
            # floor would otherwise go unevaluated and pass by default. An
            # external quote closes that gap for live screening only: it is a
            # current figure with no history, so it must never be used for a
            # point-in-time view.
            external = _external_market_cap(constituent.ticker)
            if external is not None:
                constituent.market_cap = external
                constituent.notes.append("market cap from external quote, not filings")
    else:
        constituent.notes.append("no price history")

    # -- apply the screens --------------------------------------------------

    if constituent.market_cap is None:
        constituent.notes.append("market-cap screen not evaluated")
    elif constituent.market_cap < min_cap:
        constituent.failures.append(
            f"market cap ${constituent.market_cap / 1e9:.1f}B below ${min_cap / 1e9:.0f}B"
        )

    if constituent.avg_dollar_volume is None:
        constituent.notes.append("liquidity screen not evaluated")
    elif constituent.avg_dollar_volume < min_volume:
        constituent.failures.append(
            f"avg daily volume ${constituent.avg_dollar_volume / 1e6:.1f}M "
            f"below ${min_volume / 1e6:.0f}M"
        )

    if require_ocf:
        if constituent.operating_cash_flow is None:
            constituent.notes.append("cash-generation screen not evaluated")
        elif constituent.operating_cash_flow <= 0:
            constituent.failures.append(
                f"operating cash flow negative "
                f"({constituent.operating_cash_flow / 1e9:.2f}B {constituent.reporting_currency})"
            )

    if constituent.failures:
        constituent.status = Status.SCREENED_OUT
    elif constituent.stability_flag:
        constituent.status = Status.SPECULATIVE
    else:
        constituent.status = Status.INVESTABLE
    return constituent


def _external_market_cap(ticker: str) -> float | None:
    """Current market cap from an external quote, for live screening only.

    Deliberately separate from `prices.market_cap`, which is point-in-time and
    built from filings. Anything reached through here is a present-day figure
    with no usable history behind it.
    """
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
    except Exception:
        return None
    value = info.get("marketCap")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def build(
    as_of: date | None = None,
    client: SECClient | None = None,
    tickers: Iterable[str] | None = None,
    refresh_prices: bool = False,
) -> UniverseSnapshot:
    """Screen the whole candidate list as it stood on `as_of`."""
    as_of = as_of or date.today()
    client = client or SECClient()

    wanted = {t.upper() for t in tickers} if tickers else None
    pool = [c for c in candidates() if wanted is None or c.ticker in wanted]

    for constituent in pool:
        cik = client.ticker_to_cik(constituent.ticker)
        constituent.cik = cik
        if cik is not None:
            company = client.company(constituent.ticker)
            if company is not None:
                constituent.company_name = company.name
                constituent.sic = company.sic
                constituent.sic_description = company.sic_description
            facts = client.company_facts(cik)
        else:
            facts = None

        history = prices.load(constituent.ticker, refresh=refresh_prices)
        screen(constituent, facts, history, as_of)

    return UniverseSnapshot(as_of=as_of, constituents=pool)


# ---------------------------------------------------------------------------
# Change tracking
# ---------------------------------------------------------------------------


def previous_snapshot(before: date, directory: Path = SNAPSHOT_DIR) -> dict[str, Any] | None:
    """Most recent saved snapshot strictly older than `before`."""
    if not directory.exists():
        return None
    dated: list[tuple[date, Path]] = []
    for path in directory.glob("*.json"):
        try:
            stamp = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if stamp < before:
            dated.append((stamp, path))
    if not dated:
        return None
    return UniverseSnapshot.load(max(dated)[1])


def diff(snapshot: UniverseSnapshot, directory: Path = SNAPSHOT_DIR) -> list[str]:
    """Human-readable changes since the previous snapshot.

    A holding losing investable status is the most actionable thing this module
    produces, and it is invisible without comparing dated runs.
    """
    previous = previous_snapshot(snapshot.as_of, directory)
    if previous is None:
        return ["first snapshot - nothing to compare against"]

    was = {c["ticker"]: c for c in previous["constituents"]}
    now = {c.ticker: c for c in snapshot.constituents}
    changes: list[str] = []

    for ticker, current in now.items():
        before = was.get(ticker)
        if before is None:
            changes.append(f"{ticker}: added to candidate list ({current.status})")
        elif before["status"] != current.status:
            changes.append(f"{ticker}: {before['status']} -> {current.status}")

    for ticker in was:
        if ticker not in now:
            changes.append(f"{ticker}: removed from candidate list")

    return changes or ["no status changes since the previous snapshot"]


# ---------------------------------------------------------------------------
# Rule-based peer discovery
# ---------------------------------------------------------------------------


def sic_peers(
    client: SECClient,
    sic_codes: Iterable[int] = SEMI_SIC_CODES,
    known: Iterable[str] = (),
) -> dict[int, list[str]]:
    """Semiconductor-industry filers grouped by SIC code, for gap-finding.

    Two uses. It surfaces candidates the curated list has missed, and it is the
    entry point for reconstructing a survivorship-bias-free semiconductor core
    at a historical date — the part of this universe that classification codes
    *can* define, unlike the hyperscaler, power, and software segments.

    This walks the curated list's own SIC assignments rather than enumerating
    every SEC filer; a full-registry scan belongs with the backtester.
    """
    known_upper = {t.upper() for t in known}
    wanted = set(sic_codes)
    out: dict[int, list[str]] = {}
    for constituent in candidates():
        company = client.company(constituent.ticker)
        if company is None or company.sic not in wanted:
            continue
        if company.ticker in known_upper:
            continue
        out.setdefault(company.sic, []).append(company.ticker)
    return out


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report(snapshot: UniverseSnapshot) -> str:
    """Readable summary: who is in, who is out, and precisely why."""
    lines = [
        f"AI-infrastructure universe as of {snapshot.as_of}",
        "=" * 72,
    ]
    for segment, members in snapshot.by_segment().items():
        label = members[0].segment_label
        live = sum(1 for m in members if m.investable)
        lines.append(f"\n{label}  ({live}/{len(members)} investable)")
        for m in sorted(members, key=lambda x: x.ticker):
            cap = f"${m.market_cap / 1e9:,.0f}B" if m.market_cap else "cap n/a"
            marker = {
                Status.INVESTABLE: "  ok  ",
                Status.SPECULATIVE: " flag ",
                Status.SCREENED_OUT: " out  ",
                Status.INSUFFICIENT_DATA: " data ",
            }[m.status]
            lines.append(f"  {marker} {m.ticker:6} {cap:>10}  {m.status}")
            if m.stability_flag:
                lines.append(f"           flag: {m.stability_flag}")
            for failure in m.failures:
                lines.append(f"           fails: {failure}")
            for note in m.notes:
                lines.append(f"           note: {note}")

    counts = {
        status: len(snapshot.by_status(status))
        for status in (
            Status.INVESTABLE,
            Status.SPECULATIVE,
            Status.SCREENED_OUT,
            Status.INSUFFICIENT_DATA,
        )
    }
    lines.append("\n" + "=" * 72)
    lines.append(
        "  ".join(f"{name}: {count}" for name, count in counts.items())
        + f"  |  total {len(snapshot.constituents)}"
    )
    return "\n".join(lines)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Build the AI-infrastructure universe")
    parser.add_argument("--as-of", help="ISO date to screen as of (default today)")
    parser.add_argument("--refresh-prices", action="store_true")
    parser.add_argument("--save", action="store_true", help="persist a dated snapshot")
    parser.add_argument("--tickers", nargs="*", help="limit to these tickers")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    snapshot = build(as_of=as_of, tickers=args.tickers, refresh_prices=args.refresh_prices)
    print(report(snapshot))

    print("\nChanges since previous snapshot:")
    for change in diff(snapshot):
        print(f"  {change}")

    if args.save:
        path = snapshot.save()
        print(f"\nsnapshot written to {path}")


if __name__ == "__main__":
    main()
