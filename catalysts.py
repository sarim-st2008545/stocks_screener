"""
Catalyst monitoring for the compliant watchlist.

Fundamentals tell you what is worth owning; catalysts tell you when it is in
play. On a swing horizon that second question is the one that matters, and it
is answerable from filings alone - no paid feed, no news API.

Two sources, both free:

  Form 4   Insider transactions, filed within two business days. Clustered
           open-market buying by officers and directors is one of the few
           fundamentals-adjacent patterns with reasonable academic support.
           Crucially, most Form 4 activity is NOT a signal - option exercises,
           tax withholding and stock grants dominate the volume. Only code P
           (open-market purchase) reflects an insider choosing to buy with
           their own money, so that is what gets surfaced.

  8-K      Material events, filed within four business days. Item codes carry
           most of the meaning: 2.02 is an earnings release, 1.01 a material
           agreement, 5.02 an executive change.

Discovery goes through EDGAR's daily master index - one 609 KB pipe-delimited
file covering every filer for a day - rather than polling each company.

Usage:
    python catalysts.py                    # last 5 business days
    python catalysts.py --days 10
    python catalysts.py --min-importance 3
    python catalysts.py --notify           # also push to Telegram
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import requests

from aaoifi_screener import CACHE_DIR, USER_AGENT
from universe import DATA_DIR, RateLimiter

DAILY_INDEX_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/{year}/QTR{quarter}/master.{stamp}.idx"
)
ARCHIVES_BASE = "https://www.sec.gov/Archives/"

FORMS_OF_INTEREST = {"4", "8-K", "8-K/A"}

# Form 4 transaction codes. Only P is an open-market purchase decision; the
# rest are compensation mechanics or disposals, and treating them as buying
# signal is the classic way to get fooled by insider data.
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

# 8-K items worth waking up for, and how much they matter. Anything not listed
# is reported at importance 1.
EIGHT_K_IMPORTANCE: list[tuple[str, int]] = [
    ("results of operations", 4),
    ("completion of acquisition", 4),
    ("entry into a material definitive agreement", 3),
    ("termination of a material definitive agreement", 3),
    ("bankruptcy", 5),
    ("notice of delisting", 4),
    ("non-reliance", 5),  # restatement - thesis-breaking
    ("material impairment", 4),
    ("costs associated with exit", 3),
    ("departure of directors", 3),
    ("changes in registrant's certifying accountant", 4),
    ("unregistered sale", 2),
    ("material modification to rights", 2),
    ("regulation fd", 2),
    ("other events", 1),
    ("financial statements and exhibits", 0),  # boilerplate, always attached
]

# An insider buy only registers above this size, to filter out token purchases.
MIN_BUY_VALUE_USD = 25_000

# Distinct officers/directors buying inside this window counts as a cluster.
CLUSTER_WINDOW_DAYS = 45
CLUSTER_MIN_BUYERS = 2


# ---------------------------------------------------------------------------
# Filing discovery
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
    def accession(self) -> str:
        return Path(self.path).stem

    @property
    def url(self) -> str:
        """Human-readable filing index page."""
        return (
            f"https://www.sec.gov/Archives/edgar/data/{self.cik}/"
            f"{self.accession.replace('-', '')}/{self.accession}-index.htm"
        )


def business_days(end: date, count: int) -> list[date]:
    """The last `count` weekdays up to and including `end`."""
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(days)


class DailyIndex:
    """EDGAR's per-day filing manifest, cached to disk."""

    def __init__(self, session: requests.Session, cache_dir: Path = CACHE_DIR / "daily_index"):
        self.session = session
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, day: date) -> str | None:
        stamp = day.strftime("%Y%m%d")
        cached = self.cache_dir / f"master.{stamp}.idx"
        if cached.exists():
            return cached.read_text(encoding="utf-8", errors="replace")

        url = DAILY_INDEX_URL.format(
            year=day.year, quarter=(day.month - 1) // 3 + 1, stamp=stamp
        )
        try:
            response = self.session.get(url, timeout=60)
        except requests.RequestException as exc:
            print(f"  ! index fetch failed for {day}: {exc}")
            return None
        # SEC answers 403, not 404, for a date it has no index for - today
        # before publication, holidays, or anything in the future.
        if response.status_code in (403, 404):
            return None
        if not response.ok:
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
                continue  # same accession indexed under several filers
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
    date: str
    shares_after: float | None
    url: str

    @property
    def is_open_market_buy(self) -> bool:
        return self.code == "P" and self.value >= MIN_BUY_VALUE_USD


