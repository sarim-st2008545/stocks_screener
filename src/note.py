"""Per-company research note — every number, and where it came from.

Assembles everything the system can currently establish about one company into a
single readable page: the universe screens, the statements with provenance, the
quality frameworks with their individual signals, and an explicit list of what is
*not* yet computable.

That last section matters as much as the rest. A research note that silently omits
valuation reads as though valuation were considered, so unbuilt layers are named
rather than left out.

    python -m src.note NVDA
    python -m src.note MU --as-of 2024-06-30
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date
from typing import Any

from src import fundamentals as fundamentals_mod
from src import cycle as cycle_mod
from src import prices, quality, universe, valuation as valuation_mod
from src.facts import FactSet
from src.sec_client import SECClient

RULE = "=" * 78
THIN = "-" * 78


@dataclass
class Note:
    ticker: str
    as_of: date
    constituent: universe.Constituent | None
    fundamentals: fundamentals_mod.Fundamentals | None
    assessment: quality.QualityAssessment | None
    market_cap: float | None
    price: float | None
    valuation: "valuation_mod.Valuation | None" = None
    cycle: "cycle_mod.CyclePosition | None" = None
    error: str | None = None


def build(ticker: str, as_of: date | None = None, wacc: float | None = None) -> Note:
    ticker = ticker.upper()
    as_of = as_of or date.today()
    client = SECClient()

    cik = client.ticker_to_cik(ticker)
    if cik is None:
        return Note(ticker, as_of, None, None, None, None, None, "not an SEC filer")

    facts = client.company_facts(cik)
    history = prices.load(ticker)

    constituent = next((c for c in universe.candidates() if c.ticker == ticker), None)
    if constituent is not None:
        company = client.company(ticker)
        if company is not None:
            constituent.cik = cik
            constituent.company_name = company.name
            constituent.sic = company.sic
            constituent.sic_description = company.sic_description
        universe.screen(constituent, facts, history, as_of)

    f = fundamentals_mod.Fundamentals(FactSet(facts, as_of=as_of), ticker)
    shares = f.shares_outstanding
    market_cap = (
        prices.market_cap(history, shares.value, as_of)
        if history is not None and shares.present
        else None
    )
    price = history.raw_close(as_of) if history is not None else None
    valued = valuation_mod.value(f, market_cap=market_cap, price=price, as_of=as_of)
    # The valuation layer now supplies the cost of capital the ROIC spread needs.
    assessment = quality.assess(
        f, market_cap=market_cap, wacc=wacc if wacc is not None else valued.cost_of_capital.wacc
    )

    position = cycle_mod.assess(f, cyclical_segment=cycle_mod.is_cyclical(ticker))
    return Note(
        ticker, as_of, constituent, f, assessment, market_cap, price, valued, position
    )


def _money(value: float | None, currency: str = "USD") -> str:
    if value is None:
        return "n/a"
    for unit, scale in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
        if abs(value) >= scale:
            return f"{value / scale:,.2f}{unit} {currency}"
    return f"{value:,.0f} {currency}"


def _pct(value: float | None, places: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{places}f}%"


def render(note: Note) -> str:
    if note.error:
        return f"{note.ticker}: {note.error}"

    f = note.fundamentals
    a = note.assessment
    currency = f.currency
    out: list[str] = []

    # -- header -------------------------------------------------------------
    c = note.constituent
    name = (c.company_name if c and c.company_name else note.ticker) or note.ticker
    out.append(RULE)
    out.append(f"{note.ticker} - {name}")
    out.append(f"research note as of {note.as_of}   |   reporting currency {currency}")
    out.append(RULE)

    # -- eligibility --------------------------------------------------------
    out.append("")
    out.append("1. UNIVERSE ELIGIBILITY")
    out.append(THIN)
    if c is None:
        out.append("  not in the configured AI-infrastructure universe")
    else:
        out.append(f"  segment          {c.segment_label}")
        out.append(f"  thesis           {c.note}")
        out.append(f"  SIC              {c.sic} {c.sic_description or ''}")
        out.append(f"  status           {c.status}")
        out.append(f"  market cap       {_money(note.market_cap, 'USD')}   (screen: >= $2B)")
        out.append(
            f"  avg daily volume {_money(c.avg_dollar_volume, 'USD')}   (screen: >= $10M)"
        )
        out.append(f"  operating cash   {_money(c.operating_cash_flow, currency)}   (screen: > 0)")
        if c.stability_flag:
            out.append(f"  STABILITY FLAG   {c.stability_flag}")
        for failure in c.failures:
            out.append(f"  FAILS            {failure}")
        for n in c.notes:
            out.append(f"  note             {n}")

    # -- statements ---------------------------------------------------------
    out.append("")
    out.append("2. STATEMENTS  (d = derived, not read directly from a filing)")
    out.append(THIN)
    out.append(f"  {'':2}{'line item':26}{'value':>18}  source")
    for key, item in f.line_items().items():
        if item.present:
            mark = "d " if item.derived else "  "
            out.append(
                f"  {mark}{key:26}{_money(item.value, currency):>18}  {item.source[:30]}"
            )
        else:
            out.append(f"    {key:26}{'unavailable':>18}  {item.source}")

    period = f.revenue.period_end
    if period:
        out.append("")
        out.append(
            f"  income figures are trailing twelve months ending {period}"
            f" ({f.revenue.basis}); balance-sheet figures are instants"
        )

    # -- ratios -------------------------------------------------------------
    out.append("")
    out.append("3. RATIOS")
    out.append(THIN)
    groups = {
        "margins": ["gross_margin", "operating_margin", "net_margin", "fcf_margin"],
        "returns": ["return_on_equity", "return_on_assets", "roic"],
        "leverage": ["net_debt_to_ebitda", "debt_to_equity", "interest_coverage"],
        "liquidity": ["current_ratio", "quick_ratio"],
        "quality": ["fcf_conversion", "asset_turnover"],
        "capital cycle": ["capex_intensity", "rd_intensity", "inventory_days"],
    }
    ratios = f.ratios()
    for group, keys in groups.items():
        out.append(f"  {group}")
        for key in keys:
            value = ratios.get(key)
            if key in ("interest_coverage", "current_ratio", "quick_ratio",
                       "net_debt_to_ebitda", "fcf_conversion", "asset_turnover",
                       "inventory_days"):
                shown = "n/a" if value is None else f"{value:,.2f}"
            else:
                shown = _pct(value)
            out.append(f"      {key:24}{shown:>12}")

    # -- quality ------------------------------------------------------------
    out.append("")
    out.append("4. QUALITY FRAMEWORKS")
    out.append(THIN)
    out.append(f"  Piotroski F-Score (2000)        {a.piotroski.label()}")
    for s in a.piotroski.signals:
        mark = "pass" if s.passed else ("  --" if s.passed is None else "fail")
        out.append(f"      [{mark}] {s.name:32} {s.detail}")
    out.append("")
    out.append(f"  Altman (1968/1995)             {a.altman.label()}")
    out.append(f"      {a.altman.reason}")
    if a.altman.components:
        parts = "  ".join(f"{k} {v:+.3f}" for k, v in a.altman.components.items())
        out.append(f"      components: {parts}")
    if a.altman.agrees is False:
        out.append(
            "      the two variants disagree, which is itself a finding: book and"
        )
        out.append(
            "      market equity are telling different stories about this company"
        )
    out.append("")
    out.append("  Value creation (Koller et al.)")
    out.append(
        f"      ROIC {_pct(a.roic)}   WACC {_pct(a.wacc)}   spread "
        f"{'n/a' if a.roic_wacc_spread is None else f'{a.roic_wacc_spread * 100:+.1f}%'}"
    )
    if a.creates_value is not None:
        verdict = "clears its cost of capital" if a.creates_value else "does NOT clear its cost of capital"
        out.append(f"      {verdict}")
    out.append("")
    out.append("  Earnings quality")
    out.append(f"      FCF conversion {'n/a' if a.fcf_conversion is None else f'{a.fcf_conversion:.2f}'}  -  {a.fcf_assessment}")
    out.append(f"      gross margin {_pct(a.gross_margin)}  -  {a.gross_margin_trend}")
    out.append("")
    out.append("  Balance-sheet health (Moody's / S&P bands)")
    for label, verdict in a.balance_sheet.items():
        out.append(f"      {label:24}{verdict}")

    # -- cycle --------------------------------------------------------------
    cp = note.cycle
    if cp is not None:
        out.append("")
        out.append("5. CAPITAL-CYCLE POSITION")
        out.append(THIN)
        out.append(f"  position         {cp.position}")
        out.append(f"  capital profile  {cp.capex_profile}")
        repeatable = cp.earnings_repeatable
        if repeatable is not None:
            out.append(
                "  trailing earnings look "
                + ("repeatable" if repeatable else "NOT repeatable as a valuation basis")
            )
        caveat = cp.valuation_caveat
        if caveat:
            out.append(f"  CAVEAT           {caveat}")
        out.append("")
        for s_ in (cp.gross_margin, cp.operating_margin, cp.inventory_days, cp.capex_intensity):
            out.append(f"      {s_.label()}")
        for line in cp.evidence:
            out.append(f"      evidence: {line}")
        for line in cp.notes:
            out.append(f"      note: {line}")

    # -- valuation ----------------------------------------------------------
    v = note.valuation
    if v is not None:
        out.append("")
        out.append("6. VALUATION")
        out.append(THIN)
        coc = v.cost_of_capital
        out.append(f"  {coc.label()}")
        if coc.beta_adjusted is not None:
            out.append(
                f"      beta {coc.beta_raw:.2f} raw -> {coc.beta_adjusted:.2f} adjusted"
                f"   risk-free {coc.risk_free_rate:.2%}   equity premium {coc.equity_risk_premium:.2%}"
            )
        out.append(f"  {v.dcf.label()}   [{v.dcf.reliability}]")
        if v.dcf.spread is not None:
            out.append(
                f"      band is {v.dcf.spread:.0%} of the base case across"
                f" {len(v.dcf.grid)} sensitivity points"
            )
        for caveat in v.dcf.caveats:
            out.append(f"      caveat: {caveat}")
        out.append(f"  {v.verdict}")
        out.append("")
        out.append(f"  reverse DCF: {v.expectations.verdict}")
        out.append(
            "      this is the more useful reading - a conservative forward DCF sits"
        )
        out.append(
            "      below price for nearly every name in this sector, which cannot rank them"
        )
        out.append("")
        out.append("  multiples against the company's own history")
        for key, label in (
            ("pe", "P/E"),
            ("ev_ebitda", "EV/EBITDA"),
            ("ev_sales", "EV/Sales"),
            ("fcf_yield", "FCF yield"),
            ("peg", "PEG"),
        ):
            value_ = getattr(v.multiples, key)
            if value_ is None:
                out.append(f"      {label:11} n/a")
                continue
            shown = f"{value_:.2%}" if key.endswith("yield") else f"{value_:,.1f}"
            out.append(f"      {label:11} {shown:>8}   {v.multiples.versus_own_history(key)}")
        for n in v.multiples.notes + v.dcf.notes + coc.notes:
            out.append(f"      note: {n}")

    # -- data quality -------------------------------------------------------
    coverage = f.coverage()
    out.append("")
    out.append("7. DATA QUALITY")
    out.append(THIN)
    out.append(
        f"  line items resolved   {coverage['line_items_present']}/{coverage['line_items_total']}"
        f"      ratios computed  {coverage['ratios_present']}/{coverage['ratios_total']}"
    )
    if coverage["derived"]:
        out.append("  derived figures:")
        for key, source in coverage["derived"].items():
            out.append(f"      {key:24}{source[:44]}")
        out.append("      (legitimate accounting identities, labelled so they are auditable)")
    if coverage["missing_line_items"]:
        out.append(f"  unavailable: {', '.join(coverage['missing_line_items'])}")
    hidden = f.view.excluded_future_facts
    out.append(
        f"  point-in-time gate    {hidden} fact(s) hidden as not yet filed at {note.as_of}"
    )
    for n in a.notes:
        out.append(f"  note: {n}")

    # -- not yet built ------------------------------------------------------
    out.append("")
    out.append("8. NOT YET BUILT  (named so this note is not mistaken for complete)")
    out.append(THIN)
    for phase, item in (
        ("Phase 6", "composite score - percentile rank against the investable pool"),
        ("Phase 7", "smart money - 13F institutional positioning as corroboration"),
        ("Phase 8", "events - 8-K, Form 4 insider buying, earnings calendar"),
        ("Phase 9", "SIGNAL - the buy/hold/sell decision, which needs 4 and 6 first"),
        ("Phase 10", "portfolio - position sizing against your wallet and sleeves"),
        ("Phase 11", "backtest - proof any of this works before real money"),
    ):
        out.append(f"  {phase:9} {item}")
    out.append("")
    out.append("  No buy or sell recommendation exists yet: valuation alone cannot")
    out.append("  produce one. It needs the quality gates combined through the decision")
    out.append("  matrix (Phase 9) and a validated backtest (Phase 11).")
    out.append(RULE)
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser(description="Full research note for one company")
    parser.add_argument("ticker")
    parser.add_argument("--as-of", help="ISO date to analyse as of (default today)")
    parser.add_argument(
        "--wacc",
        type=float,
        help="cost of capital for the ROIC spread, until Phase 4 supplies one",
    )
    args = parser.parse_args()
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    print(render(build(args.ticker, as_of=as_of, wacc=args.wacc)))


if __name__ == "__main__":
    main()
