"""
AAOIFI Shari'ah compliance screener for US-listed equities.

Data sources (all free, no API key required):
  - SEC EDGAR company_tickers.json  -> ticker to CIK mapping
  - SEC EDGAR submissions API       -> SIC code, company metadata
  - SEC EDGAR companyfacts API      -> XBRL financial concepts
  - yfinance                        -> market capitalisation

AAOIFI Shari'ah Standard No. 21 applies three financial ratios plus a
business-activity screen. Ratio denominators differ between interpretations;
this module defaults to market capitalisation (the common contemporary
application) and allows total assets as an alternative.

IMPORTANT: this is a screening aid, not a religious ruling. Ratio thresholds,
denominator choice, and the treatment of edge cases (operating leases, hotel
revenue, cash vs interest-bearing cash) are matters of scholarly interpretation.
Confirm your standard with a scholar you trust.

Usage:
    pip install requests yfinance
    python aaoifi_screener.py AAPL MSFT JPM TSLA
    python aaoifi_screener.py --file tickers.txt --out results.csv --json
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# SEC requires a descriptive User-Agent with contact details. Requests without
# one are rejected. Replace with your own name and email before running.
USER_AGENT = "Sarim Toqeer sarimtoqeer02@gmail.com"

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# SEC fair-access guidance is 10 requests/second. We stay well under.
REQUEST_DELAY_SECONDS = 0.15

CACHE_DIR = Path(__file__).parent / ".sec_cache"

# A company that stops using an XBRL concept leaves its last value in
# companyfacts forever. Mastercard's LongTermDebt ends in 2011, Verizon's in
# 2013, Realty Income's in 2017 - all while those companies carried billions in
# debt under other tags. Any fact older than this is treated as absent, so
# resolution falls through to a concept that is still being maintained.
MAX_FACT_AGE_DAYS = 550

# Income-statement facts need a longer horizon than balance-sheet facts. A
# trailing-twelve-month figure is stitched from four quarters, so the oldest
# one is already a year behind the window's end date; cutting at 550 days
# severs otherwise-valid chains. Eli Lilly's interest income failed to resolve
# for exactly this reason - three fresh quarters and a fourth just past the line.
MAX_DURATION_FACT_AGE_DAYS = 900

# A ratio built from a window this far in the past is reported but flagged,
# since it no longer describes the company you would be buying today.
STALE_INCOME_WINDOW_DAYS = 450

# Income-statement windows must line up before a ratio between them means
# anything. Revenue for a quarter over interest income for a year is noise.
PERIOD_ALIGNMENT_TOLERANCE_DAYS = 45

# Balance-sheet components summed into one figure must come from the same
# filing date, give or take a few days of fiscal-calendar drift.
BALANCE_SHEET_ALIGNMENT_DAYS = 10

# Duration classification, in days between start and end.
QUARTER_DAYS = (80, 100)
ANNUAL_DAYS = (330, 400)


class Standard(str, Enum):
    """Ratio denominator basis. AAOIFI is commonly applied against market cap."""

    MARKET_CAP = "market_cap"
    TOTAL_ASSETS = "total_assets"


@dataclass
class Thresholds:
    """AAOIFI Shari'ah Standard No. 21 limits."""

    debt_ratio_max: float = 0.30
    interest_securities_ratio_max: float = 0.30
    noncompliant_income_ratio_max: float = 0.05


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    REVIEW = "REVIEW"                        # sector or data is scholar-dependent
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"  # e.g. recent IPO, thin XBRL history


# ---------------------------------------------------------------------------
# Business activity screen (SIC-based)
# ---------------------------------------------------------------------------
#
# SIC codes are coarse. They reliably catch the clear cases (banks, brewers,
# tobacco) but cannot detect, say, a supermarket chain deriving 3% of revenue
# from alcohol. Codes are therefore split into two tiers: outright exclusion,
# and flag-for-review where scholars differ or the code is ambiguous.

EXCLUDED_SIC_RANGES: list[tuple[int, int, str]] = [
    (2082, 2085, "Alcoholic beverages (malt, wine, distilled spirits)"),
    (2100, 2199, "Tobacco products"),
    (6020, 6199, "Conventional banking and credit institutions"),
    (6200, 6299, "Security and commodity brokers, dealers, exchanges"),
    (6300, 6411, "Conventional insurance"),
    (6712, 6712, "Bank holding companies"),
    (6726, 6726, "Investment offices (interest-bearing funds)"),
    (3480, 3489, "Ordnance and accessories"),
    (3760, 3769, "Guided missiles, space vehicles"),
    (3795, 3795, "Tanks and tank components"),
    (7993, 7993, "Coin-operated amusement / gaming devices"),
]

