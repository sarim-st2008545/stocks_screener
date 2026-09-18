# Review — `implementation_plan.md`

Independent adversarial review, 2026-09-01. Reviewed as a trader would review a
colleague's plan before committing capital: looking for the thing that kills it, not
for things to praise.

**Scope.** `implementation_plan.md` read in full, then `ai_semi_scanner.py`,
`AI_Semiconductor_Sector_Momentum_Strategy.md`, `config/universe.yaml`,
`config/rules.yaml`, `config/portfolio.yaml`, `README.md`, all of `src/`, and the git
history. The "Open Questions for You" section is deliberately untouched, as you asked.

**Evidence standard, stated up front.** This sandbox cannot reach PyPI or any
market-data host (`pip download yfinance` → proxy 403; `stooq.com` → 403
`blocked-by-allowlist`). **I could not run a single backtest on real prices.** Every
number below labelled *(sim)* comes from Monte Carlo and factor-model simulation, and
those simulations can only measure *structural* properties of your ruleset — how often
it fires, which gate binds, how wide the stop ends up, whether the position sizing fits
in the account, what closes the trades. They cannot tell you whether your entry
predicts anything. Where I could not measure something, I say so rather than guessing.

---

## Verdict

The engineering is better than the strategy. Your data layer, point-in-time discipline,
and validation methodology are stronger than most retail systems and stronger than some
professional ones. The trading logic they serve does not currently hold up.

Three findings dominate, in order of how much they should change what you do next:

1. **Part 3 is not a plan. It is already built, and it already failed its own
   pre-registered validation gate.** Neither `implementation_plan.md` nor `README.md`
   records this. Part 4 then proposes wiring that failed engine into the swing engine as
   a quality filter.
2. **The swing engine's R:R gate is algebraically self-defeating.** It forces the stop
   under 1.0×ATR, which is what produces the sizing contradiction, the low win rate, and
   the dependence on drift rather than edge. Two of the three gate conditions can never
   reject anything.
3. **At $1,000, the arithmetic does not reach the goal.** With the plan's own rules,
   ~20 gate-cleared signals a year *(sim)* and a risk-per-trade that actually fits five
   positions, the expected annual result is single-digit to low-double-digit dollars.
   The "active cash" framing implies something the size of the account cannot deliver.

None of this is fatal to the project. It is fatal to the plan as currently written.
There is a good system in here; it is roughly 40% of the current document plus one
honest decision about what the other 60% was for.

---

## 1. The finding that outranks everything else

Commit `f1028bc`, dated 2026-08-17, is titled **"Stage 1 gate FAILED: the analysis adds
negative value."**

The nine-layer fundamental engine described across Part 3 of the plan — Piotroski,
Altman, ROIC−WACC, reverse DCF, cycle position, the 16-metric composite, the four
sequential gates — is not future work. It is fully implemented in `src/`, it has 500
passing tests, and you backtested it. The results in that commit message:

| | Strategy | Benchmark | |
|---|---|---|---|
| **vs allocation-matched blend** (55% SPY / 20% SOXX / 15% GLD / 10% cash, same rebalance dates) | $4,808 · CAGR 15.0% · Sharpe 0.70 · maxDD 26.1% | $5,283 · CAGR 16.0% · Sharpe 0.75 · maxDD 26.2% | beta 0.98, alpha −0.00%/period |
| **selection isolated** (100% sector, equal-weight picks, SOXX when nothing qualifies) | $11,392 · CAGR 24.2% · vol 32.3% · Sharpe 0.62 · maxDD 49.2% | SOXX $18,205 · 29.4% · 32.4% · Sharpe 0.79 · maxDD 45.8% | |

Your own words in that commit: *"The strategy IS the blend, slightly worse."* And:
*"this does not proceed to paper trading. Phase 13 stays blocked."*

Two things about this deserve emphasis, and they pull in opposite directions.

**First, in your favour: this is the single most credible thing in the repository.** You
pre-registered a pass/fail gate in `README.md` — beat SOXX on risk-adjusted return, OOS
Sharpe at least half of in-sample, max drawdown under 35%, stable across walk-forward
windows — and then you honoured it against your own work. The failure was also *not*
overfitting: out-of-sample Sharpe came in at 134% of in-sample, selection was exercised
in 87% of rebalances, and the diagnosis was structural rather than statistical (the
margin-of-safety rule kept NVIDIA out; the most-held names were KLAC ×18, AMAT ×18,
TER ×15 — a value tilt inside a sector whose entire decade was a growth story). Most
retail quants never build the gate. Almost none obey it.

**Second, against the plan: the documents have lost this.** `README.md` still shows
Phases 0–12 all ✅, with Phase 11 reading "Built and validated on a short window. Full
11-year run in progress." `implementation_plan.md` contains no occurrence of "Stage 1",
"negative alpha", "allocation-matched", or "blocked" — it presents the whole fundamental
apparatus as forward-looking design. `data/` is gitignored and the saved result files
are not in the tree, so **that commit message is the only surviving record of the most
important experiment you have run.** If you leave it there, in six months you will
rebuild this.

**Where this bites the new plan.** Part 4 proposes taking the composite score from this
engine and using it as a "quality badge" on swing setups, and using score + swing signal
together as a "super setup" trigger with larger size. That is upgrading a rejected signal
to a position-size multiplier. If the composite could not rank names profitably over 11
years at a quarterly horizon, there is no prior reason it ranks them over a 3–25 day
horizon, and a "super setup" rule would concentrate risk exactly where the composite is
most confident — i.e. into the value tilt that already underperformed.

