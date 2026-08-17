"""Institutional positioning from SEC Form 13F — corroboration only.

Managers with $100M+ in qualifying US securities must file quarterly, within 45
days of quarter end. That is the only auditable record of what large investors
hold, and it is free directly from SEC EDGAR.

**This layer can never create a thesis, only support one.** The reasons are
structural, not fixable:

- **No short positions.** A manager can be net short through swaps while their
  13F shows a long position, or nothing at all.
- **Derivatives are opaque.** Puts and calls appear without strike or expiry, so a
  large put line is indistinguishable from a hedge on an unreported long book.
  Michael Burry's final filing disclosed NVDA/PLTR puts at large notional values;
  notional is not cost basis, and the position could not be read without his own
  commentary.
- **45-day lag on a quarterly snapshot.** Intra-quarter trading is invisible, so a
  "new position" may already have been sold before it becomes readable.
- **No foreign-listed holdings**, and filers can simply stop filing — Scion
  deregistered in November 2025.

So the output here adjusts confidence in a conclusion the fundamentals already
reached. A cluster of independent managers adding the same name in one quarter is
meaningful evidence that sophisticated investors reached a similar view; one
manager adding is noise. Nothing in this module can move a name across the
quality gate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

from src import config
from src.sec_client import SECClient

HOLDINGS_DIR = config.DATA_DIR / "sec" / "13f"

# Filers tracked, chosen for genuine, media-covered AI/semiconductor positioning
# rather than fame alone. CIKs are resolved at runtime from the filer name so a
# wrong hardcoded number cannot silently attribute holdings to the wrong manager.
TRACKED_FILERS: tuple[dict[str, str], ...] = (
    {"name": "Coatue Management", "note": "explicit AI picks-and-shovels thesis"},
    {"name": "Tiger Global Management", "note": "large NVDA/TSM/AMZN positions"},
    {"name": "Altimeter Capital Management", "note": "vocal AI-infrastructure bull"},
    {"name": "Appaloosa", "note": "increased MU, TSM, VST"},
    {"name": "Duquesne Family Office", "note": "tech exposure roughly doubled in Q1 2026"},
    {"name": "ARK Investment Management", "note": "AI/robotics thesis; also publishes daily"},
    {"name": "Citadel Advisors", "note": "frequently covered for chip-stock moves"},
    {"name": "Berkshire Hathaway", "note": "bellwether; little direct semis exposure"},
    {"name": "Pershing Square Capital Management", "note": "broadly covered"},
)

# A cluster is the pattern with real support. One manager adding a name is noise;
# several independently reaching the same conclusion in one quarter is evidence.
CLUSTER_MIN_FILERS = 3

# A buyer only counts toward a cluster if the position is a meaningful share of
# their own book. Measured against live filings, Citadel appears in almost every
# name in the universe: it is a multi-strategy firm running thousands of
# positions, so its presence carries little information and it inflated every
# cluster count. Requiring conviction turns "who holds this" into "who has
# committed to this".
MIN_CONVICTION_WEIGHT = 0.005  # 0.5% of the filer's reported equity book


@dataclass(frozen=True)
class Holding:
    """One reported position in one filing."""

    filer: str
    cik: int
    quarter_end: date
    filed: date | None
    issuer: str
    cusip: str
    value_usd: float
    shares: float
    put_call: str | None = None

    @property
    def is_derivative(self) -> bool:
        """Options carry no strike or expiry here, so they cannot be interpreted."""
        return bool(self.put_call)


@dataclass
class FilerQuarter:
    """One manager's complete holdings for one quarter."""

    filer: str
    cik: int
    quarter_end: date
    filed: date | None
    holdings: list[Holding] = field(default_factory=list)

    def equity_only(self) -> list[Holding]:
        return [h for h in self.holdings if not h.is_derivative]

    def by_cusip(self) -> dict[str, Holding]:
        """One aggregated position per security.

        A filer can report the same security on several lines — different
        investment-discretion categories, or separate managers within the firm.
        Coatue reports Taiwan Semiconductor twice. Keying a dict on CUSIP without
        summing silently discards all but one line, understating the position and
        turning a held stake into a phantom "trimmed" the following quarter.
        """
        merged: dict[str, Holding] = {}
        for holding in self.equity_only():
            existing = merged.get(holding.cusip)
            if existing is None:
                merged[holding.cusip] = holding
                continue
            merged[holding.cusip] = Holding(
                filer=existing.filer,
                cik=existing.cik,
                quarter_end=existing.quarter_end,
                filed=existing.filed,
                issuer=existing.issuer,
                cusip=existing.cusip,
                value_usd=existing.value_usd + holding.value_usd,
                shares=existing.shares + holding.shares,
                put_call=None,
            )
        return merged

    @property
    def total_value(self) -> float:
        return sum(h.value_usd for h in self.equity_only())