REVIEW_SIC_RANGES: list[tuple[int, int, str]] = [
    (213, 213, "Hog farming - pork exposure"),
    (2011, 2013, "Meat packing - verify pork and slaughter method"),
    # 2080/2081 is the generic "Beverages" bucket, used by brewers and
    # soft-drink makers alike. Constellation Brands files under 2080. Dedicated
    # soft-drink producers (Coca-Cola, PepsiCo, Monster) file under 2086 and
    # pass cleanly, so only the ambiguous codes land here.
    (2080, 2081, "Beverages - generic SIC, verify alcohol exposure"),
    (5122, 5122, "Drugs and proprietaries - verify product mix"),
    (5812, 5813, "Eating and drinking places - verify alcohol revenue share"),
    (5921, 5921, "Liquor stores"),
    (6500, 6599, "Real estate - verify tenant mix and financing structure"),
    (6798, 6798, "REITs - verify financing structure and tenant mix"),
    (7011, 7011, "Hotels - verify alcohol, gambling, entertainment revenue"),
    (7812, 7841, "Motion picture production and distribution"),
    (7900, 7999, "Amusement and recreation - verify gambling exposure"),
]

# A SIC code records the filer's primary industry as EDGAR captured it, in some
# cases decades ago, and it cannot express revenue mix. Where a code is known to
# be wrong or too coarse for screening, override it by CIK. Add entries here as
# you find disagreements with your reference screener.
SIC_OVERRIDES: dict[int, tuple[Status, str]] = {
    # Constellation Brands - EDGAR lists the generic beverages code 2080, but
    # essentially all revenue is beer, wine and spirits.
    16918: (Status.FAIL, "Alcoholic beverages (override: SIC 2080 is generic)"),
}


def classify_sic(sic: int | None, cik: int | None = None) -> tuple[Status, str]:
    """Return the business-activity verdict for a SIC code."""
    if cik is not None and cik in SIC_OVERRIDES:
        status, reason = SIC_OVERRIDES[cik]
        prefix = "Excluded sector" if status is Status.FAIL else "Requires review"
        return status, f"{prefix}: {reason}"

    if sic is None:
        return Status.INSUFFICIENT_DATA, "No SIC code reported"

    for low, high, reason in EXCLUDED_SIC_RANGES:
        if low <= sic <= high:
            return Status.FAIL, f"Excluded sector: {reason} (SIC {sic})"

    for low, high, reason in REVIEW_SIC_RANGES:
        if low <= sic <= high:
            return Status.REVIEW, f"Requires review: {reason} (SIC {sic})"

    return Status.PASS, f"Sector not excluded (SIC {sic})"


# ---------------------------------------------------------------------------
# XBRL fact layer
# ---------------------------------------------------------------------------
#
# companyfacts returns every observation a company has ever tagged for a
# concept: many filings, restatements, quarterly and annual durations, all mixed
# together. Three things have to happen before a number is usable.
#
#   1. Restatements collapse. The same (start, end) window can appear in an
#      original 10-Q and again in a later 10-K with a different value. Keep the
#      most recently filed.
#   2. Stale concepts drop out. See MAX_FACT_AGE_DAYS.
#   3. Durations get classified. A balance-sheet fact has no start date; an
#      income-statement fact does, and whether it spans a quarter or a year
#      determines what it can legitimately be divided by.


@dataclass(frozen=True)
class Fact:
    """One usable XBRL observation."""

    concept: str
    value: float
    end: date
    start: date | None
    form: str
    filed: str

    @property
    def days(self) -> int | None:
        """Length of the reporting window, or None for balance-sheet facts."""
        return (self.end - self.start).days if self.start else None

    @property
    def is_instant(self) -> bool:
        return self.start is None

    @property
    def is_annual(self) -> bool:
        days = self.days
        return days is not None and ANNUAL_DAYS[0] <= days <= ANNUAL_DAYS[1]

    @property
    def is_quarterly(self) -> bool:
        days = self.days
        return days is not None and QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]

    def label(self) -> str:
        if self.is_instant:
            return f"{self.concept} @ {self.end}"
        return f"{self.concept} {self.start}..{self.end}"


@dataclass(frozen=True)
class Window:
    """A trailing-twelve-month figure and the period it covers."""

    value: float
    start: date
    end: date
    concept: str
    basis: str  # "annual" or "4x quarterly"

    def label(self) -> str:
        return f"{self.concept} {self.start}..{self.end} ({self.basis})"


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


