"""
Tests for the AAOIFI screener's deterministic logic.

The network layer is not covered here. Everything below runs against
hand-built XBRL blobs so results do not drift with market data or filings.

REFERENCE_DATE is pinned so staleness tests stay meaningful over time.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import aaoifi_screener as s

REFERENCE_DATE = date(2026, 8, 5)

fails = []


def check(name, got, want):
    if got != want:
        fails.append(f"{name}: got {got!r}, want {want!r}")
    else:
        print(f"  ok  {name}")


def approx(name, got, want, tol=1e-6):
    if got is None or abs(got - want) > tol:
        fails.append(f"{name}: got {got!r}, want ~{want!r}")
    else:
        print(f"  ok  {name}")


def instant(concept, end, val, filed="2026-07-01", form="10-Q"):
    return concept, {"end": end, "val": val, "filed": filed, "form": form}


def duration(concept, start, end, val, filed="2026-07-01", form="10-Q"):
    return concept, {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def build(*entries):
    """Assemble a companyfacts-shaped blob from (concept, entry) pairs."""
    us_gaap = {}
    for concept, entry in entries:
        us_gaap.setdefault(concept, {"units": {"USD": []}})["units"]["USD"].append(entry)
    return {"facts": {"us-gaap": us_gaap}}


def sel(facts):
    return s.FactSelector(facts, reference_date=REFERENCE_DATE)


# ---------------------------------------------------------------------------
print("--- SIC classification ---")
check("JPMorgan bank (6021)", s.classify_sic(6021)[0], s.Status.FAIL)
check("Brewer 2082", s.classify_sic(2082)[0], s.Status.FAIL)
check("Tobacco 2111", s.classify_sic(2111)[0], s.Status.FAIL)
check("Insurance 6311", s.classify_sic(6311)[0], s.Status.FAIL)
check("Prepackaged software 7372", s.classify_sic(7372)[0], s.Status.PASS)
check("Semiconductors 3674", s.classify_sic(3674)[0], s.Status.PASS)
check("Hotels 7011", s.classify_sic(7011)[0], s.Status.REVIEW)
check("REIT 6798", s.classify_sic(6798)[0], s.Status.REVIEW)
check("Soft drinks 2086 (should pass)", s.classify_sic(2086)[0], s.Status.PASS)
check("None", s.classify_sic(None)[0], s.Status.INSUFFICIENT_DATA)

# Regression: Constellation Brands is an alcohol producer filing under the
# generic beverages code 2080, which the old 2082-2085 range let straight past.
check("Generic beverages 2080 -> REVIEW", s.classify_sic(2080)[0], s.Status.REVIEW)
check("STZ override by CIK -> FAIL", s.classify_sic(2080, cik=16918)[0], s.Status.FAIL)

print("--- cap tiers ---")
check("3.5T mega", s.classify_cap_tier(3.5e12), "mega")
check("50B large", s.classify_cap_tier(50e9), "large")
check("5B mid", s.classify_cap_tier(5e9), "mid")
check("500M small", s.classify_cap_tier(500e6), "small")
check("100M micro", s.classify_cap_tier(100e6), "micro")
check("None", s.classify_cap_tier(None), "unknown")

# ---------------------------------------------------------------------------
print("--- fact normalisation ---")

# Restatements: same period filed twice, newest filing wins.
restated = build(
    instant("Assets", "2026-06-30", 100, filed="2026-07-01"),
    instant("Assets", "2026-06-30", 110, filed="2026-08-01"),
)
facts = sel(restated).facts_for("Assets")
check("restatement collapses to one fact", len(facts), 1)
check("newest filing wins", facts[0].value, 110.0)

# Staleness: a concept a company stopped maintaining must read as absent.
stale_only = build(instant("LongTermDebt", "2011-03-31", 100_000_000))
check("stale-only concept -> no facts", sel(stale_only).facts_for("LongTermDebt"), [])
check("stale-only concept -> instant None", sel(stale_only).instant("LongTermDebt"), None)

# Balance-sheet and income-statement facts age out on different clocks: a
# balance sheet from 600 days ago is not current, but a quarter from 600 days
# ago is a legitimate link in a trailing-twelve-month chain.
split_horizon = build(
    instant("LongTermDebt", "2024-12-31", 500),
    duration("Revenues", "2024-10-01", "2024-12-31", 500),
)
check("instant past 550 days drops", sel(split_horizon).instant("LongTermDebt"), None)
check(
    "duration past 550 days survives",
    len(sel(split_horizon).facts_for("Revenues")),
    1,
)

# Regression (Eli Lilly): three fresh quarters and a fourth just past the old
# 550-day cutoff meant no TTM could be built and the ratio read as unknown.
lilly_style = build(
    duration("InvestmentIncomeInterest", "2024-10-01", "2024-12-31", 40),
    duration("InvestmentIncomeInterest", "2025-01-01", "2025-03-31", 35),
    duration("InvestmentIncomeInterest", "2025-04-01", "2025-06-30", 38),
    duration("InvestmentIncomeInterest", "2025-07-01", "2025-09-30", 40),
)
chains = [
    w for w in sel(lilly_style).ttm_candidates("InvestmentIncomeInterest")
    if w.basis == "4x quarterly"
]
check("chain spanning the instant cutoff still forms", len(chains), 1)
approx("chain sums all four quarters", chains[0].value, 153.0)

# Regression (Microsoft, Mastercard): neither tags a pure interest concept, so
# the fallback list has to reach InvestmentIncomeNet or the ratio is lost.
msft_style = build(
    duration("InvestmentIncomeNet", "2025-07-01", "2026-06-30", 3_301_000_000, form="10-K"),
)
check(
    "InvestmentIncomeNet resolves when nothing else is tagged",
    len(sel(msft_style).ttm_candidates_first(s.INTEREST_INCOME_CONCEPTS)),
    1,
)

# Duration classification.
mixed = build(
    duration("Revenues", "2026-04-01", "2026-06-30", 90),
    duration("Revenues", "2025-07-01", "2026-06-30", 360),
    instant("Assets", "2026-06-30", 1000),
)
rev_facts = {f.days: f for f in sel(mixed).facts_for("Revenues")}
check("quarter classified", rev_facts[90].is_quarterly, True)
check("annual classified", rev_facts[364].is_annual, True)
check("balance-sheet fact is instant", sel(mixed).instant("Assets").is_instant, True)

# ---------------------------------------------------------------------------
print("--- debt resolution ---")

# Regression (Mastercard): LongTermDebt was abandoned in 2011 while the real
# debt kept being tagged under the component concepts. The old resolver took
# the stale tag and reported near-zero debt.
ma_style = build(
    instant("LongTermDebt", "2011-03-31", 100_000_000),
    instant("LongTermDebtNoncurrent", "2026-06-30", 15_000_000_000),
    instant("LongTermDebtCurrent", "2026-06-30", 3_000_000_000),
)
est = s.resolve_debt(sel(ma_style))
approx("stale tag ignored, components used", est.value, 18_000_000_000.0)
check("debt as-of is current", est.as_of, date(2026, 6, 30))

# Regression (Marriott): LongTermDebt is current but holds only $23M; the real
# $16.5bn sits in DebtAndCapitalLeaseObligations. Largest candidate wins.
mar_style = build(
    instant("LongTermDebt", "2026-06-30", 23_000_000),
    instant("DebtAndCapitalLeaseObligations", "2026-06-30", 16_915_000_000),
)
est = s.resolve_debt(sel(mar_style))
approx("understated tag overridden by larger measure", est.value, 16_915_000_000.0)
check("candidates recorded for audit", len(est.candidates), 2)

# Regression (Realty Income): REITs use NotesPayable/SecuredDebt, none of which
# the old concept list looked at, so debt read as 8% instead of ~50%.
reit_style = build(
    instant("NotesPayable", "2026-03-31", 24_911_912_000),
    instant("SecuredDebt", "2026-03-31", 37_420_000),
)
approx("REIT debt found", s.resolve_debt(sel(reit_style)).value, 24_949_332_000.0)

# Components from different filings must not be summed together.
misaligned = build(
    instant("LongTermDebtNoncurrent", "2026-06-30", 15_000_000_000),
    instant("LongTermDebtCurrent", "2025-06-30", 3_000_000_000),
)
approx("misaligned components not summed", s.resolve_debt(sel(misaligned)).value, 15_000_000_000.0)

check("no debt tagged -> None (not 0)", s.resolve_debt(sel(build())).value, None)

# ---------------------------------------------------------------------------
print("--- liquid assets ---")

liquid_facts = build(
    instant("CashAndCashEquivalentsAtCarryingValue", "2026-06-30", 30_000_000_000),
    instant("ShortTermInvestments", "2026-06-30", 20_000_000_000),
    instant("MarketableSecuritiesNoncurrent", "2026-06-30", 50_000_000_000),
)
approx(
    "cash + short-term + non-current securities",
    s.resolve_liquid_assets(sel(liquid_facts)).value,
    100_000_000_000.0,
)

# Overlapping short-term tags describe the same pool; take the largest, not the sum.
overlapping = build(
    instant("CashAndCashEquivalentsAtCarryingValue", "2026-06-30", 10_000_000_000),
    instant("ShortTermInvestments", "2026-06-30", 20_000_000_000),
    instant("MarketableSecuritiesCurrent", "2026-06-30", 18_000_000_000),
)
approx(
    "overlapping short-term tags not double-counted",
    s.resolve_liquid_assets(sel(overlapping)).value,
    30_000_000_000.0,
)

check("nothing tagged -> None", s.resolve_liquid_assets(sel(build())).value, None)

# ---------------------------------------------------------------------------
print("--- trailing twelve months ---")

quarters_only = build(
    duration("Revenues", "2025-07-01", "2025-09-30", 100),
    duration("Revenues", "2025-10-01", "2025-12-31", 200),
    duration("Revenues", "2026-01-01", "2026-03-31", 300),
    duration("Revenues", "2026-04-01", "2026-06-30", 400),
)
windows = sel(quarters_only).ttm_candidates("Revenues")
chain = [w for w in windows if w.basis == "4x quarterly"]
check("four quarters chain into one TTM", len(chain), 1)
approx("TTM sums the four quarters", chain[0].value, 1000.0)
check("TTM window starts at oldest quarter", chain[0].start, date(2025, 7, 1))
check("TTM window ends at newest quarter", chain[0].end, date(2026, 6, 30))

gapped = build(
    duration("Revenues", "2025-07-01", "2025-09-30", 100),
    duration("Revenues", "2026-01-01", "2026-03-31", 300),
    duration("Revenues", "2026-04-01", "2026-06-30", 400),
)
check(
    "broken quarter chain yields no TTM",
    [w for w in sel(gapped).ttm_candidates("Revenues") if w.basis == "4x quarterly"],
    [],
)

# ---------------------------------------------------------------------------
print("--- period alignment ---")

# Regression (Visa): annual interest income over a single quarter of revenue
# produced 7.0% and a false FAIL. The true ratio against the matching fiscal
# year is ~2.0%.
visa_style = build(
    duration("RevenueFromContractWithCustomerExcludingAssessedTax",
             "2024-10-01", "2025-09-30", 39_900_000_000, form="10-K"),
    duration("RevenueFromContractWithCustomerExcludingAssessedTax",
             "2025-04-01", "2025-06-30", 9_900_000_000),
    duration("RevenueFromContractWithCustomerExcludingAssessedTax",
             "2025-07-01", "2025-09-30", 10_100_000_000),
    duration("RevenueFromContractWithCustomerExcludingAssessedTax",
             "2025-10-01", "2025-12-31", 10_700_000_000),
    duration("RevenueFromContractWithCustomerExcludingAssessedTax",
             "2026-01-01", "2026-03-31", 11_230_000_000),
    duration("InvestmentIncomeInterestAndDividend",
             "2024-10-01", "2025-09-30", 791_000_000, form="10-K"),
)
selector = sel(visa_style)
pair = s.align_windows(
    selector.ttm_candidates_first(s.INTEREST_INCOME_CONCEPTS),
    selector.ttm_candidates_first(s.REVENUE_CONCEPTS),
)
check("aligned pair found", pair is not None, True)
interest_w, revenue_w = pair
check("alignment ignores the newer unmatched quarter chain", revenue_w.end, date(2025, 9, 30))
approx("ratio computed on matching periods", interest_w.value / revenue_w.value, 0.019824, tol=1e-5)

unalignable = build(
    duration("Revenues", "2025-07-01", "2026-06-30", 1000, form="10-K"),
    duration("InvestmentIncomeInterest", "2023-01-01", "2023-12-31", 50, form="10-K"),
)
sel_un = sel(unalignable)
check(
    "stale interest income drops out entirely",
    sel_un.ttm_candidates_first(s.INTEREST_INCOME_CONCEPTS),
    [],
)

# ---------------------------------------------------------------------------
print("--- end-to-end with mocked client ---")


class MockClient:
    def __init__(self, sic, facts, cik=320193):
        self.sic, self._facts, self._cik = sic, facts, cik

    def ticker_to_cik(self, t):
        return self._cik

    def submissions(self, cik):
        return {"name": "Test Corp", "sic": str(self.sic)}

    def company_facts(self, cik):
        return self._facts


s.get_market_cap = lambda t: 1_000_000_000_000.0  # 1T
s.yf_info = lambda t: {}

clean = build(
    instant("LongTermDebt", "2026-06-30", 98_000_000_000),
    instant("CashAndCashEquivalentsAtCarryingValue", "2026-06-30", 30_000_000_000),
    instant("ShortTermInvestments", "2026-06-30", 20_000_000_000),
    duration("Revenues", "2025-07-01", "2026-06-30", 400_000_000_000, form="10-K"),
    duration("InvestmentIncomeInterest", "2025-07-01", "2026-06-30", 4_000_000_000, form="10-K"),
)

r = s.screen_ticker("TEST", MockClient(7372, clean), reference_date=REFERENCE_DATE)
print(f"  status={r.status.value} debt={r.debt_ratio:.2%} "
      f"liquid={r.liquid_ratio:.2%} income={r.income_ratio:.2%}")
check("clean tech co passes", r.status, s.Status.PASS)
approx("debt ratio 9.8%", r.debt_ratio, 0.098)
approx("liquid ratio 5.0%", r.liquid_ratio, 0.05)
approx("income ratio 1.0%", r.income_ratio, 0.01)
check("cap tier mega", r.cap_tier, "mega")
check("balance sheet date recorded", r.balance_sheet_date, "2026-06-30")

rb = s.screen_ticker("BANK", MockClient(6021, clean), reference_date=REFERENCE_DATE)
check("bank fails on sector", rb.status, s.Status.FAIL)
check("bank ratios not computed", rb.debt_ratio, None)

heavy = build(
    instant("LongTermDebt", "2026-06-30", 400_000_000_000),
    instant("CashAndCashEquivalentsAtCarryingValue", "2026-06-30", 30_000_000_000),
    duration("Revenues", "2025-07-01", "2026-06-30", 400_000_000_000, form="10-K"),
    duration("InvestmentIncomeInterest", "2025-07-01", "2026-06-30", 4_000_000_000, form="10-K"),
)
check("40% debt fails", s.screen_ticker("HEAVY", MockClient(7372, heavy),
                                        reference_date=REFERENCE_DATE).status, s.Status.FAIL)

no_interest = build(
    instant("LongTermDebt", "2026-06-30", 98_000_000_000),
    instant("CashAndCashEquivalentsAtCarryingValue", "2026-06-30", 30_000_000_000),
    duration("Revenues", "2025-07-01", "2026-06-30", 400_000_000_000, form="10-K"),
)
rn = s.screen_ticker("NOINT", MockClient(7372, no_interest), reference_date=REFERENCE_DATE)
check("untagged interest income -> REVIEW", rn.status, s.Status.REVIEW)
check("reason names the gap", any("interest income not separately tagged" in x
                                  for x in rn.reasons), True)

ripo = s.screen_ticker("IPO", MockClient(7372, None), reference_date=REFERENCE_DATE)
check("no facts -> INSUFFICIENT_DATA", ripo.status, s.Status.INSUFFICIENT_DATA)

# A ratio built from a window well over a year old is reported, but must not
# be presented as a clean PASS.
old_window = build(
    instant("LongTermDebt", "2026-06-30", 98_000_000_000),
    instant("CashAndCashEquivalentsAtCarryingValue", "2026-06-30", 30_000_000_000),
    duration("Revenues", "2024-02-01", "2025-01-31", 400_000_000_000, form="10-K"),
    duration("InvestmentIncomeInterest", "2024-02-01", "2025-01-31", 4_000_000_000, form="10-K"),
)
ro = s.screen_ticker("OLDWIN", MockClient(7372, old_window), reference_date=REFERENCE_DATE)
check("stale income window -> REVIEW not PASS", ro.status, s.Status.REVIEW)
approx("ratio still reported", ro.income_ratio, 0.01)
check("reason names the stale period", any("old period" in x for x in ro.reasons), True)

# ---------------------------------------------------------------------------
print("--- non-compliant income bound ---")

BASE_BALANCE = [
    instant("LongTermDebt", "2026-06-30", 98_000_000_000),
    instant("CashAndCashEquivalentsAtCarryingValue", "2026-06-30", 30_000_000_000),
]
REV = duration("Revenues", "2025-07-01", "2026-06-30", 400_000_000_000, form="10-K")


def bounded(*extra):
    return build(*BASE_BALANCE, REV, *extra)


small_bound = bounded(
    duration("NonoperatingIncomeExpense", "2025-07-01", "2026-06-30", 320_000_000, form="10-K")
)
rbound = s.screen_ticker("BOUND", MockClient(7372, small_bound), reference_date=REFERENCE_DATE)
approx("bound ratio computed", rbound.income_bound_ratio, 0.0008)
check("bounded name stays REVIEW, never PASS", rbound.status, s.Status.REVIEW)
check("exact ratio still unknown", rbound.income_ratio, None)
check(
    "reason states the bound clears the limit",
    any("comfortably inside" in x for x in rbound.reasons),
    True,
)

# These lines are net of interest expense and frequently negative; the size of
# the line is what bounds the component, not its sign.
negative_bound = bounded(
    duration("NonoperatingIncomeExpense", "2025-07-01", "2026-06-30", -320_000_000, form="10-K")
)
approx(
    "negative aggregate bounds by magnitude",
    s.screen_ticker("NEG", MockClient(7372, negative_bound), reference_date=REFERENCE_DATE)
    .income_bound_ratio,
    0.0008,
)

# A company tagging several aggregates should be bounded by the most
# interest-specific one, which is also the tightest.
both = bounded(
    duration("NonoperatingIncomeExpense", "2025-07-01", "2026-06-30", 40_000_000_000, form="10-K"),
    duration("InterestIncomeExpenseNonoperatingNet", "2025-07-01", "2026-06-30",
             800_000_000, form="10-K"),
)
rboth = s.screen_ticker("BOTH", MockClient(7372, both), reference_date=REFERENCE_DATE)
check(
    "tightest concept wins",
    rboth.income_bound_source.startswith("InterestIncomeExpenseNonoperatingNet"),
    True,
)
approx("tight bound used", rboth.income_bound_ratio, 0.002)

wide_bound = bounded(
    duration("NonoperatingIncomeExpense", "2025-07-01", "2026-06-30", 40_000_000_000, form="10-K")
)
rwide = s.screen_ticker("WIDE", MockClient(7372, wide_bound), reference_date=REFERENCE_DATE)
approx("wide bound reported", rwide.income_bound_ratio, 0.10)
check(
    "bound above the limit is called out",
    any("ABOVE the 5% limit" in x for x in rwide.reasons),
    True,
)
check("uninformative bound still only REVIEW", rwide.status, s.Status.REVIEW)

no_bound = bounded()
rnb = s.screen_ticker("NOBOUND", MockClient(7372, no_bound), reference_date=REFERENCE_DATE)
check("no aggregate available -> no bound", rnb.income_bound_ratio, None)
check(
    "falls back to the manual-check message",
    any("verify manually in the 10-K" in x for x in rnb.reasons),
    True,
)

# The bound must never displace a real figure.
check("exact ratio takes precedence over any bound", r.income_bound_ratio, None)

# Whole-pipeline regression: a Marriott-shaped filer must not pass on the back
# of an understated debt tag.
mar_full = build(
    instant("LongTermDebt", "2026-06-30", 23_000_000),
    instant("DebtAndCapitalLeaseObligations", "2026-06-30", 500_000_000_000),
    instant("CashAndCashEquivalentsAtCarryingValue", "2026-06-30", 1_000_000_000),
    duration("Revenues", "2025-07-01", "2026-06-30", 25_000_000_000, form="10-K"),
    duration("InvestmentIncomeInterest", "2025-07-01", "2026-06-30", 100_000_000, form="10-K"),
)
rm = s.screen_ticker("MARLIKE", MockClient(7372, mar_full), reference_date=REFERENCE_DATE)
check("understated debt tag does not produce a false PASS", rm.status, s.Status.FAIL)

print()
if fails:
    print(f"FAILED ({len(fails)}):")
    for f in fails:
        print("  x", f)
    sys.exit(1)
print("ALL TESTS PASSED")
