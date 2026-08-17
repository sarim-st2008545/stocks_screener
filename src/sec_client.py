"""SEC EDGAR access — polite, cached, and rate-limited.

SEC publishes fair-access guidance capping traffic at roughly 10 requests per
second across all sec.gov hosts, and rejects requests without a contact
User-Agent. Both are enforced here so no caller has to remember.

Three access paths, each chosen against a measured alternative:

- **Bulk `companyfacts.zip`** (~1.4 GB, one download) for whole-universe sweeps.
  Individual company payloads average several megabytes, so a few hundred of
  them move more bytes than the entire archive, in a few hundred requests.
- **Per-company JSON** for ad-hoc single-ticker work, cached to disk.
- **Submissions** for company metadata (name, SIC, fiscal year end). Payloads
  are large because they carry full filing history, so sweeps fetch them
  un-cached and keep only the handful of fields they need.
"""

from __future__ import annotations

import json
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from src import config

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
BULK_FACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"

# SEC fair access allows ~10 req/s. Staying meaningfully under it is cheap
# insurance against being blocked mid-sweep.
DEFAULT_RATE_PER_SECOND = 6.0

CACHE_DIR = config.BASE_DIR / ".sec_cache"

# Cache lifetimes, in hours. A cache with no expiry means a new 10-Q never
# reaches the system — the screen keeps returning last quarter's verdict and
# looks perfectly healthy while doing it.
TTL_FACTS_HOURS = 24
TTL_SUBMISSIONS_HOURS = 24
TTL_TICKER_MAP_HOURS = 24 * 30  # CIK assignments effectively never change


class RateLimiter:
    """Shared token gate so concurrent workers stay inside SEC's fair-access rate.

    Each worker reserves the next slot under a lock and sleeps outside it, so
    network latency overlaps while the global request rate stays fixed.
    """

    def __init__(self, per_second: float = DEFAULT_RATE_PER_SECOND):
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


@dataclass(frozen=True)
class Company:
    """The handful of metadata fields worth keeping from a submissions payload."""

    cik: int
    ticker: str
    name: str
    sic: int | None
    sic_description: str | None
    fiscal_year_end: str | None


class SECClient:
    """Caching, rate-limited client for SEC EDGAR public endpoints."""

    def __init__(
        self,
        user_agent: str | None = None,
        cache_dir: Path = CACHE_DIR,
        rate_per_second: float = DEFAULT_RATE_PER_SECOND,
        session: requests.Session | None = None,
    ):
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent or config.user_agent(),
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.limiter = RateLimiter(rate_per_second)
        self._ticker_map: dict[str, int] | None = None

    # -- transport ----------------------------------------------------------

    def get_json(
        self,
        url: str,
        cache_key: str | None = None,
        ttl_hours: float | None = None,
    ) -> dict[str, Any] | None:
        """Fetch JSON, serving from disk cache when a key is given and fresh.

        Returns None on any failure rather than raising: a sweep over hundreds
        of companies should report what it could not fetch and carry on, not
        abort on one bad response.
        """
        cached_path = self.cache_dir / f"{cache_key}.json" if cache_key else None
        if cached_path and cached_path.exists() and not self._expired(cached_path, ttl_hours):
            try:
                return json.loads(cached_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cached_path.unlink(missing_ok=True)  # corrupt cache, refetch

        self.limiter.wait()
        try:
            response = self.session.get(url, timeout=30)
        except requests.RequestException as exc:
            print(f"  ! request failed for {url}: {exc}")
            return None

        if response.status_code != 200:
            print(f"  ! HTTP {response.status_code} for {url}")
            return None

        try:
            payload = response.json()
        except ValueError:
            print(f"  ! non-JSON response from {url}")
            return None

        if cached_path:
            cached_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    @staticmethod
    def _expired(path: Path, ttl_hours: float | None) -> bool:
        """Whether a cached file is older than its lifetime.

        `ttl_hours=None` means never expire — appropriate only for immutable
        content, never for filings.
        """
        if ttl_hours is None:
            return False
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        return age_hours > ttl_hours

    def clear_cache(self, prefix: str = "") -> int:
        """Delete cached responses, optionally by key prefix. Returns the count."""
        removed = 0
        for path in self.cache_dir.glob(f"{prefix}*.json"):
            path.unlink(missing_ok=True)
            removed += 1
        self._ticker_map = None
        return removed

    # -- endpoints ----------------------------------------------------------

    def ticker_to_cik(self, ticker: str) -> int | None:
        if self._ticker_map is None:
            data = self.get_json(
                SEC_TICKERS_URL,
                cache_key="company_tickers",
                ttl_hours=TTL_TICKER_MAP_HOURS,
            )
            if data is None:
                return None
            self._ticker_map = {
                entry["ticker"].upper(): int(entry["cik_str"]) for entry in data.values()
            }
        # SEC writes class shares with a dash (BRK-B); some sources use a dot.
        return self._ticker_map.get(ticker.upper().replace(".", "-"))

    def submissions(self, cik: int, cache: bool = True) -> dict[str, Any] | None:
        """Company metadata and recent filings.

        Note: the `filings.recent` block is capped at 1000 entries. Filers with
        heavy 6-K/8-K traffic page the remainder into `filings.files`, which
        this does not follow — callers needing full history must paginate.
        """
        return self.get_json(
            SEC_SUBMISSIONS_URL.format(cik=cik),
            cache_key=f"sub_{cik}" if cache else None,
            ttl_hours=TTL_SUBMISSIONS_HOURS,
        )

    def company(self, ticker: str) -> Company | None:
        """Metadata for a ticker, or None if it is not an SEC filer."""
        cik = self.ticker_to_cik(ticker)
        if cik is None:
            return None
        payload = self.submissions(cik)
        if payload is None:
            return None
        sic_raw = payload.get("sic")
        try:
            sic = int(sic_raw) if sic_raw not in (None, "") else None
        except (TypeError, ValueError):
            sic = None
        return Company(
            cik=cik,
            ticker=ticker.upper(),
            name=payload.get("name") or ticker.upper(),
            sic=sic,
            sic_description=payload.get("sicDescription"),
            fiscal_year_end=payload.get("fiscalYearEnd"),
        )

    def company_facts(self, cik: int) -> dict[str, Any] | None:
        return self.get_json(
            SEC_FACTS_URL.format(cik=cik),
            cache_key=f"facts_{cik}",
            ttl_hours=TTL_FACTS_HOURS,
        )

    def facts_for_ticker(self, ticker: str) -> dict[str, Any] | None:
        cik = self.ticker_to_cik(ticker)
        return self.company_facts(cik) if cik is not None else None


class BulkFactsArchive:
    """Random access to every filer's XBRL facts from one downloaded zip.

    For sweeps over more than a few dozen companies this is strictly cheaper
    than per-company requests, in both bytes and request count.
    """

    def __init__(self, path: Path, session: requests.Session | None = None):
        self.path = Path(path)
        self.session = session or requests.Session()
        self._zip: zipfile.ZipFile | None = None
        self._members: set[str] | None = None

    def close(self) -> None:
        """Release the file handle.

        Windows will not let the archive be replaced while it is open, so a
        refresh fails silently without this.
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
        print(f"  downloading {BULK_FACTS_URL} (~1.4 GB, one time)")

        try:
            with self.session.get(BULK_FACTS_URL, stream=True, timeout=180) as response:
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