class FactSelector:
    """Period-aware access to one company's XBRL facts."""

    def __init__(
        self,
        facts: dict[str, Any] | None,
        reference_date: date | None = None,
        max_age_days: int = MAX_FACT_AGE_DAYS,
        max_duration_age_days: int = MAX_DURATION_FACT_AGE_DAYS,
    ):
        self._us_gaap: dict[str, Any] = (facts or {}).get("facts", {}).get("us-gaap", {})
        self.reference_date = reference_date or date.today()
        self.max_age_days = max_age_days
        self.max_duration_age_days = max_duration_age_days
        self._cache: dict[str, list[Fact]] = {}

    # -- normalisation ------------------------------------------------------

    def facts_for(self, concept: str) -> list[Fact]:
        """Fresh, de-duplicated observations for a concept, oldest first."""
        if concept in self._cache:
            return self._cache[concept]

        entries = self._us_gaap.get(concept, {}).get("units", {}).get("USD", [])
        instant_cutoff = self.reference_date - timedelta(days=self.max_age_days)
        duration_cutoff = self.reference_date - timedelta(days=self.max_duration_age_days)

        # Collapse restatements: for a given period, the newest filing wins.
        best: dict[tuple[date | None, date], dict[str, Any]] = {}
        for entry in entries:
            end = _parse_date(entry.get("end"))
            if end is None or entry.get("val") is None:
                continue
            start = _parse_date(entry.get("start"))
            if end < (duration_cutoff if start else instant_cutoff):
                continue
            key = (start, end)
            incumbent = best.get(key)
            if incumbent is None or (entry.get("filed") or "") >= (incumbent.get("filed") or ""):
                best[key] = entry

        result = [
            Fact(
                concept=concept,
                value=float(entry["val"]),
                end=key[1],
                start=key[0],
                form=entry.get("form") or "",
                filed=entry.get("filed") or "",
            )
            for key, entry in best.items()
        ]
        result.sort(key=lambda f: f.end)
        self._cache[concept] = result
        return result

    # -- balance sheet ------------------------------------------------------

    def instant(self, concept: str) -> Fact | None:
        """Most recent balance-sheet value for a concept."""
        candidates = [f for f in self.facts_for(concept) if f.is_instant]
        return candidates[-1] if candidates else None

    def instant_max(self, concepts: list[str]) -> Fact | None:
        """Largest current value across overlapping concepts.

        Used where several tags describe the same pool - ShortTermInvestments,
        MarketableSecuritiesCurrent and AvailableForSaleSecuritiesDebtSecurities
        often cover the same securities, so summing them would double-count.
        """
        live = [h for h in (self.instant(c) for c in concepts) if h is not None]
        return max(live, key=lambda f: f.value) if live else None

    def instant_first(self, concepts: list[str]) -> Fact | None:
        """First available value in priority order."""
        for concept in concepts:
            hit = self.instant(concept)
            if hit is not None:
                return hit
        return None

    # -- income statement ---------------------------------------------------

    def ttm_candidates(self, concept: str) -> list[Window]:
        """Every defensible trailing-twelve-month figure for a concept.

        Annual facts are used directly. Where only quarterly facts exist, four
        contiguous quarters are chained together. Returning all candidates
        rather than only the newest lets the caller pick a window that lines up
        with the other side of the ratio.
        """
        facts = self.facts_for(concept)
        windows = [
            Window(f.value, f.start, f.end, concept, "annual")
            for f in facts
            if f.is_annual and f.start is not None
        ]

        quarters = {f.end: f for f in facts if f.is_quarterly}
        newest_first = sorted(quarters.values(), key=lambda f: f.end, reverse=True)

        for seed in newest_first[:8]:  # bound the work; older seeds add nothing
            chain = [seed]
            cursor = seed.start
            while len(chain) < 4 and cursor is not None:
                target = cursor - timedelta(days=1)
                nxt = next((q for q in newest_first if abs((q.end - target).days) <= 5), None)
                if nxt is None:
                    break
                chain.append(nxt)
                cursor = nxt.start
            if len(chain) == 4 and chain[-1].start is not None:
                windows.append(
                    Window(
                        value=sum(c.value for c in chain),
                        start=chain[-1].start,
                        end=chain[0].end,
                        concept=concept,
                        basis="4x quarterly",
                    )
                )

        return windows

    def ttm_candidates_first(self, concepts: list[str]) -> list[Window]:
        """Candidates from the first concept in priority order that has any.

        Deliberately does not merge across concepts: Revenues and
        RevenueFromContractWithCustomer overlap, and mixing them double-counts.
        """
        for concept in concepts:
            windows = self.ttm_candidates(concept)
            if windows:
                return windows
        return []


def align_windows(
    numerator: list[Window],
    denominator: list[Window],
    tolerance_days: int = PERIOD_ALIGNMENT_TOLERANCE_DAYS,
) -> tuple[Window, Window] | None:
    """Pick the most recent pair of windows covering the same period.

    Without this, Visa's quarterly revenue gets divided into its annual interest
    income and the non-compliant income ratio comes out 3.5x too high.
    """
    best: tuple[Window, Window] | None = None
    best_key: tuple[date, int] | None = None

    for num in numerator:
        for den in denominator:
            if abs((num.end - den.end).days) > tolerance_days:
                continue
            # Prefer the latest common period, then genuine annual facts over
            # quarters stitched together.
            annual_bonus = int(num.basis == "annual") + int(den.basis == "annual")
            key = (min(num.end, den.end), annual_bonus)
            if best_key is None or key > best_key:
                best, best_key = (num, den), key

    return best


