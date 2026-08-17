"""Tests for the events layer.

Two bugs found against live filings in the earlier codebase are pinned here: a
joint Form 4 naming two reporting owners double-counted every transaction, and a
purchase filled across several price points read as several separate buys. Both
inflate apparent insider activity, which is the direction that misleads.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.events import (
    CLUSTER_MIN_BUYERS,
    MIN_BUY_VALUE_USD,
    EventScan,
    FilingRef,
    InsiderTrade,
    Kind,
    attach_scores,
    build_events,
    business_days,
    detect_clusters,
    merge_same_day_lots,
    parse_8k_items,
    parse_form4,
    rate_8k,
    report,
)

REF = FilingRef(
    cik=1045810,
    ticker="NVDA",
    company="NVIDIA CORP",
    form="4",
    filed=date(2026, 8, 12),
    path="edgar/data/1045810/x.txt",
)


def form4(
    owners: list[tuple[str, str]],
    transactions: list[tuple[str, float, float, str]],
    symbol: str = "NVDA",
) -> str:
    owner_xml = "".join(
        f"<reportingOwner><reportingOwnerId><rptOwnerName>{name}</rptOwnerName>"
        f"</reportingOwnerId><reportingOwnerRelationship><isOfficer>1</isOfficer>"
        f"<officerTitle>{title}</officerTitle></reportingOwnerRelationship>"
        "</reportingOwner>"
        for name, title in owners
    )
    txn_xml = "".join(
        "<nonDerivativeTransaction>"
        f"<transactionDate><value>{when}</value></transactionDate>"
        f"<transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>"
        "<transactionAmounts>"
        f"<transactionShares><value>{shares}</value></transactionShares>"
        f"<transactionPricePerShare><value>{price}</value></transactionPricePerShare>"
        "</transactionAmounts>"
        "<postTransactionAmounts><sharesOwnedFollowingTransaction><value>10000</value>"
        "</sharesOwnedFollowingTransaction></postTransactionAmounts>"
        "</nonDerivativeTransaction>"
        for code, shares, price, when in transactions
    )
    return (
        "<ownershipDocument>"
        f"<issuer><issuerTradingSymbol>{symbol}</issuerTradingSymbol></issuer>"
        f"{owner_xml}<nonDerivativeTable>{txn_xml}</nonDerivativeTable>"
        "</ownershipDocument>"
    )


# ---------------------------------------------------------------------------
# Form 4 parsing
# ---------------------------------------------------------------------------


class TestForm4:
    def test_reads_a_purchase(self):
        trades = parse_form4(form4([("Tan Lip Bu", "CEO")], [("P", 105263, 95.00, "2026-08-11")]), REF)
        assert len(trades) == 1
        assert trades[0].code == "P"
        assert trades[0].value == pytest.approx(105263 * 95.0)
        assert "CEO" in trades[0].role

    def test_joint_filing_does_not_double_count(self):
        """Regression: iterating the transaction table inside an owner loop
        reported every transaction once per reporting owner."""
        xml = form4(
            [("Habiger David C", "Director"), ("Mahoney Michael F", "CEO")],
            [("P", 1000, 100.0, "2026-08-11")],
        )
        trades = parse_form4(xml, REF)
        assert len(trades) == 1
        assert "&" in trades[0].owner

    def test_split_executions_merge_into_one_trade(self):
        """Regression: one Carvana purchase filled at several prices read as a
        $1.3M buy plus a $240k buy when it was a single $1.54M trade."""
        xml = form4(
            [("Garcia Ernest", "CEO")],
            [("P", 13000, 100.0, "2026-08-11"), ("P", 2400, 100.0, "2026-08-11")],
        )
        trades = parse_form4(xml, REF)
        assert len(trades) == 1
        assert trades[0].shares == 15400
        assert trades[0].value == pytest.approx(1_540_000)

    def test_different_dates_stay_separate(self):
        """Two purchases on different days are two decisions, not one."""
        xml = form4(
            [("Johnson Gerald", "Director")],
            [("P", 130, 455.90, "2026-08-13"), ("P", 70, 469.93, "2026-08-12")],
        )
        assert len(parse_form4(xml, REF)) == 2

    def test_merged_price_is_volume_weighted(self):
        trades = merge_same_day_lots(
            [
                InsiderTrade("X", "O", "officer", "P", 100, 10.0, 1000, "2026-08-11", REF.filed),
                InsiderTrade("X", "O", "officer", "P", 300, 20.0, 6000, "2026-08-11", REF.filed),
            ]
        )
        assert trades[0].price == pytest.approx(7000 / 400)

    def test_symbol_comes_from_the_filing(self):
        trades = parse_form4(form4([("A", "CEO")], [("P", 10, 10.0, "2026-08-11")], symbol="INTC"), REF)
        assert trades[0].ticker == "INTC"

    def test_malformed_submission_returns_nothing(self):
        assert parse_form4("not xml at all", REF) == []
        assert parse_form4("<ownershipDocument><broken>", REF) == []


class TestBuyFiltering:
    def make(self, code: str, value: float) -> InsiderTrade:
        return InsiderTrade("NVDA", "Owner", "officer", code, 100, value / 100, value,
                            "2026-08-11", REF.filed)

    def test_only_open_market_purchases_count(self):
        """Grants, option exercises and tax withholding dominate Form 4 volume
        and carry no directional meaning."""
        assert self.make("P", 100_000).is_open_market_buy is True
        for code in ("A", "M", "F", "G", "C", "X", "S", "D"):
            assert self.make(code, 100_000).is_open_market_buy is False, code

    def test_token_purchases_are_filtered_out(self):
        assert self.make("P", MIN_BUY_VALUE_USD - 1).is_open_market_buy is False
        assert self.make("P", MIN_BUY_VALUE_USD).is_open_market_buy is True

    def test_sales_are_reported_but_rated_low(self):
        events = build_events([self.make("S", 17_400_000)], [])
        assert len(events) == 1
        assert events[0].kind == Kind.INSIDER_SALE
        assert events[0].importance == 1
        assert "not a view" in events[0].detail

    def test_large_buys_rate_above_small_ones(self):
        big = build_events([self.make("P", 10_000_000)], [])[0]
        small = build_events([self.make("P", 30_000)], [])[0]
        assert big.importance > small.importance


# ---------------------------------------------------------------------------
# Clusters
# ---------------------------------------------------------------------------


class TestClusters:
    def buy(self, owner: str, when: str, value: float = 100_000) -> InsiderTrade:
        return InsiderTrade("NVDA", owner, "officer", "P", 100, value / 100, value,
                            when, date.fromisoformat(when))

    def test_two_distinct_insiders_form_a_cluster(self):
        events = detect_clusters([self.buy("Alice", "2026-08-01"), self.buy("Bob", "2026-08-10")])
        assert len(events) == 1
        assert events[0].kind == Kind.INSIDER_CLUSTER
        assert events[0].importance == 5

    def test_one_insider_buying_twice_is_not_a_cluster(self):
        """One purchase is weak evidence however often it is repeated."""
        events = detect_clusters([self.buy("Alice", "2026-08-01"), self.buy("Alice", "2026-08-10")])
        assert events == []

    def test_purchases_too_far_apart_are_not_a_cluster(self):
        events = detect_clusters([self.buy("Alice", "2026-01-01"), self.buy("Bob", "2026-08-10")])
        assert events == []

    def test_cluster_threshold_matches_the_constant(self):
        buys = [self.buy(f"P{i}", "2026-08-01") for i in range(CLUSTER_MIN_BUYERS)]
        assert len(detect_clusters(buys)) == 1

    def test_cluster_names_the_buyers(self):
        events = detect_clusters([self.buy("Alice", "2026-08-01"), self.buy("Bob", "2026-08-02")])
        assert "Alice" in events[0].detail and "Bob" in events[0].detail


# ---------------------------------------------------------------------------
# 8-K
# ---------------------------------------------------------------------------


class TestEightK:
    def test_items_read_from_the_header(self):
        text = (
            "ACCESSION NUMBER: 1\n"
            "ITEM INFORMATION: Results of Operations and Financial Condition\n"
            "ITEM INFORMATION: Financial Statements and Exhibits\n"
        )
        assert parse_8k_items(text) == [
            "Results of Operations and Financial Condition",
            "Financial Statements and Exhibits",
        ]

    def test_restatement_rates_highest(self):
        assert rate_8k(["Non-Reliance on Previously Issued Financial Statements"]) == 5

    def test_earnings_rates_high(self):
        assert rate_8k(["Results of Operations and Financial Condition"]) == 4

    def test_boilerplate_alone_rates_zero(self):
        assert rate_8k(["Financial Statements and Exhibits"]) == 0

    def test_most_severe_item_wins(self):
        """Boilerplate is attached to almost every 8-K, so averaging would dilute
        a restatement into background noise."""
        items = ["Financial Statements and Exhibits", "Non-Reliance on Previously Issued"]
        assert rate_8k(items) == 5

    def test_unknown_item_rates_one_not_zero(self):
        assert rate_8k(["Some Novel Item Nobody Listed"]) == 1

    def test_no_items_rates_zero(self):
        assert rate_8k([]) == 0

    def test_earnings_filings_are_typed_as_earnings(self):
        ref = FilingRef(1, "CSCO", "CISCO", "8-K", date(2026, 8, 12), "p")
        events = build_events([], [(ref, ["Results of Operations and Financial Condition"])])
        assert events[0].kind == Kind.EARNINGS

    def test_other_material_events_are_typed_separately(self):
        ref = FilingRef(1, "NEE", "NEXTERA", "8-K", date(2026, 8, 11), "p")
        events = build_events([], [(ref, ["Other Events"])])
        assert events[0].kind == Kind.MATERIAL_EVENT


# ---------------------------------------------------------------------------
# Ranking and reporting
# ---------------------------------------------------------------------------


class TestRanking:
    def event(self, ticker: str, importance: int, score: float | None):
        ref = FilingRef(1, ticker, ticker, "8-K", date(2026, 8, 12), "p")
        built = build_events([], [(ref, ["Other Events"])])[0]
        built.importance = importance
        built.composite_score = score
        return built

    def test_importance_dominates_company_quality(self):
        strong_minor = self.event("A", 1, 95.0)
        weak_major = self.event("B", 5, 10.0)
        ordered = sorted([strong_minor, weak_major], key=lambda e: e.sort_key())
        assert ordered[0].ticker == "B"

    def test_quality_breaks_ties_on_equal_importance(self):
        low = self.event("A", 4, 20.0)
        high = self.event("B", 4, 80.0)
        ordered = sorted([low, high], key=lambda e: e.sort_key())
        assert ordered[0].ticker == "B"

    def test_unscored_sorts_last_among_equals_not_first(self):
        """A missing score means missing data, not a bad company - but it must not
        outrank a measured one either."""
        unscored = self.event("A", 4, None)
        scored = self.event("B", 4, 30.0)
        ordered = sorted([unscored, scored], key=lambda e: e.sort_key())
        assert ordered[0].ticker == "B"

    def test_scores_attach_by_ticker(self):
        events = [self.event("NVDA", 4, None)]
        attach_scores(events, {"NVDA": 79.3})
        assert events[0].composite_score == 79.3


class TestFiltering:
    def scan(self) -> EventScan:
        ref = FilingRef(1, "X", "X", "8-K", date(2026, 8, 12), "p")
        low = build_events([], [(ref, ["Financial Statements and Exhibits"])])[0]
        high = build_events([], [(ref, ["Results of Operations"])])[0]
        low.composite_score = 20.0
        high.composite_score = 70.0
        return EventScan(date(2026, 8, 17), 5, [low, high], 2, 1)

    def test_importance_filter(self):
        assert len(self.scan().filtered(min_importance=3)) == 1

    def test_score_filter_keeps_unscored_names(self):
        result = self.scan()
        result.events[0].composite_score = None
        kept = result.filtered(min_score=50.0)
        assert any(e.composite_score is None for e in kept)

    def test_report_is_ascii_safe(self):
        report(self.scan()).encode("cp1252")

    def test_report_states_events_do_not_fire_signals(self):
        assert "never fire a signal" in report(self.scan())

    def test_empty_result_says_so_rather_than_looking_broken(self):
        empty = EventScan(date(2026, 8, 17), 1, [], 0, 41)
        assert "normal result" in report(empty)


class TestBusinessDays:
    def test_skips_weekends(self):
        # 2026-08-17 is a Monday.
        days = business_days(date(2026, 8, 17), 3)
        assert days == [date(2026, 8, 13), date(2026, 8, 14), date(2026, 8, 17)]

    def test_returns_oldest_first(self):
        days = business_days(date(2026, 8, 17), 5)
        assert days == sorted(days)
        assert len(days) == 5