**But one part of it did survive, and it is worth keeping.** Drawdown control was real:
26.1% versus the sector's 45.8%, and 26.1% versus the blend's 26.2% at equal return.
That is a genuine, validated property. It says the engine is good at *avoiding bad
balance sheets and expensive cyclicals*, and bad at *picking winners*. So the defensible
use of Part 3 is as a **veto, not a ranking**: a name that fails the Eligibility and
Quality gates is ineligible for swing trades, full stop. That is a binary filter using
the one thing that validated. It is not a score, not a badge, and not a size multiplier.

**Recommendation.** Before finalising the plan, rewrite Part 3 as a report of a completed
and failed experiment, restate what survived, and demote its role in Part 4 from
"quality badge / super setup" to "eligibility veto". Add the Stage 1 result to `README.md`
and un-tick Phase 11. This costs you an afternoon and saves the plan's credibility with
its only reader who matters.

---

## 2. The swing engine's risk gate is self-defeating

This is the part I want you to check on paper, because it needs no data and no simulation.

Part 2 sets `T1 = entry + 1.5 × ATR` and `T2 = entry + 3.0 × ATR`, then gates every setup
on **R:R@T1 ≥ 1.5 AND R:R@T2 ≥ 2.0**, plus **max stop width 10% (core) / 15% (high-beta)**.

Let `R` be the stop distance and `A` the ATR. Then:

```
R:R@T1 = 1.5A / R  ≥ 1.5   ⟹   R ≤ 1.0 × A
R:R@T2 = 3.0A / R  ≥ 2.0   ⟹   R ≤ 1.5 × A
```

The first condition is strictly tighter than the second. **The T2 gate cannot reject a
setup the T1 gate accepts — it is unreachable code.** In simulation, 0 of 127 signals
failed the T2 gate alone *(sim)*. And the stop-width caps almost never bind either: 1 of
127 signals had a stop wider than 10%, none wider than 15%, median stop width 2.1% of
entry *(sim)*. So the core-vs-high-beta tiering — the thing `ai_semi_scanner.py` encodes
as `max_stop_pct: 10.0` vs `17.0`, and which the plan sets at 10 vs 15 — **changed the
outcome for at most one signal in five simulated years.** Three gate conditions and a
two-tier risk taxonomy, and only one of them ever fires.

That surviving condition, `R ≤ 1.0 × A`, is the problem. It is not a risk control. It is
a mandate to place your stop inside one day's normal range, on 40–75% annualised-vol
semiconductor names. And because the plan chooses the stop as *the closer of* the 50-DMA
and the 10-day swing low, the actual stops that clear the gate cluster tighter still —
median ~0.6×ATR *(sim)*.

Everything downstream follows from that one number.

**It sets the win rate.** With entry the day after the signal and a 0.6×ATR stop, 66% of
trades stop out before ever touching T1 *(sim)*. The Chandelier trail, the 20-EMA
two-close rule, and the RS-loss exit — three of the four runner exits — manage a position
that exists in roughly a third of trades. The 25-day time stop and the hard stop do
almost all the work. The trade-management section of the plan is elaborate in the branch
that rarely executes.

**It makes the "move stop to breakeven at T1" rule counterproductive.** In simulation
that rule converts 20.2% of all trades into scratches — trades that reached +1.5×ATR,
banked half, and then gave the runner back at zero *(sim)*. At a 1.0×ATR stop the rule is
roughly a wash; at a sane 2.0×ATR stop it is actively harmful (E[R] 0.161 with the rule vs
0.174 without) *(sim)*. It feels like risk control and costs expectancy, because on a
volatile name "entry" is a price the stock revisits constantly.

**It points the T1 gate in the wrong direction.** Holding the stop at 2.0×ATR and sweeping
the first target: E[R] runs 0.162 → 0.219 → 0.252 → 0.271 → 0.296 as T1 goes 1.5× → 2.0×
→ 2.5× → 3.0× → 4.0×ATR *(sim)*. Expectancy improves monotonically as you push the first
target out. The R:R@T1 ≥ 1.5 gate does the opposite: with T1 pinned at 1.5×ATR it forces
you into the tightest-stop, nearest-target corner of the parameter space — the corner the
sweep identifies as worst. A gate whose stated purpose is "only take favourable
risk/reward" is selecting the least favourable geometry available.

**Recommendation.** Delete the R:R@T2 gate (it is inert). Either drop the R:R@T1 gate or
move T1 to 2.5–3.0×ATR so the gate stops dictating a sub-1-ATR stop. Set the stop from
volatility first — 2.0–2.5×ATR, or the structural level *further* from entry rather than
closer — and let R:R be a *reported diagnostic*, not an entry condition. Drop the
breakeven-stop rule, or replace it with "trail to breakeven only after +2R". Collapse the
core/high-beta tiering unless you can show a case where it binds.

---

## 3. The position-sizing rules contradict each other

Part 2.3 specifies **~1% risk per trade**, **max 5 open positions**, max 2 per sub-segment.
Those three numbers cannot coexist with the stop the gate produces.

Median stop width on gate-cleared signals is **1.78% of entry** *(sim)*. Risking 1% of a
$1,000 account on a 1.78%-wide stop requires a position of `1% / 1.78% = 56% of capital`.

| | |
|---|---|
| Notional per position at 1% risk | **56% of capital** |
| Five positions open | **281% of capital** |
| Positions affordable in a cash account | **1** |
| Risk per trade that actually fits 5 positions | **0.36%** = $3.56 per trade |

