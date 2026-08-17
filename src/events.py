"""Material events from SEC filings — 8-K, Form 4, and earnings dates.

Fundamentals describe a quarter already ended. Events answer what changed since,
and they come free from EDGAR: no paid news feed is involved anywhere here.

Discovery goes through EDGAR's **daily master index** — one pipe-delimited file
per day listing every filing by every filer — rather than polling 41 companies
individually. One request covers the whole universe for a day.

Two firm rules carried over from the design:

- **An event never fires a signal by itself.** It raises or lowers confidence and,
  where it contradicts a thesis, flags the name for review.
- **Only open-market purchases count as insider buying.** Grants, option
  exercises and tax withholding dominate Form 4 volume and mean nothing
  directionally. Treating them as buying is the classic way to be fooled by
  insider data.

The Form 4 and 8-K parsing here is adapted from the earlier codebase, where two
specific bugs were found against live filings and are guarded by tests below: a
joint filing naming two reporting owners double-counted every transaction, and a
purchase filled across several price points read as several separate buys.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

from src import config, universe
from src.sec_client import SECClient

DAILY_INDEX_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/master.{stamp}.idx"
)
ARCHIVES_BASE = "https://www.sec.gov/Archives/"
INDEX_CACHE = config.BASE_DIR / ".sec_cache" / "daily_index"
EVENTS_DIR = config.DATA_DIR / "pit" / "events"

FORMS_OF_INTEREST = {"4", "8-K", "8-K/A"}

# Only P is a decision to spend one's own money. The rest are compensation
# mechanics or disposals.
TRANSACTION_CODES = {
    "P": "open-market purchase",
    "S": "open-market sale",
    "A": "grant or award",
    "M": "option exercise",
    "F": "shares withheld for tax",
    "G": "gift",
    "C": "conversion",
    "D": "disposition to issuer",
    "X": "option exercise (in the money)",
}

# 8-K items and how much they matter, most severe first. Anything unlisted rates 1.
EIGHT_K_IMPORTANCE: tuple[tuple[str, int], ...] = (
    ("non-reliance", 5),  # a restatement is thesis-breaking
    ("bankruptcy", 5),
    ("results of operations", 4),
    ("completion of acquisition", 4),
    ("notice of delisting", 4),
    ("material impairment", 4),
    ("changes in registrant's certifying accountant", 4),
    ("entry into a material definitive agreement", 3),
    ("termination of a material definitive agreement", 3),
    ("costs associated with exit", 3),
    ("departure of directors", 3),
    ("unregistered sale", 2),
    ("material modification to rights", 2),
    ("regulation fd", 2),
    ("other events", 1),
    # Boilerplate attached to almost every 8-K, so it must not raise importance.
    ("financial statements and exhibits", 0),
)

MIN_BUY_VALUE_USD = 25_000
CLUSTER_WINDOW_DAYS = 45
CLUSTER_MIN_BUYERS = 2


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FilingRef:
    cik: int
    ticker: str
    company: str
    form: str
    filed: date
    path: str

    @property
    def url(self) -> str:
        return ARCHIVES_BASE + self.path


def business_days(end: date, count: int) -> list[date]:
    """The `count` most recent weekdays ending at `end`, oldest first."""
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


class DailyIndex:
    """EDGAR's per-day filing manifest, cached to disk."""

    def __init__(self, client: SECClient, cache_dir: Path = INDEX_CACHE):
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, day: date) -> str | None:
        stamp = day.strftime("%Y%m%d")
        cached = self.cache_dir / f"master.{stamp}.idx"
        if cached.exists():
            return cached.read_text(encoding="utf-8", errors="replace")

        url = DAILY_INDEX_URL.format(
            year=day.year, quarter=(day.month - 1) // 3 + 1, stamp=stamp
        )
        self.client.limiter.wait()
        try:
            response = self.client.session.get(url, timeout=60)
        except Exception as exc:
            print(f"  ! index fetch failed for {day}: {exc}")
            return None
        # SEC answers 403, not 404, for a day it has no index for: today before
        # publication, weekends, holidays, or anything in the future.
        if response.status_code in (403, 404):
            return None
        if response.status_code != 200:
            print(f"  ! HTTP {response.status_code} for {day}")
            return None
        cached.write_text(response.text, encoding="utf-8")
        return response.text

    def filings(self, day: date, tickers_by_cik: dict[int, str]) -> list[FilingRef]:
        text = self.fetch(day)
        if text is None:
            return []

        found: list[FilingRef] = []
        seen: set[tuple[int, str]] = set()
        for line in text.splitlines():
            parts = line.split("|")
            if len(parts) != 5 or not parts[0].strip().isdigit():
                continue
            cik = int(parts[0])
            ticker = tickers_by_cik.get(cik)
            if ticker is None or parts[2].strip() not in FORMS_OF_INTEREST:
                continue
            path = parts[4].strip()
            key = (cik, path)
            if key in seen:
                # The same accession is indexed under several filers on a joint
                # Form 4, and fetching it twice would double-count it.
                continue
            seen.add(key)
            found.append(
                FilingRef(
                    cik=cik,
                    ticker=ticker,
                    company=parts[1].strip(),
                    form=parts[2].strip(),
                    filed=day,
                    path=path,
                )
            )
        return found


