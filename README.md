# AI Infrastructure Equity Research & Portfolio System

A fundamentals-first research and portfolio-management system for long-term investing in
the AI infrastructure sector — semiconductors, memory, fab equipment, data-center power,
hyperscalers, and the software built on top of them.

It reads financial statements, values companies, cross-checks against what large
institutional investors actually filed, and produces **BUY / ADD / HOLD / TRIM / EXIT**
guidance with the evidence attached. It manages a real portfolio against a stated wallet
size, including diversifying ETF and gold sleeves.

It does not predict prices. Every output traces to a filed number, a disclosed holding, or
a published rule — never to a hunch.

---

## Table of contents

1. [Mandate and scope](#1-mandate-and-scope)
2. [What this is not](#2-what-this-is-not)
3. [The universe](#3-the-universe)
4. [Data sources and their limits](#4-data-sources-and-their-limits)
5. [The analysis rulebook](#5-the-analysis-rulebook)
6. [Smart-money layer](#6-smart-money-layer)
7. [News and events layer](#7-news-and-events-layer)
8. [Signal generation](#8-signal-generation)
9. [Portfolio construction and fund management](#9-portfolio-construction-and-fund-management)
10. [Validation: backtest → paper → live](#10-validation-backtest--paper--live)
11. [Architecture](#11-architecture)
12. [Build phases and deliverables](#12-build-phases-and-deliverables)
13. [Open decisions](#13-open-decisions)
14. [Honest limitations](#14-honest-limitations)
15. [Not investment advice](#15-not-investment-advice)

---

## 1. Mandate and scope

| Dimension | Decision |
|---|---|
| **Sector** | AI infrastructure: AI silicon, memory, foundry/fab equipment, networking, servers, data-center power, hyperscalers, AI software |
| **Horizon** | Long-term. Holding periods measured in quarters and years, not days |
| **Primary lens** | Fundamentals — balance sheet, cash flow, returns on capital, valuation |
| **Secondary lens** | Institutional positioning (13F), material news and filings |
| **Company profile** | Established and cash-generative. Pre-revenue and speculative names are explicitly out of scope |
| **Account size** | Configurable; designed and tested against a starting wallet of **$1,000** |
| **Diversification** | Sector satellite sleeve + broad-market ETF core + gold sleeve |
| **Rebalance cadence** | Quarterly review, threshold-triggered action |
| **Decision authority** | The system recommends and shows its work. The human approves every trade |

### Deferred, deliberately

- **Shari'ah / AAOIFI screening** — handled externally via Musaffa or Zoya for now. The
  prior codebase's AAOIFI engine is retained (see [§11](#11-architecture)) and can be
  reattached later as a pre-filter on the universe without disturbing anything else.
- **Telegram delivery** — the last thing to build, once signals are trustworthy. A signal
  engine that pushes to a phone before it has passed validation just makes bad decisions
  faster.

---

## 2. What this is not

Stating this precisely, because every design choice below follows from it.

- **Not a price predictor.** No forecast of where any stock will trade. The system
  estimates what a business is worth from its financials and compares that to the current
  price. Those are different activities.
- **Not a day-trading or swing-trading tool.** No RSI-triggered entries, no ATR stop
  losses, no momentum signals. Long-horizon fundamentals only.
- **Not an auto-trader.** It never places an order. It proposes, with reasons; the human
  decides and confirms.
- **Not a copy-trading tool.** Institutional filings are used as *corroboration* of a
  thesis the fundamentals already support — never as the thesis itself. See
  [§6](#6-smart-money-layer) for why blindly following 13F filings is unsound.
- **Not a black box.** Every recommendation carries the numbers, the rule invoked, and the
  source it came from. If the system cannot explain a call in one screen, it does not make
  the call.

---

## 3. The universe

### How constituents are chosen

Rather than inventing a definition, the system follows the method professional index
providers use for sector funds — MarketVector/VanEck for **SMH**, NYSE for **SOXX**:

1. **Industry classification screen** — a formal taxonomy (GICS sub-industry or
   equivalent), not narrative.
2. **Minimum market capitalisation** — excludes micro-caps.
3. **Minimum liquidity** — average daily traded value floor.
4. **US listing requirement** — the system reads SEC filings, so foreign-listed shares
   qualify only via US-listed ADRs (TSM, ASML, ARM do; Tokyo Electron does not).
5. **Profitability / cash-generation screen** — this project's own addition, enforcing the
   "established and cash-generative" mandate. See [§5](#5-the-analysis-rulebook).

Because SOXX and SMH are *pure-play semiconductor* indices by construction, they exclude
hyperscalers, power, and software. This universe is therefore broader than any single ETF,
organised into nine sub-segments so that portfolio concentration can be measured **by
segment**, not just by ticker — owning NVDA, AMD, and TSM is one bet on AI silicon
demand wearing three names.

### Benchmark

**SOXX (iShares Semiconductor ETF)** is the primary benchmark for backtesting and live
comparison. Reasoning: the 2026 AI rally broadened beyond GPU designers into equipment,
foundry, and memory; SOXX's ~30-name basket with real weight in Applied Materials, Lam
Research, and KLA reflects a diversified value-chain thesis better than SMH's ~25-name,
NVDA/TSM-concentrated index (single-name weight above 15%).

Secondary benchmarks: **SMH** (concentrated AI mega-cap comparison) and **SPY** (was this
sector bet worth making at all versus just owning the market?).

### Sub-segments and candidate constituents

Market-cap tiers and profitability flags below are research-time observations and are
**re-derived from filings at runtime**, never hardcoded as fact.

| Segment | Names | Notes |
|---|---|---|
| **AI accelerators** | NVDA, AMD | NVDA: dominant data-center GPU share plus CUDA software moat. AMD: #2 merchant GPU, also a CPU play |
| **CPU / general silicon** | INTC, ARM, AMD | ARM: profitable IP-royalty model behind Grace, Graviton, custom hyperscaler CPUs. **INTC flagged**: policy-backed turnaround (US government ~10% equity stake, 18A node) but profitability not yet restored — fails the stability screen today |
| **Networking silicon** | AVGO, MRVL, ANET, CSCO, ALAB, CRDO | AVGO+MRVL together hold roughly 95% of hyperscaler custom-ASIC co-design (Google TPU, AWS Trainium, Meta MTIA, Microsoft Maia). ANET: #1 AI data-center Ethernet switching. **ALAB, CRDO flagged**: 2024 IPOs, thin public track record |
| **Memory & storage** | MU, WDC, STX | The biggest 2026 shift. HBM/DRAM shortage — Micron sold out through 2027, DRAM contract pricing up sharply — moved memory from commodity-cyclical to AI-critical. Still genuinely cyclical; the system must treat it as such |
| **Foundry & fab equipment** | TSM, ASML, AMAT, LRCX, KLAC, ENTG, TER, MKSI | TSM: sole advanced-node foundry for NVDA/AMD/AAPL/AVGO. ASML: sole EUV supplier. Multi-year capex floor — industry wafer-fab-equipment spend forecast around $144B for 2026 |
| **Servers & integrators** | DELL, HPE, CLS, SMCI | Thin margins on AI servers despite large backlogs. **SMCI flagged**: huge backlog, but 2024 auditor resignation, SEC scrutiny, and ongoing dilution make it a governance risk, not a stable holding |
| **Hyperscalers** | MSFT, GOOGL, AMZN, META, ORCL, AAPL | The demand engine — the five largest are committing roughly $690–725B of 2026 capex, about three-quarters AI-directed. Collectively the *most stable* way to hold this theme, at the cost of diluted pure-play exposure |
| **Data-center power** | VRT, ETN, GEV, CEG, VST, TLN, NEE, PWR | Now unambiguously part of the AI trade — order books are explicitly AI-driven and nuclear operators sign direct hyperscaler PPAs. Excluded as too speculative: pre-revenue small modular reactor names |
| **AI software** | PLTR, ZETA | PLTR: genuinely profitable now; valuation is the risk, not the business. **ZETA flagged**: only just GAAP-profitable, small-cap, thinnest track record on this list |

**On flagged names.** A flag is not exclusion — it routes the name into a
`SPECULATIVE` bucket that is reported, sized smaller, and never allowed into the core
sleeve. The mandate says stable; the flag is how the system enforces that without
pretending the name does not exist.

### The 2026 bear case, encoded

A credible counter-narrative exists and the system must hold it, not suppress it:
circular vendor financing among NVDA/OpenAI/Oracle/CoreWeave, GPU depreciation risk
(3–5 year useful life financed with longer-dated debt), hyperscaler capex approaching
~94% of operating cash flow, and roughly $662B of signed-but-unbuilt data-center leases
(Moody's).

This does not change the universe. It does argue for weighting the
profitability/cash-generation screen heavily, treating neoclouds as out of scope, and
tracking **capex intensity and inventory days** as cycle-turn indicators
([§5.6](#56-cycle-awareness)).

---

## 4. Data sources and their limits

The rule for this project: **every source is documented alongside what it cannot tell us.**
A source used beyond its actual reach is how a data-driven system quietly becomes an
assumption-driven one.

| Layer | Source | Access | What it cannot do |
|---|---|---|---|
| Fundamentals | **SEC EDGAR XBRL** (`companyfacts`, bulk archive) | Free, no key. Fair-access limit ~10 req/s | Concept coverage is uneven — companies abandon tags, multi-class filers strip share counts. Foreign private issuers are a real gap, see below |
| Filing dates | **SEC EDGAR submissions** | Free | — |
| Prices | **yfinance** (daily OHLCV, batched) | Free, unofficial | Unofficial API, can break. Splits/dividends need care |
| Institutional holdings | **SEC Form 13F-HR** + SEC DERA quarterly bulk datasets | Free | See [§6](#6-smart-money-layer) — no shorts, 45-day lag, quarterly snapshot only |
| Material events | **SEC 8-K** via EDGAR daily index | Free | Item codes are coarse; narrative sits in exhibits |
| Insider transactions | **SEC Form 4** | Free | Dominated by grants and option exercises; only open-market purchases carry signal |
| News | RSS / public news APIs (TBD — see [§13](#13-open-decisions)) | Varies | Headlines are not facts. Sentiment is an input, never a trigger |
| Analyst estimates | **Gap — see [§13](#13-open-decisions)** | — | Forward P/E and PEG need consensus estimates, which have no good free source |
| Gold | Price via ETF proxy (GLD / IAU) | Free | ETF, not spot; carries an expense ratio |

### Point-in-time discipline

This is the single most important data rule in the project, and the most commonly botched.

SEC XBRL returns **every observation a company has ever tagged, including restatements**.
Backtesting on today's version of a 2022 balance sheet uses numbers that were not knowable
in 2022 — the classic look-ahead bias that makes a strategy look brilliant in a backtest
and mediocre in reality.

The fix is what professional shops call a point-in-time database. Two mechanical rules:

1. **Filter every fact by its filing date, not its fiscal period.** A fact is usable at
   date *T* only if it was *first filed* on or before *T*.
2. **Apply a reporting-lag buffer.** SEC deadlines set the floor — 10-K at 60/75/90 days
   depending on filer status, 10-Q at 40/45 days. Measured fiscal-end-to-filing gaps
   average around 43 days and reach ~61. The system therefore treats a fact as *known* at:

   | Fact type | Buffer after fiscal period end |
   |---|---|
   | Quarterly (10-Q) | **90 days** |
   | Annual (10-K) | **105 days** |

   Never faster than the statutory deadline. Configurable, logged in every backtest run.

### Foreign private issuers — measured, not assumed

Verified against live EDGAR during Phase 0, across all 41 universe names:

- **Reporting currency varies.** TSMC files IFRS in **TWD**; ASML files US-GAAP in
  **EUR**. Assuming USD returned an *empty fact set* for both — data that was present
  read as absent, the exact failure this project cannot tolerate. The fact engine now
  detects each filer's reporting currency and records it.
- **Ratios are currency-neutral, valuation is not.** Piotroski, Altman, ROIC, margins,
  leverage, and growth all divide like-for-like and work natively in any currency.
  Anything mixing filings with market prices — P/E, EV/EBITDA, FCF yield, market cap —
  needs FX conversion, and **point-in-time historical FX rates become a Phase 4
  dependency** for non-USD filers.
- **TSMC cannot currently be analysed from SEC data at all.** Its FY2025 20-F was filed
  2026-04-16, but the only fact reaching SEC's XBRL API is a cover-page share count —
  the IFRS financial statements are not tagged there. Its newest usable financial data
  is FY2024. Names in this state are flagged `INSUFFICIENT_DATA` and held through the
  sector ETF rather than scored on absent data.

Current measured coverage: **32 of 41 names** resolve a full core statement set. Most of
the remainder are concept-naming variation (`Liabilities` untagged where it is derivable
from assets minus equity; `StockholdersEquity` tagged with the noncontrolling-interest
variant), which Phase 2 resolves with alternative-concept lists. TSMC is the one genuine
data gap.

### Survivorship discipline — and what it cannot fix here

Testing today's constituent list against history silently deletes every company that went
bankrupt, was acquired, or was delisted — inflating results. Two mitigations apply, and one
limitation is structural.

**Dated snapshots, going forward.** Every universe build writes a dated snapshot to
`data/pit/universe/`. From now on, what was in the set on each date is *recorded* rather
than reconstructed from hindsight, and status changes are diffed between runs.

**Delisting returns.** For performance-related delistings without an explicit return, the
loss is modelled at **−30%**, not 0% and not dropped from the sample. (Shumway, *The
Delisting Bias in CRSP Data*, Journal of Finance 1997, shows the naive treatment materially
understates losses.)

**The structural limitation, stated plainly.** The candidate list was written in 2026, so it
contains companies that survived to 2026. Measured against live EDGAR, SIC codes cannot fix
this by defining membership rule-based: Amazon files as *Retail-Catalog & Mail-Order
Houses*, Entegris as *Plastics Products*, and KLA as *Optical Instruments & Lenses*. A
classification screen would admit irrelevant names and miss real ones.

So the honest split is:

| Segment group | Reconstructable backwards? | Why |
|---|---|---|
| Semiconductors, memory, equipment | **Yes** — SIC 3674 and neighbours plus market-cap and liquidity screens over all SEC filers | Classification codes genuinely identify these businesses |
| Hyperscalers, data-center power, AI software | **No** — curated by thesis | "Is this an AI-infrastructure play?" is a judgement no SIC code encodes |

A backtest over the semiconductor core can therefore claim to be survivorship-bias-free; one
including the adjacent segments cannot, and must report that it is biased rather than imply
otherwise. `universe.sic_peers()` is the entry point for the rule-based reconstruction, and
building it out is part of Phase 11, not an afterthought.

---

## 5. The analysis rulebook

Every rule below is a published, named framework with a citable source. Where a threshold
is a practitioner convention rather than a proven constant, it says so — those are
calibration bands to revisit, not laws.

### 5.1 Financial strength — Piotroski F-Score

Nine binary tests, summed 0–9. Source: Piotroski (2000), *Journal of Accounting Research*.

*Profitability:* positive ROA; positive operating cash flow; ROA improving year over year;
operating cash flow exceeding ROA (accruals quality — flags earnings propped up by
non-cash items).
*Leverage & liquidity:* long-term-debt ratio falling; current ratio rising; no new share
issuance.
*Efficiency:* gross margin rising; asset turnover rising.

Convention: **≥8 high quality, ≤2 avoid**; many quant screens use ≥7 as the "good" line.

**Applied with a caveat.** Piotroski built and validated this within *value* (high
book-to-market) stocks. Fast-growing tech names issue equity and invest heavily, so they
score moderately even when excellent. Used here as a quality *input*, never a pass/fail
gate.

### 5.2 Bankruptcy risk — Altman Z-Score

Source: Altman (1968), *Journal of Finance*, plus the 1995 non-manufacturer revision.

The original Z-Score includes a sales/assets term that unfairly penalises asset-light
businesses. This project therefore uses **Z''**, the non-manufacturer variant:

```
Z'' = 6.56·(Working Capital/Assets) + 3.26·(Retained Earnings/Assets)
    + 6.72·(EBIT/Assets)            + 1.05·(Equity/Total Liabilities)

Safe > 2.6   |   Grey 1.1–2.6   |   Distress < 1.1
```

**Variant selection matters here.** Fabless designers (NVDA, AMD, ARM, PLTR) are
functionally non-manufacturers — Z'' applies. Integrated device manufacturers with real
fabs (MU, INTC, TSM) carry genuine manufacturing asset bases, where the original Z or Z' is
more defensible. The system records which variant it used and why, per company.

### 5.3 Quality and moat

| Metric | Rule | Source |
|---|---|---|
| **ROIC − WACC spread** | The core value-creation test. Economic profit = (ROIC − WACC) × invested capital. A *sustained, positive, stable-or-widening* spread is evidence of a durable moat | Koller/Goedhart/Wessels, *Valuation* (McKinsey) |
| **Gross margin trend** | Rising or stable = pricing power. Sector context is essential: NVDA runs ~75%, AMD ~50–54%, MU swings from ~10% to ~45% on memory pricing | Standard moat analysis |
| **FCF conversion** | FCF/net income ≥ **80–100%** wanted; persistently <60% is an earnings-quality red flag (echoes Piotroski's accruals test) | Practitioner convention |
| **R&D intensity** | Semis norm ~10–25% of revenue. A fall to 10–12% often signals harvesting a mature line or losing the process race | Sector convention |

### 5.4 Balance sheet health

Thresholds follow rating-agency frameworks (Moody's Global Technology Hardware &
Semiconductor methodology; S&P Corporate Methodology).

| Metric | Safe | Moderate | Risky |
|---|---|---|---|
| Net debt / EBITDA | < 1.5–2.0× | 2.0–3.0× | > 3.5–4.0× |
| Interest coverage (EBIT/interest) | ≥ 4–5× | 3.0× | < 1.5× |
| Current ratio | 1.5–3.0× | — | < 1.0× |
| Quick ratio | ≥ 1.0× | — | < 1.0× |

The quick ratio matters specifically for memory and foundry names, where a current ratio
can be flattered by slow-moving inventory during a downcycle.

### 5.5 Valuation

Two independent estimates, reported side by side. When they disagree, that disagreement is
the finding.

**(a) Intrinsic — discounted cash flow.** Sources: CFA Institute curriculum; Damodaran,
*Investment Valuation*; Koller et al., *Valuation*.

- WACC from market values, never book: `WACC = (E/V)·Re + (D/V)·Rd·(1−Tc)`
- Cost of equity via CAPM: `Re = Rf + β·ERP`, with Rf = 10-year Treasury, ERP in the
  historically cited 4–6% band, and adjusted beta `0.67·β_raw + 0.33` (Bloomberg
  convention)
- Terminal value via Gordon Growth, with **g capped at long-run nominal GDP growth
  (~2–4%)** and always g < WACC. Cross-checked against an exit-multiple estimate
- **Mandatory sensitivity grid**: WACC ±200bp × terminal growth ±100bp, reported as a
  *range*. A DCF that outputs one number is a false precision machine; the output here is
  always a band with bull/base/bear cases

**(b) Relative — sector multiples.** Compared against the company's own 5-year history
*and* its sub-segment peers, never against absolute cross-sector cutoffs.

| Multiple | Reference band | Note |
|---|---|---|
| Forward P/E | Normal-cycle mega-cap semis ~20–30×; AI leaders have traded 30–50×+ | Sell-side generally treats >40× as "priced for perfection" |
| PEG | ~1.0 fair, <1.0 cheap, >2.0 expensive | Lynch, *One Up on Wall Street* |
| EV/EBITDA | Through-cycle semis ~10–15× | AI-exposed names have run 20–30×+ |
| EV/Sales | Fabless 5–10×+; cyclical memory 2–4× | Segment-dependent |
| FCF yield | <2% expensive, 4–6% fair, >7–8% cheap | For mature cash-generative names |

These bands are **sell-side conventions distorted upward by the AI cycle since 2023** —
explicitly calibration bands, tracked and revisited, not constants.

**(c) Margin of safety.** Graham's discipline: act on the buy side only when price sits
below the base-case fair-value estimate by a required margin. Default **25%**,
configurable. Graham's full defensive-investor criteria (20-year dividend history,
P/E ≤ 15, P/B ≤ 1.5) structurally exclude nearly this entire universe and are therefore
reported as a *reference discipline*, not applied as filters.

### 5.6 Cycle awareness

Semiconductors are capital-cycle businesses, and ignoring that is how fundamental
investors buy memory at the top of a pricing spike.

| Indicator | Reading |
|---|---|
| Capex / revenue | Fabless 3–8%; IDMs and foundries 25–50%, highly cyclical (TSM has run 30–50%+) |
| Inventory days | Above ~120 days signals downcycle risk |
| Gross margin vs. own 5-year range | Peak-of-cycle margins flag earnings that may not repeat |

Each name carries a **cycle position** annotation. A memory company at peak margins with
peak multiples is a fundamentally different proposition from the same company at trough,
and the system must say which it is looking at.

### 5.7 Composite scoring — and an honest caveat

Pillar scores are **percentile ranks within the sector universe**, not absolute grades:
"more profitable than 80% of the AI-infra names you could actually buy" is both the
comparison that matters and far more robust than hand-picked cutoffs. Missing pillars are
renormalised away, never scored zero — a company should not be punished twice for a
disclosure gap.

Proposed pillar weights:

| Pillar | Weight | Built from |
|---|---|---|
| Quality & moat | 30% | ROIC−WACC spread, margin trend, FCF conversion |
| Financial strength | 25% | Piotroski F-Score, Altman Z'', leverage, coverage |
| Valuation | 25% | DCF vs. price, relative multiples vs. peers and own history |
| Growth | 20% | Revenue, FCF, and EPS trends, quality-adjusted |

**Every individual rule above is a published framework. These four weights are not.**
They are a starting configuration to be validated by walk-forward backtest
([§10](#10-validation-backtest--paper--live)), and tuning them is exactly the activity that
overfits a backtest. They will be treated as a hypothesis under test, not a setting to
optimise until the equity curve looks good.

---

## 6. Smart-money layer

### What is actually verifiable

**SEC Form 13F-HR** is the only auditable record of what large investors hold. Managers
with **$100M+** in qualifying US-listed securities must file **within 45 days of each
quarter end**.

Access is free and direct — no aggregator subscription required:

- **SEC DERA quarterly 13F data sets** — bulk ZIPs of all 13F holdings, flattened from
  as-filed XML. The right foundation for a local historical database.
- **EDGAR full-text search** + per-filing `infotable.xml` for current filings. Pre-Q3-2013
  filings are fixed-width TXT.
- **`edgartools`** (Python, actively maintained) handles both formats back to ~2005, with
  purpose-built helpers for holdings history and quarter-over-quarter comparison.

Aggregators (WhaleWisdom, Dataroma, GuruFocus, HedgeFollow) were evaluated and rejected:
paid API tiers, restrictive rate limits, and scraping-ToS ambiguity, in exchange for data
the SEC gives away.

### What 13F cannot tell you

This is why 13F is a corroboration layer and never a signal source:

- **No short positions.** A manager can be net short via swaps while their 13F shows a long
  position or nothing at all.
- **Derivatives are opaque.** Puts and calls appear without strike, expiry, or context, so a
  large put line is indistinguishable from a hedge on an unreported long book. Michael
  Burry's final filing disclosed NVDA/PLTR puts with large notional values — notional is not
  cost basis, and the position could not be interpreted without his own commentary.
- **45-day lag on a quarterly snapshot.** Intra-quarter trading is invisible. A "new
  position" may already have been sold before you can read about it.
- **No foreign-listed holdings.** ASML on Euronext is invisible; the US ADR is not.
- **Filers can vanish.** Scion deregistered in November 2025; there is no further visibility.

### Filers tracked

Prioritised for genuine, media-covered AI/semiconductor positioning:

| Manager | Fund | Relevance |
|---|---|---|
| Philippe Laffont | Coatue | Explicit AI "picks-and-shovels" thesis — TSM, LRCX, AMAT over NVDA directly |
| Chase Coleman | Tiger Global | Large NVDA/TSM/AMZN positions |
| Brad Gerstner | Altimeter | Vocal AI-infrastructure bull |
| David Tepper | Appaloosa | Increased MU, TSM, VST (AI power) |
| Stanley Druckenmiller | Duquesne | Tech exposure roughly doubled in Q1 2026 — AVGO, INTC added |
| Cathie Wood | ARK | AI/robotics/semis thesis. **Also publishes daily holdings** on its own site — far more current than 13F |
| Ken Griffin | Citadel | Frequently covered for chip-stock moves |
| Warren Buffett | Berkshire | Bellwether; little direct semis exposure |
| Bill Ackman | Pershing Square | Broadly covered |
| Michael Burry | Scion | **Historical only** — deregistered Nov 2025 |

### How positioning becomes evidence

Institutional positioning **adjusts confidence in a fundamentals-driven conclusion; it never
creates one.** Concretely:

- **Cluster corroboration** — three or more tracked filers independently adding the same
  name in the same quarter is a meaningful signal that sophisticated investors reached a
  similar conclusion. One filer adding is noise.
- **Consensus exit** — multiple tracked filers exiting a name the fundamentals still like
  raises a flag for human review. It does not auto-sell.
- **Confidence adjustment only.** A name must clear the fundamental gates on its own.
  13F can move a `HOLD` toward an `ADD` at the margin; it can never manufacture a `BUY` on
  a business the numbers reject.

### Retail commentators — excluded, and why

**Decided: out of scope entirely.** The system tracks SEC filings and 13F institutional
positioning. It does not ingest retail commentary, finfluencer picks, or social sentiment
from any source.

The question arose over **Kenan Grace**, a finance content creator (YouTube channel around
500–650K subscribers, `@KenanGrace` on X, branded "The Chart King" teaching fractal trading
and technical analysis, with a linked public brokerage portfolio around **$30–50K**).
Research found three disqualifying problems, and they generalise to the whole category:

1. **Nowhere near the 13F threshold.** At $30–50K versus a $100M filing requirement, there
   is no SEC record and never will be. Holdings would have to come from scraping social
   posts — self-reported, unaudited, unverifiable, with no record of what was sold or when.
   A system built on filed data cannot absorb that without lowering its own evidentiary bar.
2. **Methodologically opposite to this project.** Chart-based technical trading against a
   long-horizon fundamentals mandate. Importing the calls imports a method the rest of the
   design rejects.
3. **A name collision worth recording.** A similarly spelled but entirely unrelated person,
   *Keenan Gracey*, was convicted in a fake-billionaire stock-promotion fraud (2018–19).
   Noted only so the two are never conflated by a future data pipeline.

The evidence hierarchy is therefore two tiers, not three: **Tier 1** — SEC filings and
audited financials; **Tier 2** — 13F institutional positioning, as corroboration only.

---

## 7. News and events layer

News answers *what changed*, which fundamentals alone cannot — a filing is a quarterly
photograph.

| Source | Use |
|---|---|
| **SEC 8-K** | Material events, rated by item code. Non-reliance on previously issued financials (a restatement) is thesis-breaking and rates highest; earnings releases and completed acquisitions rate high; routine exhibits rate zero |
| **SEC Form 4** | Insider transactions. Only open-market purchases above a dollar floor carry signal — grants, option exercises, and tax withholding dominate volume and mean nothing directionally. Clusters of distinct insiders buying within a short window are the pattern with real support |
| **Earnings calendar** | Known catalysts ahead; a reason to wait rather than act |
| **News / RSS** | Sector-level context: capex announcements, export controls, supply agreements, pricing cycles |

Two firm rules:

1. **News never fires a signal by itself.** It raises or lowers confidence and, where it
   contradicts a thesis, flags the name for review.
2. **LLM summarisation is for reading, not deciding.** An LLM may compress ten filings into
   a paragraph a human reads. It never produces a number that feeds the scoring engine.
   Numbers come from filings.

---

## 8. Signal generation

### The framework

Quality-at-a-reasonable-price, the standard institutional long-only discipline: separate
*is this a good business?* from *is it available at a sensible price?*, and let quality
deterioration override price attractiveness in both directions.

**Gate 1 — Eligibility.** In universe, US-listed, filings current, adequate liquidity.
Fails here and the name is not analysed further.

**Gate 2 — Quality.** Composite quality and financial-strength pillars, Altman Z'' out of
distress, interest coverage above the floor, FCF conversion acceptable. A name failing Gate
2 is **never a buy at any price** — this is the rule that prevents value traps.

**Gate 3 — Valuation.** Price versus the DCF base case and versus peer/own-history
multiples, with the required margin of safety applied.

**Gate 4 — Corroboration.** 13F cluster activity, insider purchases, recent 8-K events,
cycle position. Adjusts confidence within a band; cannot move a decision across the quality
gate.

### The decision matrix

| | **Undervalued** (below fair value − MoS) | **Fair** | **Overvalued** |
|---|---|---|---|
| **Quality rising** | `STRONG BUY` | `ADD` | `HOLD` |
| **Quality stable** | `BUY` | `HOLD` | `HOLD` / `TRIM` if extreme |
| **Quality deteriorating** | `AVOID` (value trap) | `TRIM` | `EXIT` |
| **Gate 2 failure** | `AVOID` | `AVOID` | `EXIT` if held |

Deliberate asymmetries: deteriorating quality plus a cheap price is `AVOID`, not `BUY` —
cheapness is a consequence of deterioration, not an opportunity. And a Gate 2 failure while
held is `EXIT` regardless of valuation.

### Every signal carries its evidence

A signal without its reasoning is a tip, not research. Each output records:

- The decision and which matrix cell produced it
- Fair-value range (bear/base/bull) versus current price, with the implied margin of safety
- Each pillar score with the specific metrics behind it
- Which rules passed and failed, by name — `Altman Z'' 3.1 (safe)`, `Piotroski 7/9`,
  `interest coverage 12× (safe)`
- Corroborating and contradicting evidence, separately — including a **"what would falsify
  this"** line
- Cycle position, and whether current margins look peak, mid, or trough
- Data-quality notes: which figures were unavailable and what was substituted

### Cadence

Signals are recomputed **when new data arrives** — a fresh 10-Q, a 13F quarter, a material
8-K — plus a scheduled weekly refresh for prices and valuation. Not intraday. A long-horizon
system that re-signals hourly is inviting the churn it exists to avoid.

---

## 9. Portfolio construction and fund management

### The $1,000 problem, stated plainly

At this account size the sector-satellite arithmetic gets tight, and the plan should say so
before it proposes a solution.

Standard **core-satellite** construction puts a diversified core at 70–90% of a portfolio
and thematic satellites at 10–30%, with advisory practice often recommending just **1–5%
per single theme**. Applied literally to $1,000, a single-sector satellite is **$100–200**.
Spread across five names at the institutional "no name over 10% of sleeve" guideline, that
is $20–65 per position — too small for stock-specific research to pay for itself, while
still carrying full single-company risk.

Compounding this: several names in this sector trade far above $100/share, and **ASML has
traded near $1,850**. Fractional shares are not a convenience here, they are the only
mechanism by which single-name exposure exists at all. Fidelity supports fractional from
$1 (S&P 500 stocks), Schwab Stock Slices from $5 (S&P 500), Robinhood from $1 across a
broader universe.

**Consequence for the design:** at $1,000 the satellite sleeve should be **ETF-dominant**,
with individual stock picks as a small conviction slice. As the account grows, the
individual-name share can rise. The system will make this explicit rather than pretend a
$40 position in eight chip stocks is a portfolio.

### Proposed allocation

Framework-grounded, and configurable — this is a starting template, not dogma.

| Sleeve | Target | Instrument | Rationale |
|---|---|---|---|
| **Core — broad market** | 45% | Total-market or S&P 500 ETF | Cheap market beta. The part that is not a bet |
| **Satellite — AI infrastructure** | 35% | Split: sector ETF (SOXX/SMH) + top-scoring individual names | The thesis sleeve. ETF-weighted at small account sizes |
| **Gold** | 10% | GLD or IAU | World Gold Council research puts the risk-adjusted optimum at 5–8%, with 4–15% improving Sharpe across portfolio types; a 5% allocation has been shown to improve Sharpe by ~12% while reducing volatility |
| **Cash reserve** | 10% | — | Dry powder for margin-of-safety opportunities, and a drawdown buffer |

A 35% single-theme satellite sits **above** the conservative 1–5%-per-theme advisory
guidance and at the top of the 20–30% satellite band. That is a deliberate, informed
choice given a stated sector conviction — and the system will state it as elevated
concentration rather than quietly normalise it. It also assumes this account is not the
whole net worth; if it is, the satellite should come down.

### Position sizing

- **Max single name: 10% of total portfolio** — the widely used wealth-management threshold
  at which a position is formally "concentrated." Regulatory anchor: the RIC 25/5/50
  diversification test caps a diversified fund at 25% per issuer, with positions ≥5%
  summing to no more than 50%.
- **Max per sub-segment: 20%** — because NVDA + AMD + TSM is one bet on AI silicon demand,
  not three independent ones.
- **Conviction weighting via fractional Kelly.** Full Kelly assumes edges are known
  exactly; estimation error makes the computed fraction too large, producing drawdowns far
  worse than modelled. Practice uses fractions — **half-Kelly** (Thorp; retains ~75% of
  growth at much lower variance) or **quarter-Kelly** (~50% of growth at roughly 25% of
  volatility). This system defaults to **quarter-Kelly**, floored and capped by the
  position limits above. Given how imprecisely a fundamental score maps to a probabilistic
  "edge," the conservative fraction is the honest one.
- **Minimum position $25**, to keep positions meaningful relative to costs.

### Rebalancing

**Threshold bands with a calendar check** — the combination most commonly recommended for
individual accounts. Vanguard-referenced research finds an annual review plus a ±5-point
band captures roughly 99% of the return of continuous rebalancing while cutting
transactions by about 95% and costs from an estimated 0.20–0.30%/yr to 0.02–0.05%/yr.

- **Band: ±5 percentage points** from sleeve target triggers action
- **Calendar: quarterly review**, acting only on breaches
- Contributions are directed to the most underweight sleeve before any selling is proposed
- Tax awareness noted as out of scope until account structure is known

### The portfolio ledger

State the system maintains, so recommendations are portfolio-aware rather than generic:

- Positions: ticker, shares (fractional), cost basis, entry date, **the thesis recorded at
  entry**, and **the falsification condition** that would end it
- Cash balance and sleeve weights versus target, with drift
- Transaction log: every proposed action, whether it was accepted, and the reasoning at the
  time
- Realised and unrealised P&L, per position and per sleeve
- **Thesis-breach alerts** — when a recorded falsification condition triggers

That last pair is the point of building this rather than buying it. No commercial screener
knows why *you* bought something or what you said would prove you wrong. Recording the
thesis at entry and checking it against new filings is the feature a personal tool can
have and a product cannot.

### Human in the loop

The system proposes; it never executes. A proposed trade shows the reasoning, the resulting
sleeve weights, and the concentration impact. On acceptance, the ledger updates. On
rejection, the reason is logged — because a record of overrides is how you find out whether
the system or the human is the weaker link.

---

## 10. Validation: backtest → paper → live

**No real money until both gates are cleared.** Each stage produces a report; a failed
stage sends the strategy back for revision, not forward with a caveat.

### Stage 1 — Historical backtest

Requirements, all non-negotiable:

- **Point-in-time fundamentals** with the filing-date gate from
  [§4](#4-data-sources-and-their-limits). Any look-ahead invalidates the run
- **Split-aware prices.** As-traded prices for market cap and multiples, adjusted prices for
  returns. Conflating them understates historical market caps by the split ratio
- **Survivorship handling per [§4](#4-data-sources-and-their-limits)** — genuinely
  bias-free for the semiconductor core, explicitly biased for the curated adjacent segments,
  with −30% modelled for performance delistings. Results must state which universe they used
- **Transaction costs and slippage** modelled explicitly, not assumed to be zero
- **Minimum 10 years** of history, to span at least one full business cycle and multiple
  regimes — critically including the 2022 semiconductor downcycle and the 2018–19 memory
  crash, not just the 2023–26 AI upcycle. A backtest that only sees the boom has learned
  nothing about the bust
- **Walk-forward validation**, not one optimisation over all history: rolling in-sample and
  out-of-sample windows at roughly a 3:1 ratio, scaled for a long-horizon strategy to about
  **5-year train / 1–2-year test**, rolled across the full history

Metrics reported, against SOXX, SMH, and SPY:

| Metric | Poor | Acceptable | Good |
|---|---|---|---|
| CAGR vs. benchmark | below | ≈ benchmark | +2–4pp or better |
| Sharpe | < 0.5 | 0.5–1.0 | 1.0–2.0 |
| Sortino | < 1.0 | 1.0–2.0 | 2.0–3.0 |
| Max drawdown | > 40% | 30–40% | < 30% |
| Calmar (CAGR/MaxDD) | < 0.5 | 0.5–1.0 | ≈1.0 or better |
| Information ratio | < 0.25 | 0.25–0.5 | > 0.5 |

Plus alpha and beta from a CAPM regression with a t-statistic, and in-sample versus
out-of-sample Sharpe **side by side**.

**Overfitting checks.** The S&P 500's own long-run Sharpe is roughly 0.4–0.5. A
fundamentals strategy showing a sustained pre-cost Sharpe above ~1.5 should be treated as
suspect, not celebrated. An out-of-sample Sharpe below half the in-sample figure is a red
flag. Where feasible, report the **Deflated Sharpe Ratio** and **Probability of Backtest
Overfitting** (Bailey & López de Prado). Optimise toward Calmar or Sortino rather than raw
return — raw-return optimisation selects fragile parameter sets that merely avoided
disaster in-sample.

**Stage 1 pass gate:** beats SOXX on risk-adjusted return over the full period, out-of-sample
Sharpe holds at least half the in-sample figure, max drawdown under 35%, and results are
stable across walk-forward windows rather than driven by one lucky period.

### Stage 2 — Paper trading

**Minimum one month as you specified; three to six months recommended, and here is why.**
Practitioner consensus puts the forward-testing floor at 3–6 months with 100+ independent
decisions. A quarterly-rebalance fundamentals strategy generates very few decisions per
month — one month may contain **zero** rebalance cycles and no earnings reports for some
holdings, meaning a one-month pass would be close to statistically meaningless. The
recommendation is a **minimum of two full rebalance cycles (~6 months)**, spanning at least
one earnings season for every holding.

If you want to start with a month, the honest framing is a **one-month operational
shakedown** — proving the plumbing works, the data arrives, no look-ahead crept into live
signals — followed by a continuing evaluation period before real capital. Those are two
different tests and should not share one label.

Logged throughout: timestamped signals **with the exact data snapshot used** (this is the
proof that no look-ahead crept in), intended versus actual price, slippage, daily equity
curve, rolling drawdown, and paper Sharpe against backtested Sharpe for the same window.

**Stage 2 pass gate:**

- Paper Sharpe retains **≥50%** of the backtested Sharpe for the equivalent window (the
  common practitioner rule of thumb for expected in-sample-to-live decay)
- Does not underperform SOXX by more than **5 percentage points annualised**
- No drawdown beyond **1.5× the backtest's worst** or 25% absolute, whichever is tighter
- Every trade traces to a documented rule, with zero undocumented discretionary overrides
- No data-integrity incidents

### Stage 3 — Live, staged

Even after both gates: begin with a fraction of intended capital, compare live results
against the paper period, and scale up only as they agree.

### Presentation honesty

Following the SEC Marketing Rule's treatment of hypothetical performance and GIPS
convention on backtested results:

1. Backtest and paper results are labelled **"hypothetical — not actual trading results"**
2. Assumptions are disclosed with the numbers: costs, slippage, rebalance frequency,
   lag buffers, universe construction
3. Backtest, paper, and live equity curves are **never spliced into one continuous line**
   without a clearly marked transition
4. Hypothetical performance stays structurally separate from any real track record

This is a personal tool with no regulatory obligation. The standards are adopted because
they are the difference between measuring a strategy and flattering it.

---

## 11. Architecture

### Module plan

```
config/
  universe.yaml          # sub-segments, constituents, flags, benchmark
  rules.yaml             # thresholds, weights, MoS, lag buffers — all tunable in one place
  portfolio.yaml         # wallet size, sleeve targets, position limits

data/
  sec/                   # XBRL facts, filing-date index, 13F datasets
  prices/                # OHLCV cache
  pit/                   # point-in-time snapshots, universe membership history
  portfolio/             # ledger, transactions, theses

src/
  sec_client.py          # EDGAR access: rate limiting, caching, bulk archive
  facts.py               # XBRL fact resolution WITH FILING DATES (point-in-time core)
  universe.py            # constituent management, membership history
  fundamentals.py        # statement construction, TTM chaining, derived ratios
  quality.py             # Piotroski, Altman Z'', ROIC-WACC, FCF conversion
  valuation.py           # DCF with sensitivity, relative multiples, margin of safety
  cycle.py              # capex intensity, inventory days, margin-vs-range position
  holdings_13f.py        # 13F ingestion, quarter-over-quarter diffs, cluster detection
  events.py              # 8-K, Form 4, earnings calendar
  news.py                # news ingestion and LLM summarisation (read-only, never scores)
  signals.py             # the gates and decision matrix
  portfolio.py           # ledger, sizing, rebalancing, thesis tracking
  backtest.py            # PIT-correct engine, walk-forward, metrics
  paper.py               # forward-test runner and logging
  report.py              # dashboard and per-name research notes

tests/                   # one suite per module, mirroring the current convention
```

### On the existing code

You judged the previous codebase out of scope, and for the Shari'ah-specific logic, the
swing-trading signal engine, and the Telegram bot, that is right — those are either
deferred or contradict the new mandate.

**Two components are worth keeping, and rebuilding them would be a real cost:**

1. **The XBRL fact-resolution layer** (currently inside `aaoifi_screener.py`). It already
   handles the exact problems point-in-time fundamentals require: stale abandoned tags,
   TTM chaining from quarterly facts, period alignment, restatement collapse to the
   most-recently-filed value, and choosing among competing debt concepts. That logic was
   hard-won — the existing documentation records several real bugs it now guards against,
   each with a regression test. It needs *extending* with filing-date awareness, not
   replacing.
2. **The SEC access layer** (currently inside `universe.py`) — bulk `companyfacts`
   handling, fair-access rate limiting, batched price fetching, and snapshot/diff logic.

Recommended: extract both into `sec_client.py` and `facts.py`, drop everything
Shari'ah-specific, and delete `briefs.py`, `signals.py`, `backtest.py`, `server.py`, and
`static/` outright. Their test suites come along with the parts that survive.

`HALAL_SCREENER_README.md` is retained for now — it documents the fact-resolution edge
cases in detail, which is exactly the knowledge the new `facts.py` must not lose. It can be
deleted once that migration is done and tested.

### Interface

A local web dashboard, built after the engine works and is validated:

- **Universe** — all names, scores by pillar, current signal, sortable
- **Company** — full research note: statements, ratios, DCF with sensitivity band, peer
  comparison, cycle position, 13F holders, recent events, and the falsification line
- **Portfolio** — sleeve weights versus target, drift, positions with recorded theses, P&L
- **Actions** — proposed trades with reasoning and concentration impact, accept or reject
- **Validation** — backtest and paper results, clearly labelled hypothetical

---

## 12. Build phases and deliverables

Each phase ships something testable. No phase starts before the one below it is tested.

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Foundation** ✅ | `sec_client.py`, `facts.py` — point-in-time fact resolution, currency detection, cache TTLs; config scaffolding | **Done.** PIT gate proven by test; NVIDIA's revenue matches its reported fiscal years at every as-of date |
| **1. Universe** ✅ | `universe.py`, `prices.py` — split-aware prices, market-cap/liquidity/cash screens, dated snapshots and diffs | **Done.** 40/41 names screen through, TSMC flagged `INSUFFICIENT_DATA`, unevaluated screens recorded rather than passed |
| **2. Fundamentals** ✅ | `fundamentals.py` — statements, TTM chaining, 17 derived ratios, provenance on every figure, coverage report | **Done.** 26/41 names resolve all 16 line items, most others 14–15. Micron's FY2023 memory crash reproduces exactly from point-in-time data (−9.1% gross margin, −$6.1bn FCF, 183 inventory days) |
| **3. Quality** ✅ | `quality.py` — Piotroski, Altman Z'' with original Z as cross-check, ROIC−WACC, FCF conversion, balance-sheet bands | **Done.** Verified across 12 names spanning every segment. Z''-vs-Z disagreement surfaces Intel's book-vs-market gap; unevaluable signals never count as failures |
| **4. Valuation** ✅ | `valuation.py` — WACC from CAPM, DCF with sensitivity grid, **reverse DCF**, own-history multiples, margin of safety | **Done.** Never a single fair value; the reverse DCF is primary because a conservative forward DCF sits below price for nearly the whole sector and so cannot rank it |
| **5. Cycle** ✅ | `cycle.py` — margin-vs-own-range positioning, inventory and capex corroboration, repeatability verdict | **Done.** All three memory names read PEAK; stable businesses correctly have no cycle to place; Apple's margin high is identified as secular, not cyclical |
| **6. Scoring** | Composite pillars, percentile ranks within universe | Direction tested in both polarities; missing pillars renormalise, never zero |
| **7. Smart money** | `holdings_13f.py` — DERA ingestion, per-filer history, cluster detection | Reproduces a known filer's holdings for a past quarter; cluster logic tested |
| **8. Events & news** | `events.py`, `news.py` — 8-K, Form 4, earnings calendar, summaries | Events attach to names; numbers never sourced from an LLM |
| **9. Signals** | `signals.py` — four gates, decision matrix, full evidence payload | Every signal carries its reasoning and falsification condition |
| **10. Portfolio** | `portfolio.py` — ledger, quarter-Kelly sizing, rebalancing, thesis tracking | Simulates a $1,000 account end-to-end; limits provably enforced |
| **11. Backtest** | `backtest.py` — PIT-correct, survivorship-free, walk-forward, full metrics | **Stage 1 gate cleared or strategy revised** |
| **12. Dashboard** | Local web interface | Every number on screen traceable to its source |
| **13. Paper trading** | `paper.py` — forward-test runner and logging | **Stage 2 gate cleared** |
| **14. Live, staged** | Fractional capital, live-versus-paper comparison | Live tracks paper within tolerance |
| **Later** | Telegram delivery; AAOIFI pre-filter reattached | On request |

Phases 0–2 are the foundation everything else rests on, and the point-in-time work in
Phase 0 is what makes Phase 11 mean anything. Getting Phase 0 wrong invalidates every
number the system ever produces.

---

## 13. Open decisions

Flagged rather than assumed.

1. **Analyst estimates.** Forward P/E and PEG both need forward EPS, and there is no
   reliable free consensus source. Options: (a) drop forward multiples and use only trailing
   and DCF — defensible and fully data-grounded; (b) derive internal growth estimates from
   historical trends, clearly labelled as the system's own projection, not consensus;
   (c) pay for a data feed. **Recommendation: (a) initially, (b) later, clearly labelled.**
2. **News source.** Free RSS covers headlines but not depth; paid APIs cost money. Needs a
   decision before Phase 8, not before Phase 0.
3. **Satellite sizing.** The proposed 35% single-theme allocation is above conventional
   advisory guidance. Confirm whether the configured wallet is a standalone experiment or
   part of a larger portfolio — the answer changes the right number. *(Wallet size itself
   is settled: a config value, $1,000 default, adjustable at any time.)*
4. **Broker.** Fractional-share availability differs: Fidelity and Schwab restrict
   fractional stock purchases to S&P 500 names, which would exclude some names in this
   universe; Robinhood's universe is broader. Affects what the portfolio module can
   actually propose. Needed by Phase 10.
5. **Paper-trading duration.** One month proves the plumbing; roughly six months proves the
   strategy. See [§10](#10-validation-backtest--paper--live) for why the distinction
   matters at this rebalance frequency.

**Settled since first draft:** retail commentary excluded entirely
([§6](#6-smart-money-layer)); wallet size is configurable rather than fixed; legacy code
archived to `legacy/` rather than deleted; repository under local version control.

---

## 14. Honest limitations

Written down now, because a system whose limits are only discovered later gets trusted
beyond them.

- **Fundamentals are quarterly photographs.** A 10-Q describes a period already ended. Even
  with perfect data the system is looking backwards; it infers durability, it does not see
  ahead.
- **DCF is assumption-sensitive.** Small changes to WACC or terminal growth swing fair value
  substantially. That is why the output is always a range with a sensitivity grid — a DCF
  reported as a single number is false precision.
- **Valuation bands are AI-cycle-distorted.** The multiple ranges in
  [§5.5](#55-valuation) are practitioner conventions inflated by the post-2023 buildout, not
  constants. They need periodic recalibration or they will read every name as expensive in a
  downcycle and cheap at a peak.
- **13F is lagged, long-only, and partial.** Detailed in [§6](#6-smart-money-layer). Used as
  corroboration for exactly this reason.
- **Single-sector concentration is the design, and it is a real risk.** A diversified core
  and gold sleeve reduce it; they do not remove it. If AI capex disappoints, most of this
  universe falls together — the sub-segments are far more correlated than their count
  suggests.
- **Backtests are not forecasts.** Even a methodologically clean backtest describes one
  historical path. This universe's history is dominated by a boom; the strategy has less
  evidence about busts than the metrics will imply.
- **Small accounts constrain what is possible.** At $1,000 the individual-name sleeve is
  small enough that fees, spreads, and rounding matter relative to expected edge.
- **Coverage gaps are reported, never filled.** When a figure is not tagged, the system says
  so. It does not substitute an estimate and present it as a fact. Missing data must never
  read as good data.
- **Some names in the universe cannot be analysed from SEC data.** TSMC is the current
  example — its IFRS statements never reach SEC's XBRL API. The system flags these rather
  than scoring them, which means the sector's single most important foundry is held only
  through the ETF sleeve, not analysed directly. That is a real limitation of the free-data
  mandate, not an oversight.
- **Non-USD filers need FX conversion for valuation.** Ratio-based quality scoring works in
  any currency, but every price-to-fundamentals multiple requires a point-in-time exchange
  rate, adding a data dependency that does not exist for US filers.

---

## 15. Not investment advice

This is a personal research tool. It produces analysis, not recommendations from a licensed
adviser, and no output should be read as personalised investment advice.

All backtested and paper-trading results are **hypothetical and do not represent actual
trading**. Past performance — real or simulated — does not indicate future results. Every
investment decision, and its consequences, remain the user's own.
