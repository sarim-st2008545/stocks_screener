"""Tests for configuration loading.

The behaviour worth guarding is that a missing threshold fails loudly. A config
lookup that quietly returns None is how a screening rule stops being applied
without anyone noticing.
"""

from __future__ import annotations

import pytest

from src import config


class TestLookup:
    def test_dotted_path_resolves(self):
        assert config.get("rules.point_in_time.settle_days") == 2

    def test_missing_path_raises_by_default(self):
        with pytest.raises(config.ConfigError):
            config.get("rules.point_in_time.no_such_key")

    def test_missing_file_raises_by_default(self):
        with pytest.raises(config.ConfigError):
            config.get("no_such_file.anything")

    def test_default_is_returned_when_given(self):
        assert config.get("rules.nope.nope", 42) == 42
        assert config.get("no_such_file.anything", "fallback") == "fallback"

    def test_bare_filename_is_rejected(self):
        """A file alone is not a value; asking for one is a caller bug."""
        with pytest.raises(config.ConfigError):
            config.get("rules")

    def test_falsy_values_are_not_mistaken_for_missing(self):
        """0 and False are real settings, not absent ones."""
        assert config.get("portfolio.execution.assumed_commission_usd") == 0.0
        assert config.get("portfolio.execution.broker") is None


class TestRulesIntegrity:
    """The rulebook should stay internally consistent as it is edited."""

    def test_scoring_weights_sum_to_one(self):
        weights = config.get("scoring.weights", None) or config.get("rules.scoring.weights")
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_sleeve_targets_sum_to_one(self):
        sleeves = config.get("portfolio.sleeves")
        total = sum(s["target_pct"] for s in sleeves.values())
        assert total == pytest.approx(1.0)

    def test_kelly_fraction_is_conservative(self):
        """Full Kelly over-bets under estimation error; fractional is the rule."""
        assert 0 < config.get("portfolio.sizing.kelly_fraction") <= 0.5

    def test_margin_of_safety_is_meaningful(self):
        assert 0 < config.get("rules.valuation.margin_of_safety") < 1

    def test_pit_lags_are_never_faster_than_sec_deadlines(self):
        """10-K deadlines run to 90 days and 10-Q to 45; fallbacks must not
        assume knowledge sooner than the statute allows."""
        assert config.get("rules.point_in_time.fallback_lag_days.quarterly") >= 45
        assert config.get("rules.point_in_time.fallback_lag_days.annual") >= 90

    def test_position_limits_are_ordered(self):
        single = config.get("portfolio.limits.max_single_name_pct_of_portfolio")
        segment = config.get("portfolio.limits.max_segment_pct_of_portfolio")
        assert 0 < single <= segment <= 1

    def test_altman_zones_are_ordered(self):
        for variant in ("z_double_prime", "z_original"):
            safe = config.get(f"rules.altman.{variant}.safe_above")
            distress = config.get(f"rules.altman.{variant}.distress_below")
            assert distress < safe


class TestUniverse:
    def test_every_segment_has_members(self):
        for name, segment in config.get("universe.segments").items():
            assert segment.get("members"), f"segment {name} is empty"

    def test_tickers_are_unique_across_segments(self):
        """A ticker in two segments would be double-counted in concentration."""
        seen: dict[str, str] = {}
        for name, segment in config.get("universe.segments").items():
            for member in segment["members"]:
                ticker = member["ticker"]
                assert ticker not in seen, f"{ticker} in both {seen.get(ticker)} and {name}"
                seen[ticker] = name

    def test_every_member_carries_a_note(self):
        """A constituent without a stated reason is an unexamined assumption."""
        for segment in config.get("universe.segments").values():
            for member in segment["members"]:
                assert member.get("note"), f"{member['ticker']} has no note"

    def test_excluded_categories_state_a_reason(self):
        for entry in config.get("universe.excluded"):
            assert entry.get("reason")


class TestUserAgent:
    def test_placeholder_is_rejected(self, monkeypatch):
        """SEC rejects requests without a real contact; a placeholder would get
        the whole project rate-limited."""
        monkeypatch.setenv("USER_AGENT", "your_email_here")
        with pytest.raises(config.ConfigError):
            config.user_agent()

    def test_empty_is_rejected(self, monkeypatch):
        monkeypatch.setenv("USER_AGENT", "   ")
        with pytest.raises(config.ConfigError):
            config.user_agent()

    def test_real_value_passes(self, monkeypatch):
        monkeypatch.setenv("USER_AGENT", "Test User test@example.com")
        assert config.user_agent() == "Test User test@example.com"
