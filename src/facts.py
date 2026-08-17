"""Point-in-time XBRL fact resolution.

This is the foundation the whole system rests on. If it leaks future
information, every backtest number the project ever produces is worthless —
so the point-in-time gate is enforced here, once, rather than trusted to
callers.

Two problems are solved together:

**Point-in-time correctness.** SEC XBRL returns every observation a company has
ever tagged, including restatements filed years later. Asking "what did Micron's
balance sheet look like in 2022?" today returns figures nobody had in 2022. Every
fact therefore carries its EDGAR filing date, and a `FactSet` built with
``as_of=some_date`` can only see facts filed on or before that date. Restatements
collapse to the newest version *that existed at as_of*, which is exactly what an
investor standing on that date would have read.

**Fact selection.** Inherited from the prior codebase and hard-won against live
filings — companies abandon tags without deleting history, income-statement facts
need trailing-twelve-month chaining, and the two sides of a ratio must cover the
same period. Each rule below exists because omitting it produced a specific wrong
number against real SEC data; see `legacy/HALAL_SCREENER_README.md`.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable

from src import config

# ---------------------------------------------------------------------------
# Tunables, resolved once at import from config/rules.yaml
# ---------------------------------------------------------------------------

ENFORCE_FILING_DATE: bool = config.get("rules.point_in_time.enforce_filing_date", True)
SETTLE_DAYS: int = config.get("rules.point_in_time.settle_days", 2)
FALLBACK_LAG_QUARTERLY: int = config.get("rules.point_in_time.fallback_lag_days.quarterly", 90)
FALLBACK_LAG_ANNUAL: int = config.get("rules.point_in_time.fallback_lag_days.annual", 105)
REJECT_FILED_BEFORE_END: bool = config.get(
    "rules.point_in_time.reject_filed_before_period_end", True
)

MAX_INSTANT_AGE_DAYS: int = config.get("rules.facts.max_instant_age_days", 550)
MAX_DURATION_AGE_DAYS: int = config.get("rules.facts.max_duration_age_days", 900)
ANNUAL_DAYS: tuple[int, int] = tuple(config.get("rules.facts.annual_days", [330, 400]))
QUARTER_DAYS: tuple[int, int] = tuple(config.get("rules.facts.quarter_days", [80, 100]))
PERIOD_ALIGNMENT_TOLERANCE_DAYS: int = config.get(
    "rules.facts.period_alignment_tolerance_days", 45
)


# ISO 4217 codes are three uppercase letters. Everything else in an XBRL
# `units` map is a count, a rate, or a per-share measure.
_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")


def parse_date(value: Any) -> date | None:
    """Lenient ISO date parse. Returns None rather than raising on junk."""
    if isinstance(value, date):
        return value
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Fact
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fact:
    """One usable XBRL observation, with the date it became public."""

    concept: str
    value: float
    end: date
    start: date | None
    form: str
    filed: date | None
    unit: str = "USD"
    accession: str | None = None

    @property
    def days(self) -> int | None:
        """Length of the reporting window; None for balance-sheet facts."""
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

    def known_by(self, as_of: date) -> bool:
        """Whether this observation was public knowledge on `as_of`.

        The primary gate is the real EDGAR filing date plus a short settle
        margin covering propagation into the companyfacts API. That is exact,
        so it is preferred over any period-plus-lag approximation.

        Where an observation carries no usable filing date, the fallback is
        the conservative statutory-deadline buffer. It is never faster than
        the SEC deadline, so it cannot manufacture early knowledge.
        """
        if self.filed is not None and ENFORCE_FILING_DATE:
            # A filing date preceding the period it reports on is malformed and
            # would leak the future. Fall through to the conservative rule.
            if not (REJECT_FILED_BEFORE_END and self.filed < self.end):
                return self.filed + timedelta(days=SETTLE_DAYS) <= as_of

        lag = FALLBACK_LAG_ANNUAL if self.is_annual or self.is_instant else FALLBACK_LAG_QUARTERLY
        return self.end + timedelta(days=lag) <= as_of

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
    filed: date | None = None

    def label(self) -> str:
        return f"{self.concept} {self.start}..{self.end} ({self.basis})"


# ---------------------------------------------------------------------------
# FactSet
# ---------------------------------------------------------------------------


class FactSet:
    """Point-in-time, period-aware access to one company's XBRL facts.

    Construct with ``as_of`` to see the company as it appeared on that date::

        today = FactSet(payload)                        # current view
        then  = FactSet(payload, as_of=date(2022, 6, 30))  # what was knowable then

    The same code path serves live screening and backtesting, which is the point:
    a backtest that runs through a different code path than production is testing
    something other than production.
    """

    def __init__(
        self,
        facts: dict[str, Any] | None,
        as_of: date | None = None,
        *,
        max_instant_age_days: int = MAX_INSTANT_AGE_DAYS,
        max_duration_age_days: int = MAX_DURATION_AGE_DAYS,
        taxonomies: Iterable[str] = ("us-gaap", "ifrs-full"),
        currency: str | None = None,
    ):
        payload = (facts or {}).get("facts", {})
        self._payload = payload
        self._taxonomies = [payload.get(t, {}) for t in taxonomies if t in payload]
        self.as_of = as_of or date.today()
        self.max_instant_age_days = max_instant_age_days
        self.max_duration_age_days = max_duration_age_days
        # Foreign private issuers report in their functional currency: TSMC files
        # IFRS in TWD, ASML files US-GAAP in EUR. Hardcoding USD returned an
        # empty fact set for both — data that was present read as absent, which
        # is the one failure mode this project must never have.
        self.reporting_currency = currency or self._detect_currency()
        self._cache: dict[str, list[Fact]] = {}
        # Populated as facts are resolved, so callers can report what the view
        # excluded rather than silently presenting a thinner picture.
        self.excluded_future_facts: int = 0

    def _detect_currency(self) -> str:
        """Infer the filer's reporting currency from its own unit usage."""
        counts: Counter[str] = Counter()
        for taxonomy in self._taxonomies:
            for concept in taxonomy.values():
                for unit in concept.get("units", {}):
                    if _CURRENCY_PATTERN.match(unit):
                        counts[unit] += 1
        if not counts:
            return "USD"
        return counts.most_common(1)[0][0]

    @property
    def is_usd(self) -> bool:
        """Whether financials are directly comparable to USD market data.

        Ratios are currency-neutral, so quality and strength scoring works in
        any reporting currency. Anything mixing filings with market prices —
        P/E, EV/EBITDA, FCF yield, market cap — needs FX conversion first.
        """
        return self.reporting_currency == "USD"

    # -- normalisation ------------------------------------------------------

    def facts_for(self, concept: str) -> list[Fact]:
        """Fresh, de-duplicated, point-in-time-safe observations, oldest first."""
        if concept in self._cache:
            return self._cache[concept]

        entries: list[dict[str, Any]] = []
        for taxonomy in self._taxonomies:
            units = taxonomy.get(concept, {}).get("units", {})
            entries.extend(units.get(self.reporting_currency, []))

        instant_cutoff = self.as_of - timedelta(days=self.max_instant_age_days)
        duration_cutoff = self.as_of - timedelta(days=self.max_duration_age_days)

        # Collapse restatements: for a given period, the newest filing that
        # existed at as_of wins. Anything filed later is invisible, which is the
        # whole point — it was invisible to an investor standing on that date too.
        best: dict[tuple[date | None, date], Fact] = {}
        for entry in entries:
            end = parse_date(entry.get("end"))
            value = entry.get("val")
            if end is None or value is None:
                continue

            start = parse_date(entry.get("start"))
            if end < (duration_cutoff if start else instant_cutoff):
                continue

            fact = Fact(
                concept=concept,
                value=float(value),
                end=end,
                start=start,
                form=entry.get("form") or "",
                filed=parse_date(entry.get("filed")),
                unit=self.reporting_currency,
                accession=entry.get("accn"),
            )

            if not fact.known_by(self.as_of):
                self.excluded_future_facts += 1
                continue

            key = (start, end)
            incumbent = best.get(key)
            if incumbent is None or _filed_sort_key(fact) >= _filed_sort_key(incumbent):
                best[key] = fact

        result = sorted(best.values(), key=lambda f: f.end)
        self._cache[concept] = result
        return result

    # -- balance sheet ------------------------------------------------------

    def instant(self, concept: str) -> Fact | None:
        """Most recent balance-sheet value known at as_of."""
        candidates = [f for f in self.facts_for(concept) if f.is_instant]
        return candidates[-1] if candidates else None

    def instant_max(self, concepts: list[str]) -> Fact | None:
        """Largest current value across overlapping concepts.

        Used where several tags describe the same pool — ShortTermInvestments,
        MarketableSecuritiesCurrent and AvailableForSaleSecuritiesDebtSecurities
        often cover the same securities, so summing them would double-count.
        """
        live = [h for h in (self.instant(c) for c in concepts) if h is not None]
        return max(live, key=lambda f: f.value) if live else None

    def instant_first(self, concepts: list[str]) -> Fact | None:
        """Best available value across alternative concepts for one quantity.

        Freshest data wins, with the caller's priority order as the tiebreak.
        Priority alone is not enough: companies abandon tags without deleting
        history, so a more-specific concept last tagged years ago must not beat
        a general one the company still maintains.
        """
        best: tuple[date, int, Fact] | None = None
        for rank, concept in enumerate(concepts):
            hit = self.instant(concept)
            if hit is None:
                continue
            # Later end date wins; earlier position in the list breaks ties.
            key = (hit.end, -rank)
            if best is None or key > (best[0], best[1]):
                best = (hit.end, -rank, hit)
        return best[2] if best else None

    # -- income statement ---------------------------------------------------

    def ttm_candidates(self, concept: str) -> list[Window]:
        """Every defensible trailing-twelve-month figure for a concept.

        Annual facts are used directly. Where only quarterly facts exist, four
        contiguous quarters are chained. Returning all candidates rather than
        only the newest lets `align_windows` pick a pair covering the same
        period, which is what keeps ratios honest.
        """
        facts = self.facts_for(concept)
        windows = [
            Window(f.value, f.start, f.end, concept, "annual", f.filed)
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
                filed_dates = [c.filed for c in chain if c.filed is not None]
                windows.append(
                    Window(
                        value=sum(c.value for c in chain),
                        start=chain[-1].start,
                        end=chain[0].end,
                        concept=concept,
                        basis="4x quarterly",
                        # The chain is only knowable once its last link is filed.
                        filed=max(filed_dates) if filed_dates else None,
                    )
                )

        return windows

    def ttm_candidates_best(self, concepts: list[str]) -> list[Window]:
        """Candidates from whichever alternative concept is best maintained.

        Deliberately does not merge across concepts: Revenues and
        RevenueFromContractWithCustomer overlap, and mixing them double-counts.
        One concept is chosen outright — the one with the most recent data,
        with the caller's priority order as the tiebreak.

        Choosing purely by priority order was wrong against real filings.
        NVIDIA abandoned ``Revenues`` in 2018 for
        ``RevenueFromContractWithCustomerExcludingAssessedTax``, but the stale
        tag still carries windows inside the 900-day duration horizon, so a
        2020 view reported FY2019 revenue of $12.4bn instead of FY2020's
        $10.9bn — a stale figure that looked entirely plausible.
        """
        best_windows: list[Window] = []
        best_key: tuple[date, int] | None = None
        for rank, concept in enumerate(concepts):
            windows = self.ttm_candidates(concept)
            if not windows:
                continue
            key = (max(w.end for w in windows), -rank)
            if best_key is None or key > best_key:
                best_windows, best_key = windows, key
        return best_windows

    def ttm(self, concepts: list[str] | str) -> Window | None:
        """Single most recent TTM figure, preferring genuine annual facts."""
        names = [concepts] if isinstance(concepts, str) else concepts
        candidates = self.ttm_candidates_best(names)
        if not candidates:
            return None
        return max(candidates, key=lambda w: (w.end, w.basis == "annual"))

    # -- introspection ------------------------------------------------------

    def has(self, concept: str) -> bool:
        return bool(self.facts_for(concept))

    def coverage(self, concepts: Iterable[str]) -> dict[str, bool]:
        """Which of these concepts this company actually tags.

        Coverage is reported, never filled in. A missing figure must read as
        missing, not as zero.
        """
        return {c: self.has(c) for c in concepts}

    # -- share counts -------------------------------------------------------

    # Cover-page share count first: it is the most recent figure a filer
    # publishes and is stated as of the filing date rather than a period end.
    SHARE_CONCEPTS: tuple[tuple[str, str], ...] = (
        ("dei", "EntityCommonStockSharesOutstanding"),
        ("us-gaap", "CommonStockSharesOutstanding"),
        ("us-gaap", "CommonStockSharesIssued"),
        ("us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding"),
    )

    def shares_outstanding(self) -> Fact | None:
        """Point-in-time share count, in shares rather than currency.

        Returns None rather than a guess for multi-class filers. SEC's
        `companyfacts` strips XBRL dimensions, so Alphabet and Meta report a
        per-class breakdown that arrives without the class labels — the parts
        are visible but which is which is not. A missing share count produces a
        missing market cap, which is recoverable; a wrong one silently corrupts
        every valuation multiple built on it.
        """
        for taxonomy, concept in self.SHARE_CONCEPTS:
            facts = self._facts_in_unit(taxonomy, concept, "shares")
            if not facts:
                continue
            latest_end = max(f.end for f in facts)
            current = [f for f in facts if f.end == latest_end]
            # Several same-period values with no dimension labels means multiple
            # share classes. Summing them would be a guess.
            if len({round(f.value) for f in current}) > 1:
                continue
            return max(current, key=lambda f: (f.filed or date.min))
        return None

    def _facts_in_unit(self, taxonomy: str, concept: str, unit: str) -> list[Fact]:
        """Point-in-time facts for a concept measured in a non-currency unit."""
        entries = (
            self._payload.get(taxonomy, {}).get(concept, {}).get("units", {}).get(unit, [])
        )
        cutoff = self.as_of - timedelta(days=self.max_instant_age_days)
        out: list[Fact] = []
        for entry in entries:
            end = parse_date(entry.get("end"))
            value = entry.get("val")
            if end is None or value is None or end < cutoff:
                continue
            fact = Fact(
                concept=concept,
                value=float(value),
                end=end,
                start=parse_date(entry.get("start")),
                form=entry.get("form") or "",
                filed=parse_date(entry.get("filed")),
                unit=unit,
                accession=entry.get("accn"),
            )
            if fact.known_by(self.as_of):
                out.append(fact)
        return out

    def latest_filing_date(self) -> date | None:
        """Filing date of the most recent fact visible at as_of.

        Scans the raw payload rather than the resolution cache, so it reports
        what the filer has actually published even when every individual
        concept has aged out.
        """
        newest: date | None = None
        for taxonomy in self._taxonomies:
            for concept in taxonomy.values():
                for entries in concept.get("units", {}).values():
                    for entry in entries:
                        filed = parse_date(entry.get("filed"))
                        if filed is None or filed > self.as_of:
                            continue
                        if newest is None or filed > newest:
                            newest = filed
        return newest

    def data_quality(self, required: Iterable[str] = ()) -> dict[str, Any]:
        """Whether this company can be analysed at all, and what is missing.

        Exists because silence is the dangerous failure. TSMC files a 20-F whose
        IFRS financial statements never reach SEC's XBRL API — only a cover-page
        share count does — so a naive reader sees an empty fact set and could
        treat it as a company with no debt and no revenue. A name that cannot be
        analysed must say so and be held through the sector ETF instead.
        """
        required = list(required)
        missing = [c for c in required if not self.has(c)]
        latest = self.latest_filing_date()
        staleness = (self.as_of - latest).days if latest else None
        return {
            "reporting_currency": self.reporting_currency,
            "usd_comparable": self.is_usd,
            "latest_filing": latest,
            "staleness_days": staleness,
            "required_present": len(required) - len(missing),
            "required_total": len(required),
            "missing_concepts": missing,
            "hidden_future_facts": self.excluded_future_facts,
            "analysable": not missing and latest is not None,
        }


def _filed_sort_key(fact: Fact) -> tuple[date, str]:
    """Order restatements by filing date, then accession as a stable tiebreak."""
    return (fact.filed or date.min, fact.accession or "")


# ---------------------------------------------------------------------------
# Period alignment
# ---------------------------------------------------------------------------


def align_windows(
    numerator: list[Window],
    denominator: list[Window],
    tolerance_days: int = PERIOD_ALIGNMENT_TOLERANCE_DAYS,
) -> tuple[Window, Window] | None:
    """Pick the most recent pair of windows covering the same period.

    Without this, an annual figure gets divided into a single quarter of revenue
    and the ratio comes out roughly four times too high — a real bug this guards
    against, not a hypothetical one.
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


def ratio(
    numerator: list[Window],
    denominator: list[Window],
    tolerance_days: int = PERIOD_ALIGNMENT_TOLERANCE_DAYS,
) -> tuple[float, Window, Window] | None:
    """Period-aligned ratio of two TTM figures, or None if they cannot be paired."""
    pair = align_windows(numerator, denominator, tolerance_days)
    if pair is None:
        return None
    num, den = pair
    if den.value == 0:
        return None
    return num.value / den.value, num, den
