"""Historical validation — does any of this actually work.

Everything before this is unvalidated. This module runs the whole pipeline at each
historical rebalance date using only what was knowable then, simulates the
portfolio, and compares it against the benchmarks.

Four disciplines, each of which a backtest is normally wrong about:

**Point-in-time.** Signals are rebuilt from `FactSet(as_of=date)`, so no filing is
visible before it was filed and no split is visible before it happened. This is the
same code path production uses, not a parallel one.

**Costs are modelled, not assumed away.** Slippage and commission come from config
and are charged on every trade.

**Survivorship is disclosed rather than claimed solved.** The candidate list was
written in 2026, so it contains survivors. For the semiconductor core that could be
reconstructed rule-based; for the curated hyperscaler, power and software segments
it cannot. Every result states which universe it used and that the curated portion
is biased upward.

**Hypothetical means hypothetical.** Results are labelled as such, never spliced
into a real track record, and the pass gate is stated before the numbers so it
cannot be moved afterwards.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

from src import config, prices, signals as signals_mod, universe
from src.sec_client import SECClient

RESULTS_DIR = config.DATA_DIR / "pit" / "backtests"

# Rebalance a fortnight after each quarter's filing deadline, so a quarter's
# figures are genuinely readable by the time they are acted on.
REBALANCE_MONTHS = (2, 5, 8, 11)
REBALANCE_DAY = 20

TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@dataclass
class Metrics:
    """Standard risk and return measures for a return series."""

    periods: int = 0
    years: float = 0.0
    total_return: float | None = None
    cagr: float | None = None
    volatility: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    max_drawdown: float | None = None
    calmar: float | None = None

    def label(self) -> str:
        def fmt(value: float | None, pct: bool = True) -> str:
            if value is None:
                return "n/a"
            return f"{value:.1%}" if pct else f"{value:.2f}"

        return (
            f"CAGR {fmt(self.cagr)}  vol {fmt(self.volatility)}  "
            f"Sharpe {fmt(self.sharpe, False)}  Sortino {fmt(self.sortino, False)}  "
            f"maxDD {fmt(self.max_drawdown)}  Calmar {fmt(self.calmar, False)}"
        )


def compute_metrics(
    equity: list[tuple[date, float]], risk_free: float = 0.04
) -> Metrics:
    """Metrics from an equity curve, expected to be sampled daily.

    Periodic returns are annualised by the observed sampling frequency rather than
    assumed monthly, and the Sharpe warning from Lo (2002) applies: naive
    annualisation overstates the ratio when returns are autocorrelated, which a
    low-turnover strategy with overlapping holding periods will be.
    """
    if len(equity) < 3:
        return Metrics(periods=len(equity))

    start, end = equity[0][0], equity[-1][0]
    years = (end - start).days / 365.25
    if years <= 0:
        return Metrics(periods=len(equity))

    values = [v for _, v in equity]
    returns = [
        (values[i] / values[i - 1]) - 1
        for i in range(1, len(values))
        if values[i - 1] > 0
    ]
    if not returns:
        return Metrics(periods=len(equity), years=years)

    per_year = len(returns) / years
    total = (values[-1] / values[0]) - 1
    cagr = (values[-1] / values[0]) ** (1 / years) - 1 if values[0] > 0 else None

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / max(len(returns) - 1, 1)
    period_vol = math.sqrt(variance)
    volatility = period_vol * math.sqrt(per_year) if period_vol else 0.0

    excess = (cagr - risk_free) if cagr is not None else None
    sharpe = (excess / volatility) if excess is not None and volatility else None

    downside = [r for r in returns if r < mean]
    if downside:
        downside_var = sum((r - mean) ** 2 for r in downside) / len(downside)
        downside_vol = math.sqrt(downside_var) * math.sqrt(per_year)
        sortino = (excess / downside_vol) if excess is not None and downside_vol else None
    else:
        sortino = None

    peak = values[0]
    max_dd = 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            max_dd = max(max_dd, (peak - value) / peak)

    calmar = (cagr / max_dd) if cagr is not None and max_dd > 0 else None

    return Metrics(
        periods=len(equity),
        years=years,
        total_return=total,
        cagr=cagr,
        volatility=volatility,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_dd,
        calmar=calmar,
    )


def regress(strategy: list[float], benchmark: list[float]) -> tuple[float | None, float | None]:
    """Alpha and beta of the strategy against a benchmark, per period."""
    pairs = [(s, b) for s, b in zip(strategy, benchmark)]
    if len(pairs) < 4:
        return None, None
    mean_s = sum(s for s, _ in pairs) / len(pairs)
    mean_b = sum(b for _, b in pairs) / len(pairs)
    covariance = sum((s - mean_s) * (b - mean_b) for s, b in pairs) / len(pairs)
    variance = sum((b - mean_b) ** 2 for _, b in pairs) / len(pairs)
    if variance == 0:
        return None, None
    beta = covariance / variance
    alpha = mean_s - beta * mean_b
    return alpha, beta


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


def blended_benchmark(dates: list[date], initial: float) -> list[tuple[date, float]]:
    """A passive portfolio holding the same sleeves at the same weights.

    The right comparison for judging this strategy, and the one the original gate
    got wrong. Measured beta against SOXX is 0.44 because the strategy holds 20%
    of the sector while SOXX holds 100%, so comparing the two tests allocation far
    more than selection. This blend removes that confound and answers the question
    that decides whether the analysis is worth doing at all: does it beat simply
    holding these sleeves passively?

    Rebalanced on the strategy's own schedule, so the comparison is not quietly
    flattered by different rebalancing luck, and its cash sleeve earns the
    risk-free rate rather than nothing.
    """
    weights = {
        config.get("portfolio.sleeves.core_market.instruments")[0]:
            config.get("portfolio.sleeves.core_market.target_pct"),
        config.get("portfolio.sleeves.satellite_ai_infra.etf_instruments")[0]:
            config.get("portfolio.sleeves.satellite_ai_infra.target_pct"),
        config.get("portfolio.sleeves.gold.instruments")[0]:
            config.get("portfolio.sleeves.gold.target_pct"),
    }
    cash_weight = config.get("portfolio.sleeves.cash.target_pct")

    histories = {t: prices.load(t) for t in weights}
    if any(h is None for h in histories.values()) or not dates:
        return []

    rebalances = set(rebalance_dates(dates[0], dates[-1]))
    daily_rf = (1.04 ** (1 / TRADING_DAYS)) - 1

    shares: dict[str, float] = {}
    cash = 0.0
    series: list[tuple[date, float]] = []
    value = float(initial)

    for when in dates:
        quotes = {t: histories[t].adjusted_close(when) for t in weights}
        if any(q is None or q <= 0 for q in quotes.values()):
            continue
        if shares:
            cash *= 1 + daily_rf
            value = cash + sum(shares.get(t, 0.0) * quotes[t] for t in weights)
        if not shares or when in rebalances:
            shares = {t: (value * w) / quotes[t] for t, w in weights.items()}
            cash = value * cash_weight
        series.append((when, value))
    return series


def rebalance_dates(start: date, end: date) -> list[date]:
    out: list[date] = []
    for year in range(start.year, end.year + 1):
        for month in REBALANCE_MONTHS:
            when = date(year, month, REBALANCE_DAY)
            if start <= when <= end:
                out.append(when)
    return out


@dataclass
class Holding:
    ticker: str
    shares: float
    sleeve: str


@dataclass
class BacktestRun:
    start: date
    end: date
    # Marked every trading day, not only at rebalances. Measuring drawdown on
    # quarterly snapshots understates it badly: a crash that recovers inside a
    # quarter is invisible, and the first version of this reported a 1.5% maximum
    # drawdown for a portfolio holding 20% semiconductors through 2024-2026.
    equity: list[tuple[date, float]] = field(default_factory=list)
    rebalance_equity: list[tuple[date, float]] = field(default_factory=list)
    benchmarks: dict[str, list[tuple[date, float]]] = field(default_factory=dict)
    trades: int = 0
    costs_paid: float = 0.0
    name_periods: int = 0
    total_periods: int = 0
    names_held: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    equity_end: date | None = None

    @property
    def name_participation(self) -> float | None:
        """Share of rebalances where any individual stock was held.

        Reported because it decides whether the stock-picking is doing anything.
        A strategy that almost never holds a single name is really just its sleeve
        allocation, and its results say nothing about the analysis above it.
        """
        if self.total_periods == 0:
            return None
        return self.name_periods / self.total_periods


def simulate(
    start: date,
    end: date,
    tickers: Iterable[str] | None = None,
    initial: float | None = None,
    client: SECClient | None = None,
    cache_path: Path | None = None,
    mode: str = "portfolio",
) -> BacktestRun:
    """Walk the strategy forward through history, one rebalance at a time.

    `mode="portfolio"` runs the real thing: sleeves, caps, cash.

    `mode="selection"` isolates the stock picking. It holds the chosen names at
    equal weight with the whole balance, falling back to the sector ETF whenever
    nothing qualifies, so it stays fully sector-exposed and is directly comparable
    to SOXX. That answers the narrow question the portfolio run cannot: do the
    picks beat the index, independently of how much of the sector is held.
    """
    client = client or SECClient()
    initial = initial or config.get("portfolio.wallet.size")
    slippage = config.get("portfolio.execution.assumed_slippage_bps") / 10_000
    commission = config.get("portfolio.execution.assumed_commission_usd")

    core = config.get("portfolio.sleeves.core_market.instruments")[0]
    sector = config.get("portfolio.sleeves.satellite_ai_infra.etf_instruments")[0]
    gold = config.get("portfolio.sleeves.gold.instruments")[0]
    targets = {
        "core_market": config.get("portfolio.sleeves.core_market.target_pct"),
        "satellite_ai_infra": config.get("portfolio.sleeves.satellite_ai_infra.target_pct"),
        "gold": config.get("portfolio.sleeves.gold.target_pct"),
        "cash": config.get("portfolio.sleeves.cash.target_pct"),
    }
    etf_share = config.get("portfolio.sleeves.satellite_ai_infra.etf_share")
    max_names = config.get("portfolio.sleeves.satellite_ai_infra.max_individual_names")

    run = BacktestRun(start=start, end=end)
    # (rebalance date, holdings after rebalancing, cash after rebalancing)
    schedule: list[tuple[date, list[Holding], float]] = []
    dates = rebalance_dates(start, end)
    if len(dates) < 4:
        run.notes.append("too few rebalance dates to measure anything")
        return run

    decisions = _DecisionCache(cache_path)
    price_cache: dict[str, prices.PriceHistory | None] = {}

    def price_of(ticker: str, when: date) -> float | None:
        if ticker not in price_cache:
            price_cache[ticker] = prices.load(ticker)
        history = price_cache[ticker]
        return history.raw_close(when) if history is not None else None

    def total_return_price(ticker: str, when: date) -> float | None:
        """Adjusted close, for anything held: total return includes dividends."""
        if ticker not in price_cache:
            price_cache[ticker] = prices.load(ticker)
        history = price_cache[ticker]
        return history.adjusted_close(when) if history is not None else None

    cash = float(initial)
    holdings: list[Holding] = []

    run.equity_end = dates[-1]
    for when in dates:
        # -- mark to market -------------------------------------------------
        value = cash
        for holding in holdings:
            quote = total_return_price(holding.ticker, when)
            if quote is not None:
                value += holding.shares * quote
        if value <= 0:
            run.notes.append(f"portfolio wiped out at {when}")
            break

        # -- decide ---------------------------------------------------------
        picks = decisions.buys_at(when, tickers, client, max_names)
        run.total_periods += 1
        if picks:
            run.name_periods += 1
            for ticker, _score in picks:
                run.names_held[ticker] = run.names_held.get(ticker, 0) + 1

        # -- target weights -------------------------------------------------
        wanted: dict[str, tuple[float, str]] = {}
        if mode == "selection":
            # Fully sector-exposed either way, so the only difference from SOXX is
            # which names are held.
            if picks:
                each = 1.0 / len(picks)
                wanted = {t: (each, "selection") for t, _ in picks}
            else:
                wanted = {sector: (1.0, "selection")}
        else:
            wanted = {
                core: (targets["core_market"], "core_market"),
                gold: (targets["gold"], "gold"),
            }
            satellite = targets["satellite_ai_infra"]
            if picks:
                wanted[sector] = (satellite * etf_share, "satellite_ai_infra")
                names_budget = satellite * (1 - etf_share)
                # Equal weight within the picked names, capped per name.
                cap = config.get("portfolio.limits.max_single_name_pct_of_portfolio")
                each = min(names_budget / len(picks), cap)
                for ticker, _score in picks:
                    wanted[ticker] = (each, "satellite_ai_infra")
            else:
                # No qualifying name, so the sleeve is held through the sector ETF
                # — the strategic allocation stands even when single-stock
                # selection finds nothing.
                wanted[sector] = (satellite, "satellite_ai_infra")

        # -- rebalance to target --------------------------------------------
        held_now = {h.ticker: h for h in holdings}
        new_holdings: list[Holding] = []
        for ticker, (weight, sleeve) in wanted.items():
            quote = total_return_price(ticker, when)
            if quote is None or quote <= 0:
                continue
            target_value = value * weight
            shares = target_value / quote
            existing = held_now.pop(ticker, None)
            delta_shares = shares - (existing.shares if existing else 0.0)
            traded_value = abs(delta_shares) * quote
            if traded_value > 1e-6:
                cost = traded_value * slippage + commission
                cash -= cost
                run.costs_paid += cost
                run.trades += 1
            cash -= delta_shares * quote
            new_holdings.append(Holding(ticker, shares, sleeve))

        # Anything no longer wanted is sold.
        for ticker, existing in held_now.items():
            quote = total_return_price(ticker, when)
            if quote is None:
                new_holdings.append(existing)  # cannot price it, so cannot sell it
                continue
            proceeds = existing.shares * quote
            cost = proceeds * slippage + commission
            cash += proceeds - cost
            run.costs_paid += cost
            run.trades += 1

        holdings = new_holdings

        marked = cash + sum(
            h.shares * (total_return_price(h.ticker, when) or 0.0) for h in holdings
        )
        run.rebalance_equity.append((when, marked))
        schedule.append((when, list(holdings), cash))

    # -- expand to a daily curve ------------------------------------------
    # Holdings are fixed between rebalances, so marking each trading day is cheap
    # and it is the only way volatility and drawdown mean anything.
    calendar = prices.load(config.get("universe.benchmark.market"))
    if calendar is not None and schedule:
        trading_days = [
            ts.date()
            for ts in calendar.frame.index
            if schedule[0][0] <= ts.date() <= (run.equity_end or end)
        ]
        for day in trading_days:
            active = None
            for stamp, positions, spare in schedule:
                if stamp <= day:
                    active = (positions, spare)
                else:
                    break
            if active is None:
                continue
            positions, spare = active
            marked = spare
            missing = False
            for holding in positions:
                quote = total_return_price(holding.ticker, day)
                if quote is None:
                    missing = True
                    break
                marked += holding.shares * quote
            if not missing and marked > 0:
                run.equity.append((day, marked))
    if not run.equity:
        run.equity = list(run.rebalance_equity)
        run.notes.append("daily marking unavailable; metrics fall back to rebalance dates")

    # -- benchmarks ---------------------------------------------------------
    blend_series = blended_benchmark([d for d, _ in run.equity], float(initial))
    if len(blend_series) >= 3:
        run.benchmarks["BLEND"] = blend_series

    for name in (
        config.get("universe.benchmark.primary"),
        config.get("universe.benchmark.secondary"),
        config.get("universe.benchmark.market"),
    ):
        history = prices.load(name)
        if history is None:
            continue
        series: list[tuple[date, float]] = []
        base: float | None = None
        for when, _ in run.equity:
            quote = history.adjusted_close(when)
            if quote is None:
                continue
            if base is None:
                base = quote
            series.append((when, float(initial) * quote / base))
        if len(series) >= 3:
            run.benchmarks[name] = series

    return run


class _DecisionCache:
    """Caches which names the strategy would buy at each historical date.

    Rebuilding every signal at every rebalance is expensive, and the result is
    deterministic for a given as-of date, so it is worth persisting: iterating on
    the report should not mean re-deriving a decade of decisions.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or (RESULTS_DIR / "decision_cache.json")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: dict[str, list[list[Any]]] = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.data = {}

    def buys_at(
        self,
        when: date,
        tickers: Iterable[str] | None,
        client: SECClient,
        limit: int,
    ) -> list[tuple[str, float]]:
        key = when.isoformat()
        if key in self.data:
            return [(t, s) for t, s in self.data[key]][:limit]

        result = signals_mod.build(as_of=when, tickers=tickers, client=client)
        picks = sorted(
            [(s.ticker, s.composite or 0.0) for s in result.buys()],
            key=lambda pair: -pair[1],
        )
        self.data[key] = [[t, s] for t, s in picks]
        self.path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        return picks[:limit]