So you have to pick one. Either you run 1% risk and hold one position at a time with 56%
of the account in a single semiconductor — which is not diversification, it is a
concentrated bet with a stop — or you run 0.36% risk and hold five, in which case each
trade risks $3.56 and a full 1R winner pays $3.56.

This is not a rounding problem. It is what a sub-1-ATR stop does to sizing: **the tighter
the stop, the larger the position, and a tight stop on a volatile name is a leverage
instruction wearing a risk-management costume.** Widening the stop to 2.0–2.5×ATR
(section 2) makes the 1%/5-position combination close to feasible, which is a second
independent reason to do it.

One more thing the plan does not mention: `min_position_usd: 25.0` in
`config/portfolio.yaml` and a `$1,000` wallet mean the swing sleeve can hold very few
distinct positions before hitting granularity limits, and slippage at 10 bps on a $200
position is $0.20 against a $3.56 risk budget — 6% of R gone to friction per side.

---

## 4. Signal frequency is overstated by 5–15×

Part 2.4 states that 41 tickers produce **2–6 setups per week**. Running an exact port of
your four entry filters and R:R gates over a 41-name factor-model panel calibrated to the
real universe (market vol 32%, per-name vols 0.25–0.75, betas 0.9–1.6, five years):

| | per week | per year |
|---|---|---|
| Pass all four entry filters | 0.48 *(sim)* | ~25 |
| ...and clear the R:R gates | 0.39 *(sim)* | ~20 |
| Plan's claim | 2–6 | 104–312 |

The cause is the conjunction in Filter 4. You require the dip trigger **and** a bullish
reversal candle **on the same bar**, on top of Filter 3 (price above the 50-DMA) and
Filters 1–2 (15-day return positive, RS vs SMH positive). Each is reasonable alone; ANDed
together on a single bar they are rare.

This matters more than it looks. It sets how long paper trading must run before it means
anything. At 20 signals a year, the README's Stage 2 gate ("minimum ~6 months") gives you
**about 10 trades** — statistically indistinguishable from noise at any expectancy you
could plausibly have. To get 100 trades you need five years of live signals, or you need to
loosen the conjunction, or you need a much larger universe.

There is also mild adverse selection inside Filter 4. Gate pass rates by candle type:
hammer 71%, bullish engulfing 63%, inside-day-higher-close 91% *(sim)*. The gate
preferentially keeps inside days — the narrowest-range, weakest-evidence pattern — because
narrow range means a tight stop means a flattering R:R. The composition of your signals
shifts toward inside days *after* the gate. Your risk filter is selecting against your own
reversal evidence.

---

## 5. What is actually generating the expectancy: drift, not edge

I ran the plan's full exit architecture against entries with **no predictive power at all**
beyond the asset's drift. This is the null hypothesis: if your filters are worthless, this
is what the trade management alone produces.