class Action:
    NEW = "NEW"
    ADDED = "ADDED"
    TRIMMED = "TRIMMED"
    EXITED = "EXITED"
    HELD = "HELD"


@dataclass
class PositionChange:
    """What one manager did with one name between consecutive quarters."""

    filer: str
    cusip: str
    issuer: str
    action: str
    shares_before: float
    shares_after: float
    value_after: float
    weight_after: float | None = None

    @property
    def share_change_pct(self) -> float | None:
        if self.shares_before == 0:
            return None
        return (self.shares_after - self.shares_before) / self.shares_before


@dataclass
class Corroboration:
    """Institutional positioning in one name, as evidence rather than a signal."""

    ticker: str
    cusip: str | None
    quarter_end: date | None
    holders: list[str] = field(default_factory=list)
    new_positions: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    trimmed: list[str] = field(default_factory=list)
    exited: list[str] = field(default_factory=list)
    total_value_usd: float = 0.0
    # Position weight in each holder's own book, so conviction can be separated
    # from incidental exposure.
    weights: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def buyers(self) -> list[str]:
        return self.new_positions + self.added

    @property
    def conviction_buyers(self) -> list[str]:
        """Buyers for whom this is a meaningful position, not a rounding error."""
        return [
            filer
            for filer in self.buyers
            if (self.weights.get(filer) or 0.0) >= MIN_CONVICTION_WEIGHT
        ]

    @property
    def is_cluster(self) -> bool:
        """Whether enough managers independently committed in the same quarter."""
        return len(self.conviction_buyers) >= CLUSTER_MIN_FILERS

    @property
    def is_consensus_exit(self) -> bool:
        return len(self.exited + self.trimmed) >= CLUSTER_MIN_FILERS

    @property
    def largest_weight(self) -> float | None:
        return max(self.weights.values()) if self.weights else None

    @property
    def confidence_adjustment(self) -> str:
        """The only thing this layer is permitted to influence.

        Deliberately expressed in words rather than as a score multiplier, so it
        cannot be silently multiplied into a composite and mistaken for a
        fundamental measurement.
        """
        if self.is_cluster:
            return "supportive"
        if self.is_consensus_exit:
            return "review - several tracked managers reduced or exited"
        if self.conviction_buyers:
            return "weak support - fewer managers than a cluster requires"
        if self.buyers:
            return "none - bought only as an immaterial position"
        if not self.holders:
            return "none - no tracked manager holds this"
        return "neutral - held but not materially changed"

    def label(self) -> str:
        if self.quarter_end is None:
            return f"{self.ticker}: no 13F data"
        stale = (date.today() - self.quarter_end).days
        return (
            f"{self.ticker}: {len(self.holders)} tracked holder(s) as of "
            f"{self.quarter_end} ({stale}d old) - {self.confidence_adjustment}"
        )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _strip_namespace(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _text(node: Any, *names: str) -> str | None:
    for child in node.iter():
        if _strip_namespace(child.tag) in names and child.text:
            return child.text.strip()
    return None


def parse_information_table(
    xml: str,
    filer: str,
    cik: int,
    quarter_end: date,
    filed: date | None = None,
) -> list[Holding]:
    """Parse a 13F information table into holdings.

    Tolerates namespace variation and missing fields: EDGAR filings are prepared
    by hundreds of different agents and are inconsistent in both.
    """
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []

    holdings: list[Holding] = []
    for node in root.iter():
        if _strip_namespace(node.tag) != "infoTable":
            continue
        cusip = _text(node, "cusip")
        issuer = _text(node, "nameOfIssuer") or ""
        raw_value = _text(node, "value")
        raw_shares = _text(node, "sshPrnamt")
        put_call = _text(node, "putCall")
        if not cusip or raw_value is None:
            continue
        try:
            value = float(raw_value.replace(",", ""))
            shares = float((raw_shares or "0").replace(",", ""))
        except ValueError:
            continue
        holdings.append(
            Holding(
                filer=filer,
                cik=cik,
                quarter_end=quarter_end,
                filed=filed,
                issuer=issuer,
                cusip=cusip.strip().upper(),
                # Values were reported in thousands before 2023 and in whole
                # dollars after. Scaling by magnitude is a heuristic, so the
                # threshold is deliberately far from any plausible real holding.
                value_usd=value * 1000 if value < 1e6 else value,
                shares=shares,
                put_call=put_call.strip().upper() if put_call else None,
            )
        )
    return holdings


def quarter_end_for(as_of: date) -> date:
    """The most recent quarter whose 13F deadline has passed.

    Filings are due 45 days after quarter end, so a view on 1 May cannot yet see
    the March quarter. Applying this keeps the layer point-in-time correct.
    """
    lag_days = 45
    candidates = []
    for year in (as_of.year, as_of.year - 1):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            end = date(year, month, day)
            if end + timedelta(days=lag_days) <= as_of:
                candidates.append(end)
    return max(candidates) if candidates else date(as_of.year - 1, 12, 31)


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------


def diff_quarters(
    current: FilerQuarter,
    previous: FilerQuarter | None,
    material_change: float = 0.10,
) -> list[PositionChange]:
    """What a manager did between two quarters.

    Small share drifts are ignored: a 2% move is usually flow or rounding, not a
    decision, and treating it as one would turn every filing into a wall of noise.
    """
    now = current.by_cusip()
    before = previous.by_cusip() if previous else {}
    total = current.total_value or 1.0
    changes: list[PositionChange] = []

    for cusip, holding in now.items():
        prior = before.get(cusip)
        prior_shares = prior.shares if prior else 0.0
        if prior is None:
            action = Action.NEW
        elif prior_shares == 0:
            action = Action.NEW
        else:
            delta = (holding.shares - prior_shares) / prior_shares
            if delta > material_change:
                action = Action.ADDED
            elif delta < -material_change:
                action = Action.TRIMMED
            else:
                action = Action.HELD
        changes.append(
            PositionChange(
                filer=current.filer,
                cusip=cusip,
                issuer=holding.issuer,
                action=action,
                shares_before=prior_shares,
                shares_after=holding.shares,
                value_after=holding.value_usd,
                weight_after=holding.value_usd / total,
            )
        )

    for cusip, prior in before.items():
        if cusip not in now:
            changes.append(
                PositionChange(
                    filer=current.filer,
                    cusip=cusip,
                    issuer=prior.issuer,
                    action=Action.EXITED,
                    shares_before=prior.shares,
                    shares_after=0.0,
                    value_after=0.0,
                )
            )
    return changes


def corroborate(
    ticker: str,
    cusip: str | None,
    changes_by_filer: dict[str, list[PositionChange]],
    quarter_end: date | None,
) -> Corroboration:
    """Aggregate what tracked managers did with one name."""
    result = Corroboration(ticker=ticker.upper(), cusip=cusip, quarter_end=quarter_end)
    if cusip is None:
        result.notes.append("no CUSIP mapping; 13F positions cannot be matched")
        return result

    for filer, changes in changes_by_filer.items():
        for change in changes:
            if change.cusip != cusip:
                continue
            if change.action != Action.EXITED:
                result.holders.append(filer)
                result.total_value_usd += change.value_after
                if change.weight_after is not None:
                    result.weights[filer] = change.weight_after
            if change.action == Action.NEW:
                result.new_positions.append(filer)
            elif change.action == Action.ADDED:
                result.added.append(filer)
            elif change.action == Action.TRIMMED:
                result.trimmed.append(filer)
            elif change.action == Action.EXITED:
                result.exited.append(filer)

    if quarter_end is not None:
        stale = (date.today() - quarter_end).days
        result.notes.append(
            f"snapshot is {stale} days old and long-only; shorts and hedges are invisible"
        )
    return result


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


class HoldingsClient:
    """Fetches and caches 13F filings for the tracked managers."""

    def __init__(self, client: SECClient | None = None, cache_dir: Path = HOLDINGS_DIR):
        self.client = client or SECClient()
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def resolve_cik(self, filer_name: str) -> int | None:
        """Look up a manager's CIK by name, via EDGAR full-text search.

        Resolved at runtime rather than hardcoded: a wrong constant would silently
        attribute one manager's holdings to another, and nothing downstream could
        detect it. The entity that filed the most 13F-HRs under a name match wins,
        which reliably picks the manager over incidental mentions in other filers'
        documents.
        """
        registry = self._filer_registry()
        if filer_name in registry:
            return registry[filer_name]

        quoted = filer_name.replace(" ", "+")
        payload = self.client.get_json(
            f"https://efts.sec.gov/LATEST/search-index?q=%22{quoted}%22&forms=13F-HR",
            cache_key=f"13f_entity_{filer_name.lower().replace(' ', '_')}",
        )
        if payload is None:
            return None

        buckets = payload.get("aggregations", {}).get("entity_filter", {}).get("buckets", [])
        wanted = _normalise_name(filer_name)
        best: tuple[int, int] | None = None  # (doc_count, cik)
        for bucket in buckets:
            key = bucket.get("key", "")
            match = re.search(r"CIK\s*(\d{10})", key)
            if not match:
                continue
            if wanted not in _normalise_name(key):
                continue
            count = int(bucket.get("doc_count", 0))
            if best is None or count > best[0]:
                best = (count, int(match.group(1)))

        if best is None:
            return None
        registry[filer_name] = best[1]
        self._save_filer_registry(registry)
        return best[1]

    def _filer_registry(self) -> dict[str, int]:
        path = self.cache_dir / "filer_ciks.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save_filer_registry(self, registry: dict[str, int]) -> None:
        path = self.cache_dir / "filer_ciks.json"
        path.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8")

    # -- CUSIP mapping ------------------------------------------------------

    def cusip_map(self, tickers: Iterable[str]) -> dict[str, str]:
        """Map CUSIP to ticker for the names we care about.

        SEC publishes no free CUSIP-to-ticker table, and a CUSIP licence is not
        worth buying for this. The workable route is matching the `nameOfIssuer`
        that 13F filers report against the company names SEC already publishes
        alongside tickers, then caching the CUSIP once a match is confirmed.

        Name matching is fuzzy in general, but these are large, well-known issuers
        with distinctive names, and an unmatched CUSIP is reported rather than
        guessed at. The cost of a wrong match is attributing a holding to the
        wrong company, so the bar is a normalised prefix match, not a similarity
        score.
        """
        path = self.cache_dir / "cusip_tickers.json"
        mapping: dict[str, str] = {}
        if path.exists():
            try:
                mapping = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                mapping = {}
        return mapping

    def learn_cusips(
        self, holdings: Iterable[Holding], tickers: Iterable[str]
    ) -> dict[str, str]:
        """Match reported issuer names to tickers and persist the CUSIPs found."""
        path = self.cache_dir / "cusip_tickers.json"
        mapping = self.cusip_map(tickers)

        names: dict[str, str] = {}
        for ticker in tickers:
            company = self.client.company(ticker)
            if company is not None:
                names[_normalise_name(company.name)] = company.ticker

        for holding in holdings:
            if holding.cusip in mapping:
                continue
            issuer = _normalise_name(holding.issuer)
            if not issuer:
                continue
            for company_name, ticker in names.items():
                # Require one to be a prefix of the other: "nvidia" against
                # "nvidia corp" is safe, while a substring match anywhere would
                # pair unrelated issuers that merely share a word.
                if company_name.startswith(issuer) or issuer.startswith(company_name):
                    mapping[holding.cusip] = ticker
                    break

        path.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")
        return mapping

    def filings(self, cik: int, form: str = "13F-HR", limit: int = 8) -> list[dict[str, Any]]:
        """Recent filings of a given form for one filer."""
        submissions = self.client.submissions(cik)
        if submissions is None:
            return []
        recent = submissions.get("filings", {}).get("recent", {})
        out: list[dict[str, Any]] = []
        for form_type, filed, accession, period in zip(
            recent.get("form", []),
            recent.get("filingDate", []),
            recent.get("accessionNumber", []),
            recent.get("reportDate", []),
        ):
            if not form_type.startswith(form):
                continue
            out.append({"filed": filed, "accession": accession, "period": period})
            if len(out) >= limit:
                break
        return out

    def information_table(self, cik: int, accession: str) -> str | None:
        """Fetch the holdings XML for one filing."""
        plain = accession.replace("-", "")
        # The directory listing lives at index.json inside the accession folder,
        # not at "<accession>-index.json", which returns 404.
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{plain}/index.json"
        index = self.client.get_json(index_url, cache_key=f"13f_idx_{plain}")
        if index is None:
            return None
        items = index.get("directory", {}).get("item", [])
        # The holdings table is a separate XML document from the cover page, and
        # naming is inconsistent across filing agents.
        candidates = [
            i["name"]
            for i in items
            if i.get("name", "").lower().endswith(".xml")
            and "primary_doc" not in i.get("name", "").lower()
        ]
        if not candidates:
            return None
        self.client.limiter.wait()
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{plain}/{candidates[0]}"
        try:
            response = self.client.session.get(url, timeout=30)
        except Exception:
            return None
        return response.text if response.status_code == 200 else None

    def filer_quarter(
        self, filer_name: str, cik: int, as_of: date | None = None
    ) -> FilerQuarter | None:
        """The most recent quarter for one manager that was public at `as_of`."""
        as_of = as_of or date.today()
        target = quarter_end_for(as_of)
        for filing in self.filings(cik):
            filed = _parse_iso(filing["filed"])
            period = _parse_iso(filing["period"])
            if filed is None or period is None:
                continue
            # Point-in-time: a filing submitted after the as-of date was not
            # readable then, whatever period it covers.
            if filed > as_of or period > target:
                continue
            xml = self.information_table(cik, filing["accession"])
            if xml is None:
                continue
            holdings = parse_information_table(xml, filer_name, cik, period, filed)
            if holdings:
                return FilerQuarter(filer_name, cik, period, filed, holdings)
        return None


_NAME_NOISE = re.compile(
    r"\b(inc|incorporated|corp|corporation|co|company|llc|lp|plc|ltd|limited|holdings|"
    r"holding|group|the|class|cl|a|b|c|com|technologies|technology|systems)\b"
)


def _normalise_name(value: str) -> str:
    """Reduce a company or filer name to a comparable core.

    Strips corporate-form words and punctuation so "NVIDIA CORPORATION",
    "Nvidia Corp" and "NVIDIA CORP  (CIK 0001045810)" all reduce to "nvidia".
    """
    lowered = re.sub(r"\(cik\s*\d+\)", " ", value.lower())
    lowered = re.sub(r"[^a-z0-9 ]", " ", lowered)
    lowered = _NAME_NOISE.sub(" ", lowered)
    return re.sub(r"\s+", "", lowered)


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None
