# AI / Semiconductor Sector Momentum — Dip-Entry Rotation Strategy

**Adapted from:** Strategy 4 "Sector Momentum" (InvestorsWay / Sector Rotation
System, NSE-based). Rebuilt for a single-theme, US-listed universe with a
pullback entry instead of a breakout entry.

> **Paste or upload this file at the start of any future chat** to make
> Claude aware of the exact strategy rules before running a scan, backtest,
> or trade review. This is the single source of truth for the rules — if a
> future conversation proposes something that contradicts this file, flag
> the conflict instead of silently picking one.

---

## 1. Scope & Philosophy

- **Theme-concentrated, not market-wide.** We do NOT rotate across sectors
  (tech vs. banks vs. autos). We stay inside AI / semiconductor / compute
  infrastructure and rotate *leadership within* that theme.
- **Dip entries, not breakout entries.** The original Strategy 4 buys
  strength (enters at today's close on a momentum scan). This version buys
  pullbacks within a confirmed uptrend — hold period is still weeks, not
  months.
- **Discovery is a separate, later step.** For now the universe below is
  fixed. A screening mechanism to surface new candidates within the theme
  is planned but not yet built (see Section 8).

---

## 2. Universe

| Tier | Tickers | Notes |
|---|---|---|
| Core | NVDA, AMD, MU, SMH | More liquid, tighter typical stops |
| High-beta | CRWV, BE, APLD | Thinner, wider moves — sized down accordingly |
| Excluded | ZETA | Ad-tech/marketing AI, not compute/hardware — different driver set. Track separately if desired, don't mix into this signal. |

**Benchmark for relative strength:** SMH (semiconductor ETF). All "beating
the sector" comparisons are against SMH's return, not the S&P 500 or Nasdaq.

---

## 3. Step 1 — Rank leadership within the theme

For every ticker in the universe (excluding SMH itself when used as
benchmark):

1. Compute 15-trading-day return.
2. Compute SMH's 15-trading-day return over the same window.
3. Relative strength = stock's 15d return − SMH's 15d return.
4. Rank the universe by relative strength, highest first.

This replaces the original "rank sectors, pick top 3" step — here there is
only one sector, so this step ranks *names* instead.

---

## 4. Step 2 — Entry filters (ALL must be true)

1. **Own trend positive:** 15-day return > 0.
2. **Beating the sector:** relative strength (Step 1) > 0 — the stock is
   leading SMH, not lagging it.
3. **Above the 50-day moving average:** confirms uptrend intact, not a
   dead-cat bounce.
4. **Dip trigger fired** (the key change from the original strategy):
   - Price has pulled back to within ~1% of the 20-DMA, **or**
   - RSI(2) closes below ~10–15 (short-term oversold), **and**
   - A bullish reversal candle prints at/near the pullback low (hammer,
     bullish engulfing, or an inside day followed by a higher close)

A stock must pass all four before a trade plan is even calculated.

---

## 5. Step 3 — Trade plan math

- **Entry:** trigger price from the dip signal (next day's open, or break
  of the signal candle's high — whichever convention is chosen, apply
  consistently).
- **Stop-loss:**
  - Primary: below the 50-DMA or the most recent swing low, whichever is
    closer to entry (tighter, more honest stop).
  - Fallback: entry − 2×ATR(14), used only if the 50-DMA sits above entry
    (rare after a real pullback, but guard against it).
- **T1 (bank half):** entry + 1.5×ATR(14). On touch: sell 50% of the
  position, move stop to breakeven on the remainder.
- **T2 (gate only, not an order):** entry + 3.0×ATR(14). Used only to
  check whether the setup has enough room to be worth holding — no sell
  order is placed here.
- **Runner (remaining 50%) — trail via whichever fires FIRST:**
  1. **Chandelier exit:** close below (highest high since entry − 3×ATR)
  2. **20-EMA break:** 2 consecutive daily closes below the 20-EMA
  3. **Relative-strength loss:** stock's return since entry falls below
     SMH's return since entry — sector tailwind is gone
- **Time stop:** exit the runner if none of the above has fired within
  25 trading days (~5 weeks). Re-check relative strength around week 3
  regardless of price action.

ATR uses Wilder smoothing (`ewm(alpha=1/14)`), matching TradingView's
`ta.atr()`, so any Python calculation and any Pine Script chart agree.

---

## 6. Step 4 — Risk:reward gates (reject if any fail, regardless of story)

| Gate | Core tier (NVDA/AMD/MU/SMH) | High-beta tier (CRWV/BE/APLD) |
|---|---|---|
| R:R at T1 | ≥ 1.5 : 1 | ≥ 1.5 : 1 |
| R:R at T2 | ≥ 2.0 : 1 | ≥ 2.0 : 1 |
| Max stop width (% of entry) | ≤ 10% | ≤ 15–18% |

A setup that passes Steps 2 but fails any gate here is a **FAIL** —
"right idea, math doesn't work" — not a trade. Wider stops on high-beta
names are allowed, but position size shrinks automatically under the 1%
risk rule to compensate — never widen the gate to force a trade in.

---

## 7. Position sizing & hard rules

- **1% risk rule (rule of thumb, never automated for you):** risk no more
  than ~1% of capital between entry and stop on any single trade.
- **No position sizing is calculated automatically.** Quantity is always
  your decision — this file/any future script should print price LEVELS
  only, never share counts, unless you explicitly ask for the arithmetic
  on a specific trade.
- **No broker connection, ever.** No auto order placement now or later.
- **Ask before acting.** Verify a chart? Take a trade? Treat every
  decision point as a stop-and-confirm, not an auto-pick.
- **Not financial advice.** Educational/decision-support only.
- **Max 5 open positions at once**, no more than 2 in the same
  narrower sub-theme (e.g. don't stack NVDA + AMD + MU simultaneously
  without acknowledging they're correlated — count correlated pairs as
  1.5 positions for heat purposes, same logic as the original doc).

---

## 8. What's NOT built yet (roadmap)

This file only defines the strategy. Still to build, in likely order:

1. **Python scanner** — pulls daily OHLCV (yfinance) for the universe,
   applies Steps 1–4, prints a PASS/FAIL report with full trade cards —
   the US/theme-concentrated equivalent of `sector_momentum_scanner.py`.
2. **Backtest script** — simulates this exact rule set historically on
   the universe (or a wider semiconductor list) to get real win-rate,
   profit factor, and drawdown numbers — currently we have ZERO backtest
   evidence for this adapted version. Everything above is a reasoned
   adaptation, not yet a proven edge.
3. **Discovery/screening mechanism** — expands the fixed 7-name universe
   by scanning a broader semiconductor/AI-infra list (e.g. SOXX or SMH
   holdings, plus adjacent names) for stocks that would newly qualify.
4. **TradingView Pine Script** — a chart indicator that independently
   recalculates entry/stop/T1/T2/R:R and prints the same on-chart trade
   card as the screenshot you shared (Signal / 15d vs SMH / Entry / Stop
   / T1 / R:R / Verdict) — used as an independent second opinion against
   the Python scanner, exactly like the original system's Step B.
5. **Order-setup step** — once you name your broker (Fidelity, IBKR,
   Schwab, Robinhood, etc.), translate a PASS setup into that broker's
   exact order sequence (limit entry, OCO target+stop, honest gap-risk
   caveats) — the US equivalent of the original system's Step C.

**Suggested next step:** build #1 (the scanner) first, since #2 (backtest)
and #4 (Pine script) both depend on the same math being coded once,
correctly, and #3 (discovery) is easiest to bolt on once the scanner
already loops over a list of tickers.
