"""Daily price history, split-aware and point-in-time.

Two different prices are needed for two different jobs, and conflating them
silently corrupts results:

**Raw (as-traded) price** — for market cap, valuation multiples, and anything
paired with per-share financials from a filing. yfinance's ``Close`` is
retroactively **split-adjusted**, so NVIDIA reads $48.17 for 2 January 2024 when
it actually traded at $481.68; the 10-for-1 split came five months later.
Multiplying that adjusted figure by the share count NVIDIA reported at the time
understates market cap tenfold. `raw_close` undoes the adjustment using the
split history.

**Adjusted price** — for returns and performance measurement, where the split
and dividend adjustments are exactly what you want.

Dollar volume is invariant between the two: a 10-for-1 split divides price by ten
and multiplies volume by ten, so the product is unchanged.

Point-in-time slicing is enforced on read. A `PriceHistory` built for an as-of
date cannot return a price from after it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

import pandas as pd

from src import config

PRICE_DIR = config.DATA_DIR / "prices"
CACHE_TTL_HOURS = 24

# Columns persisted to cache. `close` is as-reported by yfinance (split-adjusted);
# `adj_close` additionally reflects dividends.
COLUMNS = ["open", "high", "low", "close", "adj_close", "volume", "split"]


@dataclass
class PriceHistory:
    """One ticker's daily history, with split-aware accessors."""

    ticker: str
    frame: pd.DataFrame  # DatetimeIndex (tz-naive, normalised), COLUMNS

    # -- point-in-time slicing ---------------------------------------------

    def upto(self, as_of: date) -> pd.DataFrame:
        """Rows on or before `as_of`. The only way price data is read."""
        if self.frame.empty:
            return self.frame
        return self.frame.loc[self.frame.index <= pd.Timestamp(as_of)]

    def _last_row(self, as_of: date) -> pd.Series | None:
        window = self.upto(as_of)
        if window.empty:
            return None
        return window.iloc[-1]

    # -- prices -------------------------------------------------------------

    def split_factor_after(self, as_of: date) -> float:
        """Product of every split ratio taking effect after `as_of`.

        This is the number that converts a retroactively-adjusted price back to
        what the share actually traded at on the day.
        """
        if self.frame.empty or "split" not in self.frame:
            return 1.0
        later = self.frame.loc[self.frame.index > pd.Timestamp(as_of), "split"]
        applied = later[later > 0]
        factor = float(applied.prod()) if not applied.empty else 1.0
        return factor or 1.0

    def raw_close(self, as_of: date) -> float | None:
        """As-traded closing price — pairs with point-in-time share counts."""
        row = self._last_row(as_of)
        if row is None or pd.isna(row["close"]):
            return None
        return float(row["close"]) * self.split_factor_after(as_of)

    def adjusted_close(self, as_of: date) -> float | None:
        """Total-return basis — for performance, never for market cap."""
        row = self._last_row(as_of)
        if row is None or pd.isna(row["adj_close"]):
            return None
        return float(row["adj_close"])

    def as_of_date(self, as_of: date) -> date | None:
        """The actual trading day used, which may precede `as_of` over a weekend."""
        row = self._last_row(as_of)
        return None if row is None else row.name.date()

    def staleness_days(self, as_of: date) -> int | None:
        traded = self.as_of_date(as_of)
        return None if traded is None else (as_of - traded).days

    # -- liquidity ----------------------------------------------------------

    def avg_dollar_volume(self, as_of: date, days: int = 60) -> float | None:
        """Mean daily traded value over the trailing window.

        Split-adjustment cancels in the product, so the adjusted columns give
        the right answer without un-adjusting either side.
        """
        window = self.upto(as_of).tail(days)
        if window.empty:
            return None
        product = (window["close"] * window["volume"]).dropna()
        if product.empty:
            return None
        return float(product.mean())

    # -- returns ------------------------------------------------------------

    def total_return(self, start: date, end: date) -> float | None:
        """Dividend- and split-adjusted return between two dates."""
        first, last = self.adjusted_close(start), self.adjusted_close(end)
        if first is None or last is None or first == 0:
            return None
        return (last / first) - 1.0

    def trading_days(self, as_of: date) -> int:
        return len(self.upto(as_of))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _cache_path(ticker: str) -> Path:
    return PRICE_DIR / f"{ticker.upper().replace('/', '_')}.csv"


