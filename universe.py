"""
Standing universe sweep: screen an entire index for Shari'ah compliance and
track how the verdicts move between runs.

This is what turns the screener from a lookup tool into a discovery tool. It
maintains a compliant watchlist that every downstream layer (scoring, catalyst
monitoring, briefs) filters through, and reports what changed since last time -
a name going from PASS to FAIL is exactly the sort of thing you would otherwise
find out about far too late.

Three data paths, each chosen for a reason:

  Facts      SEC bulk companyfacts.zip (~1.3 GB, one download). Individual
             companyfacts payloads average 3.24 MB, so 550 of them would move
             more bytes than the whole archive and take 550 requests.
  Metadata   Per-ticker submissions requests, un-cached. These average 429 KB
             because they carry full filing history; only name and SIC are
             kept, in a small local registry.
  Prices     Shares outstanding from the dei taxonomy times a batched price
             download. See resolve_shares_outstanding for the multi-class
             caveat that makes a yfinance fallback necessary.

Usage:
    python universe.py                      # sweep S&P 500 + Nasdaq 100
    python universe.py --limit 25           # quick smoke run
    python universe.py --refresh-bulk       # re-download the SEC archive
    python universe.py --tickers AAPL MSFT  # ad-hoc list
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

import requests

from aaoifi_screener import (
    CACHE_DIR,
    USER_AGENT,
    MarketData,
    SECClient,
    ScreenResult,
    Standard,
    Status,
    Thresholds,
    screen_ticker,
    yf_info,
)

BULK_FACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"

DATA_DIR = Path(__file__).parent / "data"
SNAPSHOT_DIR = DATA_DIR / "snapshots"

# Tried in order until one yields a constituent list. Wikipedia dropped the
# Nasdaq-100 components table, so that index has no Wikipedia source at all;
# the second S&P entry exists so a Wikipedia layout change is not fatal.
INDEX_SOURCES: dict[str, list[str]] = {
    "sp500": [
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "https://www.slickcharts.com/sp500",
    ],
    "nasdaq100": [
        "https://www.slickcharts.com/nasdaq100",
    ],
}

# Both sources write class shares with a dot, EDGAR with a hyphen.
TICKER_FIXUPS = str.maketrans({".": "-"})

# Slickcharts rejects unfamiliar user agents with a 406.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ---------------------------------------------------------------------------
# Index constituents
# ---------------------------------------------------------------------------


class _TableParser(HTMLParser):
    """Extract every HTML table as rows of plain text.

    Deliberately generic rather than Wikipedia-specific, so the same code reads
    slickcharts. Nesting is tracked with a stack because a table inside a table
    would otherwise truncate its parent.
    """

    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._stack: list[list[list[str]]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row and self._stack:
                self._stack[-1].append(self._row)
            self._row = None
        elif tag == "table" and self._stack:
            self.tables.append(self._stack.pop())

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _tickers_from_html(html: str, min_rows: int = 20) -> list[str]:
    """Pull the symbol column from whichever table is the constituent list."""
    parser = _TableParser()
    parser.feed(html)

    for table in parser.tables:
        if len(table) < min_rows:  # constituent lists are long; infoboxes are not
            continue
        header = [h.lower().strip() for h in table[0]]
        index = next((i for i, h in enumerate(header) if h in ("symbol", "ticker")), None)
        if index is None:
            continue
        found = []
        for row in table[1:]:
            if index >= len(row):
                continue
            symbol = row[index].strip().upper().translate(TICKER_FIXUPS)
            if re.fullmatch(r"[A-Z][A-Z0-9-]{0,6}", symbol):
                found.append(symbol)
        if found:
            return found
    return []


def fetch_index(name: str, session: requests.Session, cache_dir: Path = DATA_DIR) -> list[str]:
    """Constituents for a named index, falling back to the last good copy.

    Index membership changes slowly, so a stale cached list is far better than
    an empty universe when a source changes its markup.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"index_{name}.json"

    for url in INDEX_SOURCES[name]:
        try:
            response = session.get(
                url, timeout=30, headers={"User-Agent": BROWSER_USER_AGENT}
            )
            response.raise_for_status()
            tickers = _tickers_from_html(response.text)
            if tickers:
                cached.write_text(
                    json.dumps({"fetched": str(date.today()), "source": url, "tickers": tickers})
                )
                return tickers
            print(f"  ! {name}: no constituent table at {url}")
        except requests.RequestException as exc:
            print(f"  ! {name}: fetch failed for {url} ({exc})")

    if cached.exists():
        payload = json.loads(cached.read_text())
        print(f"  - {name}: using cached list from {payload.get('fetched')}")
        return payload["tickers"]

    return []