# ---------------------------------------------------------------------------
# Concept tables
# ---------------------------------------------------------------------------
#
# Each debt candidate is one plausible reading of "total interest-bearing debt".
# Companies tag inconsistently, and some maintain a narrow LongTermDebt tag
# alongside the real total under a different name - Marriott reports $23M under
# LongTermDebt while carrying $16.5bn in DebtAndCapitalLeaseObligations. Rather
# than trusting a priority order, every candidate is computed and the largest is
# taken: understating debt produces a false PASS, whereas overstating it
# produces a FAIL that surfaces for manual review. All candidates are reported
# so the choice stays auditable.
#
# Operating lease liabilities are excluded, as they are generally not treated as
# riba-bearing. Capital/finance leases are included where a company tags them
# together with debt, on the basis that they are financing arrangements.

DEBT_CANDIDATES: list[tuple[str, list[str]]] = [
    ("combined", ["DebtLongtermAndShorttermCombinedAmount"]),
    ("long-term debt", ["LongTermDebt"]),
    ("noncurrent + current", ["LongTermDebtNoncurrent", "LongTermDebtCurrent"]),
    ("noncurrent + debt current", ["LongTermDebtNoncurrent", "DebtCurrent"]),
    ("noncurrent + short-term borrowings", ["LongTermDebtNoncurrent", "ShortTermBorrowings"]),
    ("noncurrent only", ["LongTermDebtNoncurrent"]),
    ("debt + capital leases", ["DebtAndCapitalLeaseObligations"]),
    (
        "long-term debt + capital leases",
        [
            "LongTermDebtAndCapitalLeaseObligations",
            "LongTermDebtAndCapitalLeaseObligationsCurrent",
        ],
    ),
    ("long-term debt + capital leases (noncurrent)", ["LongTermDebtAndCapitalLeaseObligations"]),
    # REITs and property companies rarely use the LongTermDebt family at all.
    ("notes payable + secured", ["NotesPayable", "SecuredDebt"]),
    ("notes payable", ["NotesPayable"]),
    ("secured + unsecured", ["SecuredDebt", "UnsecuredDebt"]),
    ("senior notes", ["SeniorNotes"]),
    ("debt current only", ["DebtCurrent"]),
    ("short-term borrowings only", ["ShortTermBorrowings"]),
]

CASH_CONCEPTS = [
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
]

SHORT_TERM_INVESTMENT_CONCEPTS = [
    "ShortTermInvestments",
    "MarketableSecuritiesCurrent",
    "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
    "OtherShortTermInvestments",
]

# AAOIFI's second ratio covers interest-bearing securities generally, not only
# the current portion. Apple holds most of its securities as non-current.
LONG_TERM_INVESTMENT_CONCEPTS = [
    "MarketableSecuritiesNoncurrent",
    "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
    "LongTermInvestments",
]

REVENUE_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenuesNetOfInterestExpense",
    # REITs report rental income rather than contract revenue and tag none of
    # the above - Equity Residential's whole $3.1bn top line sits here. Last in
    # priority so it only applies where nothing standard resolves.
    "OperatingLeaseLeaseIncome",
    "OperatingLeasesIncomeStatementLeaseRevenue",
]

# Interest income is inconsistently tagged, and absence does NOT mean zero -
# Apple and McDonald's stopped reporting it separately and now net it inside
# other income, where it cannot be separated from FX and one-off gains.
# Anything unresolved here returns REVIEW rather than a guess.
#
# InvestmentIncomeNet sits last because it is net of investment losses rather
# than a pure interest figure, but Microsoft and Mastercard tag nothing else,
# and an approximate ratio beats no ratio.
INTEREST_INCOME_CONCEPTS = [
    "InvestmentIncomeInterest",
    "InterestAndDividendIncomeOperating",
    "InvestmentIncomeInterestAndDividend",
    "InterestIncomeOther",
    "InterestAndDividendIncomeSecurities",
    "InvestmentIncomeNet",
]

TOTAL_ASSETS_CONCEPTS = ["Assets"]

# When interest income is not broken out, these aggregate lines contain it.
# They cannot give the ratio, but they bound it: if the whole non-operating
# income line is a fraction of a percent of revenue, the interest component
# inside it cannot be large. Ordered tightest first, so the most
# interest-specific concept a company tags is the one used.
#
# This is reported as evidence only - the verdict stays REVIEW. Whether an
# undisclosed figure may be treated as immaterial is a Shari'ah judgement, not
# an engineering one, so nothing is promoted to PASS on the strength of it.
INCOME_BOUND_CONCEPTS = [
    "InterestIncomeExpenseNonoperatingNet",
    "InterestIncomeExpenseNet",
    "InterestAndOtherIncome",
    "InvestmentIncomeNonoperating",
    "OtherNonoperatingIncomeExpense",
    "NonoperatingIncomeExpense",
]


