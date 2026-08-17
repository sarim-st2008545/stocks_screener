"""Tests for the price layer.

The split-adjustment tests are the important ones. yfinance rewrites historical
closes when a split happens, so pairing its `Close` with a point-in-time share
count understates market cap by the split ratio — NVIDIA by 10x for any date
before June 2024. That error looks entirely plausible in output.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.prices import COLUMNS, PriceHistory, market_cap


def history(rows: list[tuple[str, float, float, float]]) -> PriceHistory:
    """Build a history from (date, close, volume, split) tuples."""
    index = pd.DatetimeIndex([pd.Timestamp(r[0]) for r in rows])
    frame = pd.DataFrame(index=index)
    frame["open"] = [r[1] for r in rows]
    frame["high"] = [r[1] for r in rows]
    frame["low"] = [r[1] for r in rows]
    frame["close"] = [r[1] for r in rows]
    frame["adj_close"] = [r[1] for r in rows]
    frame["volume"] = [r[2] for r in rows]
    frame["split"] = [r[3] for r in rows]
    return PriceHistory("TEST", frame[COLUMNS])


NVDA_LIKE = [
    ("2024-01-02", 48.17, 400_000_000, 0.0),  # actually traded near $481.68
    ("2024-06-07", 120.00, 300_000_000, 0.0),
    ("2024-06-10", 121.79, 310_000_000, 10.0),  # 10-for-1 split takes effect
    ("2024-06-11", 127.00, 320_000_000, 0.0),
]


class TestSplitAdjustment:
    def test_raw_close_undoes_a_later_split(self):
        """The core correction: what the share actually traded at that day."""
        h = history(NVDA_LIKE)
        assert h.raw_close(date(2024, 1, 2)) == pytest.approx(481.70, abs=0.05)

    def test_raw_equals_adjusted_after_the_split(self):
        h = history(NVDA_LIKE)
        assert h.raw_close(date(2024, 6, 11)) == pytest.approx(127.00)

    def test_split_on_the_day_itself_is_not_applied(self):
        """A split effective on date D is already reflected in D's close."""
        h = history(NVDA_LIKE)
        assert h.split_factor_after(date(2024, 6, 10)) == 1.0
        assert h.raw_close(date(2024, 6, 10)) == pytest.approx(121.79)

    def test_factor_compounds_across_multiple_splits(self):
        h = history(
            [
                ("2021-06-01", 16.20, 100, 0.0),
                ("2021-07-20", 18.00, 100, 4.0),
                ("2024-06-10", 121.79, 100, 10.0),
            ]
        )
        assert h.split_factor_after(date(2021, 6, 1)) == 40.0
        assert h.raw_close(date(2021, 6, 1)) == pytest.approx(648.0)

    def test_adjusted_close_is_left_alone(self):
        """Returns want the adjustment; only market cap needs it undone."""
        h = history(NVDA_LIKE)
        assert h.adjusted_close(date(2024, 1, 2)) == pytest.approx(48.17)


class TestCacheRoundTrip:
    """Regression: normalisation must be idempotent.

    yfinance names the column "Stock Splits"; the cache stores it as "split". A
    lookup that only knew the yfinance spelling zeroed the cached column, so the
    first run priced pre-split dates correctly and every cached run afterwards
    understated them by the split ratio. Nothing errored.
    """

    def test_normalise_is_idempotent(self):
        from src.prices import _normalise

        once = history(NVDA_LIKE).frame
        twice = _normalise(once)
        assert list(twice.columns) == list(once.columns)
        assert twice["split"].sum() == once["split"].sum() == 10.0

    def test_splits_survive_a_csv_round_trip(self, tmp_path):
        from src.prices import _normalise

        original = history(NVDA_LIKE).frame
        path = tmp_path / "TEST.csv"
        original.to_csv(path)
        reloaded = _normalise(pd.read_csv(path, index_col=0, parse_dates=True))
        restored = PriceHistory("TEST", reloaded)
        assert restored.split_factor_after(date(2024, 1, 2)) == 10.0
        assert restored.raw_close(date(2024, 1, 2)) == pytest.approx(481.70, abs=0.05)

    def test_yfinance_column_spelling_also_works(self):
        from src.prices import _normalise

        raw = pd.DataFrame(
            {
                "Open": [48.0],
                "High": [48.5],
                "Low": [47.5],
                "Close": [48.17],
                "Adj Close": [48.08],
                "Volume": [4e8],
                "Stock Splits": [0.0],
                "Dividends": [0.0],
            },
            index=pd.DatetimeIndex(["2024-01-02"]),
        )
        out = _normalise(raw)
        assert list(out.columns) == COLUMNS
        assert out["close"].iloc[0] == pytest.approx(48.17)


