"""Tests for capital-cycle positioning.

The guard that matters most: a percentile computed on a narrow range is noise
wearing the costume of a signal. Microsoft's gross margin moved 1.8 points across
six years and the first version called it a cycle trough, which would have told
the valuation layer that a rock-steady business had unrepeatable earnings.
"""

from __future__ import annotations

from datetime import date

import pytest

from src import cycle
from src.cycle import Position, Series, assess
from src.fundamentals import build
from tests.test_fundamentals import annual, instant, payload

AS_OF = date(2026, 6, 30)


def fy(concept: str, val: float, year: int, instant_fact: bool = False) -> dict:
    end = f"{year}-03-31"
    filed = f"{year}-05-01"
    if instant_fact:
        return instant(concept, val, end=end, filed=filed)
    return annual(concept, val, start=f"{year - 1}-04-01", end=end, filed=filed)


def margins(by_year: dict[int, float], revenue: float = 1000.0) -> list[dict]:
    """Build gross-margin history: revenue fixed, cost varied."""
    out: list[dict] = []
    for year, margin in by_year.items():
        out.append(fy("Revenues", revenue, year))
        out.append(fy("CostOfRevenue", revenue * (1 - margin), year))
        out.append(fy("Assets", 2000.0, year, instant_fact=True))
    return out


def company(entries: list[dict]):
    return build(payload(*entries), "TEST", as_of=AS_OF)


# ---------------------------------------------------------------------------
# Series
# ---------------------------------------------------------------------------


class TestSeries:
    def test_percentile_places_the_latest_value(self):
        s = Series("m", [0.5, 0.3, 0.1, 0.2])
        assert s.percentile == pytest.approx(1.0)
        s = Series("m", [0.1, 0.3, 0.5, 0.2])
        assert s.percentile == pytest.approx(0.0)

    def test_narrow_range_yields_no_percentile(self):
        """Regression: a 2-point spread turned noise into a cycle call."""
        s = Series("m", [0.679, 0.682, 0.695, 0.697], min_spread=0.06)
        assert s.has_meaningful_range is False
        assert s.percentile is None

    def test_wide_range_is_placed_normally(self):
        s = Series("m", [0.398, 0.224, -0.091, 0.452, 0.376], min_spread=0.06)
        assert s.has_meaningful_range is True
        assert s.percentile is not None

    def test_short_history_is_not_usable(self):
        assert Series("m", [0.4, 0.3]).percentile is None
        assert Series("m", [0.4, 0.3]).usable is False

    def test_direction_reads_year_over_year(self):
        assert Series("m", [0.5, 0.3]).direction == "rising"
        assert Series("m", [0.3, 0.5]).direction == "falling"
        assert Series("m", [0.50, 0.51]).direction == "flat"

    def test_monotonic_rising_detects_a_secular_gain(self):
        # newest first, so a steady improvement decreases along the list
        assert Series("m", [0.47, 0.45, 0.42, 0.40, 0.38]).monotonic_rising is True
        assert Series("m", [0.40, 0.45, 0.30, 0.42, 0.38]).monotonic_rising is False

    def test_label_survives_a_missing_percentile(self):
        s = Series("m", [0.679, 0.682, 0.695, 0.697], min_spread=0.06)
        assert "too narrow to place" in s.label()

    def test_label_survives_no_data(self):
        assert "unavailable" in Series("m", []).label()


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------


class TestPositioning:
    def test_cycle_peak_detected(self):
        """Memory at the top of its range: Micron's own shape."""
        f = company(margins({2026: 0.40, 2025: 0.22, 2024: -0.09, 2023: 0.45, 2022: 0.38}))
        result = assess(f)
        assert result.position in (Position.PEAK, Position.LATE)
        assert result.earnings_repeatable is False

    def test_trough_detected(self):
        f = company(margins({2026: -0.05, 2025: 0.22, 2024: 0.40, 2023: 0.45, 2022: 0.38}))
        result = assess(f)
        assert result.position in (Position.TROUGH, Position.EARLY)

    def test_early_recovery_when_rising_off_a_low(self):
        # 0.11 sits at the 15% mark of a 0.05-0.45 range, inside the trough band,
        # and it is rising off the bottom.
        f = company(margins({2026: 0.11, 2025: 0.05, 2024: 0.40, 2023: 0.45, 2022: 0.42}))
        assert assess(f).position == Position.EARLY
        assert assess(f).earnings_repeatable is True

    def test_stable_business_has_no_cycle_to_place(self):
        """Regression: Microsoft, KLA, Cisco all read as peaks or troughs on
        ranges under two points."""
        f = company(margins({2026: 0.679, 2025: 0.682, 2024: 0.690, 2023: 0.697}))
        result = assess(f)
        assert result.position == Position.STABLE
        assert result.earnings_repeatable is True
        assert result.valuation_caveat is None

    def test_secular_high_is_not_a_cyclical_peak(self):
        """Apple's gross margin climbed from 38% to 47% across six years. That is
        a better business, not a cycle about to turn."""
        f = company(margins({2026: 0.469, 2025: 0.450, 2024: 0.441, 2023: 0.430, 2022: 0.382}))
        result = assess(f)
        assert result.position == Position.SECULAR_HIGH
        assert result.earnings_repeatable is True
        assert "better business" in result.valuation_caveat

    def test_mid_cycle_between_the_extremes(self):
        f = company(margins({2026: 0.30, 2025: 0.20, 2024: 0.45, 2023: 0.10, 2022: 0.28}))
        assert assess(f).position == Position.MID

    def test_short_history_is_unknown_not_guessed(self):
        f = company(margins({2026: 0.40, 2025: 0.35}))
        result = assess(f)
        assert result.position == Position.UNKNOWN
        assert result.earnings_repeatable is None

    def test_empty_company_is_unknown(self):
        result = assess(company([]))
        assert result.position == Position.UNKNOWN


