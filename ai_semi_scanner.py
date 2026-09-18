# =============================================================================
#  ai_semi_scanner.py  --  AI / Semiconductor Sector Momentum, Dip-Entry
#
#  Adapted from Strategy 4 "Sector Momentum" (NSE system) per
#  AI_Semiconductor_Sector_Momentum_Strategy.md. Theme-concentrated (no
#  cross-sector rotation): ranks leadership WITHIN a fixed AI/semi universe
#  against SMH, then looks for DIP entries (not breakouts) in the leaders.
#
#  BY DESIGN: no position sizing, no broker connection, no order placement.
#  Prints price LEVELS only. Quantity and every click is your decision.
#  NOT FINANCIAL ADVICE.
#
#  Run:   python3 ai_semi_scanner.py
# =============================================================================

import sys
import subprocess
import importlib

def ensure(pkg, imp=None):
    try:
        __import__(imp or pkg)
    except ImportError:
        print(f"[setup] Installing '{pkg}' (one-time)...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--quiet",
                               "--disable-pip-version-check", "--no-warn-script-location", pkg])
ensure("pandas"); ensure("yfinance")

import datetime as dt
import pandas as pd
import yfinance as yf

pd.set_option("display.width", 120)

# =============================================================================
# UNIVERSE  (from the strategy file -- edit this list as you add candidates)
# =============================================================================
BENCHMARK = "SMH"
CORE_TIER = ["NVDA", "AMD", "MU", "SMH"]
HIGH_BETA_TIER = ["CRWV", "BE", "APLD"]
UNIVERSE = CORE_TIER + HIGH_BETA_TIER  # SMH included as a tradeable name too

TIER_OF = {t: "core" for t in CORE_TIER}
TIER_OF.update({t: "high-beta" for t in HIGH_BETA_TIER})

# Gates -- tiered, per the strategy file
GATES = {
    "core":      {"max_stop_pct": 10.0, "min_rr1": 1.5, "min_rr2": 2.0},
    "high-beta": {"max_stop_pct": 17.0, "min_rr1": 1.5, "min_rr2": 2.0},
}

LOOKBACK_DAYS = 400   # calendar days of history to pull (~1yr + buffer for MAs)
ATR_MULT_FALLBACK = 2.0
TIME_STOP_DAYS = 25

STRATEGY_NOTE = ("Adapted strategy -- NOT YET BACKTESTED. Rules follow "
                  "AI_Semiconductor_Sector_Momentum_Strategy.md. Treat output "
                  "as a structured checklist, not a proven edge.")

print("\n" + "=" * 74)
print("  AI / SEMICONDUCTOR SECTOR MOMENTUM SCANNER  --  dip-entry, theme-only")
print("=" * 74)
print(f"  {STRATEGY_NOTE}")
print("  NOT FINANCIAL ADVICE. No orders placed. No position sizing computed.")
print("  A common rule of thumb: risk no more than ~1% of capital per trade.")


# =============================================================================
# INDICATORS
# =============================================================================
def sma(s, n):
    return s.rolling(n).mean()

def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()

def rsi(s, period=2):
    ch = s.diff()
    up = ch.clip(lower=0)
    dn = -ch.clip(upper=0)
    ag = up.ewm(alpha=1 / period, adjust=False).mean()
    al = dn.ewm(alpha=1 / period, adjust=False).mean()
    return 100 - (100 / (1 + ag / al))

def atr(df, n=14):
    # Wilder smoothing -- matches TradingView's ta.atr() so Python and any
    # future Pine Script chart agree on borderline verdicts.
    h, l, pc = df["High"], df["Low"], df["Close"].shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()

def is_bullish_reversal_candle(df):
    """Heuristic: hammer, bullish engulfing, or inside-day-then-higher-close
    on the most recent bar. Simple and conservative -- flags None if unsure."""
    if len(df) < 3:
        return False, "insufficient data"
    o, h, l, c = df["Open"].iloc[-1], df["High"].iloc[-1], df["Low"].iloc[-1], df["Close"].iloc[-1]
    po, ph, pl, pc = df["Open"].iloc[-2], df["High"].iloc[-2], df["Low"].iloc[-2], df["Close"].iloc[-2]
    body = abs(c - o)
    rng = h - l if h > l else 1e-9
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)

    # Hammer: small body near the top of the range, long lower wick
    is_hammer = (lower_wick >= 2 * body) and (upper_wick <= body * 0.6) and (c > o)
    # Bullish engulfing: today's body fully engulfs yesterday's red body
    is_engulf = (pc < po) and (c > o) and (c >= po) and (o <= pc)
    # Inside day (today's range inside yesterday's) followed by a higher close
    is_inside_up = (h <= ph) and (l >= pl) and (c > pc)

    if is_hammer:
        return True, "hammer"
    if is_engulf:
        return True, "bullish engulfing"
    if is_inside_up:
        return True, "inside day + higher close"
    return False, "no reversal candle on latest bar"


