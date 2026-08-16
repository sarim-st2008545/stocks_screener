# Halal Investment Research Tool

A research assistant for Shari'ah-compliant swing investing in US equities. It
compresses hours of screening into a short read; it does not predict prices and
never tells you to buy. Compliance filters the universe, fundamentals rank what
is left, and filings tell you when something is in play.

Four entry points, run in this order:

- `aaoifi_screener.py` — screen named tickers
- `universe.py` — sweep a whole index and track how verdicts move between runs
- `scoring.py` — rank the compliant list on fundamentals
- `catalysts.py` — watch the compliant list for insider buying and material events

## Run it

```bash
pip install requests yfinance

# ad-hoc
python aaoifi_screener.py AAPL MSFT NVDA JPM KO PM
python aaoifi_screener.py --file tickers.txt --out results.csv --json

# standing sweep of S&P 500 + Nasdaq 100
python universe.py
python universe.py --limit 25          # quick smoke run
python universe.py --refresh-bulk      # re-download the SEC archive

# rank the compliant list on fundamentals
python scoring.py --top 30

# catalysts on the compliant watchlist
python catalysts.py --days 5
python catalysts.py --min-importance 4 # earnings, acquisitions, restatements only
python catalysts.py --min-score 60     # only from better-scoring companies
python catalysts.py --notify           # push the digest to Telegram
```

Edit `USER_AGENT` at the top of `aaoifi_screener.py` first — SEC rejects requests
without a contact address.

SEC responses cache to `.sec_cache/`. Delete it to force a refresh after new filings.

## The universe sweep

A screener you have to type tickers into does not solve the actual problem, which
is not having time to find candidates. `universe.py` screens the whole index and
maintains a compliant watchlist that every later layer consumes.

Three data paths, each chosen against a measured alternative:

| Need | Source | Why |
|---|---|---|
| XBRL facts | Bulk `companyfacts.zip` (1.39 GB, one download) | Individual payloads average 3.24 MB, so 518 of them move *more* bytes than the whole archive, in 518 requests |
| SIC + company name | Per-ticker submissions, un-cached | Payloads average 429 KB because they carry full filing history; only two fields are kept, in a 32 KB registry |
| Market cap | `dei` shares outstanding × batched price | One `yf.download` per 100 tickers instead of 518 sequential `.info` calls |