def build_universe(
    session: requests.Session, indices: Iterable[str] = ("sp500", "nasdaq100")
) -> list[str]:
    """Deduplicated union of the requested indices, in stable order."""
    seen: dict[str, None] = {}
    for name in indices:
        tickers = fetch_index(name, session)
        print(f"  {name}: {len(tickers)} constituents")
        for ticker in tickers:
            seen.setdefault(ticker, None)
    return list(seen)


# ---------------------------------------------------------------------------
# Bulk facts archive
# ---------------------------------------------------------------------------


class BulkFactsArchive:
    """Random access to every filer's XBRL facts from one downloaded zip."""

    def __init__(self, path: Path, session: requests.Session):
        self.path = path
        self.session = session
        self._zip: zipfile.ZipFile | None = None
        self._members: set[str] | None = None

    def close(self) -> None:
        """Release the file handle.

        Windows will not let the archive be replaced while it is open, so
        --refresh-bulk fails silently without this.
        """
        if self._zip is not None:
            self._zip.close()
            self._zip = None
            self._members = None

    def __enter__(self) -> BulkFactsArchive:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def download(self, force: bool = False) -> bool:
        self.close()
        if self.path.exists() and not force:
            age_days = (time.time() - self.path.stat().st_mtime) / 86400
            size_gb = self.path.stat().st_size / 1e9
            print(f"  archive present ({size_gb:.2f} GB, {age_days:.1f} days old)")
            return True

        self.path.parent.mkdir(parents=True, exist_ok=True)
        partial = self.path.with_suffix(".part")
        print(f"  downloading {BULK_FACTS_URL} (~1.3 GB, one time)")

        try:
            with self.session.get(BULK_FACTS_URL, stream=True, timeout=120) as response:
                response.raise_for_status()
                total = int(response.headers.get("Content-Length") or 0)
                done = 0
                last_report = 0.0
                with open(partial, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1 << 20):
                        handle.write(chunk)
                        done += len(chunk)
                        if total and done - last_report > 50e6:
                            last_report = done
                            print(f"    {done / 1e9:.2f} / {total / 1e9:.2f} GB", flush=True)
        except requests.RequestException as exc:
            print(f"  ! bulk download failed: {exc}")
            partial.unlink(missing_ok=True)
            return False

        partial.replace(self.path)
        print(f"  downloaded {self.path.stat().st_size / 1e9:.2f} GB")
        return True

    def _open(self) -> zipfile.ZipFile | None:
        if self._zip is None:
            if not self.path.exists():
                return None
            try:
                self._zip = zipfile.ZipFile(self.path)
                self._members = set(self._zip.namelist())
            except zipfile.BadZipFile as exc:
                print(f"  ! archive unreadable ({exc}); delete it and re-run")
                return None
        return self._zip

    def facts(self, cik: int) -> dict[str, Any] | None:
        archive = self._open()
        if archive is None or self._members is None:
            return None
        name = f"CIK{cik:010d}.json"
        if name not in self._members:
            return None
        try:
            with archive.open(name) as handle:
                return json.load(handle)
        except (KeyError, json.JSONDecodeError, zipfile.BadZipFile):
            return None


# ---------------------------------------------------------------------------
# Company metadata registry
# ---------------------------------------------------------------------------


class RateLimiter:
    """Shared token gate so concurrent workers stay inside SEC's fair-access rate.

    Each worker reserves the next slot under a lock and sleeps outside it, so
    network latency overlaps while the global request rate stays fixed.
    """

    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next)
            self._next = slot + self.interval
        delay = slot - now
        if delay > 0:
            time.sleep(delay)


