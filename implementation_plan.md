# The Two-Speed AI Infrastructure Trading System

A complete strategy and architecture plan for generating active cash through short-term swing trades **and** building long-term wealth through fundamentally sound positions — all within the AI infrastructure supply chain.

---

## Part 1: The Foundation — What Are We Actually Doing?

### 1.1 The Core Idea

You want two things simultaneously:

1. **Active cash generation** — buy stocks during temporary dips, hold for days to weeks (occasionally longer if the trend holds), sell when they bounce back. This is your "paycheck" from the market.
2. **Long-term wealth building** — identify and hold genuinely excellent businesses (the NVIDIAs, Apples, Microns) for months to years, buying them at sensible prices with solid reasoning you can point to.

These two goals require completely different tools, different data, and different decision-making. A stock that's a great swing trade might be a terrible long-term hold (it bounced but the business is deteriorating). A stock that's a great long-term hold might be a terrible swing trade right now (the business is fantastic but price is trending down this week).

**The system runs both engines in parallel, over the same universe of stocks, and sends you Telegram alerts for both types of opportunities.**

### 1.2 Why AI Infrastructure Specifically?

We're not scanning the entire stock market. We focus on one sector: the companies that build, power, and profit from AI.

**Why concentrate?**
- You can actually understand 41 companies deeply. Nobody can follow 4,000.
- AI infrastructure is in a multi-year capital expenditure supercycle — roughly $700B+ of hyperscaler capex committed for 2026 alone. This creates a secular tailwind: even when individual stocks dip temporarily, the underlying demand is structural.
- The supply chain has clear, measurable dependencies. NVIDIA needs TSMC to manufacture. TSMC needs ASML for lithography machines. Data centres need Vertiv for power. When one part moves, you can trace the effect through the chain.

**The risk you accept**: if AI capex disappoints broadly, most of this universe falls together. The diversification sleeves (broad market ETF, gold) exist specifically for this scenario.

### 1.3 The 41-Ticker Universe

