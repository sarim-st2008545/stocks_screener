"""Tests for SEC EDGAR access.

No network. Responses are faked so the tests cover the behaviour that actually
bites: caching, graceful failure, rate limiting, and the Windows file-handle
bug that made archive refresh fail silently.
"""

from __future__ import annotations

import json
import time
import zipfile

import pytest
import requests

from src.sec_client import BulkFactsArchive, RateLimiter, SECClient


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text="{}"):
        self._payload = payload
        self.status_code = status_code
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Records requests and replays queued responses."""

    def __init__(self, *responses):
        self.headers: dict[str, str] = {}
        self.responses = list(responses)
        self.calls: list[str] = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if not self.responses:
            return FakeResponse(status_code=404)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


@pytest.fixture
def client(tmp_path):
    def build(*responses):
        return SECClient(
            user_agent="Test test@example.com",
            cache_dir=tmp_path / "cache",
            rate_per_second=1000.0,  # keep tests fast
            session=FakeSession(*responses),
        )

    return build


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_spaces_successive_calls(self):
        limiter = RateLimiter(per_second=50.0)  # 20ms apart
        start = time.monotonic()
        for _ in range(4):
            limiter.wait()
        # Four slots at 20ms; the first is free, so expect at least ~60ms.
        assert time.monotonic() - start >= 0.05

    def test_first_call_is_immediate(self):
        start = time.monotonic()
        RateLimiter(per_second=2.0).wait()
        assert time.monotonic() - start < 0.1

    def test_concurrent_workers_share_the_gate(self):
        """Threads must not each get their own budget — the SEC limit is global."""
        import threading

        limiter = RateLimiter(per_second=100.0)  # 10ms apart
        start = time.monotonic()
        threads = [threading.Thread(target=limiter.wait) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert time.monotonic() - start >= 0.03


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


class TestGetJson:
    def test_sets_contact_user_agent(self, client):
        c = client()
        assert "test@example.com" in c.session.headers["User-Agent"]

    def test_caches_to_disk_and_serves_from_it(self, client):
        c = client(FakeResponse({"hello": "world"}))
        assert c.get_json("https://example.com/x", cache_key="k") == {"hello": "world"}
        # Second call must not hit the network at all.
        assert c.get_json("https://example.com/x", cache_key="k") == {"hello": "world"}
        assert len(c.session.calls) == 1

    def test_uncached_requests_repeat(self, client):
        c = client(FakeResponse({"a": 1}), FakeResponse({"a": 2}))
        c.get_json("https://example.com/x")
        c.get_json("https://example.com/x")
        assert len(c.session.calls) == 2

    def test_corrupt_cache_is_discarded_and_refetched(self, client):
        c = client(FakeResponse({"fresh": True}))
        (c.cache_dir / "k.json").write_text("{ not json")
        assert c.get_json("https://example.com/x", cache_key="k") == {"fresh": True}

    def test_http_error_returns_none(self, client):
        c = client(FakeResponse(status_code=403))
        assert c.get_json("https://example.com/x") is None

    def test_network_error_returns_none(self, client):
        """A sweep over hundreds of names must survive one bad response."""
        c = client(requests.RequestException("boom"))
        assert c.get_json("https://example.com/x") is None

    def test_non_json_response_returns_none(self, client):
        c = client(FakeResponse(payload=None))
        assert c.get_json("https://example.com/x") is None

    def test_failed_request_is_not_cached(self, client):
        c = client(FakeResponse(status_code=500), FakeResponse({"ok": True}))
        assert c.get_json("https://example.com/x", cache_key="k") is None
        assert c.get_json("https://example.com/x", cache_key="k") == {"ok": True}


class TestCacheExpiry:
    """A cache that never expires means a newly filed 10-Q never reaches the
    system: the screen keeps serving last quarter's verdict and looks healthy
    the whole time. These guard against that returning."""

    def test_stale_cache_is_refetched(self, client, tmp_path):
        import os

        c = client(FakeResponse({"v": 1}), FakeResponse({"v": 2}))
        assert c.get_json("https://example.com/x", cache_key="k", ttl_hours=24) == {"v": 1}

        cached = c.cache_dir / "k.json"
        two_days_ago = time.time() - 48 * 3600
        os.utime(cached, (two_days_ago, two_days_ago))

        assert c.get_json("https://example.com/x", cache_key="k", ttl_hours=24) == {"v": 2}
        assert len(c.session.calls) == 2

    def test_fresh_cache_is_reused(self, client):
        c = client(FakeResponse({"v": 1}), FakeResponse({"v": 2}))
        c.get_json("https://example.com/x", cache_key="k", ttl_hours=24)
        assert c.get_json("https://example.com/x", cache_key="k", ttl_hours=24) == {"v": 1}
        assert len(c.session.calls) == 1

    def test_none_ttl_never_expires(self, client):
        import os

        c = client(FakeResponse({"v": 1}), FakeResponse({"v": 2}))
        c.get_json("https://example.com/x", cache_key="k", ttl_hours=None)
        cached = c.cache_dir / "k.json"
        ancient = time.time() - 4000 * 24 * 3600
        os.utime(cached, (ancient, ancient))
        assert c.get_json("https://example.com/x", cache_key="k", ttl_hours=None) == {"v": 1}

    def test_facts_endpoint_applies_a_ttl(self, client):
        """The endpoint most affected by a new filing must not cache forever."""
        import os

        c = client(FakeResponse({"facts": 1}), FakeResponse({"facts": 2}))
        assert c.company_facts(123)["facts"] == 1
        cached = c.cache_dir / "facts_123.json"
        old = time.time() - 72 * 3600
        os.utime(cached, (old, old))
        assert c.company_facts(123)["facts"] == 2

    def test_clear_cache_removes_files(self, client):
        c = client(FakeResponse({"v": 1}), FakeResponse({"v": 2}))
        c.get_json("https://example.com/x", cache_key="facts_1")
        c.get_json("https://example.com/y", cache_key="sub_1")
        assert c.clear_cache("facts_") == 1
        assert not (c.cache_dir / "facts_1.json").exists()
        assert (c.cache_dir / "sub_1.json").exists()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

TICKER_PAYLOAD = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY"},
}


class TestEndpoints:
    def test_ticker_to_cik(self, client):
        c = client(FakeResponse(TICKER_PAYLOAD))
        assert c.ticker_to_cik("nvda") == 1045810

    def test_ticker_map_fetched_once(self, client):
        c = client(FakeResponse(TICKER_PAYLOAD))
        c.ticker_to_cik("AAPL")
        c.ticker_to_cik("NVDA")
        assert len(c.session.calls) == 1

    def test_class_share_dot_is_normalised_to_dash(self, client):
        """Index sources write BRK.B; SEC writes BRK-B."""
        c = client(FakeResponse(TICKER_PAYLOAD))
        assert c.ticker_to_cik("BRK.B") == 1067983

    def test_unknown_ticker_is_none(self, client):
        c = client(FakeResponse(TICKER_PAYLOAD))
        assert c.ticker_to_cik("NOTREAL") is None

    def test_company_metadata_extracted(self, client):
        c = client(
            FakeResponse(TICKER_PAYLOAD),
            FakeResponse(
                {
                    "name": "NVIDIA CORP",
                    "sic": "3674",
                    "sicDescription": "Semiconductors",
                    "fiscalYearEnd": "0126",
                }
            ),
        )
        company = c.company("NVDA")
        assert company.cik == 1045810
        assert company.name == "NVIDIA CORP"
        assert company.sic == 3674
        assert company.fiscal_year_end == "0126"

    def test_company_survives_missing_sic(self, client):
        c = client(FakeResponse(TICKER_PAYLOAD), FakeResponse({"name": "X", "sic": ""}))
        assert c.company("NVDA").sic is None

    def test_company_unknown_ticker_is_none(self, client):
        c = client(FakeResponse(TICKER_PAYLOAD))
        assert c.company("NOTREAL") is None


# ---------------------------------------------------------------------------
# Bulk archive
# ---------------------------------------------------------------------------


@pytest.fixture
def archive_path(tmp_path):
    path = tmp_path / "companyfacts.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "CIK0001045810.json",
            json.dumps({"cik": 1045810, "facts": {"us-gaap": {}}}),
        )
        zf.writestr("CIK0000320193.json", "{ truncated")
    return path


class TestBulkArchive:
    def test_reads_a_member(self, archive_path):
        with BulkFactsArchive(archive_path) as archive:
            assert archive.facts(1045810)["cik"] == 1045810

    def test_absent_member_is_none(self, archive_path):
        with BulkFactsArchive(archive_path) as archive:
            assert archive.facts(999999999) is None

    def test_corrupt_member_is_none_not_fatal(self, archive_path):
        with BulkFactsArchive(archive_path) as archive:
            assert archive.facts(320193) is None

    def test_missing_archive_is_none(self, tmp_path):
        assert BulkFactsArchive(tmp_path / "nope.zip").facts(1) is None

    def test_unreadable_archive_is_none(self, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_text("this is not a zip file")
        assert BulkFactsArchive(bad).facts(1) is None

    def test_close_releases_the_handle(self, archive_path):
        """Windows refuses to replace an open file, which made a refresh fail
        silently. The handle must be released before the file is swapped."""
        archive = BulkFactsArchive(archive_path)
        archive.facts(1045810)  # forces the zip open
        archive.close()
        archive_path.replace(archive_path.with_suffix(".moved"))
        assert not archive_path.exists()

    def test_reopens_after_close(self, archive_path):
        archive = BulkFactsArchive(archive_path)
        assert archive.facts(1045810) is not None
        archive.close()
        assert archive.facts(1045810) is not None
        archive.close()

    def test_download_skipped_when_present(self, archive_path):
        session = FakeSession()
        archive = BulkFactsArchive(archive_path, session=session)
        assert archive.download() is True
        assert session.calls == []
