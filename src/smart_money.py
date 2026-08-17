"""Aggregate 13F positioning across tracked managers, per universe name.

Runs the whole layer: resolve each manager, fetch the most recent quarter that was
public at the as-of date and the one before it, diff them, map CUSIPs to tickers,
and report what tracked institutions did with each name in the universe.

The output is evidence, not a signal. Read `holdings_13f` for why 13F cannot
support anything stronger than a confidence adjustment.

    python -m src.smart_money
    python -m src.smart_money --tickers NVDA MU AVGO
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from src import config, universe
from src.holdings_13f import (
    CLUSTER_MIN_FILERS,
    MIN_CONVICTION_WEIGHT,
    TRACKED_FILERS,
    Action,
    Corroboration,
    FilerQuarter,
    HoldingsClient,
    PositionChange,
    corroborate,
    diff_quarters,
    quarter_end_for,
)
from src.sec_client import SECClient


@dataclass
class SmartMoneyView:
    as_of: date
    quarter_end: date
    filers_loaded: list[str] = field(default_factory=list)
    filers_missing: list[str] = field(default_factory=list)
    by_ticker: dict[str, Corroboration] = field(default_factory=dict)
    unmapped_cusips: int = 0

    def clusters(self) -> list[Corroboration]:
        return [c for c in self.by_ticker.values() if c.is_cluster]

    def consensus_exits(self) -> list[Corroboration]:
        return [c for c in self.by_ticker.values() if c.is_consensus_exit]


def build(
    as_of: date | None = None,
    tickers: Iterable[str] | None = None,
    client: SECClient | None = None,
) -> SmartMoneyView:
    as_of = as_of or date.today()
    client = client or SECClient()
    holdings = HoldingsClient(client)

    wanted = (
        [t.upper() for t in tickers]
        if tickers
        else [c.ticker for c in universe.candidates()]
    )

    quarters: dict[str, tuple[FilerQuarter, FilerQuarter | None]] = {}
    missing: list[str] = []
    all_holdings = []

    for entry in TRACKED_FILERS:
        name = entry["name"]
        cik = holdings.resolve_cik(name)
        if cik is None:
            missing.append(f"{name} (CIK not resolved)")
            continue
        current = holdings.filer_quarter(name, cik, as_of=as_of)
        if current is None:
            missing.append(f"{name} (no readable filing at {as_of})")
            continue
        # The prior quarter comes from a view one day before the current filing
        # became public, so the comparison is between two states that an investor
        # could actually have read in sequence.
        cutoff = (current.filed or current.quarter_end)
        previous = holdings.filer_quarter(
            name, cik, as_of=cutoff.replace(day=1)
        )
        if previous is not None and previous.quarter_end >= current.quarter_end:
            previous = None
        quarters[name] = (current, previous)
        all_holdings.extend(current.holdings)

    mapping = holdings.learn_cusips(all_holdings, wanted)
    ticker_for = {cusip: ticker for cusip, ticker in mapping.items()}
    cusip_for = {ticker: cusip for cusip, ticker in mapping.items()}

    changes_by_filer: dict[str, list[PositionChange]] = {}
    for name, (current, previous) in quarters.items():
        changes_by_filer[name] = diff_quarters(current, previous)

    quarter = quarter_end_for(as_of)
    view = SmartMoneyView(
        as_of=as_of,
        quarter_end=quarter,
        filers_loaded=sorted(quarters),
        filers_missing=missing,
    )

    for ticker in wanted:
        view.by_ticker[ticker] = corroborate(
            ticker, cusip_for.get(ticker), changes_by_filer, quarter
        )

    mapped = set(ticker_for)
    view.unmapped_cusips = sum(
        1 for h in all_holdings if h.cusip not in mapped
    )
    return view


def report(view: SmartMoneyView, only_active: bool = False) -> str:
    out = [
        f"Institutional positioning as of {view.as_of} "
        f"(most recent quarter public: {view.quarter_end})",
        "=" * 96,
    ]
    out.append(
        f"  filers loaded: {len(view.filers_loaded)}   "
        f"cluster threshold: {CLUSTER_MIN_FILERS} independent buyers, each holding at "
        f"least {MIN_CONVICTION_WEIGHT:.1%} of their own book"
    )
    for name in view.filers_missing:
        out.append(f"  MISSING  {name}")
    out.append("")
    out.append(f"  {'tick':6} {'holders':>7} {'value':>10}  buyers / sellers")
    out.append("-" * 96)

    entries = sorted(
        view.by_ticker.values(), key=lambda c: (-len(c.buyers), -len(c.holders), c.ticker)
    )
    for entry in entries:
        if only_active and not entry.holders:
            continue
        buyers = f"+{len(entry.buyers)}" if entry.buyers else "  "
        sellers = f"-{len(entry.trimmed + entry.exited)}" if entry.trimmed or entry.exited else ""
        marker = " CLUSTER" if entry.is_cluster else (" EXITS" if entry.is_consensus_exit else "")
        value = f"${entry.total_value_usd / 1e9:,.2f}B" if entry.total_value_usd else "-"
        detail = ", ".join(entry.buyers[:3]) if entry.buyers else ""
        out.append(
            f"  {entry.ticker:6} {len(entry.holders):>7} {value:>10}  "
            f"{buyers}{sellers}{marker}  {detail}"
        )

    out.append("")
    clusters = view.clusters()
    if clusters:
        out.append("  CLUSTERS (several managers independently buying the same name):")
        for entry in clusters:
            weights = ", ".join(
                f"{f} ({entry.weights.get(f, 0):.1%})" for f in entry.conviction_buyers
            )
            out.append(f"    {entry.ticker:6} bought by {weights}")
    else:
        out.append("  no clusters this quarter")

    exits = view.consensus_exits()
    if exits:
        out.append("  CONSENSUS REDUCTIONS (flag for review, never an auto-sell):")
        for entry in exits:
            out.append(f"    {entry.ticker:6} reduced by {', '.join(entry.trimmed + entry.exited)}")
        out.append(
            "    note: reductions are not conviction-weighted. A full exit leaves no"
        )
        out.append(
            "    ending position weight, so the filter that removes immaterial buys"
        )
        out.append(
            "    cannot be applied symmetrically here - read these more loosely."
        )

    out.append("")
    out.append(
        "  This is corroboration only. 13F is long-only, 45 days stale, and blind to"
    )
    out.append(
        "  shorts and hedges, so it can raise or lower confidence in a conclusion the"
    )
    out.append(
        "  fundamentals already reached - it can never create one."
    )
    if view.unmapped_cusips:
        out.append(
            f"  {view.unmapped_cusips} reported position(s) could not be mapped to a "
            "universe ticker and were ignored."
        )
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="13F positioning for the universe")
    parser.add_argument("--as-of", help="ISO date (default today)")
    parser.add_argument("--tickers", nargs="*", help="limit to these tickers")
    parser.add_argument("--only-active", action="store_true", help="hide names nobody holds")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    view = build(as_of=as_of, tickers=args.tickers)
    print(report(view, only_active=args.only_active))


if __name__ == "__main__":
    main()