# ---------------------------------------------------------------------------
# Walk-forward and gates
# ---------------------------------------------------------------------------


@dataclass
class WalkForward:
    """In-sample against out-of-sample, side by side."""

    windows: list[dict[str, Any]] = field(default_factory=list)

    @property
    def in_sample_sharpe(self) -> float | None:
        values = [w["in_sample"].sharpe for w in self.windows if w["in_sample"].sharpe]
        return sum(values) / len(values) if values else None

    @property
    def out_of_sample_sharpe(self) -> float | None:
        values = [w["out_of_sample"].sharpe for w in self.windows if w["out_of_sample"].sharpe]
        return sum(values) / len(values) if values else None

    @property
    def retention(self) -> float | None:
        """Fraction of in-sample Sharpe that survived out of sample.

        Below roughly half is the standard red flag for a strategy fitted to its
        own backtest.
        """
        inside, outside = self.in_sample_sharpe, self.out_of_sample_sharpe
        if not inside or inside <= 0 or outside is None:
            return None
        return outside / inside


def walk_forward(
    equity: list[tuple[date, float]], train_years: float = 5.0, test_years: float = 1.5
) -> WalkForward:
    """Roll train/test windows across the curve rather than fitting once."""
    result = WalkForward()
    if not equity:
        return result
    start, end = equity[0][0], equity[-1][0]
    cursor = start
    while True:
        train_end = cursor + timedelta(days=int(train_years * 365))
        test_end = train_end + timedelta(days=int(test_years * 365))
        if test_end > end:
            break
        inside = [(d, v) for d, v in equity if cursor <= d <= train_end]
        outside = [(d, v) for d, v in equity if train_end < d <= test_end]
        if len(inside) >= 4 and len(outside) >= 3:
            result.windows.append(
                {
                    "train": (cursor, train_end),
                    "test": (train_end, test_end),
                    "in_sample": compute_metrics(inside),
                    "out_of_sample": compute_metrics(outside),
                }
            )
        cursor = cursor + timedelta(days=int(test_years * 365))
    return result


