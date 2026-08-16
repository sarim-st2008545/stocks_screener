"""
Tests for catalyst monitoring.

All fixtures are inline; nothing here touches the network. The Form 4 sample
mirrors the real structure returned by EDGAR, including the <value> wrapper
that most leaves carry.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import catalysts as c

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok  {name}")


def approx(name, got, want, tol=1e-6):
    if got is None or abs(got - want) > tol:
        fails.append(f"{name}: got {got!r}, want ~{want!r}")
    else:
        print(f"  ok  {name}")


REF = c.FilingRef(
    cik=1001601,
    ticker="TEST",
    company="Test Corp",
    form="4",
    filed=date(2026, 8, 4),
    path="edgar/data/1001601/0001493152-26-036046.txt",
)


def form4(owners: str, transactions: str) -> str:
    return f"""
    <SEC-HEADER>junk</SEC-HEADER>
    <ownershipDocument>
      <issuer><issuerTradingSymbol>TEST</issuerTradingSymbol></issuer>
      {owners}
      <nonDerivativeTable>{transactions}</nonDerivativeTable>
    </ownershipDocument>
    """


def owner(name: str, director="0", officer="0", title="") -> str:
    return f"""
    <reportingOwner>
      <reportingOwnerId><rptOwnerName>{name}</rptOwnerName></reportingOwnerId>
      <reportingOwnerRelationship>
        <isDirector>{director}</isDirector>
        <isOfficer>{officer}</isOfficer>
        <officerTitle>{title}</officerTitle>
      </reportingOwnerRelationship>
    </reportingOwner>"""


def txn(code: str, shares: str, price: str, when="2026-08-03", after="1000") -> str:
    return f"""
    <nonDerivativeTransaction>
      <transactionDate><value>{when}</value></transactionDate>
      <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionShares><value>{shares}</value></transactionShares>
        <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
      </transactionAmounts>
      <postTransactionAmounts>
        <sharesOwnedFollowingTransaction><value>{after}</value></sharesOwnedFollowingTransaction>
      </postTransactionAmounts>
    </nonDerivativeTransaction>"""


# ---------------------------------------------------------------------------
print("--- business days ---")

# 2026-08-04 is a Tuesday.
check(
    "weekends are skipped",
    c.business_days(date(2026, 8, 4), 4),
    [date(2026, 7, 30), date(2026, 7, 31), date(2026, 8, 3), date(2026, 8, 4)],
)
check("a Saturday end date yields only weekdays",
      all(d.weekday() < 5 for d in c.business_days(date(2026, 8, 8), 5)), True)

# ---------------------------------------------------------------------------
print("--- filing references ---")

check(
    "accession extracted from path",
    REF.accession,
    "0001493152-26-036046",
)
check(
    "index URL built correctly",
    REF.url,
    "https://www.sec.gov/Archives/edgar/data/1001601/000149315226036046/"
    "0001493152-26-036046-index.htm",
)

# ---------------------------------------------------------------------------
print("--- daily index parsing ---")

INDEX = """Description:           Daily Index
CIK|Company Name|Form Type|Date Filed|File Name
--------------------------------------------------------------------------------
320193|APPLE INC|4|20260804|edgar/data/320193/0000320193-26-000001.txt
320193|APPLE INC|8-K|20260804|edgar/data/320193/0000320193-26-000002.txt
320193|APPLE INC|10-Q|20260804|edgar/data/320193/0000320193-26-000003.txt
999999|RANDOM CO|4|20260804|edgar/data/999999/0000999999-26-000001.txt
320193|APPLE INC|4|20260804|edgar/data/320193/0000320193-26-000001.txt
"""


class StubIndex(c.DailyIndex):
    def __init__(self, text):
        self._text = text

    def fetch(self, day):
        return self._text


refs = StubIndex(INDEX).filings(date(2026, 8, 4), {320193: "AAPL"})
check("only watchlist CIKs kept", {r.ticker for r in refs}, {"AAPL"})
check("only Form 4 and 8-K kept", sorted({r.form for r in refs}), ["4", "8-K"])
check("duplicate accession de-duplicated", len(refs), 2)
check("missing index day yields nothing", StubIndex(None).filings(date(2026, 8, 4), {320193: "A"}), [])


class StubSession:
    """Returns a fixed HTTP status for any index request."""

    def __init__(self, status):
        self.status = status

    def get(self, url, timeout=None):
        class Response:
            status_code = self.status
            ok = 200 <= self.status < 300
            text = ""

        return Response()


import tempfile

# SEC answers 403 for a date it has no index for, so treating only 404 as
# "not published" meant every run warned about today's missing index.
for status in (403, 404):
    with tempfile.TemporaryDirectory() as tmp:
        index = c.DailyIndex(StubSession(status), cache_dir=Path(tmp))
        check(f"HTTP {status} treated as no index", index.fetch(date(2026, 8, 5)), None)

# ---------------------------------------------------------------------------
print("--- Form 4 parsing ---")

single = c.parse_form4(form4(owner("Smith Jane", director="1"), txn("P", "1000", "50.00")), REF)
check("one transaction parsed", len(single), 1)
check("owner name title-cased", single[0].owner, "Smith Jane")
check("director role detected", single[0].role, "director")
check("transaction code read", single[0].code, "P")
approx("value is shares x price", single[0].value, 50_000.0)
check("ticker from the filing", single[0].ticker, "TEST")

officer_trade = c.parse_form4(
    form4(owner("Doe John", officer="1", title="CFO"), txn("P", "100", "10")), REF
)
check("officer title used as role", officer_trade[0].role, "CFO")

# Regression: iterating the transaction table once per reporting owner
# double-counted every transaction on jointly filed Form 4s.
joint = c.parse_form4(
    form4(owner("Smith Jane", director="1") + owner("Smith Family Trust"), txn("P", "1000", "50")),
    REF,
)
check("joint filing does not duplicate transactions", len(joint), 1)
check("both owners named", joint[0].owner, "Smith Jane & Smith Family Trust")

# Regression: a purchase filled at several prices is one buy, not several.
split = c.parse_form4(
    form4(
        owner("Maroone Michael E", director="1"),
        txn("P", "21153", "61.69", after="100000") + txn("P", "3847", "62.40", after="103847"),
    ),
    REF,
)
check("split execution merged into one trade", len(split), 1)
approx("merged shares summed", split[0].shares, 25_000.0)
approx("merged value summed", split[0].value, 21153 * 61.69 + 3847 * 62.40)
approx("merged price is volume weighted", split[0].price, split[0].value / 25_000.0)
check("final holding taken from the last lot", split[0].shares_after, 103847.0)

mixed = c.parse_form4(
    form4(owner("Smith Jane", director="1"), txn("P", "100", "10") + txn("S", "100", "10")),
    REF,
)
check("different codes stay separate", len(mixed), 2)

check("malformed XML yields nothing", c.parse_form4("<ownershipDocument><bad>", REF), [])
check("no ownership document yields nothing", c.parse_form4("random text", REF), [])

# ---------------------------------------------------------------------------
print("--- buy filtering ---")

big = c.InsiderTrade("T", "A", "director", "P", 1000, 50, 50_000, "2026-08-03", None, "")
small = c.InsiderTrade("T", "A", "director", "P", 10, 50, 500, "2026-08-03", None, "")
grant = c.InsiderTrade("T", "A", "director", "A", 1000, 50, 50_000, "2026-08-03", None, "")
check("real purchase qualifies", big.is_open_market_buy, True)
check("token purchase filtered out", small.is_open_market_buy, False)
check("stock grant is not a buy signal", grant.is_open_market_buy, False)

# ---------------------------------------------------------------------------
print("--- 8-K items ---")

EIGHTK = """
<SEC-HEADER>
CONFORMED SUBMISSION TYPE:	8-K
ITEM INFORMATION:		Results of Operations and Financial Condition
ITEM INFORMATION:		Financial Statements and Exhibits
</SEC-HEADER>
"""
items = c.parse_8k_items(EIGHTK)
check("items extracted", items, ["Results of Operations and Financial Condition",
                                 "Financial Statements and Exhibits"])
check("earnings release rated highly", c.rate_8k(items), 4)
check("restatement rated highest", c.rate_8k(["Non-Reliance on Previously Issued Financials"]), 5)
check("boilerplate alone rates zero", c.rate_8k(["Financial Statements and Exhibits"]), 0)
check("unknown item still registers", c.rate_8k(["Some Novel Item"]), 1)
check("no items", c.parse_8k_items("nothing here"), [])

# ---------------------------------------------------------------------------
print("--- cluster detection ---")


def buy(ticker, owner_name, when, value=100_000):
    return c.InsiderTrade(ticker, owner_name, "director", "P", value / 50, 50, value, when, None, "")


cluster = c.detect_clusters([
    buy("ABC", "Alice", "2026-08-01"),
    buy("ABC", "Bob", "2026-08-10"),
])
check("two distinct buyers form a cluster", len(cluster), 1)
check("cluster rated highest", cluster[0].importance, 5)
check("cluster totals the value", "$200k" in cluster[0].headline, True)

check(
    "one insider buying twice is not a cluster",
    c.detect_clusters([buy("ABC", "Alice", "2026-08-01"), buy("ABC", "Alice", "2026-08-10")]),
    [],
)
check(
    "buys spread beyond the window are not a cluster",
    c.detect_clusters([buy("ABC", "Alice", "2026-01-01"), buy("ABC", "Bob", "2026-08-10")]),
    [],
)
check(
    "buyers in different companies are not a cluster",
    c.detect_clusters([buy("ABC", "Alice", "2026-08-01"), buy("XYZ", "Bob", "2026-08-02")]),
    [],
)

# ---------------------------------------------------------------------------
print("--- event assembly ---")

events = c.build_events(
    [
        buy("ABC", "Alice", "2026-08-01", 600_000),
        c.InsiderTrade("ABC", "Carl", "CFO", "S", 40_000, 50, 2_000_000, "2026-08-02", None, ""),
        c.InsiderTrade("ABC", "Dana", "director", "A", 1000, 50, 50_000, "2026-08-02", None, ""),
    ],
    [(REF, ["Results of Operations and Financial Condition"])],
)
kinds = sorted(e.kind for e in events)
check("grant produced no event", "insider_grant" not in kinds, True)
check("buy, sale and 8-K produced events", kinds, ["8k", "insider_buy", "insider_sell"])
by_kind = {e.kind: e for e in events}
check("large buy rated 4", by_kind["insider_buy"].importance, 4)
check("sale rated low", by_kind["insider_sell"].importance, 1)

# ---------------------------------------------------------------------------
print("--- score ranking ---")

ranked = [
    c.CatalystEvent("LOW", "8k", "2026-08-04", 4, "earnings"),
    c.CatalystEvent("HIGH", "8k", "2026-08-04", 4, "earnings"),
    c.CatalystEvent("CRIT", "8k", "2026-08-04", 5, "restatement"),
    c.CatalystEvent("NOSCORE", "8k", "2026-08-04", 4, "earnings"),
]
c.attach_scores(ranked, {"LOW": 20.0, "HIGH": 90.0, "CRIT": 10.0})
check("score attached", {e.ticker: e.score for e in ranked}["HIGH"], 90.0)
check("unscored name left as None", ranked[3].score, None)

ranked.sort(key=lambda e: (-e.importance, -(e.score or -1), e.date, e.ticker))
check("importance still dominates score", ranked[0].ticker, "CRIT")
check("better business ranks first among equals", ranked[1].ticker, "HIGH")
check("unscored sorts last, not first", ranked[-1].ticker, "NOSCORE")

# ---------------------------------------------------------------------------
print("--- digest and config ---")

check("empty digest is explicit", c.format_digest([]), "No catalysts in this window.")
digest = c.format_digest(events)
check("digest names the ticker", "ABC" in digest, True)

import os
saved = {k: os.environ.pop(k, None) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
config_file = Path(c.__file__).parent / "config.json"
if not config_file.exists():
    check("telegram unconfigured returns None", c.telegram_config(), None)
else:
    print("  --  telegram config present, skipping unconfigured check")
for k, v in saved.items():
    if v is not None:
        os.environ[k] = v

print()
if fails:
    print(f"FAILED ({len(fails)}):")
    for f in fails:
        print("  x", f)
    sys.exit(1)
print("ALL TESTS PASSED")