def _cache_fresh(path: Path, ttl_hours: float = CACHE_TTL_HOURS) -> bool:
    if not path.exists():
        return False
    return (time.time() - path.stat().st_mtime) / 3600 <= ttl_hours


def _normalise(frame: pd.DataFrame) -> pd.DataFrame:
    """Reduce a yfinance frame to COLUMNS with a tz-naive daily index.

    Must be idempotent. Normalising an already-normalised frame happens on every
    cache read, and getting that wrong is invisible: yfinance names the column
    "Stock Splits" while the cache stores it as "split", so a lookup that only
    knew the yfinance spelling silently filled the cached column with zeros. The
    first run then computed market caps correctly and every subsequent run — the
    ones reading cache — understated any pre-split date by the split ratio.
    """
    if frame is None or frame.empty:
        return pd.DataFrame(columns=COLUMNS)

    out = pd.DataFrame(index=pd.DatetimeIndex(frame.index).tz_localize(None).normalize())
    lookup = {str(c).lower().replace(" ", "_"): c for c in frame.columns}

    for target, sources in {
        "open": ["open"],
        "high": ["high"],
        "low": ["low"],
        "close": ["close"],
        "adj_close": ["adj_close", "close"],
        "volume": ["volume"],
        # "split" first: the cached spelling must win on a round trip.
        "split": ["split", "stock_splits", "splits"],
    }.items():
        for name in sources:
            if name in lookup:
                out[target] = pd.to_numeric(frame[lookup[name]].values, errors="coerce")
                break
        else:
            out[target] = 0.0 if target == "split" else pd.NA

    out["split"] = out["split"].fillna(0.0)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out[COLUMNS]


def load(ticker: str, refresh: bool = False, period: str = "max") -> PriceHistory | None:
    """Full daily history for a ticker, cached to `data/prices/`.

    Returns None when no data is available, rather than an empty history that
    downstream code might read as "no volume" or "price zero".
    """
    ticker = ticker.upper()
    path = _cache_path(ticker)
    PRICE_DIR.mkdir(parents=True, exist_ok=True)

    if not refresh and _cache_fresh(path):
        try:
            cached = pd.read_csv(path, index_col=0, parse_dates=True)
            if not cached.empty:
                return PriceHistory(ticker, _normalise(cached))
        except (ValueError, pd.errors.ParserError):
            path.unlink(missing_ok=True)

    try:
        import yfinance as yf

        raw = yf.Ticker(ticker).history(period=period, auto_adjust=False, actions=True)
    except Exception as exc:  # yfinance is unofficial; failures are expected
        print(f"  ! price fetch failed for {ticker}: {exc}")
        raw = None

    if raw is None or raw.empty:
        # Fall back to a stale cache rather than reporting no data at all.
        if path.exists():
            try:
                return PriceHistory(ticker, _normalise(pd.read_csv(path, index_col=0, parse_dates=True)))
            except (ValueError, pd.errors.ParserError):
                pass
        return None

    frame = _normalise(raw)
    frame.to_csv(path)
    return PriceHistory(ticker, frame)


def load_many(tickers: Iterable[str], refresh: bool = False) -> dict[str, PriceHistory]:
    """Histories for several tickers, skipping any that cannot be fetched."""
    out: dict[str, PriceHistory] = {}
    for ticker in tickers:
        history = load(ticker, refresh=refresh)
        if history is not None:
            out[ticker.upper()] = history
    return out


def market_cap(
    history: PriceHistory,
    shares_outstanding: float | None,
    as_of: date,
) -> float | None:
    """As-traded price times point-in-time share count.

    Both sides must be on the same basis. `raw_close` undoes yfinance's
    retroactive split adjustment precisely so it can be paired with the share
    count a company reported at the time.
    """
    if not shares_outstanding or shares_outstanding <= 0:
        return None
    price = history.raw_close(as_of)
    if price is None:
        return None
    return price * shares_outstanding