# ---------------------------------------------------------------------------
# Form 4
# ---------------------------------------------------------------------------


@dataclass
class InsiderTrade:
    ticker: str
    owner: str
    role: str
    code: str
    shares: float
    price: float
    value: float
    trade_date: str
    filed: date
    shares_after: float | None = None
    url: str = ""

    @property
    def is_open_market_buy(self) -> bool:
        return self.code == "P" and self.value >= MIN_BUY_VALUE_USD

    @property
    def description(self) -> str:
        return TRANSACTION_CODES.get(self.code, f"code {self.code}")


def _value_of(node: ET.Element | None) -> str | None:
    if node is None:
        return None
    inner = node.find("./value")
    text = (inner if inner is not None else node).text
    return text.strip() if text else None


def _number(node: ET.Element | None) -> float | None:
    raw = _value_of(node)
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _describe_role(relationship: ET.Element | None) -> str:
    if relationship is None:
        return "insider"
    roles = []
    for tag, label in (
        ("isDirector", "director"),
        ("isOfficer", "officer"),
        ("isTenPercentOwner", "10% owner"),
    ):
        node = relationship.find(f"./{tag}")
        if node is not None and (node.text or "").strip() in ("1", "true"):
            roles.append(label)
    title = relationship.find("./officerTitle")
    if title is not None and title.text:
        roles.append(title.text.strip())
    return ", ".join(roles) or "insider"


def parse_form4(text: str, ref: FilingRef) -> list[InsiderTrade]:
    """Extract non-derivative transactions from a Form 4 submission."""
    match = re.search(r"<ownershipDocument>.*?</ownershipDocument>", text, re.S)
    if not match:
        return []
    try:
        doc = ET.fromstring(match.group(0))
    except ET.ParseError:
        return []

    ticker = _value_of(doc.find("./issuer/issuerTradingSymbol")) or ref.ticker

    # Owners and the transaction table are each read once. Iterating the table
    # inside an owner loop reports every transaction twice on a joint filing that
    # names two reporting owners.
    owners = doc.findall("./reportingOwner")
    names = [
        (_value_of(o.find("./reportingOwnerId/rptOwnerName")) or "unknown").title()
        for o in owners
    ]
    roles = [_describe_role(o.find("./reportingOwnerRelationship")) for o in owners]
    owner_name = " & ".join(names) if names else "unknown"
    role = roles[0] if roles else "insider"

    trades: list[InsiderTrade] = []
    for txn in doc.findall("./nonDerivativeTable/nonDerivativeTransaction"):
        shares = _number(txn.find("./transactionAmounts/transactionShares")) or 0.0
        price = _number(txn.find("./transactionAmounts/transactionPricePerShare")) or 0.0
        trades.append(
            InsiderTrade(
                ticker=ticker.upper(),
                owner=owner_name,
                role=role,
                code=_value_of(txn.find("./transactionCoding/transactionCode")) or "?",
                shares=shares,
                price=price,
                value=shares * price,
                trade_date=_value_of(txn.find("./transactionDate")) or str(ref.filed),
                filed=ref.filed,
                shares_after=_number(
                    txn.find("./postTransactionAmounts/sharesOwnedFollowingTransaction")
                ),
                url=ref.url,
            )
        )
    return merge_same_day_lots(trades)


