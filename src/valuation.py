"""Valuation — what a business is worth, as a range.

Two independent estimates, reported side by side, because when they disagree the
disagreement is the finding:

**Intrinsic (DCF).** Free cash flow projected forward, faded toward a terminal
rate, discounted at a cost of capital built from CAPM. Never reported as a single
number: a DCF's output is dominated by two assumptions nobody knows, so every
result here is a band produced by a sensitivity grid over both.

**Relative (multiples).** P/E, EV/EBITDA, EV/Sales and FCF yield measured against
the company's *own* history, since a semiconductor multiple that looks expensive
against the market may be cheap against its own five-year range.

Three honesty constraints shape the code:

- **Growth is the system's own projection, not consensus.** There is no free
  source of analyst estimates, so forward figures are derived from filed history
  and labelled as projections everywhere they appear.
- **Assumptions are capped.** Semiconductor revenue can double in a cycle year;
  extrapolating that produces a fair value several times the market's and feels
  authoritative while being nonsense.
- **Non-USD filers are refused, not fudged.** A market cap in dollars over
  earnings in Taiwan dollars is a meaningless ratio, so the multiple is withheld
  until point-in-time FX exists.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from src import config, prices
from src.fundamentals import Fundamentals

MARKET_BENCHMARK = "SPY"
TREASURY_10Y = "^TNX"


# ---------------------------------------------------------------------------
# Cost of capital
# ---------------------------------------------------------------------------


@dataclass
class CostOfCapital:
    """WACC and every input that produced it."""

    wacc: float | None
    cost_of_equity: float | None = None
    cost_of_debt: float | None = None
    risk_free_rate: float | None = None
    equity_risk_premium: float | None = None
    beta_raw: float | None = None
    beta_adjusted: float | None = None
    tax_rate: float | None = None
    equity_weight: float | None = None
    debt_weight: float | None = None
    notes: list[str] = field(default_factory=list)

    def label(self) -> str:
        if self.wacc is None:
            return "WACC: not computable"
        return (
            f"WACC {self.wacc:.2%} "
            f"(Re {self.cost_of_equity:.2%} x {self.equity_weight:.0%}, "
            f"Rd {self.cost_of_debt:.2%} x {self.debt_weight:.0%})"
        )


def risk_free_rate(as_of: date) -> float | None:
    """10-year Treasury yield on the as-of date, as a decimal."""
    history = prices.load(TREASURY_10Y)
    if history is None:
        return None
    quote = history.adjusted_close(as_of)
    if quote is None or quote <= 0:
        return None
    return quote / 100.0


def beta(ticker: str, as_of: date, benchmark: str = MARKET_BENCHMARK) -> tuple[float | None, float | None]:
    """Raw and adjusted beta from weekly returns against the market.

    Adjusted beta follows the Bloomberg convention of pulling the raw estimate
    toward 1.0, which corrects the well-known tendency of a single regression to
    overstate how far a stock's sensitivity sits from the market's.
    """
    window = config.get("rules.valuation.dcf.beta_window_years")
    minimum = config.get("rules.valuation.dcf.beta_min_observations")
    raw_weight = config.get("rules.valuation.dcf.beta_adjustment.raw_weight")
    market_weight = config.get("rules.valuation.dcf.beta_adjustment.market_weight")

    stock = prices.load(ticker)
    market = prices.load(benchmark)
    if stock is None or market is None:
        return None, None

    start = as_of - timedelta(days=int(window * 365))
    stock_window = stock.upto(as_of)
    market_window = market.upto(as_of)
    stock_window = stock_window.loc[stock_window.index >= str(start)]
    market_window = market_window.loc[market_window.index >= str(start)]
    if stock_window.empty or market_window.empty:
        return None, None

    # Weekly sampling: daily returns are noisier and more exposed to
    # non-synchronous trading, and monthly leaves too few observations.
    stock_weekly = stock_window["adj_close"].resample("W").last().pct_change().dropna()
    market_weekly = market_window["adj_close"].resample("W").last().pct_change().dropna()
    joined = stock_weekly.align(market_weekly, join="inner")
    stock_returns, market_returns = joined
    if len(stock_returns) < minimum:
        return None, None

    market_variance = float(market_returns.var())
    if market_variance == 0:
        return None, None
    covariance = float(stock_returns.cov(market_returns))
    raw = covariance / market_variance
    adjusted = raw_weight * raw + market_weight * 1.0
    return raw, adjusted


def cost_of_capital(
    f: Fundamentals,
    market_cap: float | None,
    as_of: date | None = None,
) -> CostOfCapital:
    """WACC from market-value weights, CAPM equity cost, and observed debt cost."""
    as_of = as_of or f.as_of
    notes: list[str] = []

    rf = risk_free_rate(as_of)
    if rf is None:
        return CostOfCapital(None, notes=["risk-free rate unavailable"])

    erp_range = config.get("rules.valuation.dcf.equity_risk_premium_range")
    erp = sum(erp_range) / 2

    raw_beta, adjusted_beta = beta(f.ticker, as_of)
    if adjusted_beta is None:
        # A sector-average beta would be a guess presented as a measurement.
        return CostOfCapital(
            None, risk_free_rate=rf, equity_risk_premium=erp, notes=["beta not estimable"]
        )

    cost_equity = rf + adjusted_beta * erp

    debt = f.total_debt
    interest = f.interest_expense
    floor = config.get("rules.valuation.dcf.min_cost_of_debt")
    if debt.present and debt.value > 0 and interest.present and interest.value != 0:
        observed = abs(interest.value) / debt.value
        cost_debt = max(observed, floor)
        if observed < floor:
            notes.append(
                f"implied cost of debt {observed:.2%} floored to {floor:.2%}"
            )
    else:
        cost_debt = floor
        notes.append(f"no usable interest expense; cost of debt set to {floor:.2%}")

    tax_rate = f.effective_tax_rate
    if tax_rate is None:
        tax_rate = 0.21
        notes.append("effective tax rate unavailable; 21% statutory used")

    if not market_cap or market_cap <= 0:
        return CostOfCapital(
            None,
            cost_of_equity=cost_equity,
            cost_of_debt=cost_debt,
            risk_free_rate=rf,
            equity_risk_premium=erp,
            beta_raw=raw_beta,
            beta_adjusted=adjusted_beta,
            tax_rate=tax_rate,
            notes=notes + ["market cap unavailable, so WACC weights cannot be set"],
        )

    debt_value = debt.value if debt.present else 0.0
    total = market_cap + debt_value
    equity_weight = market_cap / total
    debt_weight = debt_value / total
    if not debt.present:
        notes.append("debt untagged; treated as zero weight for WACC only")

    wacc = equity_weight * cost_equity + debt_weight * cost_debt * (1 - tax_rate)
    return CostOfCapital(
        wacc=wacc,
        cost_of_equity=cost_equity,
        cost_of_debt=cost_debt,
        risk_free_rate=rf,
        equity_risk_premium=erp,
        beta_raw=raw_beta,
        beta_adjusted=adjusted_beta,
        tax_rate=tax_rate,
        equity_weight=equity_weight,
        debt_weight=debt_weight,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Growth, estimated from filed history
# ---------------------------------------------------------------------------


@dataclass
class GrowthEstimate:
    rate: float | None
    basis: str
    observations: int = 0
    capped: bool = False

    def label(self) -> str:
        if self.rate is None:
            return "growth: not estimable"
        suffix = " (capped)" if self.capped else ""
        return f"growth {self.rate:.1%} from {self.basis}{suffix}"


def growth_estimate(f: Fundamentals) -> GrowthEstimate:
    """Starting growth rate from the company's own free-cash-flow history.

    Falls back to revenue when free cash flow is too erratic to compound — memory
    makers swing through negative free cash flow, which no CAGR can describe.
    """
    lookback = config.get("rules.valuation.dcf.growth_lookback_years")
    cap = config.get("rules.valuation.dcf.max_initial_growth")
    floor = config.get("rules.valuation.dcf.min_initial_growth")

    def series(attr: str) -> list[float]:
        values: list[float] = []
        view: Fundamentals | None = f
        for _ in range(lookback):
            if view is None:
                break
            item = getattr(view, attr)
            values.append(item.value if item.present else math.nan)
            view = view.prior_year()
        return values

    for attr, label in (("free_cash_flow", "free cash flow"), ("revenue", "revenue")):
        values = [v for v in series(attr) if not math.isnan(v)]
        # CAGR needs both endpoints positive; a negative start makes the root
        # of a negative ratio, which is not a growth rate.
        if len(values) >= 3 and values[0] > 0 and values[-1] > 0:
            years = len(values) - 1
            cagr = (values[0] / values[-1]) ** (1 / years) - 1
            capped = cagr > cap or cagr < floor
            return GrowthEstimate(
                min(max(cagr, floor), cap), f"{label} CAGR over {years}y", len(values), capped
            )

    return GrowthEstimate(None, "insufficient history")


# ---------------------------------------------------------------------------
# DCF
# ---------------------------------------------------------------------------


@dataclass
class NormalisedCashFlow:
    """Starting free cash flow, averaged across a cycle where a single year lies."""

    value: float | None
    basis: str
    was_normalised: bool = False
    years_used: int = 0
    latest: float | None = None

    def label(self) -> str:
        if self.value is None:
            return "starting cash flow: unavailable"
        if not self.was_normalised:
            return f"starting cash flow {self.value / 1e9:,.1f}B ({self.basis})"
        return (
            f"starting cash flow normalised to {self.value / 1e9:,.1f}B over "
            f"{self.years_used}y (latest year was {self.latest / 1e9:,.1f}B)"
        )


def normalised_free_cash_flow(f: Fundamentals, lookback: int = 5) -> NormalisedCashFlow:
    """Mid-cycle free cash flow, because one year misvalues a cyclical badly.

    Micron's DCF came out at $27 against a market price near $970: its free cash
    flow sits near a cycle low while it spends over 40% of revenue building HBM
    capacity, and projecting that forward for a decade values the trough as if it
    were permanent. Averaging across the available years is the standard remedy
    for cyclicals (Damodaran's normalised-earnings approach).

    Normalisation applies only when the latest year is genuinely unrepresentative
    — well away from its own multi-year mean — so stable compounders keep their
    actual current cash flow.
    """
    latest = f.free_cash_flow
    if not latest.present:
        return NormalisedCashFlow(None, "free cash flow unavailable")

    values: list[float] = []
    view: Fundamentals | None = f
    for _ in range(lookback):
        if view is None:
            break
        item = view.free_cash_flow
        if item.present:
            values.append(item.value)
        view = view.prior_year()

    if len(values) < 3:
        return NormalisedCashFlow(
            latest.value, "latest year (insufficient history to normalise)", latest=latest.value
        )

    mean = sum(values) / len(values)
    if mean <= 0:
        # A cycle whose average is negative cannot support a going-concern DCF.
        return NormalisedCashFlow(
            None, f"mean free cash flow over {len(values)}y is negative", latest=latest.value
        )

    # Distinguish a cycle from a growth ramp before averaging anything, because
    # averaging a grower does not find mid-cycle — it drags the estimate back
    # toward a smaller past. NVIDIA went from roughly $4bn to $97bn of free cash
    # flow; its five-year mean of $39bn describes no year it will see again.
    #
    # A strict monotonic test is too brittle: NVIDIA's FY2023 inventory
    # correction is a real dip inside an obvious secular ramp, and one dip should
    # not reclassify the company as cyclical. Comparing the recent half of the
    # record against the older half tolerates that while still catching genuine
    # mean reversion — Micron's recent half is *negative* against a positive
    # older half, which is exactly what a cycle looks like.
    half = max(1, len(values) // 2)
    recent = sum(values[:half]) / half
    older = sum(values[-half:]) / half
    if older > 0 and recent > older * 1.5:
        return NormalisedCashFlow(
            latest.value,
            "latest year (secular growth, not a cycle)",
            False,
            len(values),
            latest.value,
        )

    deviation = abs(latest.value - mean) / mean
    # A third away from its own mean is the line between noise and a genuinely
    # unrepresentative year.
    if deviation > 0.33:
        return NormalisedCashFlow(mean, f"{len(values)}y mean", True, len(values), latest.value)
    return NormalisedCashFlow(latest.value, "latest year", False, len(values), latest.value)


@dataclass
class DCFResult:
    """Fair value per share as a range, with the grid that produced it."""

    base: float | None
    low: float | None
    high: float | None
    grid: dict[str, float] = field(default_factory=dict)
    wacc: float | None = None
    terminal_growth: float | None = None
    initial_growth: float | None = None
    enterprise_value: float | None = None
    notes: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)

    @property
    def reliability(self) -> str:
        """How much weight this estimate can carry.

        A DCF is only as good as its assumptions, and some companies' assumptions
        are load-bearing enough that the output should not drive a decision. Said
        plainly rather than left for the reader to infer from a wide band.
        """
        if self.base is None:
            return "not computable"
        if len(self.caveats) >= 2:
            return "weak - treat as one input among several, not a target"
        if self.caveats:
            return "moderate"
        spread = self.spread
        if spread is not None and spread > 0.80:
            return "weak - the sensitivity band is wider than the base case"
        return "reasonable"

    @property
    def spread(self) -> float | None:
        """How wide the band is relative to the base case."""
        if self.base is None or self.low is None or self.high is None or self.base == 0:
            return None
        return (self.high - self.low) / self.base

    def label(self) -> str:
        if self.base is None:
            return "DCF: not computable"
        return f"fair value {self.low:,.0f} - {self.high:,.0f} (base {self.base:,.0f})"


def _present_value(
    starting_fcf: float,
    initial_growth: float,
    terminal_growth: float,
    wacc: float,
    years: int,
) -> float | None:
    """Enterprise value: faded-growth projection plus a Gordon terminal value."""
    if wacc <= terminal_growth:
        return None  # the terminal formula diverges

    total = 0.0
    cash_flow = starting_fcf
    for year in range(1, years + 1):
        # Linear fade from the initial rate to the terminal rate.
        weight = (year - 1) / max(years - 1, 1)
        rate = initial_growth + (terminal_growth - initial_growth) * weight
        cash_flow *= 1 + rate
        total += cash_flow / ((1 + wacc) ** year)

    terminal = cash_flow * (1 + terminal_growth) / (wacc - terminal_growth)
    total += terminal / ((1 + wacc) ** years)
    return total


def dcf(
    f: Fundamentals,
    wacc: float | None,
    shares_outstanding: float | None,
    initial_growth: float | None = None,
    terminal_growth: float | None = None,
) -> DCFResult:
    """Per-share fair-value range from a sensitivity grid over WACC and terminal growth."""
    notes: list[str] = []
    caveats: list[str] = []

    starting = normalised_free_cash_flow(f)
    if starting.value is None:
        return DCFResult(None, None, None, notes=[starting.basis])
    if starting.value <= 0:
        # Discounting a negative cash flow forward produces a negative fair
        # value, which is arithmetic rather than analysis.
        return DCFResult(
            None, None, None, notes=["free cash flow is negative; DCF not meaningful"]
        )
    notes.append(starting.label())
    if starting.was_normalised:
        caveats.append(
            "starting cash flow was normalised across the cycle, so the estimate "
            "describes a mid-cycle business rather than the current one"
        )

    if wacc is None:
        return DCFResult(None, None, None, notes=["cost of capital unavailable"])
    if not shares_outstanding or shares_outstanding <= 0:
        return DCFResult(None, None, None, notes=["share count unavailable"])

    if initial_growth is None:
        estimate = growth_estimate(f)
        if estimate.rate is None:
            return DCFResult(None, None, None, notes=["growth not estimable from history"])
        initial_growth = estimate.rate
        notes.append(estimate.label())
        if estimate.capped:
            caveats.append(
                f"growth was capped at {initial_growth:.0%}; the company's trailing "
                "rate is higher and this deliberately conservative assumption will "
                "understate a genuine hypergrowth business"
            )

    if terminal_growth is None:
        terminal_growth = config.get("rules.valuation.dcf.terminal_growth_default")

    years = config.get("rules.valuation.dcf.projection_years")
    wacc_deltas = config.get("rules.valuation.dcf.sensitivity.wacc_delta_bps")
    growth_deltas = config.get("rules.valuation.dcf.sensitivity.terminal_growth_delta_bps")

    net_debt = f.net_debt
    grid: dict[str, float] = {}
    for wacc_delta in wacc_deltas:
        for growth_delta in growth_deltas:
            trial_wacc = wacc + wacc_delta / 10_000
            trial_growth = terminal_growth + growth_delta / 10_000
            if trial_wacc <= trial_growth or trial_wacc <= 0:
                continue
            enterprise = _present_value(
                starting.value, initial_growth, trial_growth, trial_wacc, years
            )
            if enterprise is None:
                continue
            equity_value = enterprise - (net_debt.value if net_debt.present else 0.0)
            per_share = equity_value / shares_outstanding
            grid[f"wacc{wacc_delta:+d}/g{growth_delta:+d}"] = per_share

    if not grid:
        return DCFResult(None, None, None, notes=notes + ["no valid grid point"], caveats=caveats)

    base_key = "wacc+0/g+0"
    base = grid.get(base_key)
    values = sorted(grid.values())
    if base is None:
        base = values[len(values) // 2]
        notes.append("base case fell outside the valid grid; median used")

    if not net_debt.present:
        notes.append("net debt unavailable; enterprise value used as equity value")

    enterprise = _present_value(starting.value, initial_growth, terminal_growth, wacc, years)
    return DCFResult(
        base=base,
        low=values[0],
        high=values[-1],
        grid=grid,
        wacc=wacc,
        terminal_growth=terminal_growth,
        initial_growth=initial_growth,
        enterprise_value=enterprise,
        notes=notes,
        caveats=caveats,
    )


# ---------------------------------------------------------------------------
# Reverse DCF — what the market is already assuming
# ---------------------------------------------------------------------------


@dataclass
class ImpliedExpectations:
    """The growth rate today's price requires, and whether history supports it.

    A forward DCF on this sector returns a fair value below the market price for
    almost every name, which is a real finding but a useless ranking signal: it
    cannot separate the reasonably-priced from the absurd. Inverting the question
    fixes that. Rather than asserting a fair value, solve for the growth rate that
    makes the DCF equal the traded price, then compare it against what the company
    has actually delivered.

    This is the standard framing in expectations investing, and it moves the
    judgement to where the uncertainty really sits: not "is this worth $200?" but
    "the market needs 30% growth for a decade - has this business ever done that?"
    """

    implied_growth: float | None
    historical_growth: float | None
    gap: float | None = None
    note: str = ""

    @property
    def verdict(self) -> str:
        if self.implied_growth is None:
            return "not computable"
        if self.historical_growth is None:
            return f"price implies {self.implied_growth:.1%} annual growth; no history to compare"
        if self.gap is None:
            return f"price implies {self.implied_growth:.1%} annual growth"
        if self.gap <= -0.05:
            return (
                f"price implies {self.implied_growth:.1%} growth, BELOW the "
                f"{self.historical_growth:.1%} delivered - the market is not demanding much"
            )
        if self.gap <= 0.05:
            return (
                f"price implies {self.implied_growth:.1%} growth, roughly matching the "
                f"{self.historical_growth:.1%} delivered"
            )
        return (
            f"price implies {self.implied_growth:.1%} growth against "
            f"{self.historical_growth:.1%} delivered - needs {self.gap:.1%} more than history"
        )


def implied_expectations(
    f: Fundamentals,
    wacc: float | None,
    shares_outstanding: float | None,
    price: float | None,
) -> ImpliedExpectations:
    """Solve for the growth rate that makes the DCF equal today's price."""
    starting = normalised_free_cash_flow(f)
    historical = growth_estimate(f)

    if (
        starting.value is None
        or starting.value <= 0
        or wacc is None
        or not shares_outstanding
        or not price
        or price <= 0
    ):
        return ImpliedExpectations(None, historical.rate, note="inputs unavailable")

    years = config.get("rules.valuation.dcf.projection_years")
    terminal = config.get("rules.valuation.dcf.terminal_growth_default")
    net_debt = f.net_debt
    debt_adjustment = net_debt.value if net_debt.present else 0.0

    def per_share(growth: float) -> float | None:
        enterprise = _present_value(starting.value, growth, terminal, wacc, years)
        if enterprise is None:
            return None
        return (enterprise - debt_adjustment) / shares_outstanding

    # Bisection over a wide but finite band. Above ~60% sustained for a decade the
    # question stops being quantitative and becomes "is that credible at all".
    low, high = -0.50, 0.60
    low_value, high_value = per_share(low), per_share(high)
    if low_value is None or high_value is None:
        return ImpliedExpectations(None, historical.rate, note="projection failed")
    if price < low_value:
        return ImpliedExpectations(
            low,
            historical.rate,
            note="price is below even a steep-decline projection",
        )
    if price > high_value:
        return ImpliedExpectations(
            None,
            historical.rate,
            note=f"price requires sustained growth above {high:.0%}, outside any credible band",
        )

    for _ in range(80):
        mid = (low + high) / 2
        value_at_mid = per_share(mid)
        if value_at_mid is None:
            break
        if value_at_mid < price:
            low = mid
        else:
            high = mid
    implied = (low + high) / 2

    gap = None if historical.rate is None else implied - historical.rate
    return ImpliedExpectations(implied, historical.rate, gap, starting.basis)


# ---------------------------------------------------------------------------
# Relative multiples
# ---------------------------------------------------------------------------


@dataclass
class Multiples:
    """Current multiples and where they sit against the company's own history."""

    pe: float | None = None
    ev_ebitda: float | None = None
    ev_sales: float | None = None
    fcf_yield: float | None = None
    earnings_yield: float | None = None
    peg: float | None = None
    history: dict[str, list[float]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def versus_own_history(self, name: str) -> str:
        """Whether a multiple is high or low against this company's own record."""
        current = getattr(self, name, None)
        series = [v for v in self.history.get(name, []) if v is not None and v > 0]
        if current is None or len(series) < 3:
            return "no usable history"
        median = sorted(series)[len(series) // 2]
        if median == 0:
            return "no usable history"
        ratio = current / median
        if ratio > 1.25:
            return f"{ratio:.2f}x its own median of {median:,.1f} - expensive vs history"
        if ratio < 0.80:
            return f"{ratio:.2f}x its own median of {median:,.1f} - cheap vs history"
        return f"{ratio:.2f}x its own median of {median:,.1f} - in line"


def multiples(
    f: Fundamentals,
    market_cap: float | None,
    growth: float | None = None,
    history_years: int = 5,
) -> Multiples:
    """Current valuation multiples, plus the same measures a year at a time back."""
    result = Multiples()

    if not f.view.is_usd:
        # Market cap is quoted in USD while the filings are not. Dividing one by
        # the other produces a number with no meaning.
        result.notes.append(
            f"multiples withheld: filer reports in {f.currency}, "
            "and point-in-time FX is not yet available"
        )
        return result
    if not market_cap or market_cap <= 0:
        result.notes.append("market cap unavailable")
        return result

    def compute(view: Fundamentals, cap: float) -> dict[str, float | None]:
        net_debt = view.net_debt
        enterprise = cap + (net_debt.value if net_debt.present else 0.0)
        net_income = view.net_income
        ebitda = view.ebitda
        revenue = view.revenue
        fcf = view.free_cash_flow
        return {
            "pe": cap / net_income.value if net_income.present and net_income.value > 0 else None,
            "ev_ebitda": enterprise / ebitda.value if ebitda.present and ebitda.value > 0 else None,
            "ev_sales": enterprise / revenue.value if revenue.present and revenue.value > 0 else None,
            "fcf_yield": fcf.value / cap if fcf.present else None,
            "earnings_yield": net_income.value / cap if net_income.present else None,
        }

    current = compute(f, market_cap)
    result.pe = current["pe"]
    result.ev_ebitda = current["ev_ebitda"]
    result.ev_sales = current["ev_sales"]
    result.fcf_yield = current["fcf_yield"]
    result.earnings_yield = current["earnings_yield"]

    if result.pe is not None and growth and growth > 0:
        # Lynch's heuristic: around 1.0 is fair value for a quality grower.
        result.peg = result.pe / (growth * 100)

    # Own-history comparison, rebuilt point-in-time at annual intervals so the
    # price and the fundamentals always come from the same moment.
    history: dict[str, list[float]] = {k: [] for k in current}
    price_history = prices.load(f.ticker)
    view: Fundamentals | None = f.prior_year()
    step = 1
    while view is not None and step < history_years:
        period_end = view.revenue.period_end or view.assets.period_end
        shares = view.shares_outstanding
        if period_end is None or not shares.present or price_history is None:
            break
        past_cap = prices.market_cap(price_history, shares.value, period_end)
        if past_cap:
            for key, value in compute(view, past_cap).items():
                if value is not None:
                    history[key].append(value)
        view = view.prior_year()
        step += 1

    result.history = history
    return result


# ---------------------------------------------------------------------------
# Full valuation
# ---------------------------------------------------------------------------


@dataclass
class Valuation:
    ticker: str
    as_of: date
    price: float | None
    market_cap: float | None
    cost_of_capital: CostOfCapital
    dcf: DCFResult
    multiples: Multiples
    growth: GrowthEstimate
    expectations: ImpliedExpectations
    margin_of_safety_required: float

    @property
    def implied_margin_of_safety(self) -> float | None:
        """How far below the base-case fair value the price sits."""
        if self.dcf.base is None or self.price is None or self.dcf.base <= 0:
            return None
        return 1 - (self.price / self.dcf.base)

    @property
    def verdict(self) -> str:
        """Valuation-only reading. Not a recommendation: the decision matrix in
        Phase 9 combines this with quality, and quality can veto it."""
        margin = self.implied_margin_of_safety
        if margin is None:
            return "not valuable on current data"
        if margin >= self.margin_of_safety_required:
            return f"below fair value by {margin:.0%}, clearing the {self.margin_of_safety_required:.0%} margin of safety"
        if margin > 0:
            return f"below fair value by {margin:.0%}, short of the {self.margin_of_safety_required:.0%} margin required"
        return f"above base-case fair value by {-margin:.0%}"

    def report(self) -> str:
        out = [f"{self.ticker} valuation as of {self.as_of}", "-" * 68]
        price = "n/a" if self.price is None else f"{self.price:,.2f}"
        out.append(f"  price {price}   market cap "
                   f"{'n/a' if not self.market_cap else f'{self.market_cap / 1e9:,.1f}B'}")
        out.append(f"  {self.cost_of_capital.label()}")
        coc = self.cost_of_capital
        if coc.beta_adjusted is not None:
            out.append(
                f"      beta {coc.beta_raw:.2f} raw -> {coc.beta_adjusted:.2f} adjusted"
                f"   Rf {coc.risk_free_rate:.2%}   ERP {coc.equity_risk_premium:.2%}"
            )
        out.append(f"  {self.growth.label()}")
        out.append(f"  {self.dcf.label()}")
        if self.dcf.spread is not None:
            out.append(
                f"      sensitivity band is {self.dcf.spread:.0%} of the base case"
                f" across {len(self.dcf.grid)} grid points"
            )
        out.append(f"      reliability: {self.dcf.reliability}")
        out.append(f"  {self.verdict}")
        out.append(f"  market expectations: {self.expectations.verdict}")
        out.append("  multiples")
        for name, shown in (
            ("pe", "P/E"),
            ("ev_ebitda", "EV/EBITDA"),
            ("ev_sales", "EV/Sales"),
            ("fcf_yield", "FCF yield"),
            ("peg", "PEG"),
        ):
            value = getattr(self.multiples, name)
            if value is None:
                out.append(f"      {shown:12} n/a")
                continue
            formatted = f"{value:.2%}" if name.endswith("yield") else f"{value:,.1f}"
            out.append(f"      {shown:12} {formatted:>8}   {self.multiples.versus_own_history(name)}")
        for note in self.cost_of_capital.notes + self.dcf.notes + self.multiples.notes:
            out.append(f"  note: {note}")
        return "\n".join(out)


def value(
    f: Fundamentals,
    market_cap: float | None = None,
    price: float | None = None,
    as_of: date | None = None,
) -> Valuation:
    """Full valuation picture for one company at one point in time."""
    as_of = as_of or f.as_of
    coc = cost_of_capital(f, market_cap, as_of)
    growth = growth_estimate(f)
    shares = f.shares_outstanding
    result = dcf(
        f,
        coc.wacc,
        shares.value if shares.present else None,
        initial_growth=growth.rate,
    )
    return Valuation(
        ticker=f.ticker,
        as_of=as_of,
        price=price,
        market_cap=market_cap,
        cost_of_capital=coc,
        dcf=result,
        multiples=multiples(f, market_cap, growth=growth.rate),
        growth=growth,
        expectations=implied_expectations(
            f, coc.wacc, shares.value if shares.present else None, price
        ),
        margin_of_safety_required=config.get("rules.valuation.margin_of_safety"),
    )