class TestPointInTime:
    def test_never_returns_a_future_price(self):
        h = history(NVDA_LIKE)
        assert h.upto(date(2024, 6, 8)).index[-1] == pd.Timestamp("2024-06-07")
        assert h.adjusted_close(date(2024, 6, 8)) == pytest.approx(120.00)

    def test_a_future_split_is_hidden_too(self):
        """Standing on 8 June 2024, the 10-for-1 split two days later has not
        happened: NVIDIA was really trading near $1,200, and only the split took
        it to $120. The as-of gate must hide future splits, not just future
        prices, or every historical market cap is off by the split ratio.
        """
        h = history(NVDA_LIKE)
        assert h.split_factor_after(date(2024, 6, 8)) == 10.0
        assert h.raw_close(date(2024, 6, 8)) == pytest.approx(1200.00)
        # Once the split is in the past, the two agree again.
        assert h.split_factor_after(date(2024, 6, 11)) == 1.0

    def test_before_first_trading_day_is_none(self):
        h = history(NVDA_LIKE)
        assert h.raw_close(date(2020, 1, 1)) is None
        assert h.adjusted_close(date(2020, 1, 1)) is None

    def test_uses_the_last_trading_day_before_a_weekend(self):
        h = history(NVDA_LIKE)
        # 2024-06-08 is a Saturday; the Friday close is the right answer.
        assert h.as_of_date(date(2024, 6, 8)) == date(2024, 6, 7)
        assert h.staleness_days(date(2024, 6, 8)) == 1

    def test_empty_history_is_survivable(self):
        h = history([])
        assert h.raw_close(date(2024, 1, 1)) is None
        assert h.avg_dollar_volume(date(2024, 1, 1)) is None
        assert h.trading_days(date(2024, 1, 1)) == 0


class TestLiquidity:
    def test_dollar_volume_is_split_invariant(self):
        """A split divides price and multiplies volume, so the product holds.

        This is why liquidity needs no un-adjustment while market cap does.
        """
        pre = history([("2024-01-02", 48.17, 400_000_000, 0.0)])
        post = history([("2024-01-02", 481.70, 40_000_000, 0.0)])
        assert pre.avg_dollar_volume(date(2024, 1, 2)) == pytest.approx(
            post.avg_dollar_volume(date(2024, 1, 2)), rel=1e-6
        )

    def test_averages_over_the_window(self):
        h = history(
            [
                ("2024-01-02", 10.0, 100, 0.0),
                ("2024-01-03", 10.0, 300, 0.0),
            ]
        )
        assert h.avg_dollar_volume(date(2024, 1, 3), days=2) == pytest.approx(2000.0)

    def test_window_respects_as_of(self):
        h = history(
            [
                ("2024-01-02", 10.0, 100, 0.0),
                ("2024-01-03", 10.0, 900, 0.0),
            ]
        )
        assert h.avg_dollar_volume(date(2024, 1, 2), days=5) == pytest.approx(1000.0)


class TestReturns:
    def test_total_return_uses_adjusted_prices(self):
        h = history(
            [
                ("2024-01-02", 100.0, 100, 0.0),
                ("2024-06-11", 150.0, 100, 0.0),
            ]
        )
        assert h.total_return(date(2024, 1, 2), date(2024, 6, 11)) == pytest.approx(0.5)

    def test_missing_endpoint_is_none(self):
        h = history(NVDA_LIKE)
        assert h.total_return(date(2000, 1, 1), date(2024, 6, 11)) is None


class TestMarketCap:
    def test_pairs_raw_price_with_point_in_time_shares(self):
        """Before the split NVIDIA reported ~2.47bn shares at ~$481.68."""
        h = history(NVDA_LIKE)
        cap = market_cap(h, 2_470_000_000, date(2024, 1, 2))
        assert cap == pytest.approx(1.19e12, rel=0.02)

    def test_missing_shares_gives_no_cap(self):
        h = history(NVDA_LIKE)
        assert market_cap(h, None, date(2024, 1, 2)) is None
        assert market_cap(h, 0, date(2024, 1, 2)) is None

    def test_missing_price_gives_no_cap(self):
        h = history(NVDA_LIKE)
        assert market_cap(h, 1_000_000, date(2000, 1, 1)) is None