def merge_same_day_lots(trades: list[InsiderTrade]) -> list[InsiderTrade]:
    """Collapse one filing's split executions into a single trade.

    A purchase filled across several price points is reported on separate lines.
    Left alone it reads as several distinct buys, overstating how much is going
    on: one Carvana filing appeared as a $1.3M purchase plus a $240k one when it
    was a single $1.54M trade at a volume-weighted price.
    """
    grouped: dict[tuple[str, str, str], list[InsiderTrade]] = defaultdict(list)
    for trade in trades:
        grouped[(trade.owner, trade.code, trade.trade_date)].append(trade)

    merged: list[InsiderTrade] = []
    for lots in grouped.values():
        if len(lots) == 1:
            merged.append(lots[0])
            continue
        shares = sum(lot.shares for lot in lots)
        value = sum(lot.value for lot in lots)
        first = lots[0]
        merged.append(
            InsiderTrade(
                ticker=first.ticker,
                owner=first.owner,
                role=first.role,
                code=first.code,
                shares=shares,
                price=value / shares if shares else 0.0,
                value=value,
                trade_date=first.trade_date,
                filed=first.filed,
                # The last lot carries the true post-transaction holding.
                shares_after=max(
                    (lot.shares_after for lot in lots if lot.shares_after is not None),
                    default=None,
                ),
                url=first.url,
            )
        )
    return merged


# ---------------------------------------------------------------------------
# 8-K
# ---------------------------------------------------------------------------


def parse_8k_items(text: str) -> list[str]:
    """Item descriptions from the SGML header of an 8-K submission."""
    items: list[str] = []
    for line in text[:20000].splitlines():
        if "ITEM INFORMATION:" in line.upper():
            description = line.split(":", 1)[1].strip()
            if description:
                items.append(description)
    return items


def rate_8k(items: Iterable[str]) -> int:
    """Importance 0-5 from the most severe item present.

    Taking the maximum matters: "Financial Statements and Exhibits" is attached
    to almost every 8-K and rates 0, so averaging would dilute a restatement
    into background noise.
    """
    best = 0
    for item in items:
        lowered = item.lower()
        for phrase, score in EIGHT_K_IMPORTANCE:
            if phrase in lowered:
                best = max(best, score)
                break
        else:
            best = max(best, 1)
    return best


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class Kind:
    INSIDER_BUY = "insider_buy"
    INSIDER_CLUSTER = "insider_cluster"
    INSIDER_SALE = "insider_sale"
    MATERIAL_EVENT = "material_event"
    EARNINGS = "earnings"


@dataclass
class Event:
    ticker: str
    kind: str
    when: date
    importance: int
    headline: str
    detail: str = ""
    url: str = ""
    composite_score: float | None = None

    def sort_key(self) -> tuple:
        # Importance dominates, then company quality, then recency. Unscored
        # names sort last among equals rather than first, since a missing score
        # means missing data, not a bad company.
        return (
            -self.importance,
            -(self.composite_score if self.composite_score is not None else -1),
            -self.when.toordinal(),
        )


