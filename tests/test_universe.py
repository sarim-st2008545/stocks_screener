"""Tests for universe construction.

The behaviour most worth guarding: an unevaluated screen must never read as a
passed screen. A missing market cap slipping a name past a market-cap floor is
the quiet version of the failure this whole project is built to avoid.
"""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

from src import universe
from src.prices import COLUMNS, PriceHistory
from src.universe import Constituent, Status, UniverseSnapshot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def facts_payload(
    *,
    assets: float = 100e9,
    net_income: float = 20e9,
    ocf: float | None = 25e9,
    shares: float | None = 1e9,
    filed: str = "2026-05-01",
    end: str = "2026-03-31",
) -> dict:
    us_gaap: dict = {
        "Assets": {"units": {"USD": [{"val": assets, "end": end, "filed": filed}]}},
        "NetIncomeLoss": {
            "units": {
                "USD": [
                    {
                        "val": net_income,
                        "start": "2025-04-01",
                        "end": end,
                        "filed": filed,
                        "form": "10-K",
                    }
                ]
            }
        },
    }
    if ocf is not None:
        us_gaap["NetCashProvidedByUsedInOperatingActivities"] = {
            "units": {
                "USD": [
                    {
                        "val": ocf,
                        "start": "2025-04-01",
                        "end": end,
                        "filed": filed,
                        "form": "10-K",
                    }
                ]
            }
        }
    payload: dict = {"facts": {"us-gaap": us_gaap}}
    if shares is not None:
        payload["facts"]["dei"] = {
            "EntityCommonStockSharesOutstanding": {
                "units": {"shares": [{"val": shares, "end": end, "filed": filed}]}
            }
        }
    return payload


def price_history(close: float = 100.0, volume: float = 5_000_000) -> PriceHistory:
    index = pd.DatetimeIndex(pd.date_range("2026-06-01", periods=40, freq="B"))
    frame = pd.DataFrame(index=index)
    for col in ("open", "high", "low", "close", "adj_close"):
        frame[col] = close
    frame["volume"] = volume
    frame["split"] = 0.0
    return PriceHistory("TEST", frame[COLUMNS])


def constituent(**kwargs) -> Constituent:
    base = dict(ticker="TEST", segment="seg", segment_label="Segment")
    base.update(kwargs)
    return Constituent(**base)


AS_OF = date(2026, 7, 31)


# ---------------------------------------------------------------------------
# Candidate list
# ---------------------------------------------------------------------------


class TestCandidates:
    def test_loads_every_configured_member(self):
        names = universe.candidates()
        assert len(names) >= 40
        assert {"NVDA", "MU", "MSFT", "TSM"} <= {c.ticker for c in names}

    def test_segment_and_annotation_attached(self):
        nvda = next(c for c in universe.candidates() if c.ticker == "NVDA")
        assert nvda.segment == "ai_accelerators"
        assert nvda.note

    def test_stability_flags_carried_through(self):
        smci = next(c for c in universe.candidates() if c.ticker == "SMCI")
        assert smci.stability_flag and "auditor" in smci.stability_flag.lower()

    def test_cyclical_segments_marked(self):
        mu = next(c for c in universe.candidates() if c.ticker == "MU")
        assert mu.cyclical is True
        msft = next(c for c in universe.candidates() if c.ticker == "MSFT")
        assert msft.cyclical is False


# ---------------------------------------------------------------------------
# Screening
# ---------------------------------------------------------------------------


class TestScreening:
    def test_healthy_name_is_investable(self):
        c = universe.screen(constituent(), facts_payload(), price_history(), AS_OF)
        assert c.status == Status.INVESTABLE
        assert c.failures == []
        assert c.market_cap == pytest.approx(100e9)

    def test_stability_flag_downgrades_to_speculative(self):
        c = universe.screen(
            constituent(stability_flag="thin track record"),
            facts_payload(),
            price_history(),
            AS_OF,
        )
        assert c.status == Status.SPECULATIVE

    def test_small_market_cap_is_screened_out(self):
        # $1 x 1e9 shares = $1bn, below the $2bn floor.
        c = universe.screen(constituent(), facts_payload(), price_history(close=1.0), AS_OF)
        assert c.status == Status.SCREENED_OUT
        assert any("market cap" in f for f in c.failures)

    def test_illiquid_name_is_screened_out(self):
        c = universe.screen(
            constituent(), facts_payload(), price_history(volume=100), AS_OF
        )
        assert c.status == Status.SCREENED_OUT
        assert any("volume" in f for f in c.failures)

    def test_negative_operating_cash_flow_is_screened_out(self):
        """Enforces the established-and-cash-generative mandate."""
        c = universe.screen(
            constituent(), facts_payload(ocf=-5e9), price_history(), AS_OF
        )
        assert c.status == Status.SCREENED_OUT
        assert any("cash flow" in f for f in c.failures)

    def test_missing_core_statements_is_insufficient_data(self):
        """Regression for TSMC: an empty fact set must not read as a healthy
        company with no debt and no revenue."""
        bare = {
            "facts": {
                "dei": {
                    "EntityCommonStockSharesOutstanding": {
                        "units": {
                            "shares": [
                                {"val": 5e9, "end": "2025-12-31", "filed": "2026-04-16"}
                            ]
                        }
                    }
                }
            }
        }
        c = universe.screen(constituent(), bare, price_history(), AS_OF)
        assert c.status == Status.INSUFFICIENT_DATA
        assert any("core statements" in f for f in c.failures)

    def test_no_filings_at_all_is_insufficient_data(self):
        c = universe.screen(constituent(), None, price_history(), AS_OF)
        assert c.status == Status.INSUFFICIENT_DATA

    def test_unevaluated_screen_is_recorded_not_passed_silently(self):
        """A screen that could not run must leave a visible trace."""
        c = universe.screen(
            constituent(), facts_payload(shares=None), price_history(), AS_OF
        )
        assert c.market_cap is None
        assert any("market-cap screen not evaluated" in n for n in c.notes)
        assert any("multi-class" in n for n in c.notes)

    def test_missing_price_history_is_noted(self):
        c = universe.screen(constituent(), facts_payload(), None, AS_OF)
        assert any("no price history" in n for n in c.notes)
        assert c.avg_dollar_volume is None

    def test_point_in_time_gate_applies_to_screening(self):
        """Screening as of a date before the filing must not see it."""
        c = universe.screen(
            constituent(),
            facts_payload(filed="2026-05-01"),
            price_history(),
            date(2026, 4, 1),
        )
        assert c.status == Status.INSUFFICIENT_DATA

    def test_reporting_currency_recorded(self):
        c = universe.screen(constituent(), facts_payload(), price_history(), AS_OF)
        assert c.reporting_currency == "USD"