class SicRegistry:
    """Persisted {cik: {name, sic}}.

    Submissions payloads are large and mostly filing history. Fetching them
    un-cached and keeping only these two fields turns 236 MB of disk into a few
    hundred kilobytes, and makes repeat sweeps instant.
    """

    def __init__(self, path: Path, client: SECClient):
        self.path = path
        self.client = client
        self.entries: dict[str, dict[str, Any]] = {}
        if path.exists():
            try:
                self.entries = json.loads(path.read_text())
            except json.JSONDecodeError:
                self.entries = {}

    def _fetch(self, cik: int, limiter: RateLimiter | None = None) -> dict[str, Any]:
        if limiter is not None:
            limiter.wait()
        payload = self.client.submissions(cik, cache=False) or {}
        return {"name": payload.get("name", ""), "sic": payload.get("sic")}

    def prefetch(self, ciks: Iterable[int], workers: int = 5, per_second: float = 6.0) -> None:
        """Warm the registry concurrently.

        Sequentially this is the slowest part of a first sweep - 500-odd
        requests of ~429 KB each. Results are cached, so later sweeps only pay
        for constituents that have changed.
        """
        pending = [c for c in dict.fromkeys(ciks) if str(c) not in self.entries]
        if not pending:
            return

        print(f"  fetching metadata for {len(pending)} companies ({workers} workers)", flush=True)
        limiter = RateLimiter(per_second)
        done = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._fetch, cik, limiter): cik for cik in pending}
            for future in as_completed(futures):
                cik = futures[future]
                try:
                    self.entries[str(cik)] = future.result()
                except Exception as exc:
                    print(f"  ! metadata fetch failed for CIK {cik}: {exc}")
                    self.entries[str(cik)] = {"name": "", "sic": None}
                done += 1
                if done % 100 == 0:
                    print(f"    {done}/{len(pending)}", flush=True)

    def get(self, cik: int) -> dict[str, Any]:
        key = str(cik)
        if key not in self.entries:
            self.entries[key] = self._fetch(cik)
        return self.entries[key]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=0, sort_keys=True))


class SweepClient:
    """Presents the SECClient interface, but reads facts from the bulk archive.

    The sweep already decompresses every filer's facts to read shares
    outstanding, so those parsed payloads are handed back in as a cache -
    otherwise screening decompresses and re-parses all of them a second time.
    """

    def __init__(
        self,
        client: SECClient,
        archive: BulkFactsArchive,
        registry: SicRegistry,
        facts_cache: dict[int, dict[str, Any] | None] | None = None,
    ):
        self.client = client
        self.archive = archive
        self.registry = registry
        self.facts_cache = facts_cache or {}

    def ticker_to_cik(self, ticker: str) -> int | None:
        return self.client.ticker_to_cik(ticker)

    def submissions(self, cik: int) -> dict[str, Any]:
        return self.registry.get(cik)

    def company_facts(self, cik: int) -> dict[str, Any] | None:
        if cik in self.facts_cache:
            return self.facts_cache[cik]
        return self.archive.facts(cik)


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------


def resolve_shares_outstanding(facts: dict[str, Any] | None) -> float | None:
    """Common shares outstanding from the dei taxonomy.

    Returns None for multi-class filers. The companyfacts API strips XBRL
    dimensions, and companies with Class A/B/C shares report the figure
    dimensioned by class - so Alphabet and Meta come back with no usable
    entries at all rather than a wrong one. Those names fall back to yfinance.
    """
    if not facts:
        return None
    entries = (
        facts.get("facts", {})
        .get("dei", {})
        .get("EntityCommonStockSharesOutstanding", {})
        .get("units", {})
        .get("shares", [])
    )
    dated = [e for e in entries if e.get("end") and e.get("val")]
    if not dated:
        return None
    latest = max(e["end"] for e in dated)
    at_latest = [e for e in dated if e["end"] == latest]
    if len(at_latest) != 1:
        return None  # ambiguous; do not guess
    return float(at_latest[0]["val"])


def batch_prices(tickers: list[str], chunk_size: int = 100) -> dict[str, float]:
    """Latest close for many tickers in a handful of requests."""
    prices: dict[str, float] = {}
    if not tickers:
        return prices

    try:
        import yfinance as yf
    except ImportError:
        print("  ! yfinance not installed; run: pip install yfinance")
        return prices

    for start in range(0, len(tickers), chunk_size):
        batch = tickers[start : start + chunk_size]
        try:
            frame = yf.download(
                batch,
                period="5d",
                interval="1d",
                progress=False,
                auto_adjust=False,
                # yfinance caches to SQLite, which does not tolerate concurrent
                # writers: threads=True produced "database is locked" errors and
                # spurious "possibly delisted" results for live S&P names, which
                # then fell through to the slow per-ticker path and timed out.
                threads=False,
            )
        except Exception as exc:
            print(f"  ! price batch failed ({exc})")
            continue

        if frame is None or frame.empty:
            continue

        try:
            closes = frame["Close"]
        except KeyError:
            continue

        if len(batch) == 1:
            series = closes.dropna()
            if not series.empty:
                prices[batch[0]] = float(series.iloc[-1])
            continue

        for ticker in batch:
            if ticker not in closes.columns:
                continue
            series = closes[ticker].dropna()
            if not series.empty:
                prices[ticker] = float(series.iloc[-1])

    return prices