# ---------------------------------------------------------------------------
# Balance-sheet resolution
# ---------------------------------------------------------------------------


@dataclass
class DebtEstimate:
    value: float | None
    as_of: date | None
    source: str
    candidates: dict[str, float] = field(default_factory=dict)


def resolve_debt(selector: FactSelector) -> DebtEstimate:
    """Total interest-bearing debt, taken as the largest defensible measure."""
    priced: list[tuple[str, float, date]] = []

    for label, concepts in DEBT_CANDIDATES:
        parts = [selector.instant(c) for c in concepts]
        if any(p is None for p in parts):
            continue
        ends = [p.end for p in parts]  # type: ignore[union-attr]
        if (max(ends) - min(ends)).days > BALANCE_SHEET_ALIGNMENT_DAYS:
            continue  # components come from different filings; not comparable
        priced.append((label, sum(p.value for p in parts), max(ends)))  # type: ignore[union-attr]

    if not priced:
        # No debt concept tagged at all is plausible for a debt-free company,
        # but is more often a tagging gap. Treat as unknown rather than zero.
        return DebtEstimate(None, None, "no debt concept found")

    # Only compare candidates drawn from roughly the same balance-sheet date.
    newest = max(p[2] for p in priced)
    current = [p for p in priced if (newest - p[2]).days <= 100]
    label, value, as_of = max(current, key=lambda p: p[1])

    return DebtEstimate(
        value=value,
        as_of=as_of,
        source=f"{label} @ {as_of}",
        candidates={p[0]: p[1] for p in current},
    )


def bound_noncompliant_income(
    selector: FactSelector, revenue_windows: list[Window]
) -> tuple[float, str] | None:
    """Upper bound on the non-compliant income ratio, as (ratio, description).

    Used only where the exact interest figure is missing. Takes the tightest
    aggregate line the company tags that contains interest income, and divides
    its absolute value by revenue over the same period. The absolute value
    matters because these lines are net of interest expense and are often
    negative; a bound of "this whole line is 0.4% of revenue either way" still
    tells you the interest component is small.

    Returns None when nothing can be aligned, rather than guessing.
    """
    for concept in INCOME_BOUND_CONCEPTS:
        candidates = selector.ttm_candidates(concept)
        if not candidates:
            continue
        aligned = align_windows(candidates, revenue_windows)
        if aligned is None:
            continue
        bound_window, revenue_window = aligned
        if not revenue_window.value:
            continue
        ratio = abs(bound_window.value) / abs(revenue_window.value)
        return ratio, f"{concept} {bound_window.start}..{bound_window.end}"
    return None


@dataclass
class LiquidEstimate:
    value: float | None
    as_of: date | None
    source: str


def resolve_liquid_assets(selector: FactSelector) -> LiquidEstimate:
    """Cash plus interest-bearing securities, current and non-current."""
    cash = selector.instant_first(CASH_CONCEPTS)
    short_term = selector.instant_max(SHORT_TERM_INVESTMENT_CONCEPTS)
    long_term = selector.instant_max(LONG_TERM_INVESTMENT_CONCEPTS)

    parts = [p for p in (cash, short_term, long_term) if p is not None]
    if not parts:
        return LiquidEstimate(None, None, "no cash or investment concept found")

    newest = max(p.end for p in parts)
    aligned = [p for p in parts if (newest - p.end).days <= BALANCE_SHEET_ALIGNMENT_DAYS]

    return LiquidEstimate(
        value=sum(p.value for p in aligned),
        as_of=newest,
        source=" + ".join(p.concept for p in aligned),
    )


# ---------------------------------------------------------------------------
# SEC client
# ---------------------------------------------------------------------------


class SECClient:
    """Thin, polite, caching client for SEC EDGAR public endpoints."""

    def __init__(self, user_agent: str = USER_AGENT, cache_dir: Path = CACHE_DIR):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._ticker_map: dict[str, int] | None = None

    def _get_json(self, url: str, cache_key: str | None = None) -> dict[str, Any] | None:
        if cache_key:
            cached = self.cache_dir / f"{cache_key}.json"
            if cached.exists():
                return json.loads(cached.read_text())

        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            response = self.session.get(url, timeout=30)
        except requests.RequestException as exc:
            print(f"  ! request failed for {url}: {exc}")
            return None

        if response.status_code != 200:
            print(f"  ! HTTP {response.status_code} for {url}")
            return None

        payload = response.json()
        if cache_key:
            (self.cache_dir / f"{cache_key}.json").write_text(json.dumps(payload))
        return payload

    def ticker_to_cik(self, ticker: str) -> int | None:
        if self._ticker_map is None:
            data = self._get_json(SEC_TICKERS_URL, cache_key="company_tickers")
            if data is None:
                return None
            self._ticker_map = {
                entry["ticker"].upper(): int(entry["cik_str"]) for entry in data.values()
            }
        return self._ticker_map.get(ticker.upper())

    def submissions(self, cik: int, cache: bool = True) -> dict[str, Any] | None:
        """Company metadata. Submissions payloads average 429 KB because they
        carry full filing history, so a sweep passes cache=False and keeps only
        the two fields it needs."""
        return self._get_json(
            SEC_SUBMISSIONS_URL.format(cik=cik), cache_key=f"sub_{cik}" if cache else None
        )

    def company_facts(self, cik: int) -> dict[str, Any] | None:
        return self._get_json(SEC_FACTS_URL.format(cik=cik), cache_key=f"facts_{cik}")


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