def detect_clusters(buys: list[InsiderTrade]) -> list[Event]:
    """Two or more distinct insiders buying the same name in a short window.

    One purchase is weak evidence; several people independently choosing to
    spend their own money is the pattern with real support.
    """
    by_ticker: dict[str, list[InsiderTrade]] = defaultdict(list)
    for trade in buys:
        by_ticker[trade.ticker].append(trade)

    events: list[Event] = []
    for ticker, trades in by_ticker.items():
        owners = {t.owner for t in trades}
        if len(owners) < CLUSTER_MIN_BUYERS:
            continue
        dates = sorted(_parse_day(t.trade_date) or t.filed for t in trades)
        span = (dates[-1] - dates[0]).days
        if span > CLUSTER_WINDOW_DAYS:
            continue
        total = sum(t.value for t in trades)
        events.append(
            Event(
                ticker=ticker,
                kind=Kind.INSIDER_CLUSTER,
                when=dates[-1],
                importance=5,
                headline=f"{len(owners)} insiders bought {_money(total)} within {span}d",
                detail=", ".join(sorted(owners)),
                url=trades[0].url,
            )
        )
    return events


def build_events(
    trades: list[InsiderTrade],
    filings_8k: list[tuple[FilingRef, list[str]]],
) -> list[Event]:
    events: list[Event] = []

    for trade in trades:
        when = _parse_day(trade.trade_date) or trade.filed
        if trade.is_open_market_buy:
            # Size relative to the buyer's resulting stake is what separates a
            # conviction purchase from a token one.
            events.append(
                Event(
                    ticker=trade.ticker,
                    kind=Kind.INSIDER_BUY,
                    when=when,
                    importance=4 if trade.value >= 250_000 else 3,
                    headline=f"{trade.owner} bought {_money(trade.value)}",
                    detail=f"{trade.role}; {trade.shares:,.0f} shares at ${trade.price:,.2f}",
                    url=trade.url,
                )
            )
        elif trade.code == "S" and trade.value >= MIN_BUY_VALUE_USD:
            # Reported but rated low: executives sell for diversification, tax
            # and scheduled 10b5-1 plans far more often than out of conviction.
            events.append(
                Event(
                    ticker=trade.ticker,
                    kind=Kind.INSIDER_SALE,
                    when=when,
                    importance=1,
                    headline=f"{trade.owner} sold {_money(trade.value)}",
                    detail=f"{trade.role}; often scheduled or for tax, not a view",
                    url=trade.url,
                )
            )

    for ref, items in filings_8k:
        importance = rate_8k(items)
        is_earnings = any("results of operations" in i.lower() for i in items)
        events.append(
            Event(
                ticker=ref.ticker,
                kind=Kind.EARNINGS if is_earnings else Kind.MATERIAL_EVENT,
                when=ref.filed,
                importance=importance,
                headline="; ".join(items[:2]) or "8-K filed",
                detail=ref.company,
                url=ref.url,
            )
        )

    events.extend(detect_clusters([t for t in trades if t.is_open_market_buy]))
    return events


def attach_scores(events: Iterable[Event], scores: dict[str, float]) -> None:
    """Rank equally-important events by company quality.

    Without this the feed is a few hundred equally-weighted filings a week, which
    is the same as no feed. An earnings release from a top-decile business is
    worth reading; the same filing from a weak one usually is not.
    """
    for event in events:
        event.composite_score = scores.get(event.ticker)