def _value_of(node: ET.Element | None) -> str | None:
    """Form 4 wraps most leaves in a <value> child, but not always."""
    if node is None:
        return None
    child = node.find("value")
    text = child.text if child is not None else node.text
    return text.strip() if text else None


def _number(node: ET.Element | None) -> float | None:
    raw = _value_of(node)
    if raw is None:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


def _describe_role(owner_rel: ET.Element | None) -> str:
    if owner_rel is None:
        return "insider"
    roles = []
    if _value_of(owner_rel.find("isDirector")) in ("1", "true"):
        roles.append("director")
    title = _value_of(owner_rel.find("officerTitle"))
    if _value_of(owner_rel.find("isOfficer")) in ("1", "true"):
        roles.append(title or "officer")
    if _value_of(owner_rel.find("isTenPercentOwner")) in ("1", "true"):
        roles.append("10% owner")
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

    # Owners are read once and the transaction table once. Iterating the table
    # inside an owner loop would report every transaction twice on the joint
    # filings that name two reporting owners.
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
                ticker=ticker,
                owner=owner_name,
                role=role,
                code=_value_of(txn.find("./transactionCoding/transactionCode")) or "?",
                shares=shares,
                price=price,
                value=shares * price,
                date=_value_of(txn.find("./transactionDate")) or str(ref.filed),
                shares_after=_number(
                    txn.find("./postTransactionAmounts/sharesOwnedFollowingTransaction")
                ),
                url=ref.url,
            )
        )
    return merge_same_day_lots(trades)


