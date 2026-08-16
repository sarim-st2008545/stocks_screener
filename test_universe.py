"""
Tests for the universe sweep's deterministic logic.

Network calls, the 1.3 GB archive and yfinance are all excluded - everything
here runs against fixtures so results do not drift with the market or with
whatever Wikipedia looks like today.
"""

import json
import sys
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import universe as u
from aaoifi_screener import Status

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok  {name}")


# ---------------------------------------------------------------------------
print("--- constituent table parsing ---")

WIKI_STYLE = """
<html><body>
<table class="infobox"><tr><th>Symbol</th></tr><tr><td>IGNORE</td></tr></table>
<table class="wikitable sortable">
<tr><th>Symbol</th><th>Security</th></tr>
""" + "".join(
    f'<tr><td><a href="/x">{t}</a></td><td>Company {t}</td></tr>' for t in
    ["MMM", "AOS", "ABT", "ABBV", "ACN", "ADBE", "AMD", "AES", "AFL", "A",
     "APD", "ABNB", "AKAM", "ALB", "ARE", "ALGN", "ALLE", "LNT", "ALL", "GOOGL",
     "BRK.B", "BF.B"]
) + """
</table></body></html>
"""

tickers = u._tickers_from_html(WIKI_STYLE)
check("finds the long table, not the infobox", len(tickers), 22)
check("first symbol", tickers[0], "MMM")
check("class shares use EDGAR hyphens", tickers[-2:], ["BRK-B", "BF-B"])

# Slickcharts puts the symbol in the third column and has no 'wikitable' class,
# which is why the parser is generic rather than Wikipedia-specific.
SLICK_STYLE = """
<html><body><table class="table table-hover table-sm">
<tr><th>#</th><th>Company</th><th>Symbol</th><th>Weight</th></tr>
""" + "".join(
    f'<tr><td>{i}</td><td><a href="/symbol/{t}">Co</a></td>'
    f'<td><a href="/symbol/{t}">{t}</a></td><td>1.0%</td></tr>'
    for i, t in enumerate(
        ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "AVGO", "META", "TSLA", "COST",
         "NFLX", "TMUS", "CSCO", "AMD", "PEP", "LIN", "ADBE", "QCOM", "TXN", "AMGN",
         "INTU", "ISRG"], 1)
) + """
</table></body></html>
"""
slick = u._tickers_from_html(SLICK_STYLE)
check("reads a non-wikitable layout", len(slick), 22)
check("picks the symbol column, not the rank", slick[0], "NVDA")

# A table nested inside another must not truncate its parent's rows.
NESTED = """
<html><body><table class="outer"><tr><td>
  <table class="inner"><tr><th>Symbol</th></tr><tr><td>NOPE</td></tr></table>
</td></tr></table>
<table><tr><th>Ticker</th></tr>""" + "".join(
    f"<tr><td>T{i}</td></tr>" for i in range(25)
) + "</table></body></html>"
nested = u._tickers_from_html(NESTED)
check("nested tables do not corrupt parsing", len(nested), 25)

check("no table -> empty list", u._tickers_from_html("<html><body>hi</body></html>"), [])
check(
    "short table rejected",
    u._tickers_from_html("<table><tr><th>Symbol</th></tr><tr><td>AAPL</td></tr></table>"),
    [],
)

# ---------------------------------------------------------------------------
print("--- shares outstanding ---")


def dei_facts(entries):
    return {
        "facts": {
            "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": entries}}}
        }
    }


check(
    "single class resolves",
    u.resolve_shares_outstanding(dei_facts([{"end": "2026-07-17", "val": 14_594_180_000}])),
    14_594_180_000.0,
)
check(
    "newest date wins",
    u.resolve_shares_outstanding(
        dei_facts([
            {"end": "2025-07-17", "val": 100},
            {"end": "2026-07-17", "val": 200},
        ])
    ),
    200.0,
)

# Regression: the companyfacts API strips XBRL dimensions, so Alphabet and Meta
# expose no usable share count. Guessing one would produce a wrong market cap
# and therefore wrong ratios across the board.
check(
    "multi-class returns None rather than guessing",
    u.resolve_shares_outstanding(
        dei_facts([
            {"end": "2026-07-17", "val": 5_800_000_000},
            {"end": "2026-07-17", "val": 6_100_000_000},
        ])
    ),
    None,
)
check("empty dei -> None", u.resolve_shares_outstanding(dei_facts([])), None)
check("no facts -> None", u.resolve_shares_outstanding(None), None)
check("no dei section -> None", u.resolve_shares_outstanding({"facts": {"us-gaap": {}}}), None)

# ---------------------------------------------------------------------------
print("--- bulk archive ---")