# =============================================================================
# STEP 0: pull data
# =============================================================================
end = dt.date.today() + dt.timedelta(days=1)
start = end - dt.timedelta(days=LOOKBACK_DAYS)

print(f"\n[1] Pulling daily OHLCV for {len(UNIVERSE)} tickers ({start} to {end})...")
data = {}
last_bar_date = None
market_open_warning = False

for t in UNIVERSE:
    try:
        df = yf.download(t, start=start, end=end, interval="1d",
                          auto_adjust=False, progress=False)
        if df.empty or len(df) < 60:
            print(f"    {t:<6} FAILED -- not enough data returned, skipping.")
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        data[t] = df
        bar_date = df.index[-1].date()
        if last_bar_date is None:
            last_bar_date = bar_date
    except Exception as e:
        print(f"    {t:<6} FAILED -- {e}")

if not data:
    print("\n  No data could be fetched. Check your internet connection and try again.")
    sys.exit(1)

now_et_hour = dt.datetime.now(dt.timezone.utc).hour - 4  # rough ET offset, DST-naive
if last_bar_date == dt.date.today() and 9 <= (now_et_hour % 24) < 16:
    market_open_warning = True

print(f"    Latest bar date across universe: {last_bar_date}")
if market_open_warning:
    print("    NOTE: market appears to be OPEN right now -- today's bar is still")
    print("    forming and all numbers below can move until the close.")
else:
    print("    Today's session (if already closed) should be reflected as final.")

if BENCHMARK not in data:
    print(f"\n  Benchmark {BENCHMARK} failed to download -- cannot compute relative")
    print("  strength. Aborting.")
    sys.exit(1)


# =============================================================================
# STEP 1: rank leadership within the theme (vs SMH)
# =============================================================================
def ret_n(df, n=15):
    if len(df) < n + 1:
        return None
    return float(df["Close"].iloc[-1] / df["Close"].iloc[-1 - n] - 1)

bench_ret15 = ret_n(data[BENCHMARK])
print(f"\n[2] Benchmark ({BENCHMARK}) 15-day return: {bench_ret15*100:+.1f}%")

leadership = []
for t, df in data.items():
    r15 = ret_n(df)
    if r15 is None:
        continue
    rs = r15 - bench_ret15
    leadership.append({"sym": t, "ret15": r15, "rs_vs_bench": rs, "tier": TIER_OF.get(t, "core")})

leadership.sort(key=lambda x: x["rs_vs_bench"], reverse=True)

print(f"\n    Ranked by relative strength vs {BENCHMARK} (15-day):")
print(f"    {'Ticker':<8}{'15d Ret':>10}{'RS vs '+BENCHMARK:>14}{'Tier':>12}")
for x in leadership:
    print(f"    {x['sym']:<8}{x['ret15']*100:>9.1f}%{x['rs_vs_bench']*100:>13.1f}%{x['tier']:>12}")