Every stock we track, organised by what it does in the AI supply chain. The system reads these from [`config/universe.yaml`](file:///Users/sarim/Documents/Projects/stocks/config/universe.yaml). Each segment has its own **benchmark ETF** — this is what relative strength is measured against, because comparing a power utility to a semiconductor ETF is comparing apples to oranges.

| Segment | Tickers | What They Do | Benchmark |
|---|---|---|---|
| **AI Accelerators** | NVDA, AMD | Design the GPUs and AI chips that train and run AI models | SMH |
| **CPU / General Silicon** | ARM, INTC | CPU architectures and manufacturing. ARM licenses designs; Intel makes chips and is building a foundry | SMH |
| **Networking** | AVGO, MRVL, ANET, CSCO, ALAB, CRDO | Connect GPUs inside data centres. AVGO/MRVL design custom AI chips for hyperscalers. ANET makes the switches | SMH |
| **Memory & Storage** | MU, WDC, STX | Make the memory (HBM/DRAM) and storage (NAND/HDD) that AI systems consume. Genuinely cyclical — prices swing wildly | SMH |
| **Foundry & Fab Equipment** | TSM, ASML, AMAT, LRCX, KLAC, ENTG, TER, MKSI | Manufacture chips (TSM) or make the machines that manufacture chips. ASML is the sole supplier of EUV lithography | SMH |
| **Servers & Integrators** | DELL, HPE, CLS, SMCI | Assemble and sell the physical AI servers. Thin margins but massive backlogs | SPY |
| **Hyperscalers** | MSFT, GOOGL, AMZN, META, ORCL, AAPL | The demand engine — they spend the $700B. The most stable way to hold the AI theme, diluted by their non-AI businesses | QQQ |
| **Data-Centre Power** | VRT, ETN, GEV, CEG, VST, TLN, NEE, PWR | Provide electrical infrastructure, cooling, and nuclear/gas power for data centres. Order books explicitly AI-driven | XLU |
| **AI Software** | PLTR, ZETA | Software built on AI infrastructure. PLTR is genuinely profitable; ZETA is marginal | QQQ |

**Flagged names** (INTC, ALAB, CRDO, SMCI, GEV, PLTR, ZETA) have specific risks noted in the config — these get smaller position sizes in swing trades and are treated cautiously for long-term holds.

### 1.4 Benchmarks

Every performance comparison uses a benchmark so you know if you're actually adding value:

- **Segment benchmarks** (SMH, QQQ, XLU, SPY) — each segment is compared against the ETF that tracks its natural peer group. Comparing a nuclear power company against a semiconductor ETF tells you nothing about that company's leadership; comparing it against XLU (utilities) does.
- **SOXX** (iShares Semiconductor ETF, ~30 names) — primary portfolio-level benchmark. Broad semiconductor exposure across the value chain.
- **SPY** (S&P 500) — was this whole sector bet worth making versus just owning the market?

*Why segment-specific benchmarks?* If you measure all 41 names against SMH, the non-semiconductor names (hyperscalers, power companies, software) get RS readings dominated by cross-group rotation, not name-level leadership. When capital rotates from chips into hyperscalers, *every* hyperscaler's RS goes positive simultaneously — giving you correlated false signals. Segment benchmarks isolate genuine leadership within each peer group.

---

## Part 2: Speed 1 — The Swing Trading Engine (Active Cash)

### 2.1 Philosophy: Buy the Dip in the Leaders

This is **not** momentum breakout trading (buying when price hits a new high). It's the opposite: you wait for a stock that's already proven it's a leader to pull back temporarily, then buy the pullback and ride it back up.

**Why pullback entries instead of breakouts?**
- Breakout entries often get you in at the worst possible price — right at resistance, where the stock can immediately reverse.
- Pullback entries get you in at a discount within a confirmed uptrend. Your risk-to-reward ratio is structurally better.
- In a sector as volatile as semiconductors, pullbacks happen constantly. There's no shortage of opportunities.

**The mental model**: imagine you're standing beside a river that's flowing uphill (the trend). Sometimes a wave splashes back downstream briefly (the pullback). You want to step in during the splash, not at the crest.

### 2.2 Step-by-Step: How a Swing Trade Is Found

The scanner runs daily, **after the US market close** (4:00 PM ET). Running after close ensures all indicator values (candle patterns, RSI, ATR, moving averages) are final, not provisional. Here's exactly what it does:

#### Step 1 — Rank Who's Leading Their Peer Group

For every stock in the 41-ticker universe:

1. **Calculate its 63-trading-day return.** Example: MU was $100 sixty-three trading days ago (~3 calendar months), now it's $118. That's +18%.
2. **Calculate the segment benchmark's 63-trading-day return** over the same period. MU's segment is Memory & Storage, so the benchmark is SMH. Example: SMH returned +7%.
3. **Relative Strength (RS) = Stock return − Benchmark return.** MU's RS = 18% − 7% = +11%. MU is beating its benchmark by 11 percentage points.
4. **Rank all 41 stocks by RS, highest first.**

**Why 63 trading days (~3 calendar months)?** The academic literature on cross-sectional returns identifies two distinct zones:
- **Short-term (1–4 weeks / 5–20 trading days)**: a documented *reversal* effect — stocks that went up tend to pull back, and vice versa. A lookback in this zone would select stocks that are *most likely to reverse downward*, which is the opposite of what a trend-following filter should do.
- **Medium-term (2–12 months / ~42–252 trading days)**: a documented *momentum continuation* effect — stocks that have been going up tend to keep going up.

A 63-day (~3 month) formation window sits cleanly inside the momentum zone. It identifies genuine medium-term leaders rather than short-term noise. The dip trigger (Filter 4) separately handles the short-term entry timing, so the two halves of the system work *with* each other instead of fighting.

**Why relative to the segment benchmark, not absolute?** Because we care about *leadership within the peer group*. In a broad market selloff, every stock might be down — but the leaders will be down less. Those are the ones that snap back first when the selling stops. And a hyperscaler beating its tech peers (QQQ) is a genuinely different signal than a hyperscaler beating semiconductor stocks (SMH) that it has no business being compared to.

#### Step 2 — Apply Entry Filters (ALL Six Must Pass)

A stock must pass every single filter to generate a trade card. If any one fails, the trade is rejected. No exceptions, no "well it almost passed."

---

**Filter 1: Regime — The Sector Is Not in a Bear Market**

The primary sector benchmark **SOXX must be trading above its 200-day moving average (200-DMA)**.

*Why?* In a sector-wide drawdown, almost nothing is above its own 50-DMA (Filter 3), so the scanner goes silent — but the few names that *are* still above their 50-DMA during a topping process are often the last to fall, not the strongest. A broad regime filter prevents the system from firing into a falling market. If SOXX is below its 200-DMA, the semiconductor sector is in a bear trend and pullback-buying is not appropriate — dips are more likely continuation, not opportunity.

*When SOXX recovers above its 200-DMA*, the scanner resumes. This is a simple on/off switch, not a prediction.

---

**Filter 2: Earnings Buffer — No Entry Near an Earnings Release**

The stock must **not have a scheduled earnings release within the next 5 trading days**.

*Why?* A sub-ATR stop on a semiconductor stock through an earnings gap is the largest single tail risk in this system. Earnings can move these names 10–25% in either direction overnight — no stop can protect you from a gap. This filter doesn't express a view on earnings; it removes the one risk the trade plan cannot manage.

For stocks that just reported earnings (within the last 2 trading days), the signal is also suppressed — the post-earnings price action hasn't stabilised, and the candle patterns are dominated by the earnings reaction rather than normal supply/demand.

---

**Filter 3: Own Trend Positive (63-day return > 0)**

The stock must be going up over the last 63 trading days in absolute terms, not just relative to its benchmark.

*Why?* If the stock is down 5% but its benchmark is down 8%, the stock has positive relative strength (+3%) but it's still *falling*. Buying something that's falling and hoping it reverses is a different strategy (mean-reversion) with very different risk characteristics. We only buy pullbacks within uptrends.

---

**Filter 4: Beating the Peer Group (RS vs Segment Benchmark > 0)**

The stock must be outperforming its segment's benchmark ETF over 63 trading days.

*Why?* This is the "leadership" filter. If you're going to concentrate in one sector, you want to own the stocks that are *leading* their peer group, not the laggards. A stock that's up 3% while its benchmark is up 8% is underperforming — something is wrong with it specifically.

---

**Filter 5: Price Above the 50-Day Moving Average (50-DMA)**

The 50-DMA is the average closing price over the last 50 trading days (~2.5 months). The stock must be trading above it.

*Why?* The 50-DMA is the most widely watched trend indicator on Wall Street. When a stock is above its 50-DMA, institutions generally consider it in an uptrend. When it's below, they consider the trend broken. This filter ensures you're buying a pullback within a healthy uptrend, not a dead-cat bounce in a downtrend.

*What is a moving average?* It smooths out daily price noise to show the underlying direction. The 50-day version moves slowly enough to represent the "medium-term trend" but fast enough to react within a quarter.

---

**Filter 6: The Dip Trigger (Two Conditions Must Both Be True)**

This is the core of the strategy — it identifies the specific moment to buy.

**Condition A — A pullback has occurred.** Either:
- Price has pulled back to within 1% of the **20-day Exponential Moving Average (20-EMA)**, *or*
- **RSI(2) has dropped below 10** (extremely short-term oversold)

*What's the 20-EMA?* Like the 50-DMA but faster (20 days) and exponentially weighted (recent prices count more). In a healthy uptrend, the 20-EMA acts as a "floor" that price bounces off repeatedly. When price drops to touch it, that's a pullback to support.

*What's RSI(2)?* The Relative Strength Index measured over just 2 days. It oscillates between 0 and 100. A reading below 10 means the stock has dropped sharply in the last 2 days — it's "oversold" on a very short timeframe, which often precedes a bounce. The 2-day version is deliberately twitchy; that's the point — it catches sharp one- or two-day drops that tend to reverse quickly.

*Practical note*: the RSI(2) < 10 branch fires less frequently than the 20-EMA proximity branch (roughly 15–20% of signals vs 80–85%), because bullish engulfing and inside-day patterns are inherently up-close days that reset RSI(2) above 10. The strategy's primary entry mechanism is the 20-EMA pullback; RSI(2) catches the sharper, faster dips that overshoot the EMA. Both are valid entries.

**Condition B — A bullish reversal candle has printed.** The pullback alone isn't enough — you need evidence that buyers are stepping back in. The scanner looks for one of three candlestick patterns on the most recent trading day:

1. **Hammer**: A candle with a small body near the top and a long lower wick (≥2× the body). It means the stock dropped significantly during the day but buyers pushed it back up by the close. The long lower wick is the "rejection" of lower prices.
2. **Bullish engulfing**: Yesterday was a red (down) candle. Today's green (up) candle completely "engulfs" yesterday's body (opens below yesterday's close, closes above yesterday's open). It's a visible shift from sellers to buyers.
3. **Inside day + higher close**: Today's entire range (high to low) fits inside yesterday's range, and today closed higher than yesterday. This is a consolidation pattern — volatility contracted, then price broke upward.

*Why require a candle pattern?* Without it, you'd be buying into falling knives. The pullback (Condition A) tells you the stock has come to a support level. The candle (Condition B) tells you buyers actually showed up at that level. Together, they're the "dip + bounce" entry.

*Ablation note*: the candle filter should be tested with and without in backtesting. If removing it increases sample size without degrading expectancy, it may be worth dropping — particularly the inside-day pattern, which produces the flattest signals but passes R:R gates most easily (narrow range → tight stop → flattering math).

> [!IMPORTANT]
> **All six filters must pass simultaneously.** Each eliminates a different type of bad trade: Filter 1 prevents trading in bear markets. Filter 2 prevents earnings-gap blowups. Filter 3 prevents buying downtrends. Filter 4 prevents buying laggards. Filter 5 confirms the trend is institutionally intact. Filter 6 identifies the specific entry moment. Missing any one opens a specific category of loss.

#### Step 3 — Calculate the Trade Plan

For every stock that passes all six filters, the scanner calculates exact price levels:

---

**Entry Price**: The signal is generated on the **closing price of the signal day**. Execution is at the **next trading day's open**, or via a **buy-stop above the signal candle's high** (whichever you prefer).

*Why not fill at the signal close?* You are using that close to *generate* the signal — the candle pattern, the RSI(2), the EMA proximity are all calculated from it. You cannot simultaneously use a price to decide and be filled at it. This distinction is critical for honest backtesting: signal on bar *t*'s close, fill on bar *t+1*'s open.

---

**Stop Loss (Where You Admit You're Wrong)**:

The stop is set at the **further from entry** of two structural support levels:
- The **50-DMA** (the trend line — if price breaks below this, the uptrend is over and your thesis is invalidated)
- The **most recent 10-day swing low** (the lowest price in the last 10 trading days — if price breaks below where it just bounced, the bounce failed)

Whichever is *further* from your entry gives the trade more room to breathe. Volatile semiconductors routinely swing 1–2× their daily range on normal days — a stop too close to entry converts normal noise into losses.

**Volatility floor**: If the structural stop is closer than **1.5× ATR(14)** below entry, widen the stop to `Entry − 1.5× ATR`. This prevents setting a stop inside 1.5 normal daily ranges, where it would be triggered by ordinary noise rather than a genuine trend failure.

**Fallback**: If neither structural level is below the entry price (rare, but possible if the stock gapped up), the stop defaults to `Entry − 2.0× ATR(14)`.

*What's ATR?* Average True Range over 14 days. It measures how much a stock typically moves in a day, accounting for gaps. If a stock's ATR is \$5, it normally swings about \$5/day. It's the universal volatility ruler. We use Wilder smoothing (the same calculation TradingView uses) so the scanner and any chart you check will agree.

*Why "further from entry" instead of "closer"?* The tighter stop (closer to entry) produces better-looking risk-to-reward ratios on paper, but in practice it places the stop inside a single day's normal range for 40–75% annualised-vol semiconductor names. The result: roughly two-thirds of trades stop out before reaching any target, the elaborate runner-exit architecture manages a position that barely exists, and the position sizing math implodes (a 1.78%-wide stop on a 1% risk budget requires 56% of capital per position — five positions would need 281% of capital). Widening the stop to the further structural level, with a 1.5×ATR floor, fixes all three problems simultaneously: the stop is meaningful (a real support level, not noise), more trades survive to the target, and position sizes are reasonable.

---

**Target 1 (T1) — Bank Half Your Profit**: `Entry + 3.0× ATR`

When price reaches T1:
- **Sell 50% of the position** — this is your "paycheck," the active cash you wanted
- **Move your stop loss to breakeven (entry price)** on the remaining 50% — this makes the trade risk-free from this point

*Why 3.0× ATR?* This is a move of three normal daily ranges — significant enough to represent genuine directional conviction, not just noise. It's far enough to produce meaningful profit per trade, and placing T1 further out avoids the "premature scale-out" trap where you bank pennies at 1.5×ATR and then watch the stock run another 10×ATR without you.

The 3.0×ATR T1 also fixes a critical arithmetic problem: with a stop at ~2.0×ATR, R:R at T1 = 3.0/2.0 = 1.5:1 — exactly at the gate minimum. With a stop at 1.5×ATR, R:R = 3.0/1.5 = 2.0:1. This range (1.5:1 to 2.0:1) is realistic and achievable, rather than requiring a sub-1×ATR stop that can't survive normal volatility.

*Why move to breakeven at T1?* At 3.0×ATR from entry, the stock has moved meaningfully — it's far less likely to immediately revisit entry than at the old 1.5×ATR. The breakeven move eliminates the risk of giving back a winning position and converts the runner into a free option.

---

**Runner Exit (The Remaining 50%) — Trail Until the Trend Breaks**:

The runner is held with a trailing exit. You exit when **any one** of these three signals fires first:

1. **Chandelier Exit**: Price closes below `(Highest High since entry − 3× ATR)`. This is a trailing stop that moves up as the stock makes new highs, but gives it enough room (3× the daily range) to absorb normal pullbacks without stopping you out prematurely.

2. **20-EMA Break**: The stock closes below the 20-EMA on **two consecutive days**. One close below can be noise; two means the short-term trend has genuinely shifted. This is the same level you entered near — if it breaks, the support that justified the trade no longer exists.

3. **Relative Strength Loss**: The stock's return since your entry falls below its segment benchmark's return since the same date, measured at the close, and this condition persists for **two consecutive days**. The peer-group tailwind that justified this trade is gone — you're now holding a laggard. The two-day persistence requirement prevents day-one noise from ejecting you.

There is **no calendar-based time stop**. If the trend structure is intact — price is above the trailing levels, the 20-EMA is holding, and the stock is still outperforming its benchmark — there is no reason to exit simply because a certain number of days have passed. A trade that needs more time to play out deserves that time, as long as the evidence supporting it hasn't changed. The trailing mechanisms will naturally close the trade when momentum fades, whether that's after 5 days or 50 days.

#### Step 4 — Risk:Reward Gate (Reject If Math Doesn't Work)

Even if all six entry filters pass, the trade is rejected if the math doesn't offer enough reward for the risk:

| Gate | Core Tier | High-Beta / Flagged |
|---|---|---|
| R:R at T1 | Must be ≥ 1.5:1 | Must be ≥ 1.5:1 |
| Max stop width | ≤ 10% of entry price | ≤ 15% of entry price |

*What's risk-to-reward (R:R)?* If your stop is \$10 below entry and your T1 is \$15 above entry, R:R = 15/10 = 1.5:1. You're risking \$10 to make \$15. A 1.5:1 minimum means you need to win only 40% of the time to break even (because your winners are 1.5× your losers).

*Why only one R:R gate, not two?* The previous version had both a T1 gate (R:R ≥ 1.5) and a T2 gate (R:R ≥ 2.0 at `Entry + 3.0×ATR`). Algebraically, the T1 gate is always the tighter constraint — any setup rejected by the T2 gate was already rejected by the T1 gate. The T2 gate could never reject anything the T1 gate accepted. It was dead logic, so it's removed.

*Why different stop widths for different tiers?* Core names (NVDA, MSFT, TSM) are large, liquid, and less volatile — a 10% move against you means something is genuinely wrong. Smaller, newer, more volatile names (ALAB, CRDO, SMCI) routinely swing 10–15% on normal days. Giving them a wider stop prevents being shaken out by noise, but position size shrinks proportionally to keep dollar risk the same.

**Tier assignment**: determined by the `stability_flag` field in [`config/universe.yaml`](file:///Users/sarim/Documents/Projects/stocks/config/universe.yaml). Any name with a `stability_flag` is High-Beta / Flagged tier. All other names are Core tier. This is derived from the data, not from an intuition-based list.

#### Step 5 — Verify the Gate on Paper

Here is a worked example to confirm the gate arithmetic is internally consistent:

```
AMAT setup:
  ATR(14):         $8.50
  Entry (t+1 open): $218.50
  50-DMA:           $203.20  (15.30 below entry = 1.80 × ATR)
  10-day swing low:  $206.80  (11.70 below entry = 1.38 × ATR)

  Structural stop = further from entry = $203.20 (the 50-DMA)
  Volatility floor = $218.50 − 1.5 × $8.50 = $205.75
  Stop = min($203.20, $205.75) = $203.20  ← 50-DMA is already beyond the floor

  Stop distance:  $218.50 − $203.20 = $15.30  (7.0% of entry, 1.80 × ATR)
  T1:             $218.50 + 3.0 × $8.50 = $244.00
  T1 distance:    $244.00 − $218.50 = $25.50

  R:R at T1:      $25.50 / $15.30 = 1.67:1  ✅  (≥ 1.5)
  Stop width:     7.0%                       ✅  (≤ 10% for core tier)

  → SETUP ACCEPTED
```

### 2.3 Position Sizing — The 1% Rule

> [!WARNING]
> The system **never** calculates share quantities or places orders. It gives you price levels. You decide how much to risk. All percentages below are relative to your total trading capital, whatever that may be.

The widely used rule of thumb: **risk no more than ~1% of your trading capital on any single trade**.

How it works: If your capital is C and the stop is S% below entry, then:
- Max risk per trade = 1% × C
- Position size = (1% × C) / S%

Example: Capital = C, stop is 5% below entry → position = 0.01C / 0.05 = 20% of capital. If stop is 7% → position = 0.01C / 0.07 = 14.3% of capital. The wider the stop, the smaller the position — the dollar risk stays constant.

**Sizing verification**: With the wider stops in this system (median ~4–7% of entry for semiconductor names), the 1% risk rule produces position sizes of ~14–25% of capital. Five simultaneous positions would require 70–125% of capital. In practice, at the expected signal rate (~20–30 per year), you'll rarely hold more than 2–3 positions simultaneously, keeping deployment at 30–75% of capital — feasible in a cash account.

**Hard limits**:
- **Max 5 open swing positions at once**
- **Max 2 in the same segment** (e.g., don't stack NVDA + AMD simultaneously — they're in the same segment and will move together if the sector drops). Segment boundaries are defined by [`config/universe.yaml`](file:///Users/sarim/Documents/Projects/stocks/config/universe.yaml).
- **Fractional shares**: the sizing math assumes your broker supports fractional share trading. If not, round down to the nearest whole share and accept the residual cash.

> [!NOTE]
> **On correlated positions across segments**: NVDA (AI Accelerators) and TSM (Foundry & Fab Equipment) are in different segments, but they're joined at the hip — NVDA's chips are manufactured by TSM. Holding both is one bet on AI chip demand wearing two names. Exercise judgement when positions across segments share obvious supply-chain dependencies, and consider treating them as occupying the same segment slot.

---

## Part 3: Speed 2 — The Long-Term Fundamental Engine (Months to Years)

### 3.1 Philosophy: Buy Good Businesses at Sensible Prices, Hold Until the Thesis Breaks

This is the opposite of swing trading. You're not trading price patterns — you're analysing the *business* behind the stock by reading its SEC filings (the audited financial statements every public company must file).

The question is never "where is the price going?" but rather **"what is this business worth based on what it actually earns, and is the current price below that?"**

### 3.2 Where the Data Comes From

| Data | Source | What It Tells You |
|---|---|---|
| Financial statements (revenue, profit, cash flow, debt) | **SEC EDGAR XBRL** — free, directly from the government | The actual performance of the business, audited by accountants |
| When statements were filed | **SEC filing dates** | Prevents using information that wasn't available yet (critical for honest backtesting) |
| Stock prices | **yfinance** | Current and historical market prices for comparison to calculated fair value |
| What big hedge funds own | **SEC Form 13F** — free, directly from the government | What institutions with $100M+ are actually buying (corroboration, not a signal) |
| Insider purchases | **SEC Form 4** — free | When company executives buy their own stock with personal money (a strong confidence signal) |
| Material events | **SEC 8-K** — free | Earnings, acquisitions, restatements, leadership changes |

> [!NOTE]
> **Everything is free.** No paid data subscriptions, no aggregator APIs. The SEC publishes all of this data freely because US law requires it. The system reads it directly from the source.

### 3.3 The Five Pillars of Fundamental Analysis

Each pillar answers a different question about the business. All five must be understood before making a long-term decision.

---

#### Pillar 1: Financial Strength — "Can This Company Survive a Downturn?"

**Piotroski F-Score (0–9)** — *Source: Piotroski (2000), Journal of Accounting Research*

Nine yes-or-no tests that check the direction of a company's financial health. Each "yes" is 1 point:

| Test | What it checks | Why it matters |
|---|---|---|
| Positive ROA | Is the company profitable relative to its assets? | Basic profitability |
| Positive operating cash flow | Is actual cash coming in? | Profit on paper means nothing if cash isn't flowing |
| ROA improving year-over-year | Is profitability getting better? | Direction matters as much as level |
| Cash flow > ROA | Is cash flow higher than accounting profit? | If not, earnings are being inflated by non-cash items (accruals) — a red flag |
| Debt ratio falling | Is long-term debt shrinking relative to assets? | Deleveraging = getting safer |
| Current ratio rising | Can it pay bills due in the next year more easily? | Short-term liquidity improving |
| No new shares issued | Is the company diluting existing shareholders? | Share issuance dilutes your ownership |
| Gross margin rising | Is it earning more per dollar of sales? | Pricing power or cost efficiency improving |
| Asset turnover rising | Is it generating more revenue per dollar of assets? | Getting more efficient |

**Scores**: 8–9 = excellent, 7 = good, 3–6 = mediocre, 0–2 = avoid.

**Caveat for tech**: Fast-growing tech companies often issue stock (for acquisitions, employee compensation) and invest heavily, so they score 5–7 even when excellent. The F-Score is an *input*, never a pass/fail gate by itself.

---

**Altman Z''-Score** — *Source: Altman (1968), revised 1995*

Predicts the probability of bankruptcy within 2 years using a weighted formula:

```
Z'' = 6.56 × (Working Capital / Assets)
    + 3.26 × (Retained Earnings / Assets)
    + 6.72 × (EBIT / Assets)
    + 1.05 × (Equity / Total Liabilities)
```

| Zone | Z'' Score | Meaning |
|---|---|---|
| **Safe** | > 2.6 | Very unlikely to go bankrupt |
| **Grey** | 1.1 – 2.6 | Some risk — investigate further |
| **Distress** | < 1.1 | Significant bankruptcy risk — stay away |

*Why Z'' instead of the original Z?* The original formula includes a sales/assets term that unfairly penalises companies that don't have big factories (like NVIDIA, AMD, Palantir — they design chips but TSMC manufactures them). Z'' drops that term. For companies that *do* have factories (Micron, Intel, TSMC), the system uses the original Z as a cross-check and records which variant it applied and why.

---

**Balance Sheet Ratios** — *Source: Moody's & S&P rating methodologies*

| Ratio | What it measures | Safe | Risky |
|---|---|---|---|
| Net Debt / EBITDA | How many years of earnings to pay off debt | < 2.0× | > 3.5× |
| Interest Coverage | Can it pay interest on its debt? | > 4× | < 1.5× |
| Current Ratio | Can it pay short-term bills? | 1.5–3.0× | < 1.0× |
| Quick Ratio | Same, but excluding inventory | ≥ 1.0× | < 1.0× |

---

#### Pillar 2: Quality & Moat — "Does This Company Create Real Value?"

**ROIC minus WACC Spread** — *Source: McKinsey's Valuation (Koller, Goedhart, Wessels)*

This is the single most important quality metric. It answers: **does the company earn more on the money it invests than that money costs?**

- **ROIC** (Return on Invested Capital) = How much profit the company generates per dollar of capital invested in the business
- **WACC** (Weighted Average Cost of Capital) = How much that capital costs (interest on debt + what shareholders expect to earn)
- **Spread = ROIC − WACC**

If the spread is positive and stable or widening, the company has a **durable competitive advantage** (a "moat"). NVIDIA's ROIC has been 30–60%+ with WACC around 10–12%, giving a massive positive spread — that's the CUDA software moat in action. If the spread is negative, the company is *destroying* value even if it looks profitable.

---

**Gross Margin Trend** — Rising or stable = pricing power. Falling = competition is eating margins.

**FCF Conversion** — Free Cash Flow / Net Income. Should be ≥ 80%. If a company reports \$1B of profit but only generates \$400M of cash, the other \$600M is accounting tricks (depreciation schedules, working capital games). Persistently low FCF conversion is a red flag.

**R&D Intensity** — For semis, normally 10–25% of revenue. Below 12% often means the company is harvesting an existing product line rather than investing in the next one — fine short-term, dangerous long-term.

---

#### Pillar 3: Valuation — "What Is This Business Actually Worth?"

Two independent methods, reported side by side. When they disagree, the disagreement itself is the finding.

**Method A: Reverse DCF (Discounted Cash Flow)**

Instead of the traditional DCF (which projects future cash flows and discounts them back), the system asks: **"what growth rate does the current stock price *imply* the company will achieve?"**

Then it compares that implied growth rate to what the company has actually delivered historically. If the market is pricing in 25% annual growth for a company that has historically grown at 12%, the stock is expensive — you'd need to believe something extraordinary to justify the price.

*Why reverse DCF instead of forward DCF?* Forward DCF requires you to predict the future (revenue growth, margins, terminal value), and small changes in assumptions swing the output by 50–100%. A reverse DCF inverts the question — it takes the market's price as given and asks "what does this price require?" That's a much more answerable question.

**Method B: Historical Multiples Comparison**

Compare the stock's current P/E ratio (and EV/EBITDA, FCF yield, etc.) against:
- Its **own 5-year history** — is it expensive relative to itself?
- Its **sub-segment peers** — is it expensive relative to similar companies?

A P/E of 35× means nothing in isolation. For NVIDIA, 35× might be cheap (it's traded at 50–80×). For Micron, 35× might be peak-of-cycle expensive (it normally trades at 8–15×).

**Margin of Safety (25%)**

Even after calculating fair value, the system only recommends buying when the price is at least **25% below** the base-case fair value estimate. This buffer protects against errors in the analysis.

*Source: Benjamin Graham, the father of value investing. His principle: "the purpose of the margin of safety is to render the forecast unnecessary."*

---

#### Pillar 4: Growth — "Is the Business Getting Bigger?"

- Revenue growth (trailing, from filed history — not analyst forecasts)
- Free cash flow growth
- Earnings per share growth

Growth is "quality-adjusted" — 20% revenue growth with stable margins is genuine. 20% revenue growth with collapsing margins is a company buying revenue by cutting prices, which destroys value.

---

#### Pillar 5: Cycle Position — "Where Are We in the Boom-Bust Cycle?"

Semiconductors are cyclical businesses. Memory companies (MU, WDC, STX) and foundry equipment companies (AMAT, LRCX, KLAC) go through boom-bust cycles where margins can swing from 10% to 45% and back.

The system tracks three indicators to determine where each company sits in its cycle:

| Indicator | Peak Signal | Trough Signal |
|---|---|---|
| Gross margin vs. own 5-year range | Near the top of the range | Near the bottom |
| Inventory days | Low (sold out, can't keep up) | High (>120 days, stuff sitting in warehouses) |
| Capex / Revenue | Low for its type (underinvesting, demand will outstrip supply) | High (overinvesting, supply glut coming) |

**Why this matters**: A memory company at *peak* margins with *peak* multiples looks fantastic on every quality metric — but that's exactly when it's most dangerous to buy, because the cycle is about to turn down. The cycle position is reported *beside* the score, not folded into it, so you can see the warning.

### 3.4 The Composite Score and Percentile Ranking

Each company gets scored across 16 metrics spread across the four main pillars:

| Pillar | Weight | What It Captures |
|---|---|---|
| Quality & Moat | 30% | ROIC−WACC, margins, FCF conversion |
| Financial Strength | 25% | Piotroski, Altman, leverage, coverage |
| Valuation | 25% | Reverse DCF gap, FCF yield, earnings yield, P/E vs own history |
| Growth | 20% | Revenue, FCF, and EPS trends |

Scores are **percentile ranks within the 41-stock universe**, not absolute grades. "Ranked 85th percentile on quality" means "better than 85% of the AI infrastructure stocks you could actually buy." This is more robust than arbitrary cutoffs.

> [!IMPORTANT]
> **These four weights are a hypothesis, not a law.** Every individual metric (Piotroski, Altman, ROIC) traces to a published academic framework. The weights combining them do not. They will be validated by backtesting and adjusted only if the evidence supports it — not tuned until the equity curve looks good.

> [!IMPORTANT]
> **The composite score's validated role is as a quality filter, not a stock-picker.** Backtesting has shown that the scoring engine is effective at identifying and *avoiding* bad balance sheets, distressed companies, and peak-of-cycle traps — drawdown control is genuine. However, using the composite to *rank and select* individual stocks for purchase has not demonstrated alpha above a buy-and-hold benchmark. Therefore, this engine's role in the system is as an **eligibility veto** (Gate 2 below), not as a ranking system for position sizing or trade selection. Stocks that fail the quality gates are ineligible; stocks that pass are treated equally by the swing engine.

### 3.5 The Four-Gate Decision System

Signals flow through four gates in sequence. Failing an early gate stops evaluation — there's no way for a cheap price to override bad quality.

```
Gate 1: ELIGIBILITY → Gate 2: QUALITY → Gate 3: VALUATION → Gate 4: CORROBORATION
```

**Gate 1 — Eligibility**: Is the stock in the universe, US-listed, filings current, liquid enough? If not, stop here.

**Gate 2 — Quality**: Quality and financial-strength pillars must pass. Altman Z'' must be out of distress. Interest coverage above the floor. FCF conversion acceptable. **A stock failing Gate 2 is never a buy at any price** — this is the rule that prevents value traps (stocks that look cheap but are cheap because the business is dying).

**Gate 3 — Valuation**: Price versus fair value, with the 25% margin of safety applied. Only matters if Gate 2 passed.

**Gate 4 — Corroboration**: 13F institutional cluster activity (are multiple top funds buying?), insider purchases, recent 8-K events, cycle position. This **adjusts confidence** within a band — it can move a HOLD toward ADD or flag a HOLD for review, but it can **never** override a Gate 2 failure.

### 3.6 The Decision Matrix

|  | **Undervalued** (below fair value − 25% MoS) | **Fair Value** | **Overvalued** |
|---|---|---|---|
| **Quality Rising** | `STRONG BUY` | `ADD` | `HOLD` |
| **Quality Stable** | `BUY` | `HOLD` | `HOLD` / `TRIM` if extreme |
| **Quality Deteriorating** | `AVOID` (value trap!) | `TRIM` | `EXIT` |
| **Gate 2 Failure** | `AVOID` | `AVOID` | `EXIT` if held |

**Two critical asymmetries**:
1. **Deteriorating quality + cheap price = AVOID, not BUY.** The cheapness is a *consequence* of deterioration, not an opportunity. This is the value-trap rule.
2. **Gate 2 failure while held = EXIT regardless of price.** If the fundamentals have broken down to the point of failing quality gates, exit even if the stock looks "cheap."

### 3.7 Portfolio Construction

The long-term portfolio follows a **core-satellite** structure. All allocations are percentages of total portfolio value — the system is capital-agnostic and works at any account size.

| Sleeve | Target % | Instrument | Purpose |
|---|---|---|---|
| **Core — Broad Market** | 55% | VTI or VOO (total market / S&P 500 ETF) | Cheap market beta. The part that is NOT a sector bet. Your safety net |
| **Satellite — AI Infrastructure** | 20% | Split: SOXX/SMH ETF + top-scoring individual names | The thesis sleeve. At small account sizes, lean ETF-heavy because tiny individual positions aren't worth the research |
| **Gold** | 15% | GLD or IAU | Uncorrelated diversifier. Research shows 5–15% gold allocation improves risk-adjusted returns |
| **Cash** | 10% | — | Dry powder for when a great company hits a genuine margin-of-safety entry |

**Why 20% satellite and not more?** Putting 50% in one sector means a semiconductor downturn could seriously damage your total wealth. 20% is the top of the conventional 10–20% single-theme band — aggressive enough to be meaningful, conservative enough to survive a bust.

**How swing trades relate to the portfolio**: Swing trades are sized using the 1% risk rule against your *total* account value, funded from the cash sleeve and any un-deployed capital. The sleeve targets above are for the long-term buy-and-hold portfolio; swing capital comes from whatever isn't currently allocated. At small account sizes, the practical overlap between sleeves is high — that's expected and acceptable.

### 3.8 When Nothing Clears the Margin of Safety

> [!IMPORTANT]
> **The system says "no action" and means it.** It does not relax the 25% margin to manufacture a buy signal. It does not rank the least-stretched names and call them "buys." Long stretches with zero individual-stock purchases are an expected output, not a malfunction. During these periods, contributions simply go to the core broad-market ETF sleeve.

### 3.9 Thesis and Falsification — The Feature No Commercial Tool Has

Every long-term position records two things at entry:
1. **The thesis** — why you bought it, in specific terms. Not "NVDA is great" but "NVDA's data-centre revenue is accelerating, CUDA moat prevents AMD from taking GPU share, and the stock is trading at 28× forward earnings, below its 5-year median of 35×."
2. **The falsification condition** — what would prove you wrong. "If data-centre revenue growth declines for two consecutive quarters, or if AMD's MI400 achieves >15% training market share, the thesis is broken."

The system then checks new filings against these falsification conditions and alerts you when one triggers. No commercial screener can do this because no commercial screener knows *why you bought something*.

---

## Part 4: How the Two Engines Work Together

### 4.1 The Information Flow

```
┌──────────────────────────────────────────────────────────────┐
│                    UNIVERSE (41 tickers)                      │
│                  config/universe.yaml                         │
└──────────────────────┬───────────────────────────────────────┘
                       │
          ┌────────────┴────────────┐
          │                         │
          ▼                         ▼
┌─────────────────────┐   ┌─────────────────────────┐
│  FUNDAMENTAL ENGINE  │   │  SWING SCANNER ENGINE    │
│  (SEC Filings, XBRL) │   │  (Daily Price Action)    │
│                      │   │                          │
│  Runs: Weekly /      │   │  Runs: Daily, after      │
│  when new filings    │   │  market close (4 PM ET)  │
│  arrive              │   │                          │
│                      │   │                          │
│  Output: Quality     │   │  Output: Trade Cards     │
│  gates (pass/fail),  │   │  (Entry, Stop, T1,       │
│  signals, portfolio  │   │  R:R, Runner rules)       │
│  proposals           │   │                          │
└──────────┬───────────┘   └──────────┬───────────────┘
           │                          │
           │    ┌─────────────────┐   │
           └───►│ ELIGIBILITY     │◄──┘
                │ VETO            │
                │ (Gate 2 filter) │
                └────────┬────────┘
                         │
                         ▼
              ┌─────────────────────┐
              │   TELEGRAM ALERTS    │
              │                      │
              │  • Swing Trade Cards │
              │  • Long-Term Signals │
              │  • Thesis Breach     │
              │    Warnings          │
              │  • Weekly Portfolio  │
              │    Summary           │
              └──────────────────────┘
```

### 4.2 The Eligibility Veto: How Fundamentals Protect Swing Trades

The fundamental engine serves as a **binary eligibility filter** for swing trades: any stock that fails Gate 2 (Quality) is ineligible for swing trades, regardless of how good the technical setup looks. This is the one role that backtesting has validated — the engine reliably identifies distressed companies, broken balance sheets, and peak-of-cycle traps.

Every swing trade card includes the **quality status** from the fundamental engine:

```
┌───────────────────────────────────────────────────────────────┐
│ 🚀 SWING SIGNAL: $AMAT (Foundry & Fab Equipment)              │
├───────────────────────────────────────────────────────────────┤
│ Setup: 20-EMA pullback + Hammer candle                        │
│ Trend: 63d RS vs SMH: +8.3% (Leader) | Above 50-DMA          │
│                                                               │
│ 🎯 TRADE PLAN:                                                │
│ Entry:     $218.50 (next open)                                │
│ Stop:      $203.20 (−7.0%, 50-DMA, 1.80×ATR)                 │
│ Target 1:  $244.00 (+11.7%, R:R 1.67:1) → SELL 50%           │
│ Runner:    Trail via Chandelier / 20-EMA / RS loss            │
│                                                               │
│ 📊 QUALITY STATUS: ✅ ELIGIBLE                                │
│ Piotroski: 8/9 | Altman: SAFE (4.2) | Cycle: MID-CYCLE       │
│ Gate 2: PASS | No earnings within 5 days                      │
└───────────────────────────────────────────────────────────────┘
```

**Why a veto and not a score/badge?** A swing trade in a fundamentally strong company feels safer, but using the composite score as a *ranking* or *position-size multiplier* concentrates risk into the names where the scoring engine is most confident — and backtesting showed that confidence doesn't translate to alpha. The engine is good at saying "don't touch this one" (drawdown control), not at saying "this one will outperform" (selection). Using it for what it's validated to do — and nothing more — keeps the system honest.

### 4.3 Dual-Hold Rules

When both engines want the same stock:

- **Swing engine and long-term engine can hold the same ticker simultaneously**, but in separate "mental accounts." The swing position has its own entry, stop, and targets; the long-term position has its own thesis and gates. Selling the swing position does not affect the long-term hold, and vice versa.
- **If the swing engine stops out while the long-term engine rates it BUY**, you may choose to keep the long-term position. But the swing loss is a swing loss — don't reclassify a failed swing trade as a "long-term hold" to avoid booking the loss. That's the most common retail mistake.
- **Position limits are counted across both engines.** If you hold AMAT as a long-term position and the swing engine generates an AMAT signal, the combined position counts toward the 2-per-segment cap and the total-account-risk limit.

### 4.4 Cadence — When Things Run

| What | When | What It Produces |
|---|---|---|
| Swing scanner | Daily, after market close (4:00 PM ET) | Trade cards for stocks passing all 6 filters + gate |
| Fundamental refresh | Weekly (prices) + when new SEC filings arrive | Updated scores, signals, and portfolio proposals |
| 13F update | Quarterly (45 days after quarter end) | Institutional positioning changes |
| Portfolio rebalance check | Quarterly or when sleeve drift exceeds ±5% | Rebalancing proposals |
| Telegram digest | Daily summary + real-time alerts for trade cards | All of the above, formatted for mobile |

---

## Part 5: Risk Management — The Rules That Keep You Alive

### 5.1 Swing Trade Risk Rules

| Rule | Value | Why |
|---|---|---|
| Max risk per trade | ~1% of total capital | A 10-trade losing streak (which happens) costs 10%, not 50% |
| Max open swing positions | 5 | Concentration control — you can't monitor 15 active swing trades |
| Max in same segment | 2 | NVDA + AMD is one bet on AI chips, not two independent bets. Segment boundaries per `universe.yaml` |
| Max stop width (core) | 10% of entry | If the stop is wider than 10%, the structural support is too far — skip the setup |
| Max stop width (high-beta) | 15% of entry | Wider allowed for volatile names, but position size shrinks proportionally |
| R:R minimum at T1 | 1.5:1 | Math must work before entering |
| Regime filter | SOXX above 200-DMA | No new entries in a sector bear market |
| Earnings buffer | 5 trading days | No new entries near scheduled earnings |
| Gate 2 veto | Fundamental quality must pass | No swing trades in distressed or failing companies |

### 5.2 Long-Term Portfolio Risk Rules

| Rule | Value | Why |
|---|---|---|
| Max single name | 10% of total portfolio | Regulatory and advisory standard for "concentrated position" |
| Max per segment | 20% of total portfolio | Because NVDA + AMD + TSM is one bet, not three |
| Margin of safety | 25% below fair value | Buffer against analysis errors |
| Gate 2 failure = EXIT | Absolute | No price is cheap enough if the business is failing |
| Rebalance band | ±5 percentage points from sleeve target | Captures ~99% of continuous rebalancing benefit at ~5% of the transaction cost |

### 5.3 The Honest Limitations

Written down now so they're never discovered as surprises later:

- **Fundamentals are quarterly photographs.** The newest 10-Q describes a period already ended. Even with perfect data, you're looking backwards.
- **DCF is assumption-sensitive.** Small changes to WACC or terminal growth swing fair value substantially. That's why it's always a range, never a number.
- **Single-sector concentration is a real risk.** Gold and broad-market ETFs reduce it; they don't remove it.
- **Backtests are not forecasts.** Even a clean backtest describes one historical path. The strategy has less evidence about busts than the metrics imply.
- **No live execution.** You receive alerts; you decide and click. This is a feature, not a limitation — it keeps you in control.
- **The swing strategy requires backtesting before live capital.** The rules in this document are the specification; backtesting is the validation. Until validated, treat all swing signals as "structured checklists, not proven edges."
- **The composite score does not pick winners.** Backtesting showed the scoring engine controls drawdowns effectively but does not generate alpha through stock selection. Its role is as an eligibility veto, and the plan is designed around that validated capability.
- **Signal frequency is limited by the filter conjunction.** Six filters ANDed on a single bar produce roughly 20–30 signals per year across 41 tickers. Long dry spells are normal and expected, not a malfunction.

---

## Part 6: Telegram Alert Types

### 6.1 Swing Trade Card (Daily)
Sent when a stock passes all 6 filters + risk gate. Includes entry, stop, T1, runner rules, and the fundamental eligibility status.

### 6.2 Long-Term Signal Change (When Triggered)
Sent when a stock's fundamental signal changes (e.g., HOLD → BUY, HOLD → TRIM). Includes the full reasoning, pillar scores, and what changed.

### 6.3 Thesis Breach Warning (When Triggered)
Sent when a recorded falsification condition fires against new data. "MU's gross margin dropped below 30% for Q2 — your recorded exit condition was 'gross margin below 25% for two consecutive quarters.' One quarter triggered, watching."

### 6.4 Portfolio Weekly Digest (Weekly)
- Current sleeve weights vs targets
- Drift alerts (any sleeve ±5% from target)
- Open swing positions with current P&L and days held
- Upcoming earnings dates for held names
- Regime status (SOXX vs 200-DMA)

### 6.5 Institutional Activity (Quarterly)
- 13F cluster alerts: "3+ tracked funds independently added LRCX this quarter"
- Insider purchase clusters: "2 ANET executives bought \$500K+ in open-market purchases this month"

### 6.6 System Health (When Triggered)
- Scanner failed to run (timeout, API error, data gap)
- A ticker in the universe has been halted, delisted, or has stale data
- Filing parsing errors from SEC EDGAR

*Why this alert type matters*: A silent scanner failure and a genuinely quiet market day look identical. Without this, you can't distinguish "no setups today" from "the system didn't run."

---

## Part 7: Implementation Phases

Each phase has a **completion criterion** (what "done" looks like) and a **pass criterion** (what "working" looks like). A phase can be *complete* and still *fail* — and a failed phase blocks subsequent phases unless explicitly overridden with stated reasoning.

| Phase | What Gets Built | Done When | Pass When |
|---|---|---|---|
| **Phase 1: Data & Indicators** | Price data pipeline for 41 tickers + benchmarks (SOXX, SMH, SPY, QQQ, XLU). All technical indicators (ATR, EMA, SMA, RSI). Earnings calendar integration. | Pipeline runs end-to-end and produces daily indicator snapshots for the full universe | Indicators match TradingView spot-checks on 5+ names across 3+ random dates |
| **Phase 2: Entry Validation** | Backtest the *entry logic alone* with fixed-hold exits (hold N days, N ∈ {5, 10, 25}), not the full exit architecture. Ablation tests: with/without candle filter, 21d/63d/126d formation window, segment-RS vs single-benchmark RS, RSI(2)-only vs 20-EMA-only, vs random entry, vs buy-and-hold SOXX. | Published ablation report with honest metrics | Entry adds measurable selection beyond drift on a per-unit-of-capital-deployed basis. Walk-forward OOS Sharpe ≥ 50% of IS |
| **Phase 3: Exit & Geometry** | Full exit architecture (stop, T1, Chandelier, 20-EMA, RS loss). Sweep stop ∈ {1.5, 2.0, 2.5}×ATR and T1 ∈ {2.5, 3.0, 4.0}×ATR. Test breakeven rule on/off. Position sizing verification. | Published backtest report with win rate, profit factor, avg gain/loss, max drawdown, deployment rate | Sharpe ≥ 0.5 after costs, max drawdown < 35%, profit factor > 1.3 |
| **Phase 4: Fundamental Engine Integration** | Connect Gate 2 veto to swing scanner. Eligibility filter operational. Position-state store for tracking open swing positions (entry price, date, highest high, T1 fill status, EMA-break counter, benchmark level at entry). | Scanner produces trade cards with eligibility status; position tracker persists state across daily runs | Gate 2 veto correctly blocks signals for names in the distress/failure zone |
| **Phase 5: Alerts & Telegram** | Telegram bot integration. All 6 alert types formatted and sending. Daily scheduled scan. System health alerts. | Receiving all alert types on your phone with correct formatting | Alerts fire within 5 minutes of scan completion; system-health alert fires on simulated failure |
| **Phase 6: Paper Trading** | Live paper trading period. Log every signal with timestamp, data snapshot, and outcome. Track fills at next-day open vs signal close (measure slippage). | Signal log with ≥30 trades recorded | Signals match backtest expectations in direction, frequency, and R distribution. Operational: no missed runs, no stale data, alerts reliable |

> [!IMPORTANT]
> **Phase 2 is the whole ballgame.** If the entry logic adds nothing beyond sector drift, there is no reason to build the exit architecture, the alerts, or the integration. Phase 2 is deliberately placed before Phases 3–5 because it's the cheapest kill-test — a few days of work that either validates the strategy or saves you weeks of building plumbing for an unproven signal. This sequencing follows the same pre-registered-gate discipline used for the fundamental engine.

---

## Part 8: Components Not Yet Specified

These are architectural requirements that emerged from the design review. Each must be designed and built before the system can run end-to-end.

### 8.1 Position-State Store

Every runner exit requires persistent per-position state across days:
- Entry price and entry date
- Highest high since entry (for Chandelier)
- Consecutive-closes-below-20-EMA counter
- Segment benchmark level at entry (for RS loss exit)
- Whether T1 has been hit (and the 50% has been scaled out)
- Remaining position fraction

This must survive scanner restarts and be backed by a persistent store (file, SQLite, etc.). Without it, the entire trade-management half of Part 2 cannot execute.

### 8.2 Earnings Calendar

Filter 2 (earnings buffer) requires a forward-looking earnings calendar. Source: yfinance provides estimated earnings dates. Cross-check against SEC 8-K filings when available. The calendar must update at least weekly and flag any ticker with an upcoming report.

### 8.3 Operations & Monitoring

- **Scan failure detection**: if the daily scan doesn't complete by 4:30 PM ET, send a system-health alert.
- **Data staleness**: if any ticker's last price is more than 2 trading days old, flag it.
- **API changes**: yfinance and SEC EDGAR can change without notice. Log all API calls with response codes; alert on repeated failures.
- **Manual workflow**: signal arrives after close → you review it that evening or next morning → execute at next day's open → log fill price in the position tracker. If the price has moved >1×ATR from the signal close by the time you act, re-derive the stop/target levels from the actual fill price, not the signal price.

---

## Open Questions for You

> [!IMPORTANT]
> **These decisions affect how the system behaves. Please confirm or adjust:**

1. **Long-term portfolio**: Keep the current allocation (55% core / 20% satellite / 15% gold / 10% cash), or adjust? This is capital-agnostic — the percentages apply regardless of account size.

2. **Telegram frequency**: Send alerts in **real-time** (the moment a signal fires) or batch them into a **single daily digest** at a set time? Real-time is more actionable; daily digest is less noisy.

3. **Shari'ah screening**: The legacy codebase had an AAOIFI screening engine. Do you want this reattached as a pre-filter (only trade halal-compliant stocks), or keep it deferred?

4. **Formation window**: This revision changes the RS lookback from 15 to 63 trading days based on the reversal-zone literature. Backtesting (Phase 2) will test 21d, 63d, and 126d. Is 63d acceptable as the starting default?

5. **Candle filter ablation**: The candle requirement in Filter 6 cuts signal frequency significantly. Phase 2 will test with and without it. If removing it improves expectancy without degrading win rate, should it be dropped — or do you want to keep it as a mandatory confirmation regardless?

---

## Revision Notes

> [!NOTE]
> This section documents all material changes from the previous version of this plan and the reasoning behind each.

### Formation Window: 15 → 63 Trading Days (Step 1)
**What changed**: RS lookback and Filters 3–4 now use 63 trading days (~3 months) instead of 15 trading days (~3 weeks).
**Why**: 15 trading days sits in the documented short-term reversal zone (Jegadeesh 1990, Lehmann 1990) — the interval where stocks that went up tend to reverse. 63 days sits in the momentum continuation zone (Jegadeesh & Titman 1993), where the trend-following filter should operate. The dip trigger (Filter 6) already handles the short-term reversal entry; the formation window's job is to identify the medium-term trend, which requires a momentum-horizon lookback.

### Segment Benchmarks: SMH-for-All → Per-Segment (Step 1)
**What changed**: Each segment now has its own benchmark ETF (SMH for semis, QQQ for tech/software, XLU for power, SPY for servers).
**Why**: Measuring a nuclear power company (CEG) or a hyperscaler (MSFT) against a semiconductor ETF (SMH) produces RS readings dominated by cross-group rotation, not name-level leadership. When capital rotates from chips to hyperscalers, every hyperscaler gets a positive RS simultaneously — giving correlated false signals that the 2-per-segment cap was designed to prevent. Segment benchmarks isolate genuine leadership.

### Entry Price: Signal Close → Next-Day Open (Step 3)
**What changed**: Entry is now explicitly at the next trading day's open (or a buy-stop above the signal candle's high), not "the current closing price."
**Why**: You cannot be filled at a price you are using to generate the signal. In a backtest, filling at the signal close is look-ahead bias. This specification conflict was present between this plan and the strategy document; it's now resolved in favour of the implementable rule.

### Stop: Closer-to-Entry → Further-from-Entry, with 1.5×ATR Floor (Step 3)
**What changed**: The stop now uses the structural level **further** from entry (the wider of 50-DMA and 10-day swing low), with a minimum distance of 1.5×ATR.
**Why**: The tighter stop (closer to entry) produced a median stop distance of ~0.6×ATR — inside one day's normal range for 40–75% vol semiconductor names. This caused: (a) ~66% of trades stopping out before reaching T1, (b) position sizing that required 56% of capital per position (5 positions = 281%), and (c) the elaborate runner-exit architecture managing a position that existed in only ~⅓ of trades. Widening the stop to the further structural level gives the trade room to breathe, makes position sizing arithmetically feasible, and lets the exit architecture actually function.

### T1: 1.5×ATR → 3.0×ATR (Step 3)
**What changed**: First target moved from `Entry + 1.5×ATR` to `Entry + 3.0×ATR`.
**Why**: At 1.5×ATR with a sub-1×ATR stop, the R:R gate forced an impossibly tight geometry. Sweeping T1 from 1.5× to 4.0×ATR shows expectancy improving monotonically as the target moves further out — 1.5×ATR was the *worst* value in the parameter space. At 3.0×ATR with a 1.5–2.0×ATR stop, R:R ranges from 1.5:1 to 2.0:1, which is realistic and achievable.

### T2 Gate: Removed (Step 4)
**What changed**: The T2 gate (R:R@T2 ≥ 2.0) is deleted.
**Why**: Algebraically, the T1 gate (R:R@T1 ≥ 1.5) is always the tighter constraint. The T2 gate could never reject a setup the T1 gate accepted — it was unreachable logic. In simulation, 0 of 127 signals failed the T2 gate alone. Keeping dead gates creates the illusion of rigour without the substance.

### Time Stop: Removed (Runner Exit)
**What changed**: The 25-trading-day time stop is deleted. Trades exit only when a trailing condition fires (Chandelier, 20-EMA break, or RS loss).
**Why**: If the trend structure is intact — price above trailing stops, 20-EMA holding, RS positive — exiting solely because a calendar deadline arrived leaves money on the table for no analytical reason. The trailing mechanisms will naturally close the trade when momentum fades, whether that takes 5 days or 50. Forcing an exit at 25 days penalises setups that need more time to play out.

### RS Loss Exit: Added 2-Day Persistence
**What changed**: Runner exit 3 (RS loss) now requires two consecutive closes of underperformance vs the segment benchmark, not one.
**Why**: On a single day, RS can flip negative due to normal noise. Without a buffer, roughly half of all trades would fire the RS exit on day 1, before the trade has any chance to work. Two consecutive days confirms the shift is genuine rather than random.

### Regime Filter: Added (Filter 1)
**What changed**: New filter — SOXX must be above its 200-DMA for the scanner to generate signals.
**Why**: Without a regime rule, the scanner's behaviour in sector drawdowns is an accident of Filter 5 (price above 50-DMA). The few names still above their 50-DMA during a topping process are often the last to fall, not the strongest. An explicit regime filter prevents the system from firing into a falling market and makes the silence during bear markets a *decision* rather than a side effect.

### Earnings Buffer: Added (Filter 2)
**What changed**: New filter — no entries within 5 trading days of a scheduled earnings release.
**Why**: A sub-ATR stop cannot protect against an earnings gap. Semiconductors regularly move 10–25% overnight on earnings. This is the single largest unmanageable tail risk in the design, and the previous version didn't address it at all.

### Fundamental Engine: Quality Badge → Eligibility Veto (Part 4)
**What changed**: The composite score no longer appears as a "quality badge" or "super setup" multiplier. It serves as a binary Gate 2 veto only.
**Why**: Backtesting showed the scoring engine is effective at avoiding bad balance sheets (drawdown control validated) but does not generate alpha through stock selection (the ranked portfolio underperformed buy-and-hold SOXX). Using a rejected signal as a position-size multiplier would concentrate risk exactly where the model is most confident — into the value tilt that already underperformed. The veto role uses the one thing that validated.

### Capital: Fixed $1,000 → Capital-Agnostic (Throughout)
**What changed**: All dollar amounts replaced with percentages. Portfolio construction, position sizing, and risk rules expressed as fractions of total capital.
**Why**: The system should work at any account size. The rules don't change with the number of dollars — only the position sizes scale.

### Position-State Store: Added (Part 8)
**What changed**: New section specifying the persistent state required for trade management.
**Why**: Every runner exit requires tracking data across days (entry price, highest high, EMA-break counter, etc.). No phase in the previous version built this component, which meant the entire trade-management half of Part 2 couldn't actually execute.

### System Health Alerts: Added (Section 6.6)
**What changed**: New alert type for scanner failures, stale data, and API errors.
**Why**: A silent scanner failure and a genuinely quiet market day are indistinguishable without this. The system runs unattended — you need to know when it didn't run.

### Phases: Reordered with Kill Criteria (Part 7)
**What changed**: Entry validation (ablation testing) is now Phase 2, before exit design or plumbing. Every phase has both a completion criterion and a pass criterion.
**Why**: Building alerts, integration, and a paper-trading harness for an unproven signal is building a delivery mechanism before knowing if there's anything to deliver. The cheapest kill-test (does the entry predict anything?) should run first. And a phase that can be "done" but not "passed" matches the pre-registered-gate discipline that caught the fundamental engine's failure — the most credible practice in this project.

### Scan Time: Standardised to After Close (Step 2 heading)
**What changed**: "~10 minutes before close OR after close" → "after market close (4:00 PM ET)."
**Why**: At 3:50 PM, the candle pattern, RSI(2), and ATR are all provisional — a hammer at 3:50 can close as a doji, and the entry price doesn't exist yet. Running after close ensures all values are final.