| annual drift assumption | E[R] per trade at the gate-forced 1.0×ATR stop |
|---|---|
| 0% (pure noise) | **+0.078** *(sim)* |
| +25%/yr (the sector's actual decade) | **+0.240** *(sim)* |
| +50%/yr | **+0.381** *(sim)* |
| +100%/yr | **+0.63** *(sim)* |

Read the first row carefully. At zero drift the architecture returns +0.078R per trade
before commissions, and 10 bps of slippage on each side plus the occasional gap through the
stop eats most of that. So roughly **two-thirds of the expectancy in the +25% row is the
sector going up, not the setup working.** That is the honest baseline your four filters have
to beat, and it is the same measurement your fundamental engine failed: *"The strategy IS
the blend, slightly worse."*

Now put the deployment rate on top. Twenty trades a year, average hold around a week, at
the position size that fits the account: average capital deployed is roughly **10–30%**
depending on how you resolve the sizing contradiction in section 3. A strategy that is
10–30% invested in a sector, capturing a fraction of that sector's drift, with a per-trade
edge that is mostly that same drift, is structurally a **worse-than-buy-and-hold way to own
SOXX** unless the filters add real, measurable selection.

Which brings the question back where it belongs. Your plan already says it: *"The swing
strategy has ZERO backtest evidence yet."* That sentence is the most important line in the
document, and the plan proceeds through six more parts and five phases as if it were a
footnote.

**The dollar reality at $1,000.** Combining measured frequency, the ~60% of signals you can
actually act on given the 5-position and 2-per-segment caps, and the modelled E[R] range:

| E[R] | risk/trade | trades/yr | annual P&L on $1,000 |
|---|---|---|---|
| 0.15 | 0.36% | 12 | **$6.61** (0.7%) |
| 0.24 | 0.36% | 12 | **$10.58** (1.1%) |
| 0.40 | 0.36% | 12 | **$17.63** (1.8%) |
| 0.24 | 1.0% | 12 | **$29.38** (2.9%) |
| 0.40 | 1.0% | 12 | **$48.96** (4.9%) |

That is before taxes and before your time. The best cell requires both an optimistic
expectancy and the sizing rule that puts 56% of the account in one name. Meanwhile the
plan's own framing is "active cash" — money that works harder than the buy-and-hold sleeve.
On this account size, with these rules, it does not.

I want to be precise about the claim: **this is not an argument that swing trading cannot
work, or that you cannot do it.** It is an argument that at $1,000, with ~20 signals a year
and 10–30% deployment, the *dollar* outcome is dominated by the 55% core sleeve no matter
what the swing engine does, and the *statistical* outcome cannot be measured for years. The
capital and the signal rate are both too small for the design.

---

## 6. Component-level problems in the entry logic

### 6.1 The RSI(2) branch is nearly inert

Filter 4's dip trigger is `(within 1% of 20-EMA) OR (RSI(2) < 10)`. In simulation the
OR resolves as **84.3% 20-EMA proximity, 15.7% RSI(2)** *(sim)*. And the RSI(2) branch
co-occurs almost exclusively with hammers, for a mechanical reason: bullish engulfing and
inside-day-higher-close are both *up days*, and a single up day resets RSI(2) above 15.
You are requiring a 2-period oscillator to be deeply oversold on a bar that closed green.

So the strategy you have specified is **"price pulls back to the 20-EMA and prints a
reversal candle."** It is not an RSI(2) strategy. That is fine as a strategy, but it means
the Connors & Alvarez lineage the design borrows credibility from does not apply — and note
that canonical RSI(2) is a *different system*: it uses a 200-DMA regime filter (you use
50-DMA), exits on a moving-average cross rather than an ATR target, and explicitly does not
use a tight hard stop, because in mean-reversion systems a tight stop converts the winning
tail into losses. You have taken the entry from a mean-reversion system and bolted it onto
the exit architecture of a trend-following system. Those two halves want opposite things
from the stop.

Also: the plan says `RSI(2) < 10`, `ai_semi_scanner.py` says `rsi2 < 15`. Pick one.

### 6.2 The candlestick requirement has no evidence base

The academic literature on candlestick patterns is weak to negative — the studies that find
predictive value mostly fail to survive transaction costs, data-snooping corrections, or
out-of-sample testing on liquid US large caps. I am not asserting the patterns are useless;
I am asserting there is no published basis for treating them as a *mandatory* filter, and
you are paying for them twice: they cut your signal count by a large factor (section 4) and
they introduce the adverse selection toward inside days.

Test this as an ablation, not an assumption. Run the four filters with and without the
candle requirement. If the candle adds nothing, deleting it multiplies your sample size,
which is the scarcest resource in this project.

### 6.3 The 15-day formation window sits in the reversal zone

Filters 1 and 2 use a **15-day** return and a 15-day relative-strength comparison, and treat
positive readings as bullish continuation evidence. The cross-sectional evidence points the
other way at that horizon: short-term reversal at roughly the 1-month scale is one of the
older and more robust anomalies (Jegadeesh 1990; Lehmann 1990), while momentum
*continuation* is documented at 2–12 month formation windows (Jegadeesh & Titman 1993) and
conventionally skips the most recent month precisely to avoid the reversal effect.

You are using a formation window inside the interval where the literature says the sign
flips, and then adding a dip trigger — which is a reversal bet — on top of it. The two
halves of your entry may be fighting each other. If you want momentum, use a 3–6 month
formation window and skip the last 5–10 days. If you want mean reversion, drop the
"15-day return > 0" requirement and let the 50-DMA filter carry the trend condition.

### 6.4 The benchmark is mis-specified for a third of the universe

Relative strength is measured against **SMH** for all 41 names. But `config/universe.yaml`
spans nine segments, and roughly **14 of the 41** — the six hyperscalers and the eight
power/energy names — are not semiconductor stocks. Measuring MSFT or a power utility against
a semiconductor ETF means their RS reading is dominated by *cross-group rotation*, not by
name-level leadership. When capital rotates from chips into hyperscalers, every hyperscaler's
RS goes positive simultaneously and every semi's goes negative, regardless of individual
merit. Your filter will fire on all of one group at once, which is precisely the correlated
cluster the 2-per-segment cap is meant to prevent.

Fix: benchmark each name against its own segment or a blended composite, or run
per-segment RS. Alternatively use SPY for the non-semi names.

### 6.5 The entry price is unspecified in a way that cannot be implemented

Part 2.2 says **"Entry Price: the current closing price"**, and the scan runs at ~3:50pm ET
or after the close. You cannot be filled at a close you are using to *generate* the signal.
Meanwhile `AI_Semiconductor_Sector_Momentum_Strategy.md` §5 — the older document that calls
itself the single source of truth — specifies **"next day's open, or break of the signal
candle's high."** The plan silently changed this.

This is a specification conflict, and it matters twice over. Live, it determines what you
actually pay. In a backtest, filling at the signal close is look-ahead bias, and it will
inflate your results by an amount you cannot bound after the fact.

I tried to quantify the cost and could not do it honestly: my simulation has symmetric
overnight gaps, and it showed the next-open fill costing only ~0.005R *(sim)*, which I do
not believe generalises — real reversal-candle setups gap in a skewed way that a symmetric
model cannot capture. **So I am flagging this as unresolved rather than priced.** Decide the
rule, write it in one place, and implement the backtest to match: signal on bar *t*'s close,
fill on bar *t+1*'s open (or on a stop-buy above bar *t*'s high, which also gives you a
free confirmation filter and reduces your signal count further).

### 6.6 There is no regime rule, and the current market shows why

Filter 3 requires price above the 50-DMA. In a sector-wide drawdown almost nothing is above
its 50-DMA, so **the scanner goes silent exactly when dips are most plentiful.** Worse, the
few names that *are* still above their 50-DMA during a topping process are the ones that
have not corrected yet — the last to fall. The filter set quietly concentrates you in
laggards-to-decline.

This is not hypothetical this month. As of today the semiconductor complex is in a sharp
correction: the SOX index fell about 21% in July and is down roughly a quarter to 29% from
its June peak, after an enormous run (SOXX was up ~87–89% YTD and ~174–180% over the
trailing year at the peak), and the selloff was still running through 2026-08-31 with
sell-side desks calling for further downside. **Run `ai_semi_scanner.py` today.** My
prediction is that it prints zero or near-zero setups. If it does, that is your regime
problem demonstrated on live data at no cost.

