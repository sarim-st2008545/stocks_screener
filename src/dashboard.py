"""Local dashboard — one page showing everything the system currently knows.

Generates a self-contained HTML file from the current state: the universe with
scores and decisions, the portfolio with its sleeves and recorded theses, the
proposals awaiting approval, and whatever validation has actually run.

Static rather than a server, deliberately. A page that regenerates from saved data
cannot drift from what the modules computed, has no runtime to leave listening on a
port, and can be opened months later to see exactly what the system said at the
time. Approval stays in the terminal, where it leaves a logged decision:

    python -m src.dashboard --open
    python -m src.portfolio --approve 1 --ledger data/portfolio/ledger.json

Every number rendered here is produced upstream. This module formats; it does not
compute, so nothing can appear on the page that the analysis did not produce.
"""

from __future__ import annotations

import argparse
import html
import json
import webbrowser
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from src import config, portfolio as portfolio_mod, prices, signals as signals_mod, universe

OUTPUT_PATH = config.DATA_DIR / "dashboard.html"


def _latest(directory: Path) -> dict[str, Any] | None:
    if not directory.exists():
        return None
    files = sorted(directory.glob("*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def _pct(value: float | None, places: int = 1) -> str:
    return "—" if value is None else f"{value * 100:.{places}f}%"


def _num(value: float | None, places: int = 1) -> str:
    return "—" if value is None else f"{value:,.{places}f}"


def _money(value: float | None) -> str:
    return "—" if value is None else f"${value:,.2f}"


DECISION_CLASS = {
    "STRONG BUY": "buy",
    "BUY": "buy",
    "ADD": "buy",
    "HOLD": "hold",
    "TRIM": "warn",
    "EXIT": "bad",
    "AVOID": "bad",
    "NO DATA": "muted",
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

STYLE = """
:root{--bg:#FBFBFC;--panel:#F1F4F6;--panel2:#E7ECEF;--line:#D5DCE1;--line2:#B6C1C8;
--ink:#14181D;--ink2:#3B444E;--muted:#616D78;--accent:#12595F;--accent-soft:#DCEAEA;
--good:#2C6E4A;--good-soft:#DDEBE1;--warn:#8A6412;--warn-soft:#F0E7D0;
--bad:#9C3A31;--bad-soft:#F1DEDB;
--sans:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
--serif:ui-serif,Georgia,"Times New Roman",serif;
--mono:ui-monospace,"SF Mono","Cascadia Mono",Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
--bg:#101317;--panel:#171C21;--panel2:#1F262C;--line:#2B333A;--line2:#3D4750;
--ink:#E6EAEE;--ink2:#BDC6CE;--muted:#8B96A1;--accent:#5FB6BB;--accent-soft:#143133;
--good:#7FC69B;--good-soft:#16291F;--warn:#D6AC55;--warn-soft:#2B2415;
--bad:#DE8C82;--bad-soft:#2C1A18}}
:root[data-theme="dark"]{
--bg:#101317;--panel:#171C21;--panel2:#1F262C;--line:#2B333A;--line2:#3D4750;
--ink:#E6EAEE;--ink2:#BDC6CE;--muted:#8B96A1;--accent:#5FB6BB;--accent-soft:#143133;
--good:#7FC69B;--good-soft:#16291F;--warn:#D6AC55;--warn-soft:#2B2415;
--bad:#DE8C82;--bad-soft:#2C1A18}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:1120px;margin:0 auto;padding:44px 24px 88px;display:flex;
flex-direction:column;gap:44px}
h1{font-family:var(--serif);font-size:clamp(26px,4vw,38px);margin:0;
letter-spacing:-.015em;text-wrap:balance}
h2{font-family:var(--serif);font-size:22px;margin:0 0 4px;padding-bottom:7px;
border-bottom:2px solid var(--line2);text-wrap:balance}
h3{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
color:var(--muted);margin:0}
p{margin:0;max-width:70ch}
.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.14em;
text-transform:uppercase;color:var(--accent)}
.meta{font-family:var(--mono);font-size:12px;color:var(--muted);display:flex;
flex-wrap:wrap;gap:16px;padding-top:6px;border-top:1px solid var(--line)}
section{display:flex;flex-direction:column;gap:14px}
.note{color:var(--ink2);font-size:14px}
.scroll{overflow-x:auto;border:1px solid var(--line);background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th{text-align:left;font-size:10.5px;font-weight:700;letter-spacing:.07em;
text-transform:uppercase;color:var(--muted);padding:8px 11px;
border-bottom:1px solid var(--line2);white-space:nowrap}
td{padding:6px 11px;border-bottom:1px solid var(--line);vertical-align:baseline}
tr:last-child td{border-bottom:none}
.n{font-family:var(--mono);font-variant-numeric:tabular-nums;text-align:right;
white-space:nowrap}
.t{font-family:var(--mono);font-size:13px}
.src{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.pill{font-family:var(--mono);font-size:10px;letter-spacing:.05em;
text-transform:uppercase;padding:2px 6px;border-radius:2px;display:inline-block;
min-width:74px;text-align:center}
.pill.buy{background:var(--good-soft);color:var(--good)}
.pill.hold{background:var(--panel2);color:var(--ink2)}
.pill.warn{background:var(--warn-soft);color:var(--warn)}
.pill.bad{background:var(--bad-soft);color:var(--bad)}
.pill.muted{background:var(--panel2);color:var(--muted)}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
gap:1px;background:var(--line);border:1px solid var(--line)}
.card{background:var(--panel);padding:13px 15px;display:flex;flex-direction:column;
gap:2px}
.card .k{font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;
text-transform:uppercase;color:var(--muted)}
.card .v{font-family:var(--mono);font-size:20px;font-variant-numeric:tabular-nums;
letter-spacing:-.02em}
.callout{background:var(--panel);border:1px solid var(--line);
border-left:3px solid var(--accent);padding:14px 16px;display:flex;
flex-direction:column;gap:7px}
.callout.bad{border-left-color:var(--bad)}
.callout h3{color:var(--accent)}
.callout.bad h3{color:var(--bad)}
.bar{height:7px;background:var(--panel2);position:relative;min-width:90px}
.bar span{position:absolute;inset:0 auto 0 0;background:var(--accent)}
footer{border-top:1px solid var(--line);padding-top:16px;color:var(--muted);
font-size:13px;display:flex;flex-direction:column;gap:6px}
@media(max-width:620px){.wrap{padding:28px 15px 60px;gap:34px}}
"""


@dataclass
class Snapshot:
    """Everything the page renders, gathered once."""

    as_of: date
    signals: list[dict[str, Any]]
    scores: dict[str, dict[str, Any]]
    ledger: portfolio_mod.Ledger
    plan: portfolio_mod.Plan | None
    prices_by_ticker: dict[str, float]
    backtest_text: str | None = None


def gather(
    as_of: date | None = None,
    signal_set: signals_mod.SignalSet | None = None,
    ledger: portfolio_mod.Ledger | None = None,
) -> Snapshot:
    as_of = as_of or date.today()
    board = _latest(config.DATA_DIR / "pit" / "scores") or {}
    scores = {e["ticker"]: e for e in board.get("scores", [])}

    ledger = ledger or portfolio_mod.Ledger.load()
    signal_set = signal_set or signals_mod.SignalSet(as_of, [])

    wanted = {s.ticker for s in signal_set.signals} | {p.ticker for p in ledger.positions}
    for sleeve in ("core_market", "gold"):
        wanted |= set(config.get(f"portfolio.sleeves.{sleeve}.instruments"))
    wanted |= set(config.get("portfolio.sleeves.satellite_ai_infra.etf_instruments"))

    prices_by_ticker: dict[str, float] = {}
    for ticker in wanted:
        history = prices.load(ticker)
        if history is not None:
            quote = history.raw_close(as_of)
            if quote:
                prices_by_ticker[ticker] = quote

    plan = None
    if signal_set.signals:
        segments = {c.ticker: c.segment for c in universe.candidates()}
        plan = portfolio_mod.build_plan(
            signal_set, ledger, prices_by_ticker, segments=segments, as_of=as_of
        )

    latest_backtest = None
    results = config.DATA_DIR / "pit" / "backtests"
    if results.exists():
        texts = sorted(results.glob("*.txt"))
        if texts:
            latest_backtest = texts[-1].read_text(encoding="utf-8")

    return Snapshot(
        as_of=as_of,
        signals=[
            {
                "ticker": s.ticker,
                "decision": s.decision,
                "quality": s.quality_trend,
                "valuation": s.valuation_band,
                "cycle": s.cycle_position,
                "composite": s.composite,
                "price": s.price,
                "implied_growth": s.implied_growth,
                "confidence": s.confidence,
                "evidence": s.evidence,
                "contradictions": s.contradictions,
                "falsification": s.falsification,
            }
            for s in signal_set.ordered()
        ],
        scores=scores,
        ledger=ledger,
        plan=plan,
        prices_by_ticker=prices_by_ticker,
        backtest_text=latest_backtest,
    )


def render(snapshot: Snapshot) -> str:
    ledger = snapshot.ledger
    total = ledger.total_value(snapshot.prices_by_ticker)
    buys = [s for s in snapshot.signals if s["decision"] in ("STRONG BUY", "BUY", "ADD")]

    parts: list[str] = [
        "<title>AI Infra Portfolio Desk</title>",
        f"<style>{STYLE}</style>",
        '<div class="wrap">',
        "<header>",
        f'<div class="eyebrow">Research desk &middot; {snapshot.as_of}</div>',
        "<h1>AI infrastructure portfolio</h1>",
        '<div class="meta">'
        f"<span>{len(snapshot.signals)} names analysed</span>"
        f"<span>{len(buys)} qualifying buy(s)</span>"
        f"<span>{len(ledger.positions)} position(s)</span>"
        f"<span>{_money(total)} total</span>"
        "</div>",
        "</header>",
    ]

    # -- headline -----------------------------------------------------------
    if not buys and snapshot.signals:
        required = config.get("rules.valuation.margin_of_safety")
        target = config.get("portfolio.no_qualifying_buy.direct_contributions_to")
        parts.append(
            '<div class="callout"><h3>No qualifying buys</h3>'
            f'<p class="note">Nothing clears the {required:.0%} margin of safety with an '
            "acceptable quality trend, so there is no individual-name action. "
            f"Contributions go to the {_esc(target)} sleeve instead. Long stretches with "
            "no stock purchases are an expected output of this discipline, not a fault."
            "</p></div>"
        )

    # -- portfolio ----------------------------------------------------------
    parts.append("<section><h2>Portfolio</h2>")
    parts.append('<div class="cards">')
    for label, value in (
        ("Total value", _money(total)),
        ("Cash", _money(ledger.cash)),
        ("Positions", str(len(ledger.positions))),
        ("Opened", ledger.opened or "—"),
    ):
        parts.append(f'<div class="card"><span class="k">{label}</span>'
                     f'<span class="v">{_esc(value)}</span></div>')
    parts.append("</div>")

    weights = ledger.sleeve_weights(snapshot.prices_by_ticker)
    band = config.get("portfolio.rebalancing.band_pct")
    parts.append('<div class="scroll"><table><thead><tr>'
                 "<th>Sleeve</th><th>Target</th><th>Current</th><th>Drift</th>"
                 "<th></th></tr></thead><tbody>")
    for sleeve in ("core_market", "satellite_ai_infra", "gold", "cash"):
        target = config.get(f"portfolio.sleeves.{sleeve}.target_pct")
        now = weights.get(sleeve, 0.0)
        drift = now - target
        flag = ('<span class="pill warn">outside band</span>'
                if abs(drift) >= band else "")
        parts.append(
            f'<tr><td class="t">{_esc(sleeve)}</td>'
            f'<td class="n">{_pct(target,0)}</td><td class="n">{_pct(now,0)}</td>'
            f'<td class="n">{drift*100:+.1f}%</td><td>{flag}</td></tr>'
        )
    parts.append("</tbody></table></div>")

    if ledger.positions:
        parts.append("<h3>Holdings, with the thesis recorded at entry</h3>")
        parts.append('<div class="scroll"><table><thead><tr>'
                     "<th>Ticker</th><th>Shares</th><th>Cost</th><th>Value</th>"
                     "<th>P&amp;L</th><th>Thesis / what would falsify it</th>"
                     "</tr></thead><tbody>")
        for position in ledger.positions:
            price = snapshot.prices_by_ticker.get(position.ticker)
            parts.append(
                f'<tr><td class="t">{_esc(position.ticker)}</td>'
                f'<td class="n">{_num(position.shares,4)}</td>'
                f'<td class="n">{_money(position.cost_basis)}</td>'
                f'<td class="n">{_money(position.value_at(price))}</td>'
                f'<td class="n">{_money(position.unrealised(price))}</td>'
                f'<td class="src">{_esc(position.thesis)}'
                + (f'<br>falsifies if: {_esc(position.falsification)}'
                   if position.falsification else "")
                + "</td></tr>"
            )
        parts.append("</tbody></table></div>")
    else:
        parts.append('<p class="note">No positions yet. Nothing has been bought.</p>')
    parts.append("</section>")

    # -- proposals ----------------------------------------------------------
    plan = snapshot.plan
    if plan is not None:
        parts.append("<section><h2>Proposals awaiting your approval</h2>")
        if plan.breaches:
            parts.append('<div class="callout bad"><h3>Thesis breaches</h3>')
            for breach in plan.breaches:
                parts.append(f'<p class="note">{_esc(breach)}</p>')
            parts.append("</div>")
        parts.append('<div class="scroll"><table><thead><tr>'
                     "<th>#</th><th>Action</th><th>Ticker</th><th>Sleeve</th>"
                     "<th>Amount</th><th>Why</th></tr></thead><tbody>")
        for index, proposal in enumerate(plan.proposals, start=1):
            cls = {"BUY": "buy", "SELL": "bad", "NO ACTION": "muted"}.get(
                proposal.action, "hold"
            )
            warnings = "".join(
                f'<br><span class="src">warning: {_esc(w)}</span>'
                for w in proposal.warnings
            )
            parts.append(
                f'<tr><td class="n">{index}</td>'
                f'<td><span class="pill {cls}">{_esc(proposal.action)}</span></td>'
                f'<td class="t">{_esc(proposal.ticker)}</td>'
                f'<td class="src">{_esc(proposal.sleeve)}</td>'
                f'<td class="n">{_money(proposal.amount) if proposal.amount else "—"}</td>'
                f'<td class="src">{_esc(proposal.reasoning)}{warnings}</td></tr>'
            )
        parts.append("</tbody></table></div>")
        for note in plan.notes:
            parts.append(f'<p class="note">{_esc(note)}</p>')
        parts.append(
            '<p class="note">Approve or reject from the terminal so the decision is '
            "logged: <code>python -m src.portfolio --approve N</code>. "
            "Rejections are recorded too, because a history of overrides is how you "
            "find out whether the system or the human is the weaker link.</p>"
        )
        parts.append("</section>")

    # -- decisions ----------------------------------------------------------
    if snapshot.signals:
        parts.append("<section><h2>Decisions</h2>")
        parts.append(
            '<p class="note">A high score is not a buy. The composite ranks quality and '
            "price within the pool; the gates can still veto on quality or cycle "
            "position, and every call carries what would disprove it.</p>"
        )
        parts.append('<div class="scroll"><table><thead><tr>'
                     "<th>Ticker</th><th>Decision</th><th>Score</th><th>Quality</th>"
                     "<th>Valuation</th><th>Cycle</th><th>Price implies</th>"
                     "<th>Evidence against</th></tr></thead><tbody>")
        for row in snapshot.signals:
            cls = DECISION_CLASS.get(row["decision"], "hold")
            against = "; ".join(row["contradictions"][:2])
            parts.append(
                f'<tr><td class="t">{_esc(row["ticker"])}</td>'
                f'<td><span class="pill {cls}">{_esc(row["decision"])}</span></td>'
                f'<td class="n">{_num(row["composite"])}</td>'
                f'<td class="src">{_esc(row["quality"])}</td>'
                f'<td class="src">{_esc(row["valuation"])}</td>'
                f'<td class="src">{_esc(row["cycle"])}</td>'
                f'<td class="n">{_pct(row["implied_growth"],0)}</td>'
                f'<td class="src">{_esc(against)}</td></tr>'
            )
        parts.append("</tbody></table></div></section>")

    # -- validation ---------------------------------------------------------
    parts.append("<section><h2>Validation</h2>")
    if snapshot.backtest_text:
        parts.append(
            '<div class="callout"><h3>Hypothetical &mdash; not actual trading results</h3>'
            '<p class="note">Backtested figures describe one historical path and are '
            "kept separate from any real record. The curated segments of the universe "
            "contain 2026 survivors, so treat the outcome as an upper bound.</p></div>"
        )
        parts.append(
            f'<div class="scroll"><pre class="src" style="padding:12px;margin:0;'
            f'white-space:pre">{_esc(snapshot.backtest_text)}</pre></div>'
        )
    else:
        parts.append(
            '<div class="callout bad"><h3>Nothing validated yet</h3>'
            '<p class="note">No backtest result has been saved, so no claim about '
            "performance can be made at all. Paper trading and live capital both come "
            "after the Stage 1 gate clears.</p></div>"
        )
    parts.append("</section>")

    parts.append(
        "<footer><p>Every figure traces to an SEC filing, a disclosed holding, or an "
        "arithmetic combination of filed numbers. Generated from saved data, so the page "
        "cannot drift from what the modules computed.</p>"
        "<p>Not investment advice. The system proposes; you decide.</p></footer>"
    )
    parts.append("</div>")
    return "\n".join(parts)


def write(
    path: Path = OUTPUT_PATH,
    as_of: date | None = None,
    signal_set: signals_mod.SignalSet | None = None,
) -> Path:
    snapshot = gather(as_of=as_of, signal_set=signal_set)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(snapshot), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the local dashboard")
    parser.add_argument("--as-of", help="ISO date (default today)")
    parser.add_argument("--out", default=str(OUTPUT_PATH))
    parser.add_argument("--tickers", nargs="*")
    parser.add_argument("--open", action="store_true", help="open in a browser")
    parser.add_argument(
        "--no-signals",
        action="store_true",
        help="render from saved data only, without rebuilding decisions",
    )
    args = parser.parse_args()

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    signal_set = None
    if not args.no_signals:
        signal_set = signals_mod.build(as_of=as_of, tickers=args.tickers)

    path = write(Path(args.out), as_of=as_of, signal_set=signal_set)
    print(f"dashboard written to {path}")
    if args.open:
        webbrowser.open(path.resolve().as_uri())


if __name__ == "__main__":
    main()
