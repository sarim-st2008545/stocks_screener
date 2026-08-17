"""Portfolio construction, sizing, and the ledger.

Turns decisions into proposals sized against a real wallet, and records what was
bought, why, and what would falsify the reason.

The ledger is the part no commercial screener can offer. Recording the thesis at
entry and the condition that would disprove it means a later review can check the
*reasoning* rather than only the outcome — and a thesis breach can be flagged
mechanically instead of being rationalised away.

**On Kelly sizing.** The configuration asks for fractional Kelly, and Kelly needs
a win probability and a payoff ratio. This system produces neither: a composite
percentile is a *ranking*, not a probability, and converting one into the other
would invent precision that does not exist. So sizing uses a bounded score tilt
around equal weight, calibrated to be no more aggressive than quarter-Kelly would
be, and says so. See `size_positions`.

Nothing here places an order. Every output is a proposal for a human to approve.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field, asdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from src import config, signals as signals_mod

LEDGER_PATH = config.DATA_DIR / "portfolio" / "ledger.json"


class Sleeve:
    CORE = "core_market"
    SATELLITE = "satellite_ai_infra"
    GOLD = "gold"
    CASH = "cash"


class Act:
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
    NO_ACTION = "NO ACTION"


# ---------------------------------------------------------------------------
# Ledger
# ---------------------------------------------------------------------------


@dataclass
class Position:
    ticker: str
    sleeve: str
    shares: float
    cost_basis: float  # total paid, not per share
    entry_date: str
    # The two fields that make this worth keeping rather than buying.
    thesis: str = ""
    falsification: str = ""
    segment: str = ""

    @property
    def average_price(self) -> float | None:
        return self.cost_basis / self.shares if self.shares else None

    def value_at(self, price: float | None) -> float | None:
        return None if price is None else self.shares * price

    def unrealised(self, price: float | None) -> float | None:
        value = self.value_at(price)
        return None if value is None else value - self.cost_basis


@dataclass
class Transaction:
    when: str
    action: str
    ticker: str
    sleeve: str
    shares: float
    amount: float
    reasoning: str = ""
    accepted: bool = True
    # A record of overrides is how you find out whether the system or the human
    # is the weaker link.
    rejection_reason: str = ""


@dataclass
class Ledger:
    wallet_size: float
    cash: float
    positions: list[Position] = field(default_factory=list)
    transactions: list[Transaction] = field(default_factory=list)
    opened: str = ""

    # -- state --------------------------------------------------------------

    def position(self, ticker: str) -> Position | None:
        return next((p for p in self.positions if p.ticker == ticker.upper()), None)

    def holdings_value(self, prices_by_ticker: dict[str, float]) -> float:
        return sum(
            p.value_at(prices_by_ticker.get(p.ticker)) or p.cost_basis for p in self.positions
        )

    def total_value(self, prices_by_ticker: dict[str, float]) -> float:
        return self.cash + self.holdings_value(prices_by_ticker)

    def sleeve_value(self, sleeve: str, prices_by_ticker: dict[str, float]) -> float:
        if sleeve == Sleeve.CASH:
            return self.cash
        return sum(
            p.value_at(prices_by_ticker.get(p.ticker)) or p.cost_basis
            for p in self.positions
            if p.sleeve == sleeve
        )

    def sleeve_weights(self, prices_by_ticker: dict[str, float]) -> dict[str, float]:
        total = self.total_value(prices_by_ticker)
        if total <= 0:
            return {}
        return {
            sleeve: self.sleeve_value(sleeve, prices_by_ticker) / total
            for sleeve in (Sleeve.CORE, Sleeve.SATELLITE, Sleeve.GOLD, Sleeve.CASH)
        }

    # -- mutation -----------------------------------------------------------

    def apply(self, proposal: "Proposal", price: float, when: date | None = None) -> None:
        """Record an accepted trade. Called only after human approval."""
        when = when or date.today()
        if proposal.action == Act.BUY:
            cost = proposal.amount
            if cost > self.cash + 1e-9:
                raise ValueError(
                    f"insufficient cash: {cost:,.2f} needed, {self.cash:,.2f} available"
                )
            existing = self.position(proposal.ticker)
            if existing is None:
                self.positions.append(
                    Position(
                        ticker=proposal.ticker,
                        sleeve=proposal.sleeve,
                        shares=proposal.shares,
                        cost_basis=cost,
                        entry_date=when.isoformat(),
                        thesis=proposal.thesis,
                        falsification=proposal.falsification,
                        segment=proposal.segment,
                    )
                )
            else:
                existing.shares += proposal.shares
                existing.cost_basis += cost
            self.cash -= cost
        elif proposal.action == Act.SELL:
            existing = self.position(proposal.ticker)
            if existing is None:
                raise ValueError(f"cannot sell {proposal.ticker}: not held")
            shares = min(proposal.shares, existing.shares)
            proceeds = shares * price
            # Cost basis is reduced proportionally, so a partial sale leaves the
            # remaining basis intact rather than resetting it.
            existing.cost_basis *= max(0.0, 1 - shares / existing.shares)
            existing.shares -= shares
            self.cash += proceeds
            if existing.shares <= 1e-9:
                self.positions.remove(existing)

        self.transactions.append(
            Transaction(
                when=when.isoformat(),
                action=proposal.action,
                ticker=proposal.ticker,
                sleeve=proposal.sleeve,
                shares=proposal.shares,
                amount=proposal.amount,
                reasoning=proposal.reasoning,
            )
        )

    def record_rejection(self, proposal: "Proposal", reason: str, when: date | None = None) -> None:
        self.transactions.append(
            Transaction(
                when=(when or date.today()).isoformat(),
                action=proposal.action,
                ticker=proposal.ticker,
                sleeve=proposal.sleeve,
                shares=proposal.shares,
                amount=proposal.amount,
                reasoning=proposal.reasoning,
                accepted=False,
                rejection_reason=reason,
            )
        )

    # -- persistence --------------------------------------------------------

    def save(self, path: Path = LEDGER_PATH) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "wallet_size": self.wallet_size,
                    "cash": self.cash,
                    "opened": self.opened,
                    "positions": [asdict(p) for p in self.positions],
                    "transactions": [asdict(t) for t in self.transactions],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def load(path: Path = LEDGER_PATH) -> "Ledger":
        path = Path(path)
        if not path.exists():
            size = config.get("portfolio.wallet.size")
            return Ledger(wallet_size=size, cash=size, opened=date.today().isoformat())
        payload = json.loads(path.read_text(encoding="utf-8"))
        return Ledger(
            wallet_size=payload["wallet_size"],
            cash=payload["cash"],
            opened=payload.get("opened", ""),
            positions=[Position(**p) for p in payload.get("positions", [])],
            transactions=[Transaction(**t) for t in payload.get("transactions", [])],
        )


# ---------------------------------------------------------------------------
# Proposals
# ---------------------------------------------------------------------------


@dataclass
class Proposal:
    action: str
    ticker: str
    sleeve: str
    amount: float
    shares: float
    price: float | None = None
    reasoning: str = ""
    thesis: str = ""
    falsification: str = ""
    segment: str = ""
    warnings: list[str] = field(default_factory=list)

    def label(self) -> str:
        if self.action == Act.NO_ACTION:
            return f"NO ACTION  {self.reasoning}"
        shares = f"{self.shares:,.4f}".rstrip("0").rstrip(".")
        return (
            f"{self.action:4} {self.ticker:6} {self.sleeve:20} "
            f"${self.amount:,.2f}  ({shares} shares"
            + (f" @ ${self.price:,.2f})" if self.price else ")")
        )


def size_positions(
    candidates: list[tuple[str, float, str]],
    sleeve_budget: float,
    portfolio_total: float,
) -> dict[str, float]:
    """Dollar allocation per name, score-tilted and capped.

    Not literal Kelly. Kelly requires a win probability and a payoff ratio, and a
    composite percentile is a ranking rather than a probability — converting one
    to the other would manufacture precision. Instead each name gets equal weight
    tilted modestly by its score, then every position limit is applied.

    The tilt is bounded so the best-ranked name receives at most twice the
    weakest's, which keeps the spread no wider than quarter-Kelly would produce
    from any plausible edge estimate. Conservative by construction, because the
    inputs do not support anything sharper.
    """
    if not candidates or sleeve_budget <= 0:
        return {}

    max_name = config.get("portfolio.limits.max_single_name_pct_of_portfolio")
    max_segment = config.get("portfolio.limits.max_segment_pct_of_portfolio")
    minimum = config.get("portfolio.limits.min_position_usd")

    scores = [max(score, 1.0) for _, score, _ in candidates]
    lowest, highest = min(scores), max(scores)
    spread = highest - lowest

    weights: dict[str, float] = {}
    for (ticker, score, _segment), value in zip(candidates, scores):
        # Tilt between 1.0 and 2.0 of the base weight.
        tilt = 1.0 if spread <= 0 else 1.0 + (value - lowest) / spread
        weights[ticker] = tilt

    total_tilt = sum(weights.values())
    allocations = {t: sleeve_budget * w / total_tilt for t, w in weights.items()}

    # Single-name cap, measured against the whole portfolio rather than the sleeve.
    name_cap = max_name * portfolio_total
    for ticker in list(allocations):
        allocations[ticker] = min(allocations[ticker], name_cap)

    # Segment cap: NVDA + AMD + TSM is one bet on AI silicon wearing three names.
    segment_cap = max_segment * portfolio_total
    by_segment: dict[str, list[str]] = {}
    for ticker, _score, segment in candidates:
        by_segment.setdefault(segment, []).append(ticker)
    for segment, tickers in by_segment.items():
        exposure = sum(allocations.get(t, 0.0) for t in tickers)
        if exposure > segment_cap and exposure > 0:
            scale = segment_cap / exposure
            for ticker in tickers:
                allocations[ticker] *= scale

    # Drop anything too small to be worth holding.
    return {t: a for t, a in allocations.items() if a >= minimum}


@dataclass
class Plan:
    as_of: date
    proposals: list[Proposal] = field(default_factory=list)
    sleeve_targets: dict[str, float] = field(default_factory=dict)
    sleeve_current: dict[str, float] = field(default_factory=dict)
    drift: dict[str, float] = field(default_factory=dict)
    breaches: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def has_actions(self) -> bool:
        return any(p.action in (Act.BUY, Act.SELL) for p in self.proposals)


def build_plan(
    signal_set: signals_mod.SignalSet,
    ledger: Ledger,
    prices_by_ticker: dict[str, float],
    segments: dict[str, str] | None = None,
    contribution: float = 0.0,
    as_of: date | None = None,
) -> Plan:
    """Propose trades from decisions, sleeve drift, and a wallet."""
    as_of = as_of or date.today()
    segments = segments or {}

    targets = {
        sleeve: config.get(f"portfolio.sleeves.{sleeve}.target_pct")
        for sleeve in (Sleeve.CORE, Sleeve.SATELLITE, Sleeve.GOLD, Sleeve.CASH)
    }
    band = config.get("portfolio.rebalancing.band_pct")
    etf_share = config.get("portfolio.sleeves.satellite_ai_infra.etf_share")
    max_names = config.get("portfolio.sleeves.satellite_ai_infra.max_individual_names")

    total = ledger.total_value(prices_by_ticker) + contribution
    current = ledger.sleeve_weights(prices_by_ticker)
    drift = {s: current.get(s, 0.0) - targets[s] for s in targets}

    plan = Plan(
        as_of=as_of,
        sleeve_targets=targets,
        sleeve_current=current,
        drift=drift,
    )

    # -- thesis breaches on existing holdings -------------------------------
    for position in ledger.positions:
        signal = next(
            (s for s in signal_set.signals if s.ticker == position.ticker), None
        )
        if signal is None:
            continue
        if signal.decision in (signals_mod.Decision.EXIT, signals_mod.Decision.AVOID):
            plan.breaches.append(
                f"{position.ticker}: decision is {signal.decision} - "
                f"recorded thesis was \"{position.thesis or 'not recorded'}\""
            )
            price = prices_by_ticker.get(position.ticker)
            plan.proposals.append(
                Proposal(
                    action=Act.SELL,
                    ticker=position.ticker,
                    sleeve=position.sleeve,
                    amount=(position.value_at(price) or position.cost_basis),
                    shares=position.shares,
                    price=price,
                    reasoning=f"{signal.decision}: {'; '.join(signal.contradictions[:2])}",
                )
            )
        elif signal.decision == signals_mod.Decision.TRIM:
            price = prices_by_ticker.get(position.ticker)
            half = position.shares / 2
            plan.proposals.append(
                Proposal(
                    action=Act.SELL,
                    ticker=position.ticker,
                    sleeve=position.sleeve,
                    amount=(half * price) if price else position.cost_basis / 2,
                    shares=half,
                    price=price,
                    reasoning=f"TRIM: {'; '.join(signal.contradictions[:2])}",
                )
            )

    # -- satellite individual names -----------------------------------------
    buys = [s for s in signal_set.buys() if s.composite is not None]
    buys.sort(key=lambda s: -(s.composite or 0))
    buys = buys[:max_names]

    satellite_budget = targets[Sleeve.SATELLITE] * total
    names_budget = satellite_budget * (1 - etf_share)

    if buys:
        candidates = [
            (s.ticker, s.composite or 0.0, segments.get(s.ticker, "unknown")) for s in buys
        ]
        allocations = size_positions(candidates, names_budget, total)
        for signal in buys:
            amount = allocations.get(signal.ticker)
            if amount is None:
                plan.notes.append(
                    f"{signal.ticker}: {signal.decision} but the sized position falls below "
                    f"the ${config.get('portfolio.limits.min_position_usd'):,.0f} minimum"
                )
                continue
            price = prices_by_ticker.get(signal.ticker)
            warnings: list[str] = []
            if price and price > amount:
                # At this account size a whole share is often unaffordable, which
                # makes fractional support a prerequisite rather than a nicety.
                warnings.append(
                    f"one share costs ${price:,.2f} against a ${amount:,.2f} position - "
                    "requires fractional shares"
                )
            plan.proposals.append(
                Proposal(
                    action=Act.BUY,
                    ticker=signal.ticker,
                    sleeve=Sleeve.SATELLITE,
                    amount=amount,
                    shares=(amount / price) if price else 0.0,
                    price=price,
                    reasoning=f"{signal.decision}: {'; '.join(signal.evidence[:2])}",
                    thesis="; ".join(signal.evidence[:3]),
                    falsification="; ".join(signal.falsification[:2]),
                    segment=segments.get(signal.ticker, ""),
                    warnings=warnings,
                )
            )
    else:
        target_sleeve = config.get("portfolio.no_qualifying_buy.direct_contributions_to")
        required = config.get("rules.valuation.margin_of_safety")
        plan.proposals.append(
            Proposal(
                action=Act.NO_ACTION,
                ticker="-",
                sleeve=Sleeve.SATELLITE,
                amount=0.0,
                shares=0.0,
                reasoning=(
                    f"no name clears the {required:.0%} margin of safety with an "
                    f"acceptable quality trend; direct contributions to {target_sleeve}"
                ),
            )
        )

    # -- sleeve rebalancing --------------------------------------------------
    for sleeve, gap in drift.items():
        if sleeve == Sleeve.CASH or abs(gap) < band:
            continue
        instruments = config.get(f"portfolio.sleeves.{sleeve}.instruments", None)
        if sleeve == Sleeve.SATELLITE:
            instruments = config.get("portfolio.sleeves.satellite_ai_infra.etf_instruments")
            # Filling the sleeve with a sector ETF is not the same act as buying a
            # stretched individual name, and the output would look
            # self-contradictory without saying so. Sleeve allocation is a
            # strategic decision about how much of this theme to hold; the margin
            # of safety governs which single companies to pick inside it.
            plan.notes.append(
                "the sector ETF still fills the satellite sleeve even with no "
                "individual-name buys: sleeve size is a strategic allocation, while "
                "the margin of safety governs single-stock selection"
            )
        if not instruments:
            continue
        amount = abs(gap) * total
        ticker = instruments[0]
        price = prices_by_ticker.get(ticker)
        plan.proposals.append(
            Proposal(
                action=Act.BUY if gap < 0 else Act.SELL,
                ticker=ticker,
                sleeve=sleeve,
                amount=amount,
                shares=(amount / price) if price else 0.0,
                price=price,
                reasoning=(
                    f"{sleeve} is {abs(gap):.1%} "
                    f"{'below' if gap < 0 else 'above'} its {targets[sleeve]:.0%} target, "
                    f"outside the {band:.0%} band"
                ),
            )
        )

    if not plan.proposals:
        plan.notes.append("every sleeve is inside its band and no decision requires action")
    return plan


def report(plan: Plan, ledger: Ledger, prices_by_ticker: dict[str, float]) -> str:
    total = ledger.total_value(prices_by_ticker)
    out = [
        f"Portfolio plan as of {plan.as_of}",
        "=" * 88,
        f"  total value ${total:,.2f}   cash ${ledger.cash:,.2f}   "
        f"{len(ledger.positions)} position(s)",
        "",
        "  sleeve            target   current    drift",
    ]
    for sleeve, target in plan.sleeve_targets.items():
        now = plan.sleeve_current.get(sleeve, 0.0)
        gap = plan.drift.get(sleeve, 0.0)
        flag = "  <- outside band" if abs(gap) >= config.get(
            "portfolio.rebalancing.band_pct"
        ) else ""
        out.append(f"  {sleeve:18}{target:6.0%}   {now:6.0%}   {gap:+6.1%}{flag}")

    if ledger.positions:
        out.append("")
        out.append("  holdings")
        for position in ledger.positions:
            price = prices_by_ticker.get(position.ticker)
            value = position.value_at(price)
            pnl = position.unrealised(price)
            out.append(
                f"    {position.ticker:6} {position.shares:10.4f} sh   "
                f"cost ${position.cost_basis:,.2f}   "
                f"value {'n/a' if value is None else f'${value:,.2f}'}   "
                f"P&L {'n/a' if pnl is None else f'${pnl:+,.2f}'}"
            )
            if position.thesis:
                out.append(f"           thesis: {position.thesis}")
            if position.falsification:
                out.append(f"           falsifies if: {position.falsification}")

    if plan.breaches:
        out.append("")
        out.append("  THESIS BREACHES")
        for breach in plan.breaches:
            out.append(f"    {breach}")

    out.append("")
    out.append("  proposals")
    for proposal in plan.proposals:
        out.append(f"    {proposal.label()}")
        if proposal.reasoning and proposal.action != Act.NO_ACTION:
            out.append(f"         why: {proposal.reasoning}")
        for warning in proposal.warnings:
            out.append(f"         warning: {warning}")
    for note in plan.notes:
        out.append(f"    note: {note}")

    out.append("")
    out.append("  Nothing above has been executed. Each proposal needs your approval,")
    out.append("  and rejections are logged so the record shows who overrode what.")
    broker = config.get("portfolio.execution.broker")
    if not broker:
        out.append(
            "  No broker configured, so fractional shares are assumed available."
        )
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Portfolio plan from current decisions")
    parser.add_argument("--as-of", help="ISO date (default today)")
    parser.add_argument("--contribution", type=float, default=0.0)
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--ledger", default=str(LEDGER_PATH))
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    from src import prices, universe

    signal_set = signals_mod.build(as_of=as_of, tickers=args.tickers)
    ledger = Ledger.load(Path(args.ledger))

    wanted = {s.ticker for s in signal_set.signals}
    wanted |= {p.ticker for p in ledger.positions}
    for sleeve in (Sleeve.CORE, Sleeve.GOLD):
        wanted |= set(config.get(f"portfolio.sleeves.{sleeve}.instruments"))
    wanted |= set(config.get("portfolio.sleeves.satellite_ai_infra.etf_instruments"))

    prices_by_ticker: dict[str, float] = {}
    for ticker in wanted:
        history = prices.load(ticker)
        if history is not None:
            price = history.raw_close(as_of)
            if price:
                prices_by_ticker[ticker] = price

    segments = {c.ticker: c.segment for c in universe.candidates()}
    plan = build_plan(
        signal_set,
        ledger,
        prices_by_ticker,
        segments=segments,
        contribution=args.contribution,
        as_of=as_of,
    )
    print(report(plan, ledger, prices_by_ticker))


if __name__ == "__main__":
    main()