def build_market_data(
    tickers: list[str], facts_by_ticker: dict[str, dict[str, Any] | None]
) -> dict[str, MarketData]:
    """Market caps for the whole universe, batched where possible."""
    shares = {t: resolve_shares_outstanding(facts_by_ticker.get(t)) for t in tickers}
    prices = batch_prices(tickers)

    resolved: dict[str, MarketData] = {}
    needs_fallback: list[str] = []

    for ticker in tickers:
        count, price = shares.get(ticker), prices.get(ticker)
        if count and price:
            resolved[ticker] = MarketData(market_cap=count * price)
        else:
            needs_fallback.append(ticker)

    if needs_fallback:
        print(
            f"  {len(resolved)} caps from shares x price; "
            f"{len(needs_fallback)} need per-ticker lookup (multi-class or missing price)"
        )
    for ticker in needs_fallback:
        info = yf_info(ticker)
        cap = info.get("marketCap")
        resolved[ticker] = MarketData(
            market_cap=float(cap) if cap else None,
            dividend_rate=info.get("dividendRate"),
        )

    return resolved


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


@dataclass
class SweepOutcome:
    results: list[ScreenResult]
    changes: list[str]


def run_sweep(
    tickers: list[str],
    session: requests.Session,
    refresh_bulk: bool = False,
    standard: Standard = Standard.MARKET_CAP,
    thresholds: Thresholds | None = None,
    reference_date: date | None = None,
) -> SweepOutcome:
    client = SECClient(cache_dir=CACHE_DIR)
    archive = BulkFactsArchive(CACHE_DIR / "companyfacts.zip", session)
    registry = SicRegistry(DATA_DIR / "sic_registry.json", client)

    print("Bulk facts archive")
    if not archive.download(force=refresh_bulk):
        print("  ! cannot continue without the archive")
        return SweepOutcome([], [])
    try:
        return _sweep_with_archive(
            tickers, client, archive, registry, standard, thresholds, reference_date
        )
    finally:
        archive.close()


def _sweep_with_archive(
    tickers: list[str],
    client: SECClient,
    archive: BulkFactsArchive,
    registry: SicRegistry,
    standard: Standard,
    thresholds: Thresholds | None,
    reference_date: date | None,
) -> SweepOutcome:
    print("Resolving CIKs and facts", flush=True)
    facts_by_ticker: dict[str, dict[str, Any] | None] = {}
    facts_by_cik: dict[int, dict[str, Any] | None] = {}
    unknown: list[str] = []
    for ticker in tickers:
        cik = client.ticker_to_cik(ticker)
        if cik is None:
            unknown.append(ticker)
            continue
        facts = archive.facts(cik)
        facts_by_ticker[ticker] = facts
        facts_by_cik[cik] = facts
    if unknown:
        print(f"  {len(unknown)} not in the SEC ticker registry: {', '.join(unknown[:10])}")
    missing_facts = [t for t, f in facts_by_ticker.items() if not f]
    if missing_facts:
        print(f"  {len(missing_facts)} have no XBRL facts in the archive")

    print("Company metadata", flush=True)
    registry.prefetch(facts_by_cik)

    print("Market data", flush=True)
    market = build_market_data(list(facts_by_ticker), facts_by_ticker)

    print(f"Screening {len(facts_by_ticker)} tickers", flush=True)
    sweep_client = SweepClient(client, archive, registry, facts_cache=facts_by_cik)
    results: list[ScreenResult] = []
    for index, ticker in enumerate(facts_by_ticker, start=1):
        if index % 50 == 0:
            print(f"  {index}/{len(facts_by_ticker)}", flush=True)
        results.append(
            screen_ticker(
                ticker,
                sweep_client,  # type: ignore[arg-type]
                thresholds=thresholds,
                standard=standard,
                reference_date=reference_date,
                market_data=market.get(ticker, MarketData()),
            )
        )
    registry.save()

    changes = diff_against_previous(results)
    return SweepOutcome(results, changes)


# ---------------------------------------------------------------------------
# Snapshots and change tracking
# ---------------------------------------------------------------------------


def _latest_snapshot() -> dict[str, str] | None:
    if not SNAPSHOT_DIR.exists():
        return None
    snapshots = sorted(SNAPSHOT_DIR.glob("*.json"))
    if not snapshots:
        return None
    try:
        return json.loads(snapshots[-1].read_text())
    except json.JSONDecodeError:
        return None