class TestValuationCaveats:
    def test_peak_warns_against_valuing_off_trailing_earnings(self):
        f = company(margins({2026: 0.45, 2025: 0.22, 2024: -0.09, 2023: 0.30, 2022: 0.25}))
        result = assess(f)
        if result.position == Position.PEAK:
            assert "peak is permanent" in result.valuation_caveat

    def test_trough_warns_in_the_other_direction(self):
        f = company(margins({2026: -0.05, 2025: 0.22, 2024: 0.40, 2023: 0.45, 2022: 0.38}))
        result = assess(f)
        if result.position == Position.TROUGH:
            assert "understate a recovery" in result.valuation_caveat

    def test_stable_needs_no_caveat(self):
        f = company(margins({2026: 0.679, 2025: 0.682, 2024: 0.690, 2023: 0.697}))
        assert assess(f).valuation_caveat is None


# ---------------------------------------------------------------------------
# Capital intensity and corroboration
# ---------------------------------------------------------------------------


class TestCapitalIntensity:
    def base(self, capex: float) -> list[dict]:
        entries = margins({2026: 0.30, 2025: 0.28, 2024: 0.32, 2023: 0.20})
        entries.append(fy("PaymentsToAcquirePropertyPlantAndEquipment", capex, 2026))
        return entries

    def test_capital_light_profile(self):
        result = assess(company(self.base(30.0)))  # 3% of 1000 revenue
        assert "capital-light" in result.capex_profile

    def test_capital_intensive_profile(self):
        result = assess(company(self.base(400.0)))  # 40% of revenue
        assert "capital-intensive" in result.capex_profile

    def test_unknown_when_capex_untagged(self):
        result = assess(company(margins({2026: 0.30, 2025: 0.28, 2024: 0.32, 2023: 0.20})))
        assert result.capex_profile == "unknown"

    def test_heavy_capex_is_flagged_as_evidence(self):
        result = assess(company(self.base(400.0)))
        assert any("building capacity" in e for e in result.evidence)


class TestInventoryCorroboration:
    def test_inventory_above_threshold_is_evidence(self):
        entries = margins({2026: 0.30, 2025: 0.28, 2024: 0.32, 2023: 0.20})
        # 400 inventory on 700 cost of revenue is well past 120 days
        for year in (2026, 2025, 2024, 2023):
            entries.append(fy("InventoryNet", 400.0, year, instant_fact=True))
        result = assess(company(entries))
        assert any("downcycle threshold" in e for e in result.evidence)


class TestSegmentFlag:
    def test_memory_and_equipment_are_flagged_cyclical(self):
        assert cycle.is_cyclical("MU") is True
        assert cycle.is_cyclical("AMAT") is True

    def test_hyperscalers_are_not(self):
        assert cycle.is_cyclical("MSFT") is False

    def test_cyclical_segments_listed(self):
        segments = cycle.cyclical_segments()
        assert "memory_storage" in segments
        assert "hyperscalers" not in segments

    def test_flag_appears_in_evidence(self):
        f = company(margins({2026: 0.30, 2025: 0.28, 2024: 0.32, 2023: 0.20}))
        result = assess(f, cyclical_segment=True)
        assert any("flagged cyclical" in e for e in result.evidence)


class TestReport:
    def test_report_renders_and_is_ascii_safe(self):
        f = company(margins({2026: 0.40, 2025: 0.22, 2024: -0.09, 2023: 0.45, 2022: 0.38}))
        text = assess(f).report()
        assert "POSITION" in text
        text.encode("cp1252")