# =============================================================================
# STEP 2 + 3 + 4: filters, trade plan, gates -- per stock
# =============================================================================
def evaluate(sym, df, rs_vs_bench, ret15, tier):
    close = df["Close"]
    entry = float(close.iloc[-1])
    sma50 = float(sma(close, 50).iloc[-1])
    ema20 = float(ema(close, 20).iloc[-1])
    atr_val = float(atr(df).iloc[-1])
    rsi2 = float(rsi(close, 2).iloc[-1])
    swing_low = float(df["Low"].iloc[-10:].min())

    reasons_fail_entry = []

    # --- Entry filter 1: own trend positive ---
    if ret15 <= 0:
        reasons_fail_entry.append(f"15d return {ret15*100:.1f}% not positive")

    # --- Entry filter 2: beating the sector ---
    if rs_vs_bench <= 0:
        reasons_fail_entry.append(f"RS vs {BENCHMARK} {rs_vs_bench*100:.1f}% not positive (lagging)")

    # --- Entry filter 3: above 50-DMA ---
    above_50 = entry > sma50
    if not above_50:
        reasons_fail_entry.append(f"price {entry:.2f} below 50-DMA {sma50:.2f}")

    # --- Entry filter 4: dip trigger ---
    near_20ema = abs(entry - ema20) / entry <= 0.01
    oversold = rsi2 < 15
    dip_condition = near_20ema or oversold
    candle_ok, candle_why = is_bullish_reversal_candle(df)
    dip_trigger = dip_condition and candle_ok
    if not dip_condition:
        reasons_fail_entry.append(f"no dip: price not near 20-EMA ({ema20:.2f}) and RSI(2)={rsi2:.0f} not oversold")
    elif not candle_ok:
        reasons_fail_entry.append(f"dip present but no reversal candle yet ({candle_why})")

    entry_ok = len(reasons_fail_entry) == 0

    # --- Trade plan math (computed regardless, for reference even on FAIL) ---
    # Stop = the CLOSER of (50-DMA, recent swing low) to entry -- i.e. the
    # tighter, more honest stop -- as long as it's actually below entry.
    candidates = [v for v in (sma50, swing_low) if v < entry]
    if candidates:
        sl = max(candidates)
        sl_label = "50-DMA" if sl == sma50 else "recent swing low"
    else:
        sl = entry - ATR_MULT_FALLBACK * atr_val
        sl_label = f"{ATR_MULT_FALLBACK}x ATR fallback"

    sl_dist = entry - sl
    sl_pct = (sl_dist / entry * 100) if entry else 0
    t1 = entry + 1.5 * atr_val
    t2 = entry + 3.0 * atr_val
    rr1 = (t1 - entry) / sl_dist if sl_dist > 0 else 0
    rr2 = (t2 - entry) / sl_dist if sl_dist > 0 else 0

    gate = GATES[tier]
    gate_fails = []
    if rr1 < gate["min_rr1"]:
        gate_fails.append(f"R:R@T1 {rr1:.2f} < {gate['min_rr1']}")
    if rr2 < gate["min_rr2"]:
        gate_fails.append(f"R:R@T2 {rr2:.2f} < {gate['min_rr2']}")
    if sl_pct > gate["max_stop_pct"]:
        gate_fails.append(f"stop {sl_pct:.1f}% > {gate['max_stop_pct']}% cap ({tier} tier)")

    gates_ok = len(gate_fails) == 0
    verdict = "PASS" if (entry_ok and gates_ok) else "FAIL"

    return {
        "sym": sym, "tier": tier, "entry": entry, "sl": sl, "sl_label": sl_label,
        "sl_pct": sl_pct, "t1": t1, "t2": t2, "rr1": rr1, "rr2": rr2,
        "atr": atr_val, "rsi2": rsi2, "ema20": ema20, "sma50": sma50,
        "entry_ok": entry_ok, "gates_ok": gates_ok, "verdict": verdict,
        "entry_fail_reasons": reasons_fail_entry, "gate_fail_reasons": gate_fails,
        "candle_why": candle_why,
    }


print(f"\n[3] Evaluating each ticker against the 4 entry filters + risk gates...")
results = []
for x in leadership:
    r = evaluate(x["sym"], data[x["sym"]], x["rs_vs_bench"], x["ret15"], x["tier"])
    results.append(r)

passed = [r for r in results if r["verdict"] == "PASS"]
failed = [r for r in results if r["verdict"] == "FAIL"]
passed.sort(key=lambda r: r["rr1"], reverse=True)

print("\n" + "=" * 74)
print(f"  RESULTS -- {len(passed)} PASS / {len(failed)} FAIL  (of {len(results)} evaluated)")
print("=" * 74)

if passed:
    print("\n  PASS -- best R:R at T1 first:\n")
    for r in passed:
        print(f"  {r['sym']}  ({r['tier']} tier)")
        print(f"        Entry   ${r['entry']:.2f}")
        print(f"        Stop    ${r['sl']:.2f}  (-{r['sl_pct']:.1f}%, {r['sl_label']})")
        print(f"        T1      ${r['t1']:.2f}  (R:R {r['rr1']:.2f}:1, sell 50% / move stop to breakeven)")
        print(f"        T2      ${r['t2']:.2f}  (R:R {r['rr2']:.2f}:1, gate only -- runner TRAILS via:")
        print(f"                chandelier 3xATR / 2 closes under 20-EMA / RS loss vs {BENCHMARK})")
        print(f"        Hold    trail while leadership lasts; re-check RS ~3wk; max {TIME_STOP_DAYS}d")
        print(f"        Candle  entry signal: {r['candle_why']}")
        print(f"        VERDICT PASS")
        print()
else:
    print("\n  No tickers currently pass every filter and gate.")
    print("  This is normal -- dip entries with a confirmed reversal candle are")
    print("  specific conditions, not every-day occurrences.")

if failed:
    print(f"  FAIL -- watchlist (why each didn't qualify today):\n")
    for r in failed:
        why = "; ".join(r["entry_fail_reasons"] + r["gate_fail_reasons"])
        print(f"    {r['sym']:<6} ({r['tier']:<9})  {why}")
    print()

print("-" * 74)
print("Reminder: this scan is checklist output, not a signal to act on blindly.")
print("No backtest has validated this ruleset yet. No position size is computed --")
print("that decision, and every order, is yours.")
print("-" * 74)