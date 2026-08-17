"""Financial statement construction from XBRL facts.

Turns a point-in-time `FactSet` into the line items and ratios the analysis
rulebook needs, with three properties that matter more than convenience:

**Every number carries its provenance.** A `LineItem` records which concept it
came from, whether it was derived rather than read directly, and the period it
covers. A figure whose origin cannot be shown has no business driving a buy or
sell recommendation.

**Concepts have alternatives, chosen by freshness.** Filers tag inconsistently
and abandon tags without deleting history. Measured against the universe, six of
41 names never tag `Liabilities` at all — it has to come from
`LiabilitiesAndStockholdersEquity` minus equity, or from assets minus equity —
and two tag equity only under the noncontrolling-interest variant.

**Absence is reported, never filled.** A missing figure yields `None` and a note.
It is never replaced by zero or an estimate, because a company that does not tag
debt is not a company without debt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from src.facts import FactSet, Window, align_windows

# ---------------------------------------------------------------------------
# Concept tables
# ---------------------------------------------------------------------------
# Order expresses preference, but resolution prefers the freshest concept and
# uses order only to break ties — an abandoned tag must never beat a maintained
# one. See facts.FactSet.instant_first / ttm_candidates_best.

BALANCE_CONCEPTS: dict[str, list[str]] = {
    "assets": ["Assets"],
    "assets_current": ["AssetsCurrent"],
    "liabilities": ["Liabilities"],
    "liabilities_current": ["LiabilitiesCurrent"],
    "liabilities_and_equity": ["LiabilitiesAndStockholdersEquity"],
    "equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "Cash",
    ],
    "short_term_investments": [
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "OtherShortTermInvestments",
    ],
    "inventory": ["InventoryNet"],
    "receivables": [
        "AccountsReceivableNetCurrent",
        "ReceivablesNetCurrent",
    ],
    "retained_earnings": ["RetainedEarningsAccumulatedDeficit"],
    "ppe_net": ["PropertyPlantAndEquipmentNet"],
    "goodwill": ["Goodwill"],
}

# Debt is the largest defensible measure, not the first one found. Companies
# maintain a narrow tag alongside the real total under a different name, so
# every candidate is computed and the largest wins: understating debt flatters
# leverage and safety scores, whereas overstating it surfaces for review. All
# candidates are reported so the choice stays auditable.
DEBT_CANDIDATES: list[tuple[str, list[str]]] = [
    ("total debt", ["DebtLongtermAndShorttermCombinedAmount"]),
    ("long-term debt", ["LongTermDebt"]),
    ("debt and capital leases", ["DebtAndCapitalLeaseObligations"]),
    (
        "long-term + current portion",
        ["LongTermDebtNoncurrent", "LongTermDebtCurrent"],
    ),
    (
        "notes and loans",
        ["NotesAndLoansPayable", "NotesPayable", "SecuredDebt", "UnsecuredDebt"],
    ),
    (
        "borrowings + long-term",
        ["ShortTermBorrowings", "LongTermDebtNoncurrent"],
    ),
    (
        "commercial paper + long-term",
        ["CommercialPaper", "LongTermDebtNoncurrent"],
    ),
    ("senior notes", ["SeniorNotes"]),
    ("convertible debt", ["ConvertibleDebtNoncurrent"]),
    # Finance leases are financing arrangements and count as debt; operating
    # leases are deliberately excluded. ARM tags no conventional borrowing at
    # all — only finance leases — so without these it reports debt as unknown
    # and loses its leverage scoring entirely despite being genuinely unlevered.
    ("finance leases", ["FinanceLeaseLiability"]),
    (
        "finance leases (split)",
        ["FinanceLeaseLiabilityCurrent", "FinanceLeaseLiabilityNoncurrent"],
    ),
]

INCOME_CONCEPTS: dict[str, list[str]] = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "RevenueFromContractsWithCustomers",  # IFRS
    ],
    "cost_of_revenue": [
        "CostOfRevenue",
        "CostOfGoodsAndServicesSold",
        "CostOfGoodsSold",
        "CostOfSales",
    ],
    "gross_profit": ["GrossProfit"],
    "operating_income": ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "pretax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "ProfitLossBeforeTax",
    ],
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "interest_expense": [
        "InterestExpense",
        "InterestExpenseDebt",
        "InterestExpenseNonoperating",
        "InterestAndDebtExpense",
    ],
    "research_development": [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
    ],
    "depreciation_amortisation": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "DepreciationAndAmortization",
        "Depreciation",
    ],
}

CASHFLOW_CONCEPTS: dict[str, list[str]] = {
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "CashFlowsFromUsedInOperatingActivities",  # IFRS
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForCapitalImprovements",
        "PurchaseOfPropertyPlantAndEquipment",
    ],
    "dividends_paid": [
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividends",
        "PaymentsOfDistributionsToAffiliates",
    ],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
}


# ---------------------------------------------------------------------------
# Line items
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LineItem:
    """One resolved figure, and where it came from."""

    name: str
    value: float | None
    source: str = ""
    derived: bool = False
    period_start: date | None = None
    period_end: date | None = None
    filed: date | None = None
    basis: str = ""

    @property
    def present(self) -> bool:
        return self.value is not None

    def label(self) -> str:
        if not self.present:
            return f"{self.name}: unavailable"
        origin = f"derived: {self.source}" if self.derived else self.source
        period = f" @{self.period_end}" if self.period_end else ""
        return f"{self.name}: {self.value:,.0f} ({origin}{period})"


MISSING = LineItem("", None)


def _missing(name: str, why: str = "not tagged") -> LineItem:
    return LineItem(name, None, source=why)


# ---------------------------------------------------------------------------
# Fundamentals
# ---------------------------------------------------------------------------


@dataclass
class DebtEstimate:
    """Total interest-bearing debt, with every candidate considered."""

    value: float | None
    source: str
    candidates: dict[str, float] = field(default_factory=dict)
    period_end: date | None = None


class Fundamentals:
    """Resolved statements and ratios for one company at one point in time.

    Balance-sheet items are instants; income and cash-flow items are trailing
    twelve months, chained from quarters where no annual fact exists. Ratios
    mixing the two use the ending balance rather than an average, which is
    stated here because the choice materially affects ROE and ROIC.
    """

    def __init__(self, view: FactSet, ticker: str = ""):
        self.view = view
        self.ticker = ticker.upper()
        self.as_of = view.as_of
        self.currency = view.reporting_currency
        self._notes: list[str] = []

    # -- resolution helpers -------------------------------------------------

    def _instant(self, key: str) -> LineItem:
        concepts = BALANCE_CONCEPTS.get(key, [])
        fact = self.view.instant_first(concepts)
        if fact is None:
            return _missing(key)
        return LineItem(
            key,
            fact.value,
            source=fact.concept,
            period_end=fact.end,
            filed=fact.filed,
        )

    def _instant_max(self, key: str) -> LineItem:
        """For pools where overlapping tags would double-count if summed."""
        concepts = BALANCE_CONCEPTS.get(key, [])
        fact = self.view.instant_max(concepts)
        if fact is None:
            return _missing(key)
        return LineItem(
            key, fact.value, source=fact.concept, period_end=fact.end, filed=fact.filed
        )

    def _ttm(self, table: dict[str, list[str]], key: str) -> LineItem:
        window = self.view.ttm(table.get(key, []))
        if window is None:
            return _missing(key)
        return LineItem(
            key,
            window.value,
            source=window.concept,
            period_start=window.start,
            period_end=window.end,
            filed=window.filed,
            basis=window.basis,
        )

    def _ttm_windows(self, table: dict[str, list[str]], key: str) -> list[Window]:
        return self.view.ttm_candidates_best(table.get(key, []))

    # -- balance sheet ------------------------------------------------------

    @property
    def assets(self) -> LineItem:
        return self._instant("assets")

    @property
    def equity(self) -> LineItem:
        return self._instant("equity")

    @property
    def liabilities(self) -> LineItem:
        """Total liabilities, derived where a filer does not tag it.

        Six of 41 universe names never tag `Liabilities`. The accounting
        identity supplies it exactly, so deriving is right — but the derivation
        is recorded rather than presented as a filed figure.
        """
        direct = self._instant("liabilities")
        if direct.present:
            return direct

        equity = self.equity
        for base_key, label in (
            ("liabilities_and_equity", "LiabilitiesAndStockholdersEquity - equity"),
            ("assets", "Assets - equity"),
        ):
            base = self._instant(base_key)
            if base.present and equity.present and base.period_end == equity.period_end:
                return LineItem(
                    "liabilities",
                    base.value - equity.value,
                    source=label,
                    derived=True,
                    period_end=base.period_end,
                    filed=base.filed,
                )
        return _missing("liabilities")

    @property
    def cash(self) -> LineItem:
        return self._instant("cash")

    @property
    def short_term_investments(self) -> LineItem:
        return self._instant_max("short_term_investments")

    @property
    def cash_and_investments(self) -> LineItem:
        """Cash plus liquid securities, summed only from the same balance sheet."""
        cash, investments = self.cash, self.short_term_investments
        if not cash.present:
            return _missing("cash_and_investments")
        if not investments.present or investments.period_end != cash.period_end:
            return LineItem(
                "cash_and_investments",
                cash.value,
                source=cash.source,
                period_end=cash.period_end,
            )
        return LineItem(
            "cash_and_investments",
            cash.value + investments.value,
            source=f"{cash.source} + {investments.source}",
            derived=True,
            period_end=cash.period_end,
        )

    def debt(self) -> DebtEstimate:
        """Largest defensible measure of interest-bearing debt.

        Operating lease liabilities are excluded; finance leases are included
        where a filer tags them together with debt.
        """
        candidates: dict[str, float] = {}
        period: date | None = None
        for label, concepts in DEBT_CANDIDATES:
            parts = [self.view.instant(c) for c in concepts]
            live = [p for p in parts if p is not None]
            if not live:
                continue
            # Components are summed only when drawn from the same balance sheet.
            ends = {p.end for p in live}
            if len(ends) > 1:
                continue
            candidates[label] = sum(p.value for p in live)
            period = live[0].end

        if not candidates:
            return DebtEstimate(None, "no debt concept tagged", {}, None)
        best = max(candidates.items(), key=lambda kv: kv[1])
        return DebtEstimate(best[1], best[0], candidates, period)

    @property
    def total_debt(self) -> LineItem:
        estimate = self.debt()
        if estimate.value is None:
            # Untagged debt means unknown, never zero. Treating absence as zero
            # would silently promote levered companies into the safe bucket.
            return _missing("total_debt", "no debt concept tagged")
        return LineItem(
            "total_debt",
            estimate.value,
            source=estimate.source,
            period_end=estimate.period_end,
        )

    @property
    def net_debt(self) -> LineItem:
        debt, cash = self.total_debt, self.cash_and_investments
        if not debt.present or not cash.present:
            return _missing("net_debt")
        return LineItem(
            "net_debt",
            debt.value - cash.value,
            source=f"{debt.source} - cash & investments",
            derived=True,
            period_end=debt.period_end,
        )

    @property
    def working_capital(self) -> LineItem:
        current_assets = self._instant("assets_current")
        current_liabilities = self._instant("liabilities_current")
        if not current_assets.present or not current_liabilities.present:
            return _missing("working_capital")
        return LineItem(
            "working_capital",
            current_assets.value - current_liabilities.value,
            source="AssetsCurrent - LiabilitiesCurrent",
            derived=True,
            period_end=current_assets.period_end,
        )

    # -- income statement ---------------------------------------------------

    @property
    def revenue(self) -> LineItem:
        return self._ttm(INCOME_CONCEPTS, "revenue")

    @property
    def gross_profit(self) -> LineItem:
        """Reported where tagged, otherwise revenue less cost of revenue.

        Coverage matters here: only around 60% of filers tag `GrossProfit`, and
        a margin that silently vanishes for four names in ten is worse than one
        that is derived and says so.
        """
        direct = self._ttm(INCOME_CONCEPTS, "gross_profit")
        if direct.present:
            return direct
        pair = align_windows(
            self._ttm_windows(INCOME_CONCEPTS, "revenue"),
            self._ttm_windows(INCOME_CONCEPTS, "cost_of_revenue"),
        )
        if pair is None:
            return _missing("gross_profit")
        revenue, cost = pair
        return LineItem(
            "gross_profit",
            revenue.value - cost.value,
            source=f"{revenue.concept} - {cost.concept}",
            derived=True,
            period_start=revenue.start,
            period_end=revenue.end,
            basis=revenue.basis,
        )

    @property
    def operating_income(self) -> LineItem:
        return self._ttm(INCOME_CONCEPTS, "operating_income")

    @property
    def net_income(self) -> LineItem:
        return self._ttm(INCOME_CONCEPTS, "net_income")

    @property
    def interest_expense(self) -> LineItem:
        return self._ttm(INCOME_CONCEPTS, "interest_expense")

    @property
    def research_development(self) -> LineItem:
        return self._ttm(INCOME_CONCEPTS, "research_development")

    @property
    def depreciation_amortisation(self) -> LineItem:
        return self._ttm(INCOME_CONCEPTS, "depreciation_amortisation")

    @property
    def ebit(self) -> LineItem:
        """Operating income, or pretax income plus interest expense."""
        direct = self.operating_income
        if direct.present:
            return LineItem(
                "ebit",
                direct.value,
                source=direct.source,
                period_start=direct.period_start,
                period_end=direct.period_end,
                basis=direct.basis,
            )
        pair = align_windows(
            self._ttm_windows(INCOME_CONCEPTS, "pretax_income"),
            self._ttm_windows(INCOME_CONCEPTS, "interest_expense"),
        )
        if pair is None:
            return _missing("ebit")
        pretax, interest = pair
        return LineItem(
            "ebit",
            pretax.value + interest.value,
            source=f"{pretax.concept} + {interest.concept}",
            derived=True,
            period_start=pretax.start,
            period_end=pretax.end,
        )

    @property
    def ebitda(self) -> LineItem:
        ebit = self.ebit
        da_windows = self._ttm_windows(INCOME_CONCEPTS, "depreciation_amortisation")
        if not ebit.present:
            return _missing("ebitda")
        if not da_windows:
            return _missing("ebitda", "depreciation not tagged")
        # Align D&A to the EBIT window rather than assuming they match.
        da = next(
            (w for w in da_windows if ebit.period_end and abs((w.end - ebit.period_end).days) <= 45),
            None,
        )
        if da is None:
            return _missing("ebitda", "depreciation period does not align")
        return LineItem(
            "ebitda",
            ebit.value + da.value,
            source=f"{ebit.source} + {da.concept}",
            derived=True,
            period_start=ebit.period_start,
            period_end=ebit.period_end,
        )

    @property
    def effective_tax_rate(self) -> float | None:
        pair = align_windows(
            self._ttm_windows(INCOME_CONCEPTS, "income_tax"),
            self._ttm_windows(INCOME_CONCEPTS, "pretax_income"),
        )
        if pair is None:
            return None
        tax, pretax = pair
        if pretax.value <= 0:
            return None  # a loss-making year yields no meaningful rate
        rate = tax.value / pretax.value
        return rate if 0.0 <= rate <= 0.60 else None

    # -- cash flow ----------------------------------------------------------

    @property
    def operating_cash_flow(self) -> LineItem:
        return self._ttm(CASHFLOW_CONCEPTS, "operating_cash_flow")

    @property
    def capex(self) -> LineItem:
        return self._ttm(CASHFLOW_CONCEPTS, "capex")

    @property
    def free_cash_flow(self) -> LineItem:
        """Operating cash flow less capital expenditure, period-aligned."""
        pair = align_windows(
            self._ttm_windows(CASHFLOW_CONCEPTS, "operating_cash_flow"),
            self._ttm_windows(CASHFLOW_CONCEPTS, "capex"),
        )
        if pair is None:
            ocf = self.operating_cash_flow
            if ocf.present:
                return _missing("free_cash_flow", "capex not tagged")
            return _missing("free_cash_flow")
        ocf, capex = pair
        # Capex is filed as a positive outflow in the cash-flow statement.
        return LineItem(
            "free_cash_flow",
            ocf.value - abs(capex.value),
            source=f"{ocf.concept} - {capex.concept}",
            derived=True,
            period_start=ocf.start,
            period_end=ocf.end,
            basis=ocf.basis,
        )

    @property
    def dividends_paid(self) -> LineItem:
        return self._ttm(CASHFLOW_CONCEPTS, "dividends_paid")

    # -- ratios -------------------------------------------------------------

    def _ratio(self, numerator: LineItem, denominator: LineItem) -> float | None:
        if not numerator.present or not denominator.present or denominator.value == 0:
            return None
        return numerator.value / denominator.value

    @property
    def gross_margin(self) -> float | None:
        return self._ratio(self.gross_profit, self.revenue)

    @property
    def operating_margin(self) -> float | None:
        return self._ratio(self.operating_income, self.revenue)

    @property
    def net_margin(self) -> float | None:
        return self._ratio(self.net_income, self.revenue)

    @property
    def fcf_margin(self) -> float | None:
        return self._ratio(self.free_cash_flow, self.revenue)

    @property
    def return_on_equity(self) -> float | None:
        """Ending equity, not average. Negative equity yields None rather than
        a flattering positive from two negatives."""
        equity = self.equity
        if not equity.present or equity.value <= 0:
            return None
        return self._ratio(self.net_income, equity)

    @property
    def return_on_assets(self) -> float | None:
        return self._ratio(self.net_income, self.assets)

    @property
    def invested_capital(self) -> LineItem:
        """Total debt plus equity less cash — the capital actually at work."""
        debt, equity, cash = self.total_debt, self.equity, self.cash_and_investments
        if not equity.present:
            return _missing("invested_capital")
        total = equity.value
        parts = [equity.source]
        if debt.present:
            total += debt.value
            parts.append(debt.source)
        if cash.present:
            total -= cash.value
            parts.append("- cash & investments")
        if total <= 0:
            return _missing("invested_capital", "non-positive invested capital")
        return LineItem(
            "invested_capital",
            total,
            source=" + ".join(parts),
            derived=True,
            period_end=equity.period_end,
        )

    @property
    def roic(self) -> float | None:
        """NOPAT over invested capital.

        The tax rate is the company's own effective rate where computable, since
        a statutory rate would misstate returns for filers with large permanent
        differences — common across this sector.
        """
        ebit, capital = self.ebit, self.invested_capital
        if not ebit.present or not capital.present:
            return None
        tax_rate = self.effective_tax_rate
        if tax_rate is None:
            tax_rate = 0.21  # US statutory fallback, recorded as an assumption
            self._notes.append("ROIC used the 21% statutory tax rate")
        nopat = ebit.value * (1 - tax_rate)
        return nopat / capital.value

    @property
    def net_debt_to_ebitda(self) -> float | None:
        net_debt, ebitda = self.net_debt, self.ebitda
        if not net_debt.present or not ebitda.present or ebitda.value <= 0:
            return None  # negative EBITDA makes the multiple meaningless
        return net_debt.value / ebitda.value

    @property
    def debt_to_equity(self) -> float | None:
        equity = self.equity
        if not equity.present or equity.value <= 0:
            return None
        return self._ratio(self.total_debt, equity)

    @property
    def interest_coverage(self) -> float | None:
        ebit, interest = self.ebit, self.interest_expense
        if not ebit.present or not interest.present or abs(interest.value) < 1:
            return None
        return ebit.value / abs(interest.value)

    @property
    def current_ratio(self) -> float | None:
        return self._ratio(self._instant("assets_current"), self._instant("liabilities_current"))

    @property
    def quick_ratio(self) -> float | None:
        """Excludes inventory, which a downcycle can leave unsellable."""
        current_assets = self._instant("assets_current")
        inventory = self._instant("inventory")
        current_liabilities = self._instant("liabilities_current")
        if not current_assets.present or not current_liabilities.present:
            return None
        if current_liabilities.value == 0:
            return None
        quick = current_assets.value - (inventory.value if inventory.present else 0.0)
        return quick / current_liabilities.value

    @property
    def fcf_conversion(self) -> float | None:
        """Free cash flow over net income — the earnings-quality test."""
        net_income = self.net_income
        if not net_income.present or net_income.value <= 0:
            return None
        return self._ratio(self.free_cash_flow, net_income)

    @property
    def capex_intensity(self) -> float | None:
        capex, revenue = self.capex, self.revenue
        if not capex.present or not revenue.present or revenue.value == 0:
            return None
        return abs(capex.value) / revenue.value

    @property
    def rd_intensity(self) -> float | None:
        return self._ratio(self.research_development, self.revenue)

    @property
    def inventory_days(self) -> float | None:
        """Days of inventory on hand — above ~120 signals downcycle risk."""
        inventory = self._instant("inventory")
        pair = align_windows(
            self._ttm_windows(INCOME_CONCEPTS, "cost_of_revenue"),
            self._ttm_windows(INCOME_CONCEPTS, "revenue"),
        )
        cost = pair[0] if pair else None
        if not inventory.present or cost is None or cost.value <= 0:
            return None
        return (inventory.value / cost.value) * 365

    @property
    def asset_turnover(self) -> float | None:
        return self._ratio(self.revenue, self.assets)

    # -- reporting ----------------------------------------------------------

    LINE_ITEMS = (
        "assets",
        "liabilities",
        "equity",
        "cash_and_investments",
        "total_debt",
        "net_debt",
        "working_capital",
        "revenue",
        "gross_profit",
        "operating_income",
        "net_income",
        "ebit",
        "ebitda",
        "operating_cash_flow",
        "capex",
        "free_cash_flow",
    )

    RATIOS = (
        "gross_margin",
        "operating_margin",
        "net_margin",
        "fcf_margin",
        "return_on_equity",
        "return_on_assets",
        "roic",
        "net_debt_to_ebitda",
        "debt_to_equity",
        "interest_coverage",
        "current_ratio",
        "quick_ratio",
        "fcf_conversion",
        "capex_intensity",
        "rd_intensity",
        "inventory_days",
        "asset_turnover",
    )

    def line_items(self) -> dict[str, LineItem]:
        return {name: getattr(self, name) for name in self.LINE_ITEMS}

    def ratios(self) -> dict[str, float | None]:
        return {name: getattr(self, name) for name in self.RATIOS}

    def coverage(self) -> dict[str, Any]:
        """What resolved, what was derived, and what is simply missing."""
        items = self.line_items()
        present = {k: v for k, v in items.items() if v.present}
        derived = {k: v.source for k, v in present.items() if v.derived}
        missing = [k for k, v in items.items() if not v.present]
        ratios = self.ratios()
        return {
            "ticker": self.ticker,
            "as_of": self.as_of,
            "currency": self.currency,
            "line_items_present": len(present),
            "line_items_total": len(items),
            "derived": derived,
            "missing_line_items": missing,
            "ratios_present": sum(1 for v in ratios.values() if v is not None),
            "ratios_total": len(ratios),
            "missing_ratios": [k for k, v in ratios.items() if v is None],
            "notes": list(self._notes),
        }

    def report(self) -> str:
        lines = [f"{self.ticker or 'company'} fundamentals as of {self.as_of} ({self.currency})"]
        lines.append("-" * 68)
        scale = 1e9
        for name, item in self.line_items().items():
            if item.present:
                mark = "d" if item.derived else " "
                lines.append(f" {mark} {name:24} {item.value / scale:12,.2f}B  {item.source[:28]}")
            else:
                lines.append(f"   {name:24} {'unavailable':>13}  {item.source}")
        lines.append("-" * 68)
        for name, value in self.ratios().items():
            shown = "n/a" if value is None else f"{value:,.3f}"
            lines.append(f"   {name:24} {shown:>13}")
        for note in self._notes:
            lines.append(f"   note: {note}")
        return "\n".join(lines)


def build(
    facts: dict[str, Any] | None,
    ticker: str = "",
    as_of: date | None = None,
) -> Fundamentals:
    """Convenience constructor from a raw companyfacts payload."""
    return Fundamentals(FactSet(facts, as_of=as_of), ticker=ticker)