_YF_CACHE: dict[str, dict[str, Any]] = {}


def yf_info(ticker: str) -> dict[str, Any]:
    """Cached yfinance metadata lookup; empty dict on any failure."""
    if ticker in _YF_CACHE:
        return _YF_CACHE[ticker]

    info: dict[str, Any] = {}
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
    except ImportError:
        print("  ! yfinance not installed; run: pip install yfinance")
    except Exception as exc:  # yfinance surfaces a variety of errors
        print(f"  ! market data lookup failed for {ticker}: {exc}")

    _YF_CACHE[ticker] = info
    return info


def get_market_cap(ticker: str) -> float | None:
    """Current market capitalisation via yfinance."""
    cap = yf_info(ticker).get("marketCap")
    return float(cap) if cap else None


@dataclass
class MarketData:
    """Pre-fetched market figures for one ticker.

    A universe sweep resolves these in batch and passes them in, so screening
    hundreds of names does not mean hundreds of sequential yfinance calls.
    When absent, screen_ticker falls back to a per-ticker lookup.
    """

    market_cap: float | None = None
    dividend_rate: float | None = None


# ---------------------------------------------------------------------------
# Screening result
# ---------------------------------------------------------------------------


@dataclass
class ScreenResult:
    ticker: str
    company: str = ""
    cik: int | None = None
    sic: int | None = None

    status: Status = Status.INSUFFICIENT_DATA
    reasons: list[str] = field(default_factory=list)

    market_cap: float | None = None
    denominator: float | None = None
    denominator_basis: str = ""

    debt: float | None = None
    debt_ratio: float | None = None
    debt_source: str = ""
    debt_candidates: dict[str, float] = field(default_factory=dict)

    liquid_assets: float | None = None
    liquid_ratio: float | None = None
    liquid_source: str = ""

    balance_sheet_date: str = ""

    revenue: float | None = None
    interest_income: float | None = None
    income_ratio: float | None = None
    income_source: str = ""
    income_window: str = ""

    # Upper bound on non-compliant income where the exact figure is untagged.
    income_bound_ratio: float | None = None
    income_bound_source: str = ""

    purification_per_share: float | None = None
    cap_tier: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


def classify_cap_tier(market_cap: float | None) -> str:
    """Bucket by size so blue chips can be separated from speculative names."""
    if market_cap is None:
        return "unknown"
    if market_cap >= 200e9:
        return "mega"
    if market_cap >= 10e9:
        return "large"  # blue chip territory
    if market_cap >= 2e9:
        return "mid"
    if market_cap >= 300e6:
        return "small"
    return "micro"


# ---------------------------------------------------------------------------
# Main screening logic
# ---------------------------------------------------------------------------


