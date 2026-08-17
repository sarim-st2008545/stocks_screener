"""
Tests for fundamental scoring.

Percentile direction gets its own block because getting it backwards is both
easy and silent: every number still looks plausible, the table still sorts, and
the ranking is simply upside down. A first run put Intel top of profitability
on a negative net margin and NVIDIA near the bottom on a 56% one.
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scoring as sc

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


REFERENCE_DATE = date(2026, 8, 6)


def instant(concept, end, val):
    return concept, {"end": end, "val": val, "filed": "2026-07-01", "form": "10-Q"}


def duration(concept, start, end, val, form="10-K"):
    return concept, {"start": start, "end": end, "val": val, "filed": "2026-07-01", "form": form}


def build(*entries):
    us_gaap = {}
    for concept, entry in entries:
        us_gaap.setdefault(concept, {"units": {"USD": []}})["units"]["USD"].append(entry)
    return {"facts": {"us-gaap": us_gaap}}


# ---------------------------------------------------------------------------
print("--- percentile direction ---")

values = {"BEST": 10.0, "MID": 5.0, "WORST": 1.0}

ranks = sc.percentile_ranks(values, higher_is_better=True)
check("largest value scores 100 when higher is better", ranks["BEST"], 100.0)
check("smallest value scores 0 when higher is better", ranks["WORST"], 0.0)
approx("middle sits in between", ranks["MID"], 50.0)

ranks = sc.percentile_ranks(values, higher_is_better=False)
check("smallest value scores 100 when lower is better", ranks["WORST"], 100.0)
check("largest value scores 0 when lower is better", ranks["BEST"], 0.0)

# Negative values must not be treated as large. This is the exact shape of the
# original bug: a loss-making margin outranking a profitable one.
margins = {"PROFITABLE": 0.55, "THIN": 0.02, "LOSS": -0.08}
ranks = sc.percentile_ranks(margins, higher_is_better=True)
check("profitable outranks loss-making", ranks["PROFITABLE"] > ranks["LOSS"], True)
check("loss-making scores bottom", ranks["LOSS"], 0.0)

check("empty input", sc.percentile_ranks({}, True), {})
check("single value sits mid-scale", sc.percentile_ranks({"ONLY": 3.0}, True), {"ONLY": 50.0})

tied = sc.percentile_ranks({"A": 5.0, "B": 5.0, "C": 1.0}, higher_is_better=True)
check("ties share a rank", tied["A"], tied["B"])
check("tied pair beats the lone low value", tied["A"] > tied["C"], True)

# ---------------------------------------------------------------------------
print("--- fundamentals extraction ---")

FACTS = build(
    duration("Revenues", "2025-07-01", "2026-06-30", 1_000.0),
    duration("Revenues", "2024-07-01", "2025-06-30", 800.0),
    duration("OperatingIncomeLoss", "2025-07-01", "2026-06-30", 200.0),
    duration("NetIncomeLoss", "2025-07-01", "2026-06-30", 150.0),
    duration("NetCashProvidedByUsedInOperatingActivities", "2025-07-01", "2026-06-30", 250.0),
    duration("NetCashProvidedByUsedInOperatingActivities", "2024-07-01", "2025-06-30", 200.0),
    duration("PaymentsToAcquirePropertyPlantAndEquipment", "2025-07-01", "2026-06-30", 50.0),
    duration("PaymentsToAcquirePropertyPlantAndEquipment", "2024-07-01", "2025-06-30", 40.0),
    duration("DepreciationDepletionAndAmortization", "2025-07-01", "2026-06-30", 60.0),
    duration("IncomeTaxExpenseBenefit", "2025-07-01", "2026-06-30", 40.0),
    duration(
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "2025-07-01", "2026-06-30", 190.0,
    ),
    instant("StockholdersEquity", "2026-06-30", 500.0),
    instant("LongTermDebt", "2026-06-30", 300.0),
    instant("CashAndCashEquivalentsAtCarryingValue", "2026-06-30", 100.0),
)

f = sc.extract_fundamentals("TEST", FACTS, market_cap=2_000.0, reference_date=REFERENCE_DATE)
approx("revenue", f.revenue, 1_000.0)
approx("prior-year revenue found", f.revenue_prior, 800.0)
approx("revenue growth", f.revenue_growth, 0.25)
approx("free cash flow is OCF minus capex", f.fcf, 200.0)
approx("prior free cash flow", f.fcf_prior, 160.0)
approx("fcf growth", f.fcf_growth, 0.25)
approx("EBITDA is operating income plus D&A", f.ebitda, 260.0)
approx("operating margin", f.operating_margin, 0.20)
approx("net margin", f.net_margin, 0.15)
approx("fcf margin", f.fcf_margin, 0.20)
approx("return on equity", f.roe, 0.30)
approx("net debt is debt minus cash", f.net_debt, 200.0)
approx("net debt to EBITDA", f.net_debt_to_ebitda, 200.0 / 260.0)
approx("debt to equity", f.debt_to_equity, 0.6)
approx("fcf yield", f.fcf_yield, 0.10)
approx("earnings yield", f.earnings_yield, 0.075)
# EV = 2000 market cap + 300 debt - 100 cash = 2200
approx("EV to EBITDA", f.ev_to_ebitda, 2_200.0 / 260.0)
# Effective tax 40/190 = 21.05%; NOPAT = 200 * 0.7895; invested = 500+300-100
approx("ROIC", f.roic, 200.0 * (1 - 40.0 / 190.0) / 700.0, tol=1e-4)
check("no gaps on complete data", f.gaps, [])

print("--- degenerate inputs ---")

no_facts = sc.extract_fundamentals("NONE", None, 1_000.0, reference_date=REFERENCE_DATE)
check("missing facts flagged", no_facts.gaps, ["no XBRL facts"])
check("no revenue on missing facts", no_facts.revenue, None)

no_revenue = sc.extract_fundamentals(
    "NOREV", build(instant("StockholdersEquity", "2026-06-30", 500.0)), 1_000.0,
    reference_date=REFERENCE_DATE,
)
check("missing revenue flagged", no_revenue.gaps, ["no usable revenue period"])

# Negative equity makes ROE meaningless, not excellent.
negative_equity = sc.extract_fundamentals(
    "NEGEQ",
    build(
        duration("Revenues", "2025-07-01", "2026-06-30", 1_000.0),
        duration("NetIncomeLoss", "2025-07-01", "2026-06-30", 150.0),
        instant("StockholdersEquity", "2026-06-30", -500.0),
    ),
    market_cap=2_000.0,
    reference_date=REFERENCE_DATE,
)
check("negative equity gives no ROE", negative_equity.roe, None)
check("negative equity gives no debt/equity", negative_equity.debt_to_equity, None)

# A loss-making company still gets a meaningful negative yield, which is the
# reason yields are used instead of P/E.
loss = sc.extract_fundamentals(
    "LOSS",
    build(
        duration("Revenues", "2025-07-01", "2026-06-30", 1_000.0),
        duration("NetIncomeLoss", "2025-07-01", "2026-06-30", -200.0),
    ),
    market_cap=1_000.0,
    reference_date=REFERENCE_DATE,
)
approx("loss gives a negative earnings yield", loss.earnings_yield, -0.2)
approx("loss gives a negative net margin", loss.net_margin, -0.2)

negative_ebitda = sc.extract_fundamentals(
    "NEGEBITDA",
    build(
        duration("Revenues", "2025-07-01", "2026-06-30", 1_000.0),
        duration("OperatingIncomeLoss", "2025-07-01", "2026-06-30", -100.0),
        duration("DepreciationDepletionAndAmortization", "2025-07-01", "2026-06-30", 20.0),
        instant("LongTermDebt", "2026-06-30", 300.0),
    ),
    market_cap=1_000.0,
    reference_date=REFERENCE_DATE,
)
check("negative EBITDA gives no EV/EBITDA", negative_ebitda.ev_to_ebitda, None)
check("negative EBITDA gives no leverage multiple", negative_ebitda.net_debt_to_ebitda, None)

# ---------------------------------------------------------------------------
print("--- universe scoring ---")


def make(ticker, sic=3674, **kwargs):
    item = sc.Fundamentals(ticker=ticker, sic=sic, status="PASS", market_cap=1e9)
    for key, value in kwargs.items():
        setattr(item, key, value)
    return item


pool = [
    make("STRONG", revenue_growth=0.40, fcf_growth=0.40, operating_margin=0.35,
         net_margin=0.30, fcf_margin=0.30, roe=0.45, roic=0.35,
         net_debt_to_ebitda=0.2, debt_to_equity=0.1,
         fcf_yield=0.08, earnings_yield=0.07, ev_to_ebitda=10.0),
    make("MIDDLING", revenue_growth=0.10, fcf_growth=0.08, operating_margin=0.15,
         net_margin=0.10, fcf_margin=0.09, roe=0.15, roic=0.12,
         net_debt_to_ebitda=2.0, debt_to_equity=0.8,
         fcf_yield=0.04, earnings_yield=0.03, ev_to_ebitda=18.0),
    make("WEAK", revenue_growth=-0.10, fcf_growth=-0.20, operating_margin=0.01,
         net_margin=-0.05, fcf_margin=-0.02, roe=-0.10, roic=-0.03,
         net_debt_to_ebitda=6.0, debt_to_equity=3.0,
         fcf_yield=-0.01, earnings_yield=-0.02, ev_to_ebitda=40.0),
]
scores = {s.ticker: s for s in sc.score_universe(pool)}

check("strongest name tops growth", scores["STRONG"].growth, 100.0)
check("strongest name tops profitability", scores["STRONG"].profitability, 100.0)
check("strongest name tops quality", scores["STRONG"].quality, 100.0)
check("least levered tops leverage", scores["STRONG"].leverage, 100.0)
check("cheapest tops valuation", scores["STRONG"].valuation, 100.0)
check("weakest bottoms out", scores["WEAK"].composite, 0.0)
check("ordering is strong > middling > weak",
      scores["STRONG"].composite > scores["MIDDLING"].composite > scores["WEAK"].composite, True)
check("results sorted best first", sc.score_universe(pool)[0].ticker, "STRONG")

# A name lacking a pillar should be judged on the pillars it has, not dragged
# down by the absence.
partial = pool + [make("NOLEV", revenue_growth=0.40, fcf_growth=0.40,
                       operating_margin=0.35, net_margin=0.30, fcf_margin=0.30,
                       roe=0.45, roic=0.35, fcf_yield=0.08, earnings_yield=0.07,
                       ev_to_ebitda=10.0)]
partial_scores = {s.ticker: s for s in sc.score_universe(partial)}
check("missing pillar recorded", partial_scores["NOLEV"].pillars_missing, ["leverage"])
check("missing pillar does not zero the composite",
      partial_scores["NOLEV"].composite > 50, True)

empty = sc.score_universe([make("BLANK")])
check("name with no metrics gets no composite", empty[0].composite, None)
check("all five pillars reported missing", len(empty[0].pillars_missing), 5)

print()
if fails:
    print(f"FAILED ({len(fails)}):")
    for f in fails:
        print("  x", f)
    sys.exit(1)
print("ALL TESTS PASSED")