def diff_against_previous(results: list[ScreenResult]) -> list[str]:
    """What moved since the last sweep.

    A name losing compliance is the single most actionable thing this tool
    produces, and it is invisible unless verdicts are compared over time.
    """
    previous = _latest_snapshot()
    current = {r.ticker: r.status.value for r in results}

    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    (SNAPSHOT_DIR / f"{stamp}.json").write_text(json.dumps(current, indent=0, sort_keys=True))

    if previous is None:
        return ["First sweep - no prior snapshot to compare against."]

    changes: list[str] = []
    for ticker, status in sorted(current.items()):
        was = previous.get(ticker)
        if was is None:
            changes.append(f"NEW      {ticker:6s} {status}")
        elif was != status:
            marker = "LOST" if was == "PASS" else ("GAINED" if status == "PASS" else "CHANGED")
            changes.append(f"{marker:8s} {ticker:6s} {was} -> {status}")
    for ticker in sorted(set(previous) - set(current)):
        changes.append(f"DROPPED  {ticker:6s} was {previous[ticker]}")

    return changes or ["No verdict changes since the last sweep."]


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_outputs(results: list[ScreenResult], out_dir: Path = DATA_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for result in sorted(results, key=lambda r: (r.status.value, -(r.market_cap or 0))):
        row = result.to_dict()
        row["reasons"] = " | ".join(row["reasons"])
        row["debt_candidates"] = " | ".join(
            f"{k}={v:.0f}" for k, v in sorted(row["debt_candidates"].items())
        )
        rows.append(row)

    if rows:
        with open(out_dir / "universe_screen.csv", "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    # The compliant watchlist is what every downstream layer consumes.
    watchlist = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "pass": [r.ticker for r in results if r.status is Status.PASS],
        "review": [r.ticker for r in results if r.status is Status.REVIEW],
        "fail": [r.ticker for r in results if r.status is Status.FAIL],
        "insufficient_data": [
            r.ticker for r in results if r.status is Status.INSUFFICIENT_DATA
        ],
    }
    (out_dir / "watchlist.json").write_text(json.dumps(watchlist, indent=2))


def print_summary(outcome: SweepOutcome) -> None:
    counts: dict[str, int] = {}
    for result in outcome.results:
        counts[result.status.value] = counts.get(result.status.value, 0) + 1

    print("\n--- Sweep summary ---")
    for status in ("PASS", "REVIEW", "FAIL", "INSUFFICIENT_DATA"):
        if status in counts:
            print(f"{status:20s} {counts[status]}")

    by_tier: dict[str, int] = {}
    for result in outcome.results:
        if result.status is Status.PASS:
            by_tier[result.cap_tier] = by_tier.get(result.cap_tier, 0) + 1
    if by_tier:
        print("\nCompliant by size tier:")
        for tier in ("mega", "large", "mid", "small", "micro", "unknown"):
            if tier in by_tier:
                print(f"  {tier:10s} {by_tier[tier]}")

    print("\n--- Changes since last sweep ---")
    for line in outcome.changes[:40]:
        print(f"  {line}")
    if len(outcome.changes) > 40:
        print(f"  ... and {len(outcome.changes) - 40} more")


def main() -> None:
    parser = argparse.ArgumentParser(description="Standing Shari'ah compliance sweep")
    parser.add_argument("--tickers", nargs="*", help="Screen an explicit list instead of an index")
    parser.add_argument(
        "--indices",
        nargs="*",
        default=["sp500", "nasdaq100"],
        choices=list(INDEX_SOURCES),
        help="Indices to union together",
    )
    parser.add_argument("--limit", type=int, help="Screen only the first N tickers")
    parser.add_argument(
        "--refresh-bulk", action="store_true", help="Re-download the SEC facts archive"
    )
    parser.add_argument(
        "--basis",
        choices=[s.value for s in Standard],
        default=Standard.MARKET_CAP.value,
    )
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    if args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        print("Universe")
        tickers = build_universe(session, args.indices)
        print(f"  {len(tickers)} unique tickers")

    if not tickers:
        print("No tickers to screen.")
        sys.exit(1)

    if args.limit:
        tickers = tickers[: args.limit]

    outcome = run_sweep(
        tickers, session, refresh_bulk=args.refresh_bulk, standard=Standard(args.basis)
    )
    if not outcome.results:
        sys.exit(1)

    write_outputs(outcome.results)
    print_summary(outcome)
    print(f"\nWritten to {DATA_DIR}")


if __name__ == "__main__":
    main()