def merge_same_day_lots(trades: list[InsiderTrade]) -> list[InsiderTrade]:
    """Collapse one filing's split executions into a single trade.

    A purchase filled across several price points is reported as separate
    lines. Left alone it reads as several distinct buys, which overstates how
    much is going on - the CVNA filing below is one $1.54M purchase, not a
    $1.3M one plus a $240k one.
    """
    grouped: dict[tuple[str, str, str], list[InsiderTrade]] = defaultdict(list)
    for trade in trades:
        grouped[(trade.owner, trade.code, trade.date)].append(trade)

    merged: list[InsiderTrade] = []
    for lots in grouped.values():
        if len(lots) == 1:
            merged.append(lots[0])
            continue
        shares = sum(l.shares for l in lots)
        value = sum(l.value for l in lots)
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
                date=first.date,
                # The last lot carries the true post-transaction holding.
                shares_after=max(
                    (l.shares_after for l in lots if l.shares_after is not None), default=None
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
    items = []
    for line in text[:20000].splitlines():
        if "ITEM INFORMATION:" in line.upper():
            description = line.split(":", 1)[1].strip()
            if description:
                items.append(description)
    return items


def rate_8k(items: Iterable[str]) -> int:
    best = 0
    for item in items:
        lowered = item.lower()
        for needle, weight in EIGHT_K_IMPORTANCE:
            if needle in lowered:
                best = max(best, weight)
                break
        else:
            best = max(best, 1)
    return best


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


@dataclass
class CatalystEvent:
    ticker: str
    kind: str
    date: str
    importance: int
    headline: str
    detail: str = ""
    url: str = ""
    # Fundamental composite from scoring.py, where one exists.
    score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_scores() -> dict[str, float]:
    """Fundamental composites, if scoring.py has been run."""
    path = DATA_DIR / "scores.json"
    if not path.exists():
        return {}
    try:
        return {k: float(v) for k, v in json.loads(path.read_text()).items()}
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def attach_scores(events: list[CatalystEvent], scores: dict[str, float]) -> None:
    """Annotate events with the company's fundamental score.

    An earnings release from a top-decile business is worth reading; the same
    filing from a low-quality one usually is not. Without this the feed is a
    few hundred equally-weighted 8-Ks a week, which is the same as no feed.
    """
    for event in events:
        event.score = scores.get(event.ticker)


def _money(value: float) -> str:
    if value >= 1e6:
        return f"${value / 1e6:.1f}M"
    if value >= 1e3:
        return f"${value / 1e3:.0f}k"
    return f"${value:.0f}"


def build_events(trades: list[InsiderTrade], filings_8k: list[tuple[FilingRef, list[str]]]) -> list[CatalystEvent]:
    events: list[CatalystEvent] = []

    buys = [t for t in trades if t.is_open_market_buy]
    for trade in buys:
        events.append(
            CatalystEvent(
                ticker=trade.ticker,
                kind="insider_buy",
                date=trade.date,
                importance=4 if trade.value >= 500_000 else 3,
                headline=f"{trade.owner} bought {_money(trade.value)}",
                detail=f"{trade.role}; {trade.shares:,.0f} shares at ${trade.price:,.2f}",
                url=trade.url,
            )
        )

    # Sales are reported but rated low: executives sell for diversification,
    # tax and scheduled 10b5-1 plans far more often than out of conviction.
    for trade in trades:
        if trade.code == "S" and trade.value >= 1_000_000:
            events.append(
                CatalystEvent(
                    ticker=trade.ticker,
                    kind="insider_sell",
                    date=trade.date,
                    importance=1,
                    headline=f"{trade.owner} sold {_money(trade.value)}",
                    detail=f"{trade.role}; {trade.shares:,.0f} shares at ${trade.price:,.2f}",
                    url=trade.url,
                )
            )

    events.extend(detect_clusters(buys))

    for ref, items in filings_8k:
        if not items:
            continue
        events.append(
            CatalystEvent(
                ticker=ref.ticker,
                kind="8k",
                date=str(ref.filed),
                importance=rate_8k(items),
                headline=f"8-K: {items[0]}",
                detail="; ".join(items[1:]) if len(items) > 1 else "",
                url=ref.url,
            )
        )

    return events


def detect_clusters(buys: list[InsiderTrade]) -> list[CatalystEvent]:
    """Several distinct insiders buying the same name in a short window.

    A single purchase is weak evidence; independent officers and directors
    buying around the same time is the pattern worth surfacing.
    """
    by_ticker: dict[str, list[InsiderTrade]] = defaultdict(list)
    for trade in buys:
        by_ticker[trade.ticker].append(trade)

    events: list[CatalystEvent] = []
    for ticker, trades in by_ticker.items():
        owners = {t.owner for t in trades}
        if len(owners) < CLUSTER_MIN_BUYERS:
            continue
        dates = sorted(t.date for t in trades)
        try:
            span = (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days
        except ValueError:
            continue
        if span > CLUSTER_WINDOW_DAYS:
            continue
        total = sum(t.value for t in trades)
        events.append(
            CatalystEvent(
                ticker=ticker,
                kind="insider_cluster",
                date=dates[-1],
                importance=5,
                headline=f"{len(owners)} insiders bought {_money(total)} within {span}d",
                detail=", ".join(sorted(owners)),
                url=trades[0].url,
            )
        )
    return events


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def load_watchlist(include_review: bool = True) -> list[str]:
    path = DATA_DIR / "watchlist.json"
    if not path.exists():
        print(f"  ! {path} not found - run universe.py first")
        return []
    payload = json.loads(path.read_text())
    tickers = list(payload.get("pass", []))
    if include_review:
        tickers += list(payload.get("review", []))
    return tickers


def cik_lookup(tickers: Iterable[str]) -> dict[int, str]:
    path = CACHE_DIR / "company_tickers.json"
    if not path.exists():
        print("  ! ticker registry not cached - run a screen first")
        return {}
    registry = json.loads(path.read_text())
    by_ticker = {e["ticker"].upper(): int(e["cik_str"]) for e in registry.values()}
    return {by_ticker[t]: t for t in tickers if t in by_ticker}


def scan(
    session: requests.Session,
    days: int = 5,
    end: date | None = None,
    include_review: bool = True,
    workers: int = 5,
    per_second: float = 6.0,
) -> list[CatalystEvent]:
    tickers = load_watchlist(include_review)
    if not tickers:
        return []
    tickers_by_cik = cik_lookup(tickers)
    print(f"Watching {len(tickers_by_cik)} compliant names")

    index = DailyIndex(session)
    refs: list[FilingRef] = []
    for day in business_days(end or date.today(), days):
        found = index.filings(day, tickers_by_cik)
        if found:
            print(f"  {day}: {len(found)} relevant filings")
        refs.extend(found)

    if not refs:
        print("  no Form 4 or 8-K filings by watchlist companies in this window")
        return []

    limiter = RateLimiter(per_second)

    def fetch(ref: FilingRef) -> tuple[FilingRef, str | None]:
        limiter.wait()
        try:
            response = session.get(ARCHIVES_BASE + ref.path, timeout=60)
            return ref, response.text if response.ok else None
        except requests.RequestException:
            return ref, None

    print(f"Reading {len(refs)} filings")
    trades: list[InsiderTrade] = []
    filings_8k: list[tuple[FilingRef, list[str]]] = []
    done = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in as_completed([pool.submit(fetch, r) for r in refs]):
            ref, text = future.result()
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(refs)}", flush=True)
            if not text:
                continue
            if ref.form == "4":
                trades.extend(parse_form4(text, ref))
            else:
                filings_8k.append((ref, parse_8k_items(text)))

    events = build_events(trades, filings_8k)
    scores = load_scores()
    if scores:
        attach_scores(events, scores)
        print(f"  ranked against {len(scores)} fundamental scores")
    else:
        print("  no scores.json - run scoring.py to rank events by company quality")

    # Importance first, then company quality: a restatement always outranks an
    # earnings release, but among equals the better business comes first.
    events.sort(key=lambda e: (-e.importance, -(e.score or -1), e.date, e.ticker))
    return events


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def format_digest(events: list[CatalystEvent], limit: int = 40) -> str:
    if not events:
        return "No catalysts in this window."

    lines = []
    by_kind: dict[str, int] = defaultdict(int)
    for event in events:
        by_kind[event.kind] += 1
    lines.append(
        "  ".join(f"{kind}: {count}" for kind, count in sorted(by_kind.items()))
    )
    lines.append("")

    for event in events[:limit]:
        stars = "*" * event.importance
        score = f"{event.score:4.0f}" if event.score is not None else "   -"
        lines.append(
            f"[{stars:<5}] {score}  {event.date}  {event.ticker:6s} {event.headline}"
        )
        if event.detail:
            lines.append(f"                  {event.detail}")
    if len(events) > limit:
        lines.append(f"... and {len(events) - limit} more")
    return "\n".join(lines)


def write_events(events: list[CatalystEvent], out_dir: Path = DATA_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "catalysts.json").write_text(
        json.dumps(
            {
                "generated": datetime.now().isoformat(timespec="seconds"),
                "events": [e.to_dict() for e in events],
            },
            indent=2,
        )
    )


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------


def telegram_config() -> tuple[str, str] | None:
    """Bot token and chat id from the environment or config.json."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")

    config_path = Path(__file__).parent / "config.json"
    if (not token or not chat) and config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            token = token or config.get("telegram_bot_token")
            chat = chat or config.get("telegram_chat_id")
        except json.JSONDecodeError:
            pass

    return (token, chat) if token and chat else None


def send_telegram(text: str) -> bool:
    config = telegram_config()
    if config is None:
        print(
            "  ! Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID,\n"
            "    or create config.json with telegram_bot_token / telegram_chat_id."
        )
        return False

    token, chat = config
    # Telegram rejects messages over 4096 characters.
    body = text if len(text) <= 4000 else text[:3900] + "\n... truncated"
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": body, "disable_web_page_preview": True},
            timeout=30,
        )
    except requests.RequestException as exc:
        print(f"  ! Telegram send failed: {exc}")
        return False

    if not response.ok:
        print(f"  ! Telegram returned {response.status_code}: {response.text[:200]}")
        return False
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Catalyst monitor for the compliant watchlist")
    parser.add_argument("--days", type=int, default=5, help="Business days to scan")
    parser.add_argument("--end", help="Last day to scan, YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--min-importance", type=int, default=0, help="Drop events below this rating (0-5)"
    )
    parser.add_argument(
        "--pass-only", action="store_true", help="Watch only PASS names, excluding REVIEW"
    )
    parser.add_argument(
        "--min-score",
        type=float,
        help="Drop events from companies scoring below this (0-100); needs scoring.py",
    )
    parser.add_argument("--notify", action="store_true", help="Push the digest to Telegram")
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    end = date.fromisoformat(args.end) if args.end else date.today()
    events = scan(session, days=args.days, end=end, include_review=not args.pass_only)
    events = [e for e in events if e.importance >= args.min_importance]
    if args.min_score is not None:
        # Unscored names are kept rather than hidden: a missing score means the
        # data was not available, not that the company is poor.
        events = [e for e in events if e.score is None or e.score >= args.min_score]

    write_events(events)
    digest = format_digest(events)
    print()
    print(digest)

    if args.notify and events:
        header = f"Catalysts, last {args.days} business days to {end}\n\n"
        if send_telegram(header + digest):
            print("\nPushed to Telegram.")

    print(f"\nWritten to {DATA_DIR / 'catalysts.json'}")


if __name__ == "__main__":
    main()