You need an explicit regime rule, and you need to decide which way it points: either "no new
swing entries while SOXX is below its 200-DMA" (defensive, accepts long silences), or "in a
sector drawdown, switch the dip trigger to a deeper-oversold variant and require the
*index*, not the name, to reclaim a level" (opportunistic). Either is defensible. Having
none means the system's behaviour in drawdowns is an accident of Filter 3.

---

## 7. What is genuinely good, and should not be touched

A review that only lists faults is useless for deciding what to keep. These are the parts I
would defend if someone else tried to change them.

**The point-in-time data layer.** `facts.py` gating SEC XBRL facts on real `filed` dates
plus two settle days, collapsing restatements to the newest version that existed as of the
as-of date, and `prices.py` handling split-aware un-adjustment. This is the single most
common source of fake backtest performance in retail systems and you built it correctly
before you needed it. Most professional shops buy this rather than build it.

**The validation methodology.** Walk-forward with 5-year train / 1.5-year test, retention
measured as OOS Sharpe over IS Sharpe, daily equity marking, Deflated Sharpe and
probability-of-backtest-overfitting tests, and — the detail I find most persuasive — a
*reverse* gate that fails any run with Sharpe above 1.5 as presumptively overfit. That is a
professional instinct. Keep it and apply it to the swing engine unchanged.

**Pre-registering gates and honouring them.** Covered in section 1. This is the habit that
makes the rest of the project salvageable.

**Documenting your own limitations inside your own plan.** Part 5's honest-limitations
section, including "The swing strategy has ZERO backtest evidence yet", is why this review
could be short instead of an argument.

**The universe construction.** `config/universe.yaml` is thoughtful: nine segments, explicit
liquidity and listing screens, `stability_flag` on the names with erratic fundamentals
(INTC, ALAB, CRDO, SMCI, GEV, PLTR, ZETA), `cyclical: true` on memory and foundry equipment,
and explicit *exclusions* with reasons (neoclouds, SMRs). Two notes: `ai_semi_scanner.py`
trades CRWV, which this file explicitly excludes — reconcile that. And the underlying thesis
holds up on current evidence: 2026 hyperscaler capex estimates in the $638B–$791B range
support the plan's "$700B+" premise.

**The two-speed architecture as a concept.** Separating a slow allocation sleeve from a fast
tactical sleeve, with different rules, different horizons, and a hard capital boundary
between them, is the right shape. The problem is the contents of the fast sleeve, not the
shape.

**The exit architecture, judged as design.** Scaling out at a volatility-scaled target,
trailing the remainder on a Chandelier stop, a moving-average break as a secondary trail, and
a hard time stop is a coherent and well-known combination. It fails here only because the
entry gate starves it of trades that survive to the trailing stage. Widen the stop and this
machinery starts doing what it was built to do.

---

## 8. A structural contradiction worth resolving before anything else

`README.md` §2, lines 69–70, says:

> **Not a day-trading or swing-trading tool.** No RSI-triggered entries, no ATR stop losses,
> no momentum signals. Long-horizon fundamentals only.

And §11 lists the swing-trading signal engine among things "deferred or contradict the new
mandate." `implementation_plan.md` reverses this without acknowledging that it is a reversal.