def screen_ticker(
    ticker: str,
    client: SECClient,
    thresholds: Thresholds | None = None,
    standard: Standard = Standard.MARKET_CAP,
    reference_date: date | None = None,
    market_data: MarketData | None = None,
) -> ScreenResult:
    """Run the full AAOIFI screen against a single ticker.

    Passing market_data suppresses all yfinance lookups, which is what makes a
    several-hundred-name sweep practical.
    """
    thresholds = thresholds or Thresholds()
    result = ScreenResult(ticker=ticker.upper())

    cik = client.ticker_to_cik(ticker)
    if cik is None:
        result.status = Status.INSUFFICIENT_DATA
        result.reasons.append("Ticker not found in SEC registry")
        return result
    result.cik = cik

    submissions = client.submissions(cik)
    if submissions:
        result.company = submissions.get("name", "")
        sic_raw = submissions.get("sic")
        result.sic = int(sic_raw) if sic_raw and str(sic_raw).isdigit() else None

    # --- Stage 1: business activity ---
    sector_status, sector_reason = classify_sic(result.sic, cik)
    result.reasons.append(sector_reason)
    if sector_status is Status.FAIL:
        result.status = Status.FAIL
        return result  # no point computing ratios on an excluded sector

    # --- Stage 2: financial ratios ---
    facts = client.company_facts(cik)
    if not facts:
        result.status = Status.INSUFFICIENT_DATA
        result.reasons.append("No XBRL facts available (common for recent IPOs)")
        return result

    selector = FactSelector(facts, reference_date=reference_date)

    result.market_cap = (
        market_data.market_cap if market_data is not None else get_market_cap(ticker)
    )
    result.cap_tier = classify_cap_tier(result.market_cap)

    if standard is Standard.MARKET_CAP:
        result.denominator = result.market_cap
        result.denominator_basis = "market capitalisation"
    else:
        assets = selector.instant_first(TOTAL_ASSETS_CONCEPTS)
        result.denominator = assets.value if assets else None
        result.denominator_basis = "total assets"

    if not result.denominator:
        result.status = Status.INSUFFICIENT_DATA
        result.reasons.append(f"Could not determine {result.denominator_basis}")
        return result

    debt = resolve_debt(selector)
    result.debt = debt.value
    result.debt_source = debt.source
    result.debt_candidates = debt.candidates
    if debt.value is not None:
        result.debt_ratio = debt.value / result.denominator

    liquid = resolve_liquid_assets(selector)
    result.liquid_assets = liquid.value
    result.liquid_source = liquid.source
    if liquid.value is not None:
        result.liquid_ratio = liquid.value / result.denominator

    balance_dates = [d for d in (debt.as_of, liquid.as_of) if d is not None]
    if balance_dates:
        result.balance_sheet_date = str(max(balance_dates))

    # Revenue and interest income have to describe the same twelve months.
    revenue_windows = selector.ttm_candidates_first(REVENUE_CONCEPTS)
    interest_windows = selector.ttm_candidates_first(INTEREST_INCOME_CONCEPTS)

    if revenue_windows:
        newest_revenue = max(revenue_windows, key=lambda w: (w.end, w.basis == "annual"))
        result.revenue = newest_revenue.value
        result.income_window = newest_revenue.label()

    stale_income_window = False
    aligned = align_windows(interest_windows, revenue_windows)
    if aligned:
        interest_window, revenue_window = aligned
        result.revenue = revenue_window.value
        result.interest_income = interest_window.value
        result.income_source = interest_window.label()
        result.income_window = revenue_window.label()
        if revenue_window.value:
            result.income_ratio = interest_window.value / revenue_window.value
        age = (selector.reference_date - revenue_window.end).days
        stale_income_window = age > STALE_INCOME_WINDOW_DAYS

    # --- Stage 3: verdict ---
    failures: list[str] = []
    unknowns: list[str] = []

    if result.debt_ratio is None:
        unknowns.append("interest-bearing debt not tagged in XBRL")
    elif result.debt_ratio > thresholds.debt_ratio_max:
        failures.append(
            f"Debt ratio {result.debt_ratio:.1%} exceeds {thresholds.debt_ratio_max:.0%}"
        )

    if result.liquid_ratio is None:
        unknowns.append("cash and short-term investments not tagged")
    elif result.liquid_ratio > thresholds.interest_securities_ratio_max:
        failures.append(
            f"Interest-bearing securities ratio {result.liquid_ratio:.1%} exceeds "
            f"{thresholds.interest_securities_ratio_max:.0%}"
        )

    if result.income_ratio is None:
        if revenue_windows:
            bound = bound_noncompliant_income(selector, revenue_windows)
            if bound is not None:
                result.income_bound_ratio, result.income_bound_source = bound

        if not revenue_windows:
            unknowns.append("no usable revenue period found")
        else:
            detail = (
                "interest income not separately tagged"
                if not interest_windows
                else "revenue and interest income periods do not align"
            )
            if result.income_bound_ratio is not None:
                verdict = (
                    "comfortably inside the 5% limit"
                    if result.income_bound_ratio <= thresholds.noncompliant_income_ratio_max
                    else "ABOVE the 5% limit, so the ratio could breach it"
                )
                unknowns.append(
                    f"{detail}; bounded by {result.income_bound_source} at "
                    f"{result.income_bound_ratio:.2%} of revenue - {verdict}"
                )
            else:
                unknowns.append(f"{detail} - verify manually in the 10-K")
    else:
        if result.income_ratio > thresholds.noncompliant_income_ratio_max:
            failures.append(
                f"Non-compliant income {result.income_ratio:.1%} exceeds "
                f"{thresholds.noncompliant_income_ratio_max:.0%}"
            )
        # Noted whether the ratio passed or failed: a verdict resting on a
        # year-old window should say so either way.
        if stale_income_window:
            unknowns.append(
                f"income ratio computed from an old period ({result.income_window}) - "
                "no more recent filing tags interest income"
            )

    result.reasons.extend(failures)
    result.reasons.extend(unknowns)

    if failures:
        result.status = Status.FAIL
    elif unknowns or sector_status is Status.REVIEW:
        result.status = Status.REVIEW
    else:
        result.status = Status.PASS

    # Purification: the share of dividend income attributable to prohibited
    # sources, which should be given away. Only meaningful if the name passes.
    if result.status in (Status.PASS, Status.REVIEW) and result.income_ratio:
        dividend_rate = (
            market_data.dividend_rate
            if market_data is not None
            else yf_info(ticker).get("dividendRate")
        )
        if dividend_rate:
            result.purification_per_share = float(dividend_rate) * result.income_ratio

    return result