# ---------------------------------------------------------------------------
# Snapshots and change tracking
# ---------------------------------------------------------------------------


class TestSnapshots:
    def snapshot(self, as_of: date, statuses: dict[str, str]) -> UniverseSnapshot:
        members = []
        for ticker, status in statuses.items():
            c = constituent(ticker=ticker)
            c.status = status
            members.append(c)
        return UniverseSnapshot(as_of=as_of, constituents=members)

    def test_round_trips_to_disk(self, tmp_path):
        snap = self.snapshot(date(2026, 8, 1), {"NVDA": Status.INVESTABLE})
        path = snap.save(tmp_path)
        loaded = UniverseSnapshot.load(path)
        assert loaded["as_of"] == "2026-08-01"
        assert loaded["constituents"][0]["ticker"] == "NVDA"

    def test_dates_serialise(self, tmp_path):
        snap = self.snapshot(date(2026, 8, 1), {"NVDA": Status.INVESTABLE})
        snap.constituents[0].latest_filing = date(2026, 5, 1)
        json.loads(snap.save(tmp_path).read_text(encoding="utf-8"))  # must not raise

    def test_first_snapshot_says_so(self, tmp_path):
        snap = self.snapshot(date(2026, 8, 1), {"NVDA": Status.INVESTABLE})
        assert "first snapshot" in universe.diff(snap, tmp_path)[0]

    def test_status_change_is_flagged(self, tmp_path):
        """A holding losing investable status is the most actionable output
        here, and it is invisible without comparing dated runs."""
        self.snapshot(date(2026, 7, 1), {"SMCI": Status.INVESTABLE}).save(tmp_path)
        later = self.snapshot(date(2026, 8, 1), {"SMCI": Status.SCREENED_OUT})
        changes = universe.diff(later, tmp_path)
        assert any("SMCI" in c and "SCREENED_OUT" in c for c in changes)

    def test_added_and_removed_names_flagged(self, tmp_path):
        self.snapshot(date(2026, 7, 1), {"OLD": Status.INVESTABLE}).save(tmp_path)
        later = self.snapshot(date(2026, 8, 1), {"NEW": Status.INVESTABLE})
        changes = universe.diff(later, tmp_path)
        assert any("NEW" in c and "added" in c for c in changes)
        assert any("OLD" in c and "removed" in c for c in changes)

    def test_unchanged_status_is_not_reported(self, tmp_path):
        self.snapshot(date(2026, 7, 1), {"NVDA": Status.INVESTABLE}).save(tmp_path)
        later = self.snapshot(date(2026, 8, 1), {"NVDA": Status.INVESTABLE})
        assert universe.diff(later, tmp_path) == [
            "no status changes since the previous snapshot"
        ]

    def test_future_snapshots_are_ignored_when_diffing(self):
        """Comparing against a later run would be a look-ahead."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            self.snapshot(date(2026, 9, 1), {"NVDA": Status.SCREENED_OUT}).save(directory)
            earlier = self.snapshot(date(2026, 8, 1), {"NVDA": Status.INVESTABLE})
            assert "first snapshot" in universe.diff(earlier, directory)[0]

    def test_malformed_snapshot_filenames_are_skipped(self, tmp_path):
        (tmp_path / "not-a-date.json").write_text("{}", encoding="utf-8")
        snap = self.snapshot(date(2026, 8, 1), {"NVDA": Status.INVESTABLE})
        assert "first snapshot" in universe.diff(snap, tmp_path)[0]


class TestSnapshotQueries:
    def test_investable_excludes_screened_and_missing(self):
        members = []
        for ticker, status in {
            "A": Status.INVESTABLE,
            "B": Status.SPECULATIVE,
            "C": Status.SCREENED_OUT,
            "D": Status.INSUFFICIENT_DATA,
        }.items():
            c = constituent(ticker=ticker)
            c.status = status
            members.append(c)
        snap = UniverseSnapshot(date(2026, 8, 1), members)
        assert snap.tickers() == ["A", "B"]
        assert len(snap.by_status(Status.SCREENED_OUT)) == 1

    def test_report_renders_without_error(self):
        c = constituent(ticker="NVDA", stability_flag="flagged")
        c.status = Status.SPECULATIVE
        c.market_cap = 5e12
        c.failures = ["something failed"]
        text = universe.report(UniverseSnapshot(date(2026, 8, 1), [c]))
        assert "NVDA" in text and "SPECULATIVE" in text

    def test_report_is_ascii_safe(self):
        """Printed output must survive a Windows cp1252 console."""
        c = constituent(ticker="NVDA")
        c.status = Status.INVESTABLE
        text = universe.report(UniverseSnapshot(date(2026, 8, 1), [c]))
        text.encode("cp1252")  # must not raise