with tempfile.TemporaryDirectory() as tmp:
    archive_path = Path(tmp) / "companyfacts.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("CIK0000320193.json", json.dumps({"cik": 320193, "facts": {"us-gaap": {}}}))
        zf.writestr("CIK0000789019.json", json.dumps({"cik": 789019, "facts": {"us-gaap": {}}}))

    with u.BulkFactsArchive(archive_path, session=None) as archive:
        check("reads a member by CIK", archive.facts(320193)["cik"], 320193)
        check("zero-pads short CIKs", archive.facts(789019)["cik"], 789019)
        check("absent CIK -> None", archive.facts(999999999), None)

    corrupt = Path(tmp) / "bad.zip"
    corrupt.write_bytes(b"not a zip file")
    with u.BulkFactsArchive(corrupt, None) as bad:
        check("corrupt archive -> None, no crash", bad.facts(320193), None)

    with u.BulkFactsArchive(Path(tmp) / "absent.zip", None) as missing:
        check("missing archive -> None, no crash", missing.facts(320193), None)

    # Windows locks an open zip, so the handle must be released before the
    # archive can be replaced by a refresh.
    with u.BulkFactsArchive(archive_path, session=None) as reopened:
        reopened.facts(320193)
    archive_path.replace(Path(tmp) / "moved.zip")
    check("closed archive can be replaced", (Path(tmp) / "moved.zip").exists(), True)

# ---------------------------------------------------------------------------
print("--- rate limiting and metadata prefetch ---")

limiter = u.RateLimiter(per_second=200)  # 5 ms apart
start = time.monotonic()
for _ in range(6):
    limiter.wait()
elapsed = time.monotonic() - start
check("sequential calls are spaced", elapsed >= 0.02, True)

# Slots must be reserved globally, not per thread, or concurrency would
# multiply the request rate and trip SEC's fair-access limit.
limiter = u.RateLimiter(per_second=200)
start = time.monotonic()
with ThreadPoolExecutor(max_workers=8) as pool:
    list(pool.map(lambda _: limiter.wait(), range(8)))
elapsed = time.monotonic() - start
check("concurrent workers share one rate budget", elapsed >= 0.03, True)


class FakeSubmissionsClient:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def submissions(self, cik, cache=True):
        with self.lock:
            self.calls.append(cik)
        return {"name": f"Co {cik}", "sic": "3674", "filings": "big payload"}


with tempfile.TemporaryDirectory() as tmp:
    fake = FakeSubmissionsClient()
    registry = u.SicRegistry(Path(tmp) / "reg.json", fake)
    registry.prefetch([320193, 789019, 320193], workers=3, per_second=500)
    check("prefetch deduplicates CIKs", sorted(fake.calls), [320193, 789019])
    check("keeps only name and sic", set(registry.entries["320193"]), {"name", "sic"})
    check("sic captured", registry.entries["789019"]["sic"], "3674")

    registry.prefetch([320193, 789019], workers=3, per_second=500)
    check("cached entries are not refetched", len(fake.calls), 2)

    registry.save()
    reloaded = u.SicRegistry(Path(tmp) / "reg.json", fake)
    check("registry survives a reload", reloaded.get(320193)["name"], "Co 320193")
    check("reload did not trigger a fetch", len(fake.calls), 2)

    corrupt_path = Path(tmp) / "corrupt.json"
    corrupt_path.write_text("{not json")
    check(
        "corrupt registry starts empty rather than crashing",
        u.SicRegistry(corrupt_path, fake).entries,
        {},
    )

# ---------------------------------------------------------------------------
print("--- change tracking ---")


class FakeResult:
    def __init__(self, ticker, status):
        self.ticker, self.status = ticker, status


def run_diff(previous, current, tmpdir):
    u.SNAPSHOT_DIR = Path(tmpdir) / "snapshots"
    u.SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if previous is not None:
        (u.SNAPSHOT_DIR / "2026-08-01.json").write_text(json.dumps(previous))
    results = [FakeResult(t, Status(s)) for t, s in current.items()]
    return u.diff_against_previous(results)


original_snapshot_dir = u.SNAPSHOT_DIR
with tempfile.TemporaryDirectory() as tmp:
    lines = run_diff(None, {"AAPL": "PASS"}, tmp)
    check("first sweep says so", "First sweep" in lines[0], True)

with tempfile.TemporaryDirectory() as tmp:
    lines = run_diff(
        {"AAPL": "PASS", "MSFT": "PASS", "V": "FAIL", "GONE": "PASS"},
        {"AAPL": "PASS", "MSFT": "FAIL", "V": "PASS", "NEW": "REVIEW"},
        tmp,
    )
    joined = "\n".join(lines)
    check("compliance loss flagged", any(l.startswith("LOST") and "MSFT" in l for l in lines), True)
    check("compliance gain flagged", any(l.startswith("GAINED") and "V" in l for l in lines), True)
    check("new constituent flagged", any(l.startswith("NEW") and "NEW" in l for l in lines), True)
    check("dropped constituent flagged", any(l.startswith("DROPPED") for l in lines), True)
    check("unchanged name not listed", "AAPL" in joined, False)

with tempfile.TemporaryDirectory() as tmp:
    lines = run_diff({"AAPL": "PASS"}, {"AAPL": "PASS"}, tmp)
    check("no changes reported cleanly", "No verdict changes" in lines[0], True)

u.SNAPSHOT_DIR = original_snapshot_dir

print()
if fails:
    print(f"FAILED ({len(fails)}):")
    for f in fails:
        print("  x", f)
    sys.exit(1)
print("ALL TESTS PASSED")