@dataclass
class Gate:
    name: str
    passed: bool | None
    detail: str


def evaluate_gates(
    run: BacktestRun, metrics: Metrics, wf: WalkForward, benchmark: str
) -> list[Gate]:
    """The Stage 1 pass gate, stated so it cannot be moved after the fact."""
    gates: list[Gate] = []

    bench = run.benchmarks.get(benchmark)
    bench_metrics = compute_metrics(bench) if bench else None
    if bench_metrics and metrics.sharpe is not None and bench_metrics.sharpe is not None:
        gates.append(
            Gate(
                f"beats {benchmark} on risk-adjusted return",
                metrics.sharpe > bench_metrics.sharpe,
                f"Sharpe {metrics.sharpe:.2f} vs {bench_metrics.sharpe:.2f}",
            )
        )
    else:
        gates.append(Gate(f"beats {benchmark}", None, "benchmark not comparable"))

    retention = wf.retention
    if retention is not None:
        gates.append(
            Gate(
                "out-of-sample Sharpe retains at least half of in-sample",
                retention >= 0.5,
                f"{retention:.0%} retained "
                f"({wf.in_sample_sharpe:.2f} -> {wf.out_of_sample_sharpe:.2f})",
            )
        )
    else:
        gates.append(
            Gate("out-of-sample retention", None, "too few walk-forward windows")
        )

    if metrics.max_drawdown is not None:
        gates.append(
            Gate(
                "max drawdown under 35%",
                metrics.max_drawdown < 0.35,
                f"{metrics.max_drawdown:.1%}",
            )
        )
    else:
        gates.append(Gate("max drawdown", None, "not computable"))

    if metrics.sharpe is not None and metrics.sharpe > 1.5:
        gates.append(
            Gate(
                "Sharpe is plausible rather than suspicious",
                False,
                f"{metrics.sharpe:.2f} is above 1.5; the S&P 500's own long-run "
                "Sharpe is roughly 0.4-0.5, so treat this as overfitting until proven",
            )
        )

    participation = run.name_participation
    if participation is not None:
        gates.append(
            Gate(
                "individual-name selection is actually exercised",
                participation >= 0.20,
                f"single names held in {participation:.0%} of rebalances",
            )
        )

    return gates


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report(run: BacktestRun, wf: WalkForward | None = None) -> str:
    metrics = compute_metrics(run.equity)
    benchmark = config.get("universe.benchmark.primary")
    wf = wf or walk_forward(run.equity)

    out = [
        "HYPOTHETICAL BACKTEST - NOT ACTUAL TRADING RESULTS",
        "=" * 92,
        f"  {run.start} to {run.end}   {len(run.rebalance_equity)} rebalance(s), "
        f"{metrics.periods} daily marks over {metrics.years:.1f} years",
        f"  {run.trades} trade(s)   ${run.costs_paid:,.2f} in modelled costs "
        f"(slippage {config.get('portfolio.execution.assumed_slippage_bps')}bps)",
        "",
    ]
    if not run.equity:
        out.append("  no equity curve produced")
        for note in run.notes:
            out.append(f"  note: {note}")
        return "\n".join(out)

    out.append(f"  strategy   ${run.equity[0][1]:,.0f} -> ${run.equity[-1][1]:,.0f}")
    out.append(f"             {metrics.label()}")
    strategy_returns = [
        (run.equity[i][1] / run.equity[i - 1][1]) - 1 for i in range(1, len(run.equity))
    ]
    for name, series in run.benchmarks.items():
        bench_metrics = compute_metrics(series)
        out.append(f"  {name:10} ${series[0][1]:,.0f} -> ${series[-1][1]:,.0f}")
        out.append(f"             {bench_metrics.label()}")
        bench_returns = [
            (series[i][1] / series[i - 1][1]) - 1 for i in range(1, len(series))
        ]
        alpha, beta = regress(strategy_returns, bench_returns)
        if alpha is not None:
            out.append(
                f"             alpha {alpha:+.2%}/period   beta {beta:.2f}"
            )

    participation = run.name_participation
    out.append("")
    if participation is not None:
        out.append(
            f"  individual names held in {participation:.0%} of rebalances"
            f" ({run.name_periods}/{run.total_periods})"
        )
        if participation < 0.20:
            out.append(
                "  With selection this rare the result is mostly the sleeve allocation,"
            )
            out.append(
                "  so it says little about the analysis layers above it."
            )
    if run.names_held:
        top = sorted(run.names_held.items(), key=lambda kv: -kv[1])[:8]
        out.append("  most-held names: " + ", ".join(f"{t} x{n}" for t, n in top))

    out.append("")
    out.append("  walk-forward (rolling train/test, never fitted once over everything)")
    if not wf.windows:
        out.append("    too little history for a walk-forward split")
    for window in wf.windows:
        train, test = window["train"], window["test"]
        out.append(
            f"    train {train[0]}..{train[1]}  Sharpe "
            f"{_fmt(window['in_sample'].sharpe)}   "
            f"test {test[0]}..{test[1]}  Sharpe {_fmt(window['out_of_sample'].sharpe)}"
        )
    if wf.retention is not None:
        out.append(
            f"    in-sample {wf.in_sample_sharpe:.2f} vs out-of-sample "
            f"{wf.out_of_sample_sharpe:.2f} ({wf.retention:.0%} retained)"
        )

    out.append("")
    out.append("  STAGE 1 GATE")
    gates = evaluate_gates(run, metrics, wf, benchmark)
    for gate in gates:
        mark = "pass" if gate.passed else ("  --" if gate.passed is None else "FAIL")
        out.append(f"    [{mark}] {gate.name}: {gate.detail}")
    decided = [g for g in gates if g.passed is not None]
    if decided and all(g.passed for g in decided):
        out.append("    -> gate cleared on the checks that could be evaluated")
    else:
        out.append(
            "    -> gate NOT cleared. The strategy goes back for revision rather"
        )
        out.append("       than forward with a caveat.")

    out.append("")
    out.append("  SURVIVORSHIP: the candidate list was written in 2026 and therefore")
    out.append("  contains survivors. The semiconductor core could be rebuilt from")
    out.append("  classification codes and would be unbiased; the hyperscaler, power and")
    out.append("  software segments are curated and this result is biased upward by their")
    out.append("  inclusion. Treat it as an upper bound, not an estimate.")
    for note in run.notes:
        out.append(f"  note: {note}")
    return "\n".join(out)


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Historical validation of the strategy")
    parser.add_argument("--start", default="2016-02-20")
    parser.add_argument("--end", default=date.today().isoformat())
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--capital", type=float)
    parser.add_argument("--save", action="store_true")
    parser.add_argument(
        "--mode",
        default="portfolio",
        choices=("portfolio", "selection"),
        help="portfolio runs the real sleeves; selection isolates the stock picks",
    )
    args = parser.parse_args()

    run = simulate(
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        tickers=args.tickers,
        initial=args.capital,
        mode=args.mode,
    )
    text = report(run)
    print(text)
    if args.save:
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        suffix = "" if args.mode == "portfolio" else f"_{args.mode}"
        path = RESULTS_DIR / f"{args.start}_{args.end}{suffix}.txt"
        path.write_text(text, encoding="utf-8")
        print(f"\nwritten to {path}")


if __name__ == "__main__":
    main()
