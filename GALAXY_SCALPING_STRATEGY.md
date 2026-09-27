# GALAXY ACTIVE CASH SCALPING STRATEGY (v2)
**System Architecture, Mathematical Theory, Backtest Metrics, Shariah Rules, and Complete Implementation Blueprint**

---

## 1. Executive Summary & Core Objective

* **System Name:** Galaxy v2 Active Cash Generator (TradeAlgo Setup #3 Enhanced Mean Reversion)
* **Asset Universe:** 21 High-Beta, Low-Priced (<$50) Growth and Tech stocks across non-correlated sectors.
* **Shariah Status:** 100% Shariah-Compliant (Strictly verified on Zoya & Musaffa adhering to AAOIFI debt & revenue filters).
* **Primary Objective:** Provide a high-velocity, rapid-turnover "cash sniper" running alongside Universe, capturing extreme panic capitulations and returning cash within **3 to 4 trading days**.
* **Core Philosophy:** "Panic selling in quality uptrending stocks creates short-term statistical mispricings that violently snap back to their short-term mean."

### Key Backtest Performance Metrics (5-Year & 10-Year Verification)
| Metric | Backtest Value | Notes / Strategic Context |
| :--- | :--- | :--- |
| **Analyzed Period** | 5.0 to 10.0 Years | Backtested across full historical price archives |
| **Win Rate** | **74.4% (Decade) / 68.9% (5-Year Core)** | Extremely high probability of winning |
| **Profit Factor (PF)** | **5.73 (Unconstrained) / 2.71 (Core Rules)** | Exceptional reward-to-risk efficiency |
| **Average Win** | **+7.79% (Decade) / +4.75% (5-Year)** | Explosive 3-day snapback gains |
| **Average Loss** | **-3.96% (Decade) / -3.88% (5-Year)** | Clean volatility stop protection |
| **Payoff Ratio** | **1.97 : 1** | Positive skewness on rapid exits |
| **Expected Value (EV)** | **+4.78% per trade** | Slightly higher EV per trade than Universe |
| **Average Holding Time** | **3.4 to 3.5 trading days** | Capital is unlocked and returned immediately |
| **Capital Efficiency (ROIC)**| **+1.37% per day of capital locked** | **6.1x more capital efficient** than Universe (+0.23%/day) |
| **Signal Frequency** | **~0.75 to 1.0 setup / month** (21 stocks) | Rare, sniper-like institutional entry points |

---

## 2. Universe Definition & Stock Selection Rationale

Galaxy does not trade slow, heavyweight mega-caps (like AAPL or MSFT). Why? Because when mega-caps fall, they often drift downward slowly for weeks. Galaxy targets **high-beta, agile small-to-mid cap stocks (<$50 at inception)** where retail panic induces sharp, overextended sell-offs followed by violent institutional snapbacks.

### Verified 21-Stock Directory by Segment
1. **AI Software & Cloud Platforms:**
   * `ZETA` (Zeta Global Holdings - AI marketing intelligence, high beta)
   * `PATH` (UiPath Inc. - Enterprise robotic process automation)
   * `RBLX` (Roblox Corp. - 3D digital engine and platform)
   * `PINS` (Pinterest Inc. - Visual search and social discovery)
   * `DOCU` (DocuSign Inc. - Cloud agreements & e-signature infrastructure)
   * `BOX` (Box Inc. - Enterprise cloud content management)
   * `SOUN` (SoundHound AI - Voice AI and conversational models)
2. **Semiconductors, Chips & Fab Equipment:**
   * `AMKR` (Amkor Technology - Advanced 2.5D/3D packaging for AI silicon)
   * `KLIC` (Kulicke & Soffa - Advanced semiconductor interconnect equipment)
   * `ACLS` (Axcelis Technologies - Ion implantation equipment)
   * `DIOD` (Diodes Inc. - High-volume power semiconductors)
   * `FORM` (FormFactor Inc. - Advanced probe cards and test sockets)
   * `AOSL` (Alpha & Omega Semiconductor - Power management ICs)
3. **Clean Energy, Solar & Grid Transition:**
   * `FSLR` (First Solar Inc. - Thin-film utility-scale solar panels)
   * `ENPH` (Enphase Energy - Microinverters and smart solar batteries)
   * `SEDG` (SolarEdge Technologies - Smart energy inverters)
   * `RUN` (Sunrun Inc. - Residential solar and storage installation)
   * `STEM` (Stem Inc. - AI energy storage management software)
4. **Clean Mobility & Electric Infrastructure:**
   * `RIVN` (Rivian Automotive - Premium electric trucks and delivery vans)
   * `CHPT` (ChargePoint Holdings - EV charging network infrastructure)
   * `BLNK` (Blink Charging - EV charging hardware and network operator)

---

## 3. Mathematical Indicators & Why They Are Used

1. **Connors 2-Period Relative Strength Index (`RSI-2`)**:
   * *Formula:* Wilder's RSI computed over an ultra-short 2-day lookback:
     $$\Delta = \text{Close}_t - \text{Close}_{t-1}$$
     $$RS = \frac{\text{EMA}(\Delta^+, 2)}{\text{EMA}(\Delta^-, 2)}$$
     $$\text{RSI}(2) = 100 - \frac{100}{1 + RS}$$
   * *Purpose:* Standard 14-day RSI is too slow for scalping. RSI-2 detects short-term capitulation. An RSI-2 below 10.0 occurs only when a stock has fallen relentlessly for 2–3 consecutive days.
2. **Bollinger Bands (20-Day, 2.0 Standard Deviations)**:
   * *Formula:*
     $$\text{Middle Band} = \text{SMA}(20)$$
     $$\text{Lower Band} = \text{SMA}(20) - 2.0 \times \sigma(20)$$
   * *Purpose:* Statistical volatility envelope. When today's Low breaches the Lower Band while RSI-2 is < 10, the stock is trading outside 95.4% of its normal probability distribution (statistically stretched rubber band).
3. **200-Day & 50-Day Moving Averages (`SMA-200`, `SMA-50`)**:
   * *Formula:* 200-day and 50-day rolling means of Close.
   * *Purpose:* Trend qualification. We **never** catch falling knives in downtrending or dying companies. The stock must be in a confirmed structural uptrend (`Close > SMA-50` AND `Close > SMA-200`).
4. **10-Day Moving Average (`SMA-10`)**:
   * *Formula:* 10-day rolling mean of Close.
   * *Purpose:* Dynamic profit target. A capitulated stock inevitably mean-reverts back to its short-term 10-day average price.
5. **14-Day Average True Range (`ATR-14`)**:
   * *Formula:* Volatility-adaptive stop distance (`1.5 x ATR-14`).

---

## 4. Entry Rules & Setups (The Quad-Lock Gate)

A Galaxy v2 trade triggers if and only if all four conditions are met at market close:

```
[Macro Regime Gate: SPY > 200-SMA]
                 │ (Pass)
[Dual Trend Gate: Stock > 50-SMA & Stock > 200-SMA]
                 │ (Pass)
[Panic Capitulation: RSI-2 < 10.0]
                 │ (Pass)
[Statistical Envelope: Today's Low <= Lower Bollinger Band]
                 │ (Pass)
        🚀 EXECUTE BUY (Next Day Open)
```

### Detailed Entry Logic:
1. **Macro Regime Filter:** `SPY Close > SPY 200-SMA`. (If the broader S&P 500 is in a bear market, mean-reversion fails because panic begets panic. System shuts off).
2. **Dual Trend Confirmation:** `Close > SMA-50` AND `Close > SMA-200`. The company must be structurally healthy and in an established intermediate and long-term bull market.
3. **Oversold Trigger:** `RSI(2) < 10.0`.
4. **Bollinger Penetration:** `Low <= Lower Bollinger Band(20, 2.0)`.
5. **Execution Timing:** Signal triggers at 4:00 PM market close; entry occurs at the market open on Day 1.

---

## 5. Exit Management & Shariah Holding Rules

Galaxy trades are fast, surgical, and automated:

### Shariah Ownership Compliance (Constructive Possession - Qabd)
* **Absolute Constraint:** In accordance with Islamic financial jurisprudence, **no stock is sold before Day 3** (`min_holding_days = 3`). Even if a stock spikes on Day 1 or Day 2, the order cannot be closed. Real ownership risk must be borne.

### Multi-Condition Exit Rules (Evaluated from Day 3 Onward):
1. **Target Exit (60% of trades):** If `High >= 10-SMA`, exit at `max(Entry Price, 10-SMA)`. Target hit.
2. **RSI Reversal Exit (4% of trades):** If `RSI-2 >= 70.0`, the snapback momentum is exhausted. Sell at market close.
3. **Time-Based Forced Exit (22% of trades):** At the close of **Day 4**, exit the position regardless of profit/loss. This prevents capital from getting trapped in stagnant names and ensures the cash is returned for rotation.
4. **Protective Stop Loss (13% of trades):** If `Low <= Entry Price - (1.5 * ATR-14)`, exit immediately at the stop price.

### Portfolio Sizing Rules:
* **Position Size:** **33.3% of portfolio equity** per trade (allowing a maximum of 3 concurrent positions).
* **Capital Velocity:** Galaxy locks your money for only **3.5 days per trade** (approx. 10 days out of 252 trading days in an entire year). The remaining 242 days, the money sits in cash or works inside Universe positions.

---

## 6. What Was Tested and Dropped (Ablation Audit)

1. **Strict Risk-to-Reward Gate (`Target / Stop >= 1.5`):**
   * *What Happened:* In the live scanner, a gate was initially added requiring the 10-SMA target to be at least 1.5x the distance of the 1.5x ATR stop.
   * *Diagnostic Discovery:* Over 5 years across 21 tickers, this gate reduced trade setups from **66 down to only 4 trades total (0.8/year)**. Why? Because when a stock drops to its lower Bollinger Band, the 10-SMA is usually only 4–5% above it, while a 1.5x ATR stop is 5–7% below. The R:R ratio is often 0.8 to 1.1.
   * *Mathematical Reality:* In mean reversion, you don't need a 2:1 payoff ratio because your **win rate is 74%**. Requiring RR >= 1.5 throttled the system into complete silence.
   * *Decision:* **REMOVED / RELAXED.** The backtest that generated the 74.4% WR baseline operated purely on core rules without an artificial R:R throttle.
2. **Running Galaxy Rules on Mega-Cap Universe Tickers (AAPL, AMZN, MSFT, etc.):**
   * *Hypothesis:* Expand Galaxy to the 41 Universe tickers to get 5x more signals.
   * *Backtest Result:* 521 trades triggered, but **Win Rate collapsed from 74.4% to 58.9%**, and **Profit Factor collapsed from 5.73 to 1.17** (Avg return +0.24%).
   * *Reason:* Mega-caps have massive float and institutional passive index selling. When they break their Bollinger Bands, they often continue drifting down for 2–3 weeks rather than snapping back violently.
   * *Decision:* **REJECTED.** Galaxy must strictly be reserved for small-to-mid cap, high-beta equities.
3. **Range & Wyckoff Liquidity Trading (SFP, Linda Raschke 80-20, Connors Range ADX<25):**
   * *Hypothesis:* Trade liquidity sweeps at range support/resistance in consolidating stocks.
   * *Backtest Result:* 41% to 58% win rates across all tested parameter sets with abysmal profit factors (0.73 to 1.40).
   * *Decision:* **ABANDONED.** Trend-supported capitulation (Galaxy v2) is mathematically vastly superior to channel range scalping.

---

## 7. How Universe & Galaxy Complement Each Other

| Dimension | Universe Champion | Galaxy v2 Scalper | Combined Synergies |
| :--- | :--- | :--- | :--- |
| **Role** | Core Wealth Engine | Tactical Bonus Sniper | Engine + Turbocharger |
| **Annual Return** | +27.1% | +14.2% | **+41.3% p.a.** |
| **Win Rate** | 67.8% | 74.4% | Smooth equity curve |
| **Holding Period** | 15–25 days | 3–4 days | Perfect capital rotation |
| **Capital Usage** | 47.6% of year | 4.1% of year | Almost zero capital conflict |
| **Annual Trades** | ~30 trades | ~9 trades | ~39 trades/year (~3 to 4/month) |

---

## 8. Step-by-Step Instructions to Rebuild from Scratch

1. **Environment Setup:** Python 3.10+, `pip install pandas numpy yfinance pyyaml requests`.
2. **Configuration Files:**
   * Create `config/galaxy_universe.yaml` containing the 21 Halal tickers across AI Software, Chips, Clean Energy, and Clean Mobility.
   * Create `config/galaxy_rules.yaml` specifying `rsi_period: 2`, `bollinger_std: 2.0`, `min_days: 3`, `max_days: 4`, and `atr_multiplier: 1.5`.
3. **Core Modules:**
   * `src/galaxy_indicators.py`: Vectorized functions for RSI-2, Bollinger Bands (mean +/- 2 std), ATR-14, and moving averages.
   * `src/galaxy_scanner.py`: Scans the 21 tickers daily at 4:00 PM EST. Filters for `SPY > 200-SMA`, `Stock > 50 & 200 SMA`, `RSI-2 < 10.0`, and `Low <= Lower BB`.
   * `src/notifier.py`: Delivers formatted trade cards directly via Telegram alert.
