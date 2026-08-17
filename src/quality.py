"""Quality and financial-strength assessment.

Four published frameworks, each applied with its stated caveats rather than as a
black-box number:

- **Piotroski F-Score** (Piotroski 2000) — nine binary signals of improving
  fundamental health. Validated on value stocks, so it is a quality *input* here,
  never a pass/fail gate: fast-growing tech issues equity and invests heavily,
  and scores moderately while being excellent.
- **Altman Z-Score** (Altman 1968, 1995 revision) — distress risk, with the
  variant chosen from measured manufacturing intensity rather than a hardcoded
  list, because the original Z's asset-turnover term unfairly penalises
  asset-light designers.
- **ROIC vs WACC** (Koller et al., *Valuation*) — the core value-creation test.
  A sustained positive spread is the evidence a moat is real.
- **FCF conversion** — the earnings-quality test, echoing Piotroski's accruals
  signal.

Every signal reports pass, fail, or *not evaluable*, and the three are kept
distinct. A signal that could not be computed is not a failed signal, and
scoring it as one would penalise companies for their filers' tagging habits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src import config
from src.fundamentals import Fundamentals


@dataclass(frozen=True)
class Signal:
    """One binary test, its outcome, and the numbers behind it."""

    name: str
    passed: bool | None  # None means it could not be evaluated
    detail: str = ""

    @property
    def evaluable(self) -> bool:
        return self.passed is not None


@dataclass
class PiotroskiScore:
    """F-Score with an explicit denominator.

    Reporting "6" is ambiguous when two signals could not be computed. `score`
    counts passes, `evaluable` counts the signals that could be judged at all,
    and `normalised` scales to 9 so names with different coverage stay
    comparable.
    """

    signals: list[Signal] = field(default_factory=list)

    @property
    def score(self) -> int:
        return sum(1 for s in self.signals if s.passed is True)

    @property
    def evaluable(self) -> int:
        return sum(1 for s in self.signals if s.evaluable)

    @property
    def normalised(self) -> float | None:
        if self.evaluable == 0:
            return None
        return (self.score / self.evaluable) * 9

    @property
    def assessment(self) -> str:
        value = self.normalised
        if value is None:
            return "not evaluable"
        if value >= config.get("rules.piotroski.high_quality_min"):
            return "high quality"
        if value >= config.get("rules.piotroski.good_min"):
            return "good"
        if value <= config.get("rules.piotroski.avoid_max"):
            return "weak"
        return "mixed"

    def label(self) -> str:
        if self.evaluable == 0:
            return "F-Score: not evaluable"
        return f"F-Score: {self.score}/{self.evaluable} ({self.assessment})"


def _zone_for(value: float | None, variant_key: str) -> str:
    if value is None:
        return "not evaluable"
    if value > config.get(f"rules.altman.{variant_key}.safe_above"):
        return "safe"
    if value < config.get(f"rules.altman.{variant_key}.distress_below"):
        return "distress"
    return "grey"


@dataclass
class AltmanScore:
    """Z''-Score as primary, with the original Z reported as a cross-check.

    Z'' is primary for every filer rather than switched by industry. Choosing per
    company sounded principled and did not survive contact with the data: capex
    intensity separates fabless designers (0.8-2.8% of revenue) from integrated
    manufacturers (27-42%) cleanly, but hyperscalers now run 35% on data-centre
    buildout and would be misclassified as manufacturers. Z'' is also the variant
    Altman derived for cross-industry comparability, and the one practitioners
    commonly apply across tech, so it needs no such judgement.

    The original Z is still computed where a market cap and revenue allow, since
    a disagreement between the two is itself informative.
    """

    value: float | None
    reason: str
    components: dict[str, float] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    cross_check: float | None = None
    manufacturing_intensity: float | None = None
    variant: str = "Z''"

    @property
    def zone(self) -> str:
        return _zone_for(self.value, "z_double_prime")

    @property
    def cross_check_zone(self) -> str:
        return _zone_for(self.cross_check, "z_original")

    @property
    def agrees(self) -> bool | None:
        """Whether both variants land in the same zone."""
        if self.value is None or self.cross_check is None:
            return None
        return self.zone == self.cross_check_zone

    def label(self) -> str:
        if self.value is None:
            return f"Z'': not evaluable ({self.reason})"
        text = f"Z'' {self.value:.2f} ({self.zone})"
        if self.cross_check is not None:
            flag = "" if self.agrees else "  DISAGREES"
            text += f"  |  Z {self.cross_check:.2f} ({self.cross_check_zone}){flag}"
        return text


# ---------------------------------------------------------------------------
# Piotroski F-Score
# ---------------------------------------------------------------------------


def piotroski(current: Fundamentals, prior: Fundamentals | None = None) -> PiotroskiScore:
    """Nine signals of improving fundamental health.

    `prior` must be the same company one fiscal year earlier resolved from the
    *same* as-of view, so both years were knowable on the same date. Passing a
    view built a year ago instead would reintroduce look-ahead.
    """
    if prior is None:
        prior = current.prior_year()

    signals: list[Signal] = []

    def add(name: str, passed: bool | None, detail: str = "") -> None:
        signals.append(Signal(name, passed, detail))

    # -- profitability ------------------------------------------------------

    roa = current.return_on_assets
    add(
        "positive ROA",
        None if roa is None else roa > 0,
        "" if roa is None else f"ROA {roa:.3f}",
    )

    ocf = current.operating_cash_flow
    add(
        "positive operating cash flow",
        None if not ocf.present else ocf.value > 0,
        "" if not ocf.present else f"CFO {ocf.value:,.0f}",
    )

    prior_roa = prior.return_on_assets if prior else None
    add(
        "ROA improving",
        None if roa is None or prior_roa is None else roa > prior_roa,
        "" if roa is None or prior_roa is None else f"{prior_roa:.3f} -> {roa:.3f}",
    )

    # Accruals: cash earnings should exceed accounting earnings.
    assets = current.assets
    if ocf.present and assets.present and assets.value != 0 and roa is not None:
        cfo_to_assets = ocf.value / assets.value
        add(
            "cash earnings exceed accruals",
            cfo_to_assets > roa,
            f"CFO/assets {cfo_to_assets:.3f} vs ROA {roa:.3f}",
        )
    else:
        add("cash earnings exceed accruals", None)

    # -- leverage, liquidity, funding ---------------------------------------

    def leverage_ratio(f: Fundamentals) -> float | None:
        debt, total_assets = f.total_debt, f.assets
        if not debt.present or not total_assets.present or total_assets.value == 0:
            return None
        return debt.value / total_assets.value

    now_lev, prior_lev = leverage_ratio(current), leverage_ratio(prior) if prior else None
    add(
        "leverage falling",
        None if now_lev is None or prior_lev is None else now_lev < prior_lev,
        "" if now_lev is None or prior_lev is None else f"{prior_lev:.3f} -> {now_lev:.3f}",
    )

    now_cr = current.current_ratio
    prior_cr = prior.current_ratio if prior else None
    add(
        "current ratio rising",
        None if now_cr is None or prior_cr is None else now_cr > prior_cr,
        "" if now_cr is None or prior_cr is None else f"{prior_cr:.2f} -> {now_cr:.2f}",
    )

    now_shares = current.shares_outstanding
    prior_shares = prior.shares_outstanding if prior else None
    if now_shares.present and prior_shares is not None and prior_shares.present:
        # A small drift is buyback and vesting noise, not a capital raise.
        issued = now_shares.value > prior_shares.value * 1.02
        add(
            "no new share issuance",
            not issued,
            f"{prior_shares.value:,.0f} -> {now_shares.value:,.0f}",
        )
    else:
        add("no new share issuance", None)

    # -- operating efficiency ----------------------------------------------

    now_gm = current.gross_margin
    prior_gm = prior.gross_margin if prior else None
    add(
        "gross margin rising",
        None if now_gm is None or prior_gm is None else now_gm > prior_gm,
        "" if now_gm is None or prior_gm is None else f"{prior_gm:.3f} -> {now_gm:.3f}",
    )

    now_turn = current.asset_turnover
    prior_turn = prior.asset_turnover if prior else None
    add(
        "asset turnover rising",
        None if now_turn is None or prior_turn is None else now_turn > prior_turn,
        "" if now_turn is None or prior_turn is None else f"{prior_turn:.3f} -> {now_turn:.3f}",
    )

    return PiotroskiScore(signals)


# ---------------------------------------------------------------------------
# Altman Z-Score
# ---------------------------------------------------------------------------


def _manufacturing_intensity(f: Fundamentals) -> float | None:
    ppe = f._instant("ppe_net")
    assets = f.assets
    if not ppe.present or not assets.present or assets.value == 0:
        return None
    return ppe.value / assets.value


def altman(f: Fundamentals, market_cap: float | None = None) -> AltmanScore:
    """Distress risk: Z'' primary, original Z as a cross-check where computable."""
    intensity = _manufacturing_intensity(f)

    assets = f.assets
    working_capital = f.working_capital
    retained = f._instant("retained_earnings")
    ebit = f.ebit
    liabilities = f.liabilities
    equity = f.equity
    revenue = f.revenue

    missing = [
        name
        for name, item in (
            ("assets", assets),
            ("working capital", working_capital),
            ("retained earnings", retained),
            ("EBIT", ebit),
            ("total liabilities", liabilities),
            ("equity", equity),
        )
        if not item.present
    ]
    if missing:
        return AltmanScore(None, "inputs unavailable", {}, missing)
    if assets.value == 0 or liabilities.value == 0:
        return AltmanScore(None, "zero denominator", {}, ["assets or liabilities are zero"])

    x1 = working_capital.value / assets.value
    x2 = retained.value / assets.value
    x3 = ebit.value / assets.value

    coeff = config.get("rules.altman.z_double_prime.coefficients")
    x4 = equity.value / liabilities.value
    value = (
        coeff["working_capital_to_assets"] * x1
        + coeff["retained_earnings_to_assets"] * x2
        + coeff["ebit_to_assets"] * x3
        + coeff["equity_to_total_liabilities"] * x4
    )

    # Original Z needs market value of equity and the turnover term.
    cross_check: float | None = None
    if market_cap and revenue.present:
        original = config.get("rules.altman.z_original.coefficients")
        cross_check = (
            original["working_capital_to_assets"] * x1
            + original["retained_earnings_to_assets"] * x2
            + original["ebit_to_assets"] * x3
            + original["market_equity_to_total_liabilities"] * (market_cap / liabilities.value)
            + original["sales_to_assets"] * (revenue.value / assets.value)
        )

    if intensity is None:
        reason = "Z'' applied (manufacturing intensity unknown)"
    else:
        profile = "capital-intensive" if intensity >= 0.20 else "asset-light"
        reason = f"Z'' applied ({profile}, PP&E/assets {intensity:.0%})"
    if cross_check is None:
        reason += "; original Z not computable without a market cap"

    return AltmanScore(
        value,
        reason,
        {"X1": x1, "X2": x2, "X3": x3, "X4": x4},
        [],
        cross_check=cross_check,
        manufacturing_intensity=intensity,
    )


# ---------------------------------------------------------------------------
# Full assessment
# ---------------------------------------------------------------------------


@dataclass
class QualityAssessment:
    ticker: str
    piotroski: PiotroskiScore
    altman: AltmanScore
    roic: float | None
    wacc: float | None
    roic_wacc_spread: float | None
    fcf_conversion: float | None
    fcf_assessment: str
    balance_sheet: dict[str, str]
    gross_margin: float | None
    gross_margin_trend: str
    rd_intensity: float | None
    notes: list[str] = field(default_factory=list)

    @property
    def creates_value(self) -> bool | None:
        """Whether returns clear the cost of capital — the moat test."""
        if self.roic_wacc_spread is None:
            return None
        meaningful = config.get("rules.quality.roic_wacc_spread.meaningful_spread_bps") / 10_000
        return self.roic_wacc_spread >= meaningful

    @property
    def distress_risk(self) -> str:
        return self.altman.zone

    def report(self) -> str:
        lines = [f"{self.ticker} quality assessment", "-" * 60]
        lines.append(f"  {self.piotroski.label()}")
        for s in self.piotroski.signals:
            mark = "ok " if s.passed else ("-- " if s.passed is None else "no ")
            lines.append(f"     {mark} {s.name:32} {s.detail}")
        lines.append(f"  {self.altman.label()}")
        lines.append(f"     variant chosen: {self.altman.reason}")
        roic = "n/a" if self.roic is None else f"{self.roic:.1%}"
        wacc = "n/a" if self.wacc is None else f"{self.wacc:.1%}"
        spread = "n/a" if self.roic_wacc_spread is None else f"{self.roic_wacc_spread:+.1%}"
        lines.append(f"  ROIC {roic}  WACC {wacc}  spread {spread}")
        fcf = "n/a" if self.fcf_conversion is None else f"{self.fcf_conversion:.2f}"
        lines.append(f"  FCF conversion {fcf} ({self.fcf_assessment})")
        gm = "n/a" if self.gross_margin is None else f"{self.gross_margin:.1%}"
        lines.append(f"  gross margin {gm} ({self.gross_margin_trend})")
        rd = "n/a" if self.rd_intensity is None else f"{self.rd_intensity:.1%}"
        lines.append(f"  R&D intensity {rd}")
        for name, verdict in self.balance_sheet.items():
            lines.append(f"  {name:24} {verdict}")
        for note in self.notes:
            lines.append(f"  note: {note}")
        return "\n".join(lines)


def _band(value: float | None, safe: float, warn: float, higher_is_better: bool) -> str:
    if value is None:
        return "not evaluable"
    if higher_is_better:
        if value >= safe:
            return f"safe ({value:.2f})"
        if value <= warn:
            return f"risky ({value:.2f})"
        return f"adequate ({value:.2f})"
    if value <= safe:
        return f"safe ({value:.2f})"
    if value >= warn:
        return f"risky ({value:.2f})"
    return f"moderate ({value:.2f})"


def assess(
    f: Fundamentals,
    market_cap: float | None = None,
    wacc: float | None = None,
) -> QualityAssessment:
    """Full quality picture for one company at one point in time.

    `wacc` is supplied by the valuation layer. Without it the ROIC-WACC spread
    is reported as unavailable rather than compared against a guessed hurdle
    rate, since the sign of that spread is the whole finding.
    """
    prior = f.prior_year()
    notes: list[str] = []

    fcf_conversion = f.fcf_conversion
    good = config.get("rules.quality.fcf_conversion.good_min")
    red = config.get("rules.quality.fcf_conversion.red_flag_below")
    capex_intensity = f.capex_intensity
    heavy_capex = capex_intensity is not None and capex_intensity >= 0.15

    if fcf_conversion is None:
        fcf_assessment = "not evaluable"
    elif fcf_conversion >= good:
        fcf_assessment = "strong"
    elif fcf_conversion < red:
        # Two very different causes produce the same low number, and conflating
        # them would misread a capital-cycle investment as accounting weakness.
        # Micron converts 0.20 while spending 42% of revenue on HBM capacity,
        # yet passes Piotroski's accruals test, which uses operating cash flow
        # rather than free cash flow and so is the real earnings-quality signal.
        if heavy_capex:
            fcf_assessment = (
                f"low, but capex is {capex_intensity:.0%} of revenue "
                "- investment phase, not an earnings-quality flag"
            )
        else:
            fcf_assessment = "red flag: earnings not converting to cash"
    else:
        fcf_assessment = "adequate"

    gross_margin = f.gross_margin
    prior_gm = prior.gross_margin if prior else None
    if gross_margin is None or prior_gm is None:
        trend = "trend unavailable"
    elif gross_margin > prior_gm + 0.005:
        trend = f"rising from {prior_gm:.1%}"
    elif gross_margin < prior_gm - 0.005:
        trend = f"falling from {prior_gm:.1%}"
    else:
        trend = "stable"

    roic = f.roic
    spread = None if roic is None or wacc is None else roic - wacc
    if roic is not None and wacc is None:
        notes.append("ROIC-WACC spread pending a cost of capital from the valuation layer")

    bs = config.get("rules.balance_sheet")
    balance_sheet = {
        "net debt / EBITDA": _band(
            f.net_debt_to_ebitda,
            bs["net_debt_to_ebitda"]["safe_below"],
            bs["net_debt_to_ebitda"]["risky_above"],
            higher_is_better=False,
        ),
        "interest coverage": _band(
            f.interest_coverage,
            bs["interest_coverage"]["safe_above"],
            bs["interest_coverage"]["warning_below"],
            higher_is_better=True,
        ),
        "current ratio": _band(
            f.current_ratio,
            bs["current_ratio"]["healthy_range"][0],
            bs["current_ratio"]["risk_below"],
            higher_is_better=True,
        ),
        "quick ratio": _band(
            f.quick_ratio,
            bs["quick_ratio"]["preferred_min"],
            bs["quick_ratio"]["preferred_min"] * 0.5,
            higher_is_better=True,
        ),
    }

    if not f.total_debt.present:
        # Untagged debt is unknown, not zero, so leverage cannot be scored. Four
        # universe names are in this position and are almost certainly unlevered.
        notes.append("no debt concept tagged; leverage not scored (unknown, not zero)")

    if prior is None:
        notes.append("no prior year available; year-over-year signals not evaluable")

    return QualityAssessment(
        ticker=f.ticker,
        piotroski=piotroski(f, prior),
        altman=altman(f, market_cap=market_cap),
        roic=roic,
        wacc=wacc,
        roic_wacc_spread=spread,
        fcf_conversion=fcf_conversion,
        fcf_assessment=fcf_assessment,
        balance_sheet=balance_sheet,
        gross_margin=gross_margin,
        gross_margin_trend=trend,
        rd_intensity=f.rd_intensity,
        notes=notes + f.coverage()["notes"],
    )
