"""Capital-cycle position — is this peak or trough for *this* company.

Semiconductors are capital-cycle businesses, and ignoring that is how a
fundamentals investor buys memory at the top. Micron's gross margin was 45% in
FY2022, *negative 9%* in FY2023, and 40% again by FY2025. A screen that reads
"40% gross margin, strong" without asking where in the cycle that sits will
recommend the same company at both extremes.

Every measure here is relative to the company's own record rather than to a
cross-sector threshold, because the levels differ enormously by business model:
a 40% margin is a peak for memory and a collapse for a process-control
monopoly. The output is a position annotation plus a plain statement of whether
current earnings look repeatable, which is what the valuation layer needs to know
before it treats trailing cash flow as a starting point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src import config
from src.fundamentals import Fundamentals

# Enough observations for a percentile to mean anything. Below this the position
# is reported as unknown rather than computed from two points.
MIN_OBSERVATIONS = 4


class Position:
    PEAK = "PEAK"
    LATE = "LATE_CYCLE"
    MID = "MID_CYCLE"
    EARLY = "EARLY_RECOVERY"
    TROUGH = "TROUGH"
    # No cycle to place: margins have barely moved, so trailing earnings are a
    # fair basis for valuation.
    STABLE = "STABLE"
    # At the top of its range but by secular improvement rather than a cycle,
    # which is a different proposition from a peak that will mean-revert.
    SECULAR_HIGH = "SECULAR_HIGH"
    UNKNOWN = "UNKNOWN"


@dataclass
class Series:
    """One metric's history, newest first, and where the latest value sits."""

    name: str
    values: list[float] = field(default_factory=list)
    periods: list[date] = field(default_factory=list)
    # Minimum high-to-low spread before a percentile is meaningful.
    min_spread: float = 0.0

    @property
    def current(self) -> float | None:
        return self.values[0] if self.values else None

    @property
    def observations(self) -> int:
        return len(self.values)

    @property
    def usable(self) -> bool:
        return self.observations >= MIN_OBSERVATIONS

    @property
    def low(self) -> float | None:
        return min(self.values) if self.values else None

    @property
    def high(self) -> float | None:
        return max(self.values) if self.values else None

    @property
    def median(self) -> float | None:
        if not self.values:
            return None
        ordered = sorted(self.values)
        return ordered[len(ordered) // 2]

    @property
    def spread(self) -> float | None:
        if self.low is None or self.high is None:
            return None
        return self.high - self.low

    @property
    def has_meaningful_range(self) -> bool:
        spread = self.spread
        return spread is not None and spread >= self.min_spread

    @property
    def percentile(self) -> float | None:
        """Where the latest value sits in its own range, 0 (low) to 1 (high).

        Returns None when the range is too narrow to carry information. On a
        two-point spread a percentile amplifies noise into a signal, which is how
        a stable business gets labelled a cycle trough.
        """
        if not self.usable or self.current is None:
            return None
        low, high = self.low, self.high
        if high is None or low is None or high == low:
            return None
        if not self.has_meaningful_range:
            return None
        return (self.current - low) / (high - low)

    @property
    def monotonic_rising(self) -> bool:
        """Whether the metric improved in every year of the window.

        Distinguishes a secular gain from a cyclical peak. Apple's gross margin
        climbed from 38% to 47% across six years; that is a better business, not
        a cycle about to turn.
        """
        if self.observations < 3:
            return False
        # values run newest-first, so a steady improvement decreases along it.
        return all(self.values[i] >= self.values[i + 1] for i in range(self.observations - 1))

    @property
    def direction(self) -> str:
        """Whether the metric is rising or falling year over year."""
        if self.observations < 2:
            return "unknown"
        current, previous = self.values[0], self.values[1]
        if previous == 0:
            return "unknown"
        change = (current - previous) / abs(previous)
        if change > 0.05:
            return "rising"
        if change < -0.05:
            return "falling"
        return "flat"

    def label(self) -> str:
        if self.current is None:
            return f"{self.name}: unavailable"
        if not self.usable:
            return (
                f"{self.name}: {self.current:.3f} "
                f"({self.observations}y history, too short to place)"
            )
        percentile = self.percentile
        if percentile is None:
            spread = self.spread or 0.0
            return (
                f"{self.name}: {self.current:.3f}, range only {spread:.3f} wide over "
                f"{self.observations}y - too narrow to place, {self.direction}"
            )
        return (
            f"{self.name}: {self.current:.3f} at the "
            f"{percentile:.0%} mark of its {self.observations}y range "
            f"({self.low:.3f} to {self.high:.3f}), {self.direction}"
        )


def series_for(
    f: Fundamentals, attribute: str, years: int = 6, min_spread: float = 0.0
) -> Series:
    """Collect one ratio across successive fiscal years, newest first.

    Every value comes from the same as-of view, so this is what an investor
    standing on that date could have computed — not a mix of past and present.
    """
    values: list[float] = []
    periods: list[date] = []
    view: Fundamentals | None = f
    for _ in range(years):
        if view is None:
            break
        value = getattr(view, attribute, None)
        period = view.revenue.period_end or view.assets.period_end
        if value is not None and period is not None:
            values.append(value)
            periods.append(period)
        view = view.prior_year()
    return Series(attribute, values, periods, min_spread)


@dataclass
class CyclePosition:
    """Where this company sits in its own capital cycle."""

    ticker: str
    as_of: date
    position: str
    gross_margin: Series
    operating_margin: Series
    inventory_days: Series
    capex_intensity: Series
    revenue: Series
    is_cyclical_segment: bool = False
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def capex_profile(self) -> str:
        """Fabless, capital-intensive, or somewhere between.

        Measured rather than assumed: capex intensity separates fabless
        designers from integrated manufacturers cleanly, though hyperscaler
        data-centre spending now reaches manufacturer-like levels, so this
        describes capital intensity and not industry membership.
        """
        current = self.capex_intensity.current
        if current is None:
            return "unknown"
        fabless = config.get("rules.cycle.capex_to_revenue.fabless_range")
        heavy = config.get("rules.cycle.capex_to_revenue.idm_foundry_range")
        if current <= fabless[1]:
            return f"capital-light ({current:.1%} of revenue)"
        if current >= heavy[0]:
            return f"capital-intensive ({current:.1%} of revenue)"
        return f"moderate capital intensity ({current:.1%} of revenue)"

    @property
    def earnings_repeatable(self) -> bool | None:
        """Whether trailing earnings are a fair basis for valuation.

        The question the valuation layer actually needs answered. At a cycle peak
        the answer is no, and a DCF started from peak cash flow will overvalue;
        at a trough it is also no, in the other direction.
        """
        if self.position == Position.UNKNOWN:
            return None
        return self.position in (
            Position.MID,
            Position.EARLY,
            Position.STABLE,
            Position.SECULAR_HIGH,
        )

    @property
    def valuation_caveat(self) -> str | None:
        """What the cycle position implies for using trailing figures."""
        if self.position == Position.PEAK:
            return (
                "trailing earnings sit at a cycle peak; valuing off them assumes "
                "the peak is permanent"
            )
        if self.position == Position.LATE:
            return "margins are high and turning down; trailing earnings may not repeat"
        if self.position == Position.TROUGH:
            return (
                "trailing earnings sit at a cycle trough; valuing off them assumes "
                "the trough is permanent and will understate a recovery"
            )
        if self.position == Position.EARLY:
            return "margins are recovering from a trough; trailing figures lag the business"
        if self.position == Position.SECULAR_HIGH:
            return (
                "margins are at a high but have improved every year; treat as a better "
                "business rather than a peak, while watching for the trend breaking"
            )
        return None

    def report(self) -> str:
        out = [f"{self.ticker} cycle position as of {self.as_of}", "-" * 68]
        out.append(f"  POSITION: {self.position}")
        if self.is_cyclical_segment:
            out.append("  segment is flagged cyclical in the universe definition")
        out.append(f"  capital profile: {self.capex_profile}")
        repeatable = self.earnings_repeatable
        if repeatable is not None:
            out.append(
                f"  trailing earnings look {'repeatable' if repeatable else 'NOT repeatable'}"
            )
        caveat = self.valuation_caveat
        if caveat:
            out.append(f"  caveat: {caveat}")
        out.append("")
        for s in (
            self.gross_margin,
            self.operating_margin,
            self.inventory_days,
            self.capex_intensity,
        ):
            out.append(f"  {s.label()}")
        out.append("")
        for line in self.evidence:
            out.append(f"  evidence: {line}")
        for line in self.notes:
            out.append(f"  note: {line}")
        return "\n".join(out)


def assess(f: Fundamentals, cyclical_segment: bool = False, years: int = 6) -> CyclePosition:
    """Place a company in its own capital cycle from filed history."""
    min_spread = config.get("rules.cycle.min_margin_spread")
    gross = series_for(f, "gross_margin", years, min_spread)
    operating = series_for(f, "operating_margin", years, min_spread)
    inventory = series_for(f, "inventory_days", years)
    capex = series_for(f, "capex_intensity", years)
    revenue_series = Series("revenue")

    # Revenue is a line item rather than a ratio, so collect it separately.
    values: list[float] = []
    periods: list[date] = []
    view: Fundamentals | None = f
    for _ in range(years):
        if view is None:
            break
        item = view.revenue
        if item.present and item.period_end is not None:
            values.append(item.value)
            periods.append(item.period_end)
        view = view.prior_year()
    revenue_series = Series("revenue", values, periods)

    evidence: list[str] = []
    notes: list[str] = []

    # Gross margin is the primary cycle indicator: it responds to pricing before
    # revenue does, and pricing is what actually cycles.
    primary = gross if gross.usable else operating
    if not primary.usable:
        notes.append(
            f"only {primary.observations}y of margin history; position not determinable"
        )
        return CyclePosition(
            f.ticker,
            f.as_of,
            Position.UNKNOWN,
            gross,
            operating,
            inventory,
            capex,
            revenue_series,
            cyclical_segment,
            evidence,
            notes,
        )

    percentile = primary.percentile
    direction = primary.direction
    peak_line = config.get("rules.cycle.margin_percentile_peak_above")
    trough_line = config.get("rules.cycle.margin_percentile_trough_below")

    if percentile is None and primary.usable and not primary.has_meaningful_range:
        position = Position.STABLE
        spread = primary.spread or 0.0
        evidence.append(
            f"{primary.name} has moved only {spread:.1%} across "
            f"{primary.observations}y - no cycle to place, trailing earnings are a "
            "fair basis"
        )
    elif percentile is None:
        position = Position.UNKNOWN
        notes.append("margin history has no spread; position not determinable")
    elif percentile >= peak_line and primary.monotonic_rising:
        position = Position.SECULAR_HIGH
        evidence.append(
            f"{primary.name} at the {percentile:.0%} mark of its range, but improved "
            f"every year of the window - a better business rather than a cycle peak"
        )
    elif percentile >= peak_line:
        # A peak that has already turned down is a different risk from one still
        # climbing, and the distinction matters for whether to trim.
        position = Position.LATE if direction == "falling" else Position.PEAK
        evidence.append(
            f"{primary.name} at the {percentile:.0%} mark of its own range and {direction}"
        )
    elif percentile <= trough_line:
        position = Position.EARLY if direction == "rising" else Position.TROUGH
        evidence.append(
            f"{primary.name} at the {percentile:.0%} mark of its own range and {direction}"
        )
    else:
        position = Position.MID
        evidence.append(
            f"{primary.name} mid-range at the {percentile:.0%} mark, {direction}"
        )

    # Inventory corroborates: building stock into falling margins is the classic
    # late-cycle signature, and it is visible before revenue turns.
    threshold = config.get("rules.cycle.inventory_days.downcycle_risk_above")
    if inventory.current is not None and inventory.current > threshold:
        evidence.append(
            f"inventory at {inventory.current:.0f} days, above the {threshold}-day "
            f"downcycle threshold ({inventory.direction})"
        )
        if inventory.direction == "rising" and direction == "falling":
            evidence.append(
                "inventory building while margins fall - the classic late-cycle signature"
            )
            if position in (Position.PEAK, Position.MID):
                position = Position.LATE

    if revenue_series.observations >= 2:
        evidence.append(f"revenue {revenue_series.direction} year over year")

    if cyclical_segment:
        evidence.append("segment flagged cyclical in the universe definition")

    if capex.current is not None:
        heavy = config.get("rules.cycle.capex_to_revenue.idm_foundry_range")[0]
        if capex.current >= heavy:
            evidence.append(
                f"capex at {capex.current:.0%} of revenue - building capacity, which "
                "depresses free cash flow regardless of profitability"
            )

    return CyclePosition(
        f.ticker,
        f.as_of,
        position,
        gross,
        operating,
        inventory,
        capex,
        revenue_series,
        cyclical_segment,
        evidence,
        notes,
    )


def cyclical_segments() -> set[str]:
    """Segments the universe definition marks as genuinely cyclical."""
    out: set[str] = set()
    for name, spec in config.get("universe.segments").items():
        if spec.get("cyclical"):
            out.add(name)
    return out


def is_cyclical(ticker: str) -> bool:
    """Whether a ticker sits in a segment flagged cyclical."""
    ticker = ticker.upper()
    for name, spec in config.get("universe.segments").items():
        if not spec.get("cyclical"):
            continue
        if any(m["ticker"].upper() == ticker for m in spec["members"]):
            return True
    return False