Metadata fetching is the slow part of a first run, so it goes through a
rate-limited thread pool (5 workers, 6 req/s — inside SEC's fair-access guidance)
and is cached. Later sweeps only fetch constituents that changed.

**Market caps are accurate**, spot-checked against yfinance across 18 names: zero
differences above 5%, most within 0.2%.

**Multi-class filers fall back to yfinance.** `companyfacts` strips XBRL
dimensions, so Alphabet and Meta report *no* usable share count rather than a
wrong one. `resolve_shares_outstanding` returns None instead of guessing.

### Outputs, in `data/`

- `universe_screen.csv` — every name, every ratio, every debt candidate
- `watchlist.json` — tickers bucketed by verdict; this is what downstream layers read
- `snapshots/YYYY-MM-DD.json` — one per sweep, used to diff verdicts over time
- `sic_registry.json`, `index_*.json` — caches

### Change tracking

Every sweep diffs against the previous snapshot and reports names that gained or
lost compliance. A holding going from PASS to FAIL is the most actionable thing
this tool produces, and it is invisible without comparing verdicts over time.

## Fundamental scoring

Compliance says what you *may* own; scoring says what is worth owning. Nothing
here predicts prices — every number is an accounting fact from a filing or an
arithmetic combination of them.

Scores are **percentile ranks within the investable pool** (PASS + REVIEW, 312
names), not absolute grades. A profitability score of 80 means "more profitable
than 80% of the names you could actually buy", which is both the comparison that
matters and far more robust than hand-picked thresholds.

| Pillar | Metrics | Weight |
|---|---|---|
| Growth | revenue and FCF, year over year | 25% |
| Profitability | operating, net and FCF margins | 25% |
| Quality | return on equity, return on invested capital | 20% |
| Leverage | net debt / EBITDA, debt / equity (lower better) | 15% |
| Valuation | FCF yield, earnings yield, EV/EBITDA — ranked against sector peers | 15% |

**Yields, not multiples.** A loss-making company has a meaningful negative
earnings yield but a meaningless negative P/E.

**Gross margin is deliberately absent.** Only 60% of filers tag `GrossProfit`,
and a pillar that silently vanishes for four names in ten is worse than no
pillar. Metric selection came from measuring coverage first: revenue, net
income, operating cash flow and equity are at 100%, capex 94%, D&A 96%,
operating income 88%.

**A missing pillar is renormalised away, not scored zero** — otherwise a company
would be punished twice, once for lacking the data and again by a diluted total.
38 names have no leverage score because they tag no debt concept at all; they
are almost certainly debt-free, but Layer 1's rule that untagged never means
zero applies here too.

Output is `data/scores.csv` (all metrics, auditable) and `data/scores.json`
(compact ticker → composite, which the catalyst monitor reads).

### The direction bug worth knowing about

The first run put Intel top of profitability on a **negative** net margin and
NVIDIA near the bottom on a 56% one — the percentile sort was inverted. Every
number looked plausible, the table sorted, and the ranking was simply upside
down. `test_scoring.py` now asserts direction explicitly for both polarities.

## Catalyst monitoring

Fundamentals say what is worth owning; catalysts say when it is in play. On a
swing horizon the second question is the one that matters, and filings answer it
without a paid news feed.

Discovery goes through EDGAR's daily master index — one 609 KB pipe-delimited
file covering every filer for a day — rather than polling 300 companies. A
typical day yields ~160 Form 4s and ~40 8-Ks from the watchlist.

**Form 4 — insider transactions**, filed within two business days. The important
part is what gets *excluded*: option exercises, tax withholding and stock grants
dominate Form 4 volume and mean nothing directionally. Only code `P`, an
open-market purchase above $25k, is treated as a buy signal — an insider choosing
to spend their own money. Sales are reported but rated low, because executives
sell for diversification, tax and scheduled 10b5-1 plans far more often than out
of conviction.

Two details that produce wrong counts if missed: a joint Form 4 naming two
reporting owners will report every transaction twice unless the transaction
table is read once, and a purchase filled across several price points arrives as
separate lines — one CVNA buy appeared as $1.3M plus $240k before those lots were
merged into a single $1.5M trade at a volume-weighted price.

**Clusters rate highest.** One purchase is weak evidence; two or more distinct
insiders buying the same name within 45 days is the pattern with real support.

**8-K — material events**, rated 0–5 by item code. Non-reliance on previously
issued financials (a restatement) rates 5 because it is thesis-breaking;
earnings releases and completed acquisitions rate 4; `Financial Statements and
Exhibits` rates 0 because it is attached to almost everything.

**Events are ranked by company quality.** Once `scoring.py` has run, each event
carries its company's composite score, and equally-important events sort by it.
An earnings release from a top-decile business is worth reading; the same filing
from a weak one usually is not, and without this the feed is a few hundred
equally-weighted 8-Ks a week — which is the same as no feed. Unscored names are
kept rather than hidden, since a missing score means missing data, not a bad
company.

Filter with `--min-importance 4` and `--min-score 60` for the short list. Output
goes to `data/catalysts.json`.

### Telegram alerts

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as environment variables, or put
`telegram_bot_token` / `telegram_chat_id` in `config.json`. Without them
`--notify` explains what is missing and changes nothing. Create the bot with
[@BotFather](https://t.me/botfather); get your chat id by messaging the bot and
reading `https://api.telegram.org/bot<TOKEN>/getUpdates`.

### Index sources

S&P 500 comes from Wikipedia, Nasdaq-100 from slickcharts — Wikipedia has removed
its Nasdaq-100 constituent table entirely. Both fall back to the last good cached
list, since a stale membership list beats an empty universe.

## What it does

**Stage 1 — business activity.** SIC-code screen against AAOIFI excluded sectors:
conventional finance, alcohol, tobacco, gambling, weapons. Excluded sectors fail
immediately without computing ratios.

**Stage 2 — financial ratios.** AAOIFI Shari'ah Standard No. 21, against market cap
by default (`--basis total_assets` for the alternative reading):

| Ratio | Limit |
|---|---|
| Interest-bearing debt / market cap | < 30% |
| Cash + interest-bearing securities / market cap | < 30% |
| Non-compliant income / revenue | < 5% |

**Stage 3 — verdict.** Four states, deliberately not two:

- `PASS` — clears the sector screen and all three ratios
- `FAIL` — excluded sector, or a ratio breached
- `REVIEW` — sector is scholar-dependent (hotels, REITs, restaurants), or a required
  figure wasn't tagged in XBRL
- `INSUFFICIENT_DATA` — no filings yet, typical for recent IPOs

Output also includes a market cap tier (`mega`/`large`/`mid`/`small`/`micro`) so blue
chips can be separated from speculative positions, and a purification estimate per
share where dividend and interest income data allow.

## How XBRL facts are resolved

The first live run against 24 tickers produced wrong numbers for a third of them,
all traceable to the same thing: `companyfacts` returns every observation a company
has ever tagged, and naive selection picks the wrong one. Four rules now govern it.

**Stale concepts are treated as absent.** Companies abandon tags. Mastercard's
`LongTermDebt` stops in 2011, Verizon's in 2013, Realty Income's in 2017 — while all
three carried billions in debt under other tags. Facts older than `MAX_FACT_AGE_DAYS`
(550) drop out so resolution falls through to a tag still being maintained.

**Income-statement facts get a longer horizon** (`MAX_DURATION_FACT_AGE_DAYS`, 900).
A trailing-twelve-month figure is stitched from four quarters, so its oldest link is
already a year behind the window's end. The 550-day cutoff severed valid chains.

**Debt is the largest defensible measure, not the first one found.** Marriott tags
$23M under `LongTermDebt` while carrying $16.5bn in `DebtAndCapitalLeaseObligations`;
REITs skip the `LongTermDebt` family entirely and use `NotesPayable`/`SecuredDebt`.
Every candidate is computed and the largest wins — understating debt causes a false
`PASS`, overstating causes a `FAIL` that surfaces for review. All candidates are
written to the output so the choice is auditable.

**Ratio periods must line up.** Revenue and interest income are each resolved to a
TTM window (annual fact, or four chained quarters), then paired only if their end
dates fall within 45 days. Dividing annual interest income into a single quarter of
revenue made Visa read 7.0% against a real 2.0% — a false `FAIL`.

Restatements collapse to the most recently filed value, and balance-sheet components
are only summed when they come from the same filing date.

## Known limitations

**Interest income is still the weak spot.** Apple and McDonald's no longer report it
separately — it is netted inside other income, where it cannot be separated from FX
and one-off gains. Where only `InvestmentIncomeNet` is tagged (Microsoft, Mastercard)
it is used, though it is net of investment losses rather than a pure interest figure.

Where the exact figure is missing, the screener reports an **upper bound** instead:
the tightest aggregate line the company tags that *contains* interest income, as a
percentage of revenue. If the entire non-operating income line is 0.08% of revenue,
as Apple's is, the interest component inside it cannot be material.

This is evidence, not a verdict — bounded names stay `REVIEW` and nothing is
promoted to `PASS` on the strength of it, because whether an undisclosed figure may
be treated as immaterial is a Shari'ah judgement rather than an engineering one.
In practice it does most of the work: of 178 `REVIEW` names, 126 now carry a bound
and 119 of those clear the 5% limit with a median bound of 0.49%, leaving roughly 59
that genuinely need a human. To act on the bound, raise it with your scholar first.

**Untagged debt returns unknown, not zero.** ARM and Reddit are close to debt-free
and tag no debt concept at all, so both come back `REVIEW`. Treating absence as zero
would silently pass companies that should fail.

**SIC codes are coarse.** They catch banks and brewers but cannot detect a
supermarket deriving 3% of revenue from alcohol. The `REVIEW` tier exists for this.
Constellation Brands files under the generic beverages code 2080 rather than the
2082–2085 alcohol range, so `SIC_OVERRIDES` maps known misclassifications by CIK —
add to it as you find disagreements with your reference screener.

**Operating leases are excluded from debt**, on the basis that they are not
riba-bearing. Capital/finance leases are included where a company tags them together
with debt. Change `DEBT_CANDIDATES` if your scholar's position differs.

**Market cap is a live number**, so ratios shift with price. A name near a threshold
can flip week to week. Re-screen before acting, not just quarterly.

## Verification

`test_aaoifi_screener.py` covers the deterministic logic — SIC classification, cap
tiering, fact normalisation, TTM chaining, period alignment, and end-to-end screening
against mocked filings. Each bug the live run exposed has a named regression test.

```bash
python test_aaoifi_screener.py
python test_universe.py
python test_scoring.py
python test_catalysts.py
```

78, 36, 51 and 54 assertions respectively — 219 in total, all passing. `REFERENCE_DATE` is pinned so
staleness tests stay meaningful over time. The network layer against live SEC
endpoints is *not* covered.

`test_universe.py` covers constituent-table parsing (both source layouts, plus
nested tables), shares-outstanding resolution including the multi-class case,
bulk-archive access, the shared rate limiter, and verdict diffing. It caught a
Windows bug where the 1.39 GB archive stayed locked open, which would have made
`--refresh-bulk` fail silently.

`test_catalysts.py` covers business-day arithmetic, daily-index parsing and
de-duplication, Form 4 extraction (joint filings, split executions, malformed
XML), buy filtering, 8-K item rating, cluster detection, and score ranking.

`test_scoring.py` covers percentile direction in both polarities, every derived
ratio, and the degenerate cases that should yield no value rather than a
flattering one: negative equity, negative EBITDA, and loss-making years.

## Next layers

2. ~~Fundamental scoring~~ — done
3. ~~Catalyst monitoring~~ — done
4. **Brief generation** — structured bull/bear summaries on names with live catalysts
5. **Thesis tracking** — record why you bought and what would falsify it; alert on breach
6. **Signal journal** — log every brief and what happened next

Layers 5 and 6 are where a personal tool beats every commercial product, because no
commercial product knows your thesis. Layer 6 should start logging as soon as briefs
exist — without it there is no way to tell whether any of this is working.

`validation_before.csv` and `validation_after.csv` hold the same 24 tickers screened
before and after the fact-layer rewrite, if you want to see what moved.

## ETFs

`CERTIFIED_ETFS` in the module holds a starter list (SPUS, SPTE, SPRE, SPSK, SPWO,
HLAL, UMMA). ETFs cannot be ratio-screened — you rely on the fund's certification or
look through to holdings. Verify current certification before relying on the list.

## Not a religious ruling

Threshold values, denominator choice, and the treatment of edge cases are matters of
scholarly interpretation and differ between AAOIFI, Dow Jones, and MSCI. Confirm the
standard with a scholar you trust. This is a filtering aid, not a fatwa.