def load_scores() -> dict[str, float]:
    """Composite scores from the most recent saved scoreboard, if any."""
    directory = config.DATA_DIR / "pit" / "scores"
    if not directory.exists():
        return {}
    boards = sorted(directory.glob("*.json"))
    if not boards:
        return {}
    try:
        payload = json.loads(boards[-1].read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {
        entry["ticker"]: entry["composite"]
        for entry in payload.get("scores", [])
        if entry.get("composite") is not None
    }


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _money(value: float) -> str:
    for unit, scale in (("M", 1e6), ("K", 1e3)):
        if abs(value) >= scale:
            return f"${value / scale:,.1f}{unit}"
    return f"${value:,.0f}"


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


@dataclass
class EventScan:
    as_of: date
    days_scanned: int
    events: list[Event] = field(default_factory=list)
    filings_seen: int = 0
    tickers_covered: int = 0

    def filtered(
        self, min_importance: int = 0, min_score: float | None = None
    ) -> list[Event]:
        out = [e for e in self.events if e.importance >= min_importance]
        if min_score is not None:
            # Unscored names are kept rather than hidden: a missing score means
            # missing data, not a company to ignore.
            out = [
                e for e in out if e.composite_score is None or e.composite_score >= min_score
            ]
        return sorted(out, key=lambda e: e.sort_key())


def scan(
    days: int = 5,
    as_of: date | None = None,
    tickers: Iterable[str] | None = None,
    client: SECClient | None = None,
) -> EventScan:
    """Scan recent filing days for events affecting the universe."""
    as_of = as_of or date.today()
    client = client or SECClient()
    index = DailyIndex(client)

    wanted = (
        [t.upper() for t in tickers]
        if tickers
        else [c.ticker for c in universe.candidates()]
    )
    by_cik: dict[int, str] = {}
    for ticker in wanted:
        cik = client.ticker_to_cik(ticker)
        if cik is not None:
            by_cik[cik] = ticker

    refs: list[FilingRef] = []
    for day in business_days(as_of, days):
        if day > as_of:
            continue  # point-in-time: a filing from tomorrow is not readable
        refs.extend(index.filings(day, by_cik))

    trades: list[InsiderTrade] = []
    filings_8k: list[tuple[FilingRef, list[str]]] = []
    for ref in refs:
        client.limiter.wait()
        try:
            response = client.session.get(ref.url, timeout=60)
        except Exception:
            continue
        if response.status_code != 200:
            continue
        if ref.form == "4":
            trades.extend(parse_form4(response.text, ref))
        else:
            filings_8k.append((ref, parse_8k_items(response.text)))

    events = build_events(trades, filings_8k)
    attach_scores(events, load_scores())
    return EventScan(
        as_of=as_of,
        days_scanned=days,
        events=events,
        filings_seen=len(refs),
        tickers_covered=len(by_cik),
    )


def report(
    scan_result: EventScan, min_importance: int = 0, min_score: float | None = None
) -> str:
    events = scan_result.filtered(min_importance, min_score)
    out = [
        f"Events over {scan_result.days_scanned} business day(s) to {scan_result.as_of}",
        "=" * 96,
        f"  {scan_result.filings_seen} filing(s) from {scan_result.tickers_covered} "
        f"universe names   |   {len(events)} event(s) shown",
        "",
    ]
    if not events:
        out.append("  no events matched. This is a normal result over a short window.")
    for event in events:
        score = "  -- " if event.composite_score is None else f"{event.composite_score:4.0f}"
        out.append(
            f"  [{event.importance}] {event.ticker:6} score {score}  "
            f"{event.when}  {event.kind}"
        )
        out.append(f"        {event.headline}")
        if event.detail:
            out.append(f"        {event.detail}")
    out.append("")
    out.append(
        "  Events never fire a signal on their own. They raise or lower confidence,"
    )
    out.append(
        "  and where one contradicts a thesis they flag the name for review."
    )
    out.append(
        "  Only open-market purchases count as buying: grants, option exercises and"
    )
    out.append(
        "  tax withholding dominate Form 4 volume and carry no directional meaning."
    )
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Material events from SEC filings")
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--as-of", help="ISO date (default today)")
    parser.add_argument("--min-importance", type=int, default=0)
    parser.add_argument("--min-score", type=float)
    parser.add_argument("--tickers", nargs="*")
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    result = scan(days=args.days, as_of=as_of, tickers=args.tickers)
    print(report(result, args.min_importance, args.min_score))


if __name__ == "__main__":
    main()