def screen_universe(
    tickers: list[str],
    standard: Standard = Standard.MARKET_CAP,
    thresholds: Thresholds | None = None,
    reference_date: date | None = None,
) -> list[ScreenResult]:
    """Screen a list of tickers, printing progress as it goes."""
    client = SECClient()
    results: list[ScreenResult] = []

    for index, ticker in enumerate(tickers, start=1):
        print(f"[{index}/{len(tickers)}] {ticker}")
        result = screen_ticker(ticker, client, thresholds, standard, reference_date)
        print(f"  -> {result.status.value}")
        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Certified halal ETFs
# ---------------------------------------------------------------------------
#
# ETFs cannot be screened with AAOIFI ratios directly - you either look through
# to holdings or rely on the fund's own Shari'ah certification. This is a
# starter list of US-listed funds marketed as Shari'ah compliant. Verify the
# current certification and the certifying board before relying on it.

CERTIFIED_ETFS: dict[str, dict[str, str]] = {
    "SPUS": {
        "name": "SP Funds S&P 500 Sharia Industry Exclusions ETF",
        "exposure": "US large cap",
        "certifier": "SP Funds Shariah board",
    },
    "SPTE": {
        "name": "SP Funds S&P Global Technology ETF",
        "exposure": "Global technology",
        "certifier": "SP Funds Shariah board",
    },
    "SPRE": {
        "name": "SP Funds S&P Global REIT Sharia ETF",
        "exposure": "Global REITs",
        "certifier": "SP Funds Shariah board",
    },
    "SPSK": {
        "name": "SP Funds Dow Jones Global Sukuk ETF",
        "exposure": "Sukuk (fixed income alternative)",
        "certifier": "SP Funds Shariah board",
    },
    "SPWO": {
        "name": "SP Funds S&P World ex-US ETF",
        "exposure": "Developed markets ex-US",
        "certifier": "SP Funds Shariah board",
    },
    "HLAL": {
        "name": "Wahed FTSE USA Shariah ETF",
        "exposure": "US large and mid cap",
        "certifier": "Wahed Shariah board",
    },
    "UMMA": {
        "name": "Wahed Dow Jones Islamic World ETF",
        "exposure": "Global ex-US",
        "certifier": "Wahed Shariah board",
    },
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse
    import csv

    parser = argparse.ArgumentParser(
        description="AAOIFI Shari'ah compliance screener for US equities"
    )
    parser.add_argument("tickers", nargs="*", help="Ticker symbols to screen")
    parser.add_argument("--file", help="Path to a newline-delimited file of tickers")
    parser.add_argument(
        "--basis",
        choices=[s.value for s in Standard],
        default=Standard.MARKET_CAP.value,
        help="Ratio denominator basis (default: market_cap)",
    )
    parser.add_argument(
        "--as-of",
        help="Reference date for fact staleness, YYYY-MM-DD (default: today)",
    )
    parser.add_argument("--out", default="screen_results.csv", help="Output CSV path")
    parser.add_argument("--json", action="store_true", help="Also write results as JSON")
    args = parser.parse_args()

    tickers = list(args.tickers)
    if args.file:
        tickers += [
            line.strip().upper()
            for line in Path(args.file).read_text().splitlines()
            if line.strip()
        ]

    if not tickers:
        parser.error("Provide tickers as arguments or via --file")

    reference_date = _parse_date(args.as_of) if args.as_of else None
    results = screen_universe(
        tickers, standard=Standard(args.basis), reference_date=reference_date
    )

    rows = [r.to_dict() for r in results]
    for row in rows:
        row["reasons"] = " | ".join(row["reasons"])
        row["debt_candidates"] = " | ".join(
            f"{k}={v:.0f}" for k, v in sorted(row["debt_candidates"].items())
        )

    with open(args.out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    if args.json:
        Path(args.out).with_suffix(".json").write_text(
            json.dumps([r.to_dict() for r in results], indent=2, default=str)
        )

    summary: dict[str, int] = {}
    for result in results:
        summary[result.status.value] = summary.get(result.status.value, 0) + 1

    print("\n--- Summary ---")
    for status, count in sorted(summary.items()):
        print(f"{status:20s} {count}")
    print(f"\nWritten to {args.out}")


if __name__ == "__main__":
    main()