You now have two documents that each call themselves authoritative and specify opposite
mandates, plus a third (`AI_Semiconductor_Sector_Momentum_Strategy.md`, "single source of
truth") that disagrees with the plan on entry price, and a fourth (`config/portfolio.yaml`)
that specifies `method: fractional_kelly, kelly_fraction: 0.25` while `src/portfolio.py`
explicitly refuses Kelly in favour of a bounded 1.0×–2.0× score tilt. `README.md` §9's
allocation table still shows the superseded 45/35/10/10 against the current 55/20/15/10.

This is the kind of drift that produces a live trading bug. One document is authoritative;
the others get a header pointing at it and a status line saying superseded, on what date, by
what. Do this before writing code, not after.

---

## 9. What I would do instead

The plan's Part 7 runs Phase 1 (data + indicators) → Phase 2 (scanner) → Phase 3 (alerts) →
Phase 4 (dual-engine integration) → Phase 5 (paper trading). Building the alerting and the
integration before the strategy is validated is building the delivery mechanism for an
unmeasured signal. Reordered, with the cheapest kill-tests first:

**Phase 0 — reconcile the documents (hours).** Section 8. Record the Stage 1 failure in
`README.md` and in the plan. Fix the plan-vs-scanner disagreements: `RSI(2) < 10` vs `< 15`,
15% vs 17% stop cap, 20-EMA vs 20-DMA, CRWV in the scanner vs excluded in the universe,
Kelly in the config vs not in the code. Decide the entry-fill rule and write it once.

**Phase 1 — get the data and settle the entry-fill question (days).** You need 10+ years of
daily OHLCV for 41 names plus SOXX, SMH, SPY. You already have a price layer and split
handling in `src/prices.py`. This is the gating dependency for everything below, and I could
not do it here — the sandbox has no network access to any data source.

**Phase 2 — ablation-test the entry, before building anything (days).** This is the phase the
plan is missing entirely, and it is the whole ballgame. Backtest the *entry* alone with a
fixed, dumb exit (hold N days, N ∈ {3, 5, 10, 25}) so that entry quality is not confounded
with exit design. Then ablate one filter at a time:

| test | question it answers |
|---|---|
| all four filters | baseline |
| minus the candle requirement | does 6.2's suspicion hold? |
| minus "15-day return > 0" | is 6.3 real — is the momentum leg fighting the reversal leg? |
| minus RS-vs-SMH | does RS add anything beyond the 50-DMA filter? |
| RSI(2)-only vs 20-EMA-only | which branch of the OR is carrying the strategy? |
| 15-day vs 63-day vs 126-day formation | where does the sign flip in your data? |
| segment-relative RS vs SMH RS | does 6.4 matter? |
| vs "buy any name in the universe on a random day" | the null |
| vs "buy and hold SOXX" | the benchmark that actually competes |

Compare on expectancy per trade *and* on a return-per-unit-of-capital-deployed basis, because
a 10–30%-deployed strategy must clear SOXX on far less than SOXX's full exposure to be worth
running. Apply the same walk-forward, Deflated Sharpe, and PBO machinery you built for the
fundamental engine, and pre-register the pass gate *before* you run it — exactly as you did
for Stage 1.

If the ablations show the filter set adds nothing beyond drift, stop. That is a $0 outcome
and a real result, and you will have reached it in a week instead of after building alerts,
integration, and a paper-trading harness.

**Phase 3 — only if Phase 2 passes: fix the geometry (days).** Re-optimise stop and target on
the *surviving* entry, with the R:R gate removed as a constraint (section 2). Sweep stop ∈
{1.5, 2.0, 2.5, 3.0}×ATR and T1 ∈ {2.0, 2.5, 3.0, 4.0}×ATR. Test the breakeven rule as an
on/off flag. Then re-solve position sizing so risk-per-trade and the position cap are
consistent (section 3), and add the explicit regime rule (6.6).

**Phase 4 — then the plumbing.** Scanner, Telegram alerts, dual-engine integration — where
Part 3's role is an eligibility veto, not a badge or a size multiplier (section 1).

**Phase 5 — paper trade, with realistic expectations about duration.** At ~20 signals a year
you cannot validate in six months. Either accept that paper trading is an *operational*
test (do the alerts fire, are the prices right, does the state machine handle a gap through
a stop) rather than a statistical one, or expand the universe to raise the signal rate.

---

## 10. On capital

I will not tell you what size account to trade — that is your call, and part of it sits in the
section you asked me to leave alone. But two facts belong on the table before you answer it.

At $1,000 with these rules, the swing engine's realistic annual contribution is roughly
$5–$50 (section 5), against a 55% core sleeve of $550 whose ordinary annual variation dwarfs
that entirely. The engine cannot matter to the account's outcome. And at ~20 signals a year
you cannot learn whether it works within any timeframe that would let you act on the answer.

The productive framing is that this account is **tuition, not capital.** Its job is to prove
the pipeline is correct — signals fire on the right bars, prices and splits are right, the
state machine survives gaps and halts, the alerts arrive, the logs reconcile — so that the
system is trustworthy if and when you fund it properly. That is a genuinely valuable thing to
build and it justifies the work. It is just a different goal from "active cash", and the plan
should say which one it is pursuing, because they imply different designs.

---

## 11. What I could not verify

Stated plainly so you can weight the rest:

- **No real market data.** PyPI is proxy-blocked (`pip download yfinance` → 403) and so are
  the data hosts (`stooq.com` → 403 `blocked-by-allowlist`). I ran no backtest on real
  prices. Every *(sim)* figure comes from Monte Carlo (intraday geometric Brownian motion,
  13 subperiods per day, Student-t df=4 innovations, true Wilder ATR computed from the
  simulated bars) or from a one-factor 41-name panel calibrated to your universe.
- **Simulation measures structure, not prediction.** Frequency, gate binding, stop width,
  sizing feasibility, and exit attribution follow from the rules and the volatility, so they
  transfer. Expectancy figures do *not* transfer — they are what the exit architecture
  produces given assumed drift and no entry edge. Treat them as a floor to beat, not a
  forecast.
- **The entry-price cost is unpriced.** Section 6.5. Flagged, not quantified.
- **My frequency and stop-width numbers depend on my volatility calibration.** If real
  semiconductor vol clustering makes ATR behave differently around reversal candles than my
  model does, the 0.39 signals/week and 1.78% median stop will shift. The *direction* of the
  findings will not — the R:R algebra in section 2 and the sizing arithmetic in section 3 need
  no simulation at all, and you can check both on paper in five minutes.
- **Corporate actions.** Spot-checked the universe for names acquired away and found none
  (TLN is the acquirer in its PJM gas transaction, not the target). Not exhaustive. You need
  a periodic universe-maintenance job regardless.
- **Market context** comes from search snippets dated through 2026-08-31; the sector was still
  selling off at that point. Verify before acting on 6.6.

---

## Summary

| # | Finding | Severity | Cost to fix |
|---|---|---|---|
| 1 | Part 3's engine is built and already failed its own gate; Part 4 proposes promoting it | **Critical** | Documentation + demote to veto |
| 2 | R:R@T1 gate forces stop ≤ 1.0×ATR; T2 gate and stop caps are inert | **Critical** | Delete two gates, widen stop |
| 3 | 1% risk × 5 positions = 281% of capital | **Critical** | Follows from fixing #2 |
| 4 | No entry validation anywhere in the plan; ablations missing | **Critical** | New Phase 2 |
| 5 | Frequency 0.39/wk vs claimed 2–6/wk | High | Restate; loosen Filter 4 conjunction |
| 6 | Expectancy is ~2/3 drift at 10–30% deployment | High | Benchmark per unit of capital deployed |
| 7 | Entry price unimplementable; plan and source doc disagree | High | Decide the rule; fill at t+1 |
| 8 | No regime rule; scanner goes dark in drawdowns | High | Explicit SOXX regime condition |
| 9 | 15-day formation window sits in the reversal zone | Medium | Test 63/126-day in the ablation |
| 10 | SMH benchmark wrong for ~14 of 41 names | Medium | Segment-relative RS |
| 11 | RSI(2) branch supplies 16% of signals; strategy is really 20-EMA + candle | Medium | Rename it honestly, or fix it |
| 12 | Candle filter has no evidence base and selects toward inside days | Medium | Ablate it |
| 13 | Breakeven-stop rule creates 20% scratches, hurts at sane stops | Medium | Remove or move to +2R |
| 14 | Four docs with conflicting mandates and parameters | Medium | Phase 0 |
| 15 | $1,000 cannot make the swing sleeve matter, or measure it | Structural | Reframe as pipeline validation |

---

## Appendix A — The document as a plan

Everything above judges the *strategy*. This section judges `implementation_plan.md` as a
**plan**: is it complete, internally consistent, sequenced, and buildable? These findings
hold even if the strategy turns out to work.

**The ratio.** 645 lines. Roughly 638 of them describe what the system *is*; Part 7 —
seven table rows — describes how it gets built. No time estimates, no dependency graph, no
decision points, no kill criteria, no owner for any piece of work. It is an excellent
specification and a thin plan.

### A.1 The swing engine has no capital

Section 3.7 allocates the account: 55% core / 20% satellite / 15% gold / 10% cash = 100%.
All four sleeves belong to the long-term engine. **Nowhere does the plan say what the swing
engine trades with.** Section 2.3 says "1% of your trading capital" and illustrates with a
$10,000 example inside a $1,000 document. If swing capital is the 10% cash sleeve, then 1%
risk per trade is $1.

For a system whose defining idea is two speeds running in parallel, the capital split
between them is the first thing the plan should fix, and it is absent. Two related rules are
also missing:

- **Can both engines hold the same ticker at once?** Section 4.2 gestures at it ("you might
  decide to convert the swing position into a long-term hold") but never rules. If the
  fundamental engine holds AMAT and the swing engine stops out of AMAT, which shares were
  sold?
- **What happens when both engines want the same dollar?** The satellite sleeve buys
  "top-scoring individual names"; the swing engine buys pullbacks in leaders. These will
  frequently be the same names.

### A.2 There is no position-state component

Every runner exit in Part 2 requires persistent per-position state across days: entry price,
entry date, highest high since entry, a consecutive-closes-below-20-EMA counter, SMH's level
at entry, and the remaining position fraction after the T1 scale-out. The 25-day time stop
requires a bar counter.

The plan specifies signal generation (Part 2), enrichment (Part 4), and alerting (Part 6) —
and never specifies a position store. No phase in Part 7 builds one. Phase 5's "log every
signal with timestamp and data snapshot" is signal logging, which is a different thing.
Without this component the entire trade-management half of Part 2 cannot execute. This is a
missing *component*, not a missing rule, and it is the one I would add to Part 7 first.

### A.3 No phase can fail

Every "Done When" in Part 7 is a completion criterion, not a pass criterion. Phase 2's is
"Published backtest report with honest metrics" — you can publish an honest report of a
strategy that loses money and tick the box. There is no branch anywhere in the plan, no
numeric threshold, and no definition of what "working" means.

Your `README.md` did this properly: a pre-registered Stage 1 gate (beat SOXX risk-adjusted,
OOS Sharpe ≥ half of in-sample, max drawdown under 35%, stable across walk-forward windows)
and a Stage 2 paper-trading gate with five specific conditions — and when the fundamental
engine failed Stage 1 you honoured it. **This plan is a regression from a standard you have
already met.** Every phase needs a gate with a number and a stated consequence for failing.

### A.4 The only worked example fails the plan's own gate

The trade card in section 4.2 shows `Entry $218.50 · Stop $206.80 · Target 1 $231.20`,
labelled `R:R 1.6:1`.

```
risk   = 218.50 − 206.80 = 11.70
reward = 231.20 − 218.50 = 12.70
R:R    = 12.70 / 11.70   = 1.09      ← not 1.6
```

It fails the Step 4 gate of R:R@T1 ≥ 1.5 and would be rejected by the scanner. It is also
internally inconsistent with the T1 formula: if T1 = entry + 1.5×ATR then ATR = 8.47, which
makes the stop 1.38×ATR — wider than the 1.0×ATR ceiling the gate imposes (section 2 above).

One illustration in the whole document, and it violates two of the document's own rules. The
useful inference is not that the example is wrong; it is that **the R:R gate has never been
worked through by hand**, which is consistent with the algebra in section 2 going unnoticed.

### A.5 Part 2.4 is a changelog for work that no phase owns

The "Improvements Over the Original 7-Ticker Scanner" table is written in the past tense
about four things that are not scoped anywhere:

| 2.4 claims | Actual status in the document |
|---|---|
| "Tiers derived from measured volatility and the `stability_flag` field" | Step 4's gate table hard-codes the same intuition-based list it claims to replace, by example and "etc.". No measurement procedure, no rule assigning DELL, ARM, or CEG to a tier. |
| "Benchmark adjustable per sub-segment" | Presented as fixed, then **re-opened as Open Question 1**. |
| "41 tickers → 2–6 setups per week" | Asserted with no derivation. Measured at ~0.4/week (section 4). |
| "Full backtester to prove win rate…" | The only sequencing constraint in the document, and Part 7 partly violates it (A.7). |

A plan should distinguish "done", "decided but not built", and "still an open question".
This table merges all three.

### A.6 Under-specified exactly where code needs precision

The document is generous with rationale and vague at every point where an implementer has to
make a decision. Each of these is unbuildable as written:

- **"Entry Price: the current closing price… You would enter at or near this level."**
  "At or near" is not a fill rule, and it contradicts `AI_Semiconductor_Sector_Momentum_Strategy.md`
  §5 ("next day's open, or break of the signal candle's high").
- **"Correlated pairs count as 1.5 positions for 'heat' purposes."** No correlation measure,
  no lookback window, no threshold, no pair list. There is no way to code this sentence.
- **"Max 2 in the same narrow sub-segment."** "Narrow sub-segment" is never defined, and the
  example given — NVDA + AMD + MU — spans *two* of section 1.3's nine segments, so the
  example contradicts the taxonomy.
- **Runner exit 3, RS loss.** "The stock's return since entry falls below SMH's return since
  entry." Measured on closes? With what tolerance? From day one? With no buffer and no
  minimum hold, this fires on day-1 noise in roughly half of all trades.
- **Tier membership.** Step 4's table defines both tiers by example and "etc.". DELL, ARM,
  CEG, PWR, ORCL and about twenty others are unassigned.
- **Scan time.** "~10 minutes before US market close at 3:50 PM ET, **or** after close" is
  two different systems. At 3:50 the candle pattern, RSI(2) and ATR are all provisional — a
  hammer at 3:50 can close as a doji, and the entry price does not exist yet.
- **Flagged-names list.** Section 1.3 lists INTC, ALAB, CRDO, SMCI, GEV, ZETA; `universe.yaml`
  also flags PLTR.

### A.7 Missing sections

Ordered by how much damage the omission does.

**Earnings.** A 3–25 day holding period over a universe that reports quarterly means a
meaningful share of trades will be open through an earnings release, and a sub-1-ATR stop on
a semiconductor through an earnings gap is the largest single tail risk in the design.
Earnings appear once in the entire document — as a line item in the weekly digest (6.4) —
and never as an entry or exit rule. At minimum: no new entry within N days of a scheduled
report, and a decision on whether open positions are flattened or held through.

**Regime.** No rule for what the system does in a sector drawdown or a bear market. Filter 3
silences the scanner by accident (section 6.6); that should be a decision, not a side effect.

**Transaction costs.** `config/portfolio.yaml` carries `assumed_slippage_bps: 10` and
`assumed_commission_usd: 0.0`. The plan never mentions costs at all, and at a $25 minimum
position with a $3.56 risk budget they are not a rounding error.

**Tax treatment.** Every swing trade is a short-term gain by construction. Not mentioned.

**Fractional shares.** A 20%-of-account position is $200. ASML, MSFT, NVDA-class share
prices mean the sizing math in 2.3 only works if the broker supports fractional trading. The
plan assumes continuous position sizes throughout and never states the requirement.

**Operational failure modes.** This is an unattended daily job that will alert your phone.
What happens when yfinance changes its API, a scan throws, a ticker halts, a filing is
malformed, or the scheduler misses a day? There is no operations section, no monitoring, no
"the scan failed" alert type among the five in Part 6 — so a silent failure and a genuinely
quiet market look identical to you.

**The manual workflow.** "No live execution — you receive alerts; you decide and click" is a
deliberate and good choice, but it needs a procedure: card arrives 4:05pm, you act next
morning, the price has moved 2%. Do you take it, re-derive the levels, or skip? Undefined,
and it is where discretion will quietly replace the system.

### A.8 Phase ordering

Part 7 builds the delivery mechanism before validating the thing delivered. Phase 1 expands
the scanner to 41 tickers *and adds the quality badge*; Phase 2 then builds the backtester to
find out whether any of it works; Phase 4 is titled "connect fundamental engine scoring to
swing scanner" — which Phase 1 already did, so Phases 1 and 4 overlap. Phase 3 ships
Telegram alerts for an unvalidated signal.

Reorder per section 9: reconcile the docs, get data, ablation-test the entry, fix the
geometry, *then* build plumbing. Add the position store (A.2) as its own phase, and attach a
numeric gate to each (A.3).

### A.9 What to keep

Judged as a document rather than a strategy, several things here are better than what most
retail systems ever produce, and a rewrite should not lose them.

**Section 3.9, thesis and falsification recording, is the best idea in the plan and the most
underweighted.** Recording *why* you bought and *what would prove you wrong* at entry, then
checking new filings against that condition, is the one genuinely novel component — and it
gets one section and one alert type while the swing engine gets a third of the document. It
also has the useful property of being valuable whether or not any signal engine works.

**Section 3.8** — "the system says no action and means it", long stretches with zero
purchases are "an expected output, not a malfunction" — is the hardest discipline in
systematic investing, written down before it was tested.

**Section 3.4's callout** that the pillar weights "are a hypothesis, not a law… not tuned
until the equity curve looks good" is the sentence that separates this from a curve-fitting
exercise.

**Section 5.3**, including "The swing strategy has ZERO backtest evidence yet."

**The pedagogy.** Defining ATR, moving averages, RSI, and R:R at point of use, and citing
Piotroski (2000), Altman (1968/1995), Koller et al., and Graham, makes this readable and
auditable. The cost is that it cannot double as the build spec — it is too discursive to
implement against, which is why A.6's ambiguities went unnoticed. Keep this document as the
explanatory one and derive a thin, unambiguous rules spec from it.









