"""
Layer 4: AI Swing Trade Brief & Global Sector Trend Engine.

Generates structured Bull/Bear swing trade summaries for Halal stocks using LLM
(Gemini API or OpenAI API), integrating fundamental scores, SEC catalysts,
Shari'ah compliance metrics, and macro sector trends (e.g. Memory Chips, AI Hardware).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any
import requests

from config_loader import get_llm_config, get_config
from universe import DATA_DIR

BRIEFS_DIR = DATA_DIR / "briefs"
BRIEFS_INDEX = DATA_DIR / "briefs.json"

# Known market sector themes to highlight when relevant
KNOWN_SECTOR_THEMES = {
    "SEMICONDUCTORS": {
        "title": "Memory Chips & AI Hardware Supercycle",
        "tickers": ["MU", "NVDA", "AMAT", "LRCX", "KLAC", "AVGO", "INTC", "WDC", "MCHP", "TSM"],
        "thesis": "Soaring demand for High-Bandwidth Memory (HBM3e/HBM4) and AI server accelerators is driving pricing power and multi-quarter earnings surprises across memory chip makers and fab equipment suppliers."
    },
    "CLOUD_AI_INFRA": {
        "title": "AI Cloud & Data Center Scale-Out",
        "tickers": ["MSFT", "AMZN", "GOOGL", "META", "ORCL", "DELL", "HPE", "ANET"],
        "thesis": "Record hyper-scaler capex deployments into data center networking, custom silicon, and enterprise cloud AI workloads."
    },
    "CYBERSECURITY": {
        "title": "Mission-Critical Cloud Security",
        "tickers": ["CRWD", "FTNT", "PANW", "ZS", "OKTA"],
        "thesis": "Heightened cyber threat landscape and zero-trust adoption driving resilient software billings and high FCF conversion."
    },
    "CLEAN_ENERGY_GRID": {
        "title": "Grid Modernization & Clean Power",
        "tickers": ["GEV", "FSLR", "GNRC", "ETN", "PWR"],
        "thesis": "Massive electricity demand growth from AI data centers accelerating transmission upgrade spend and renewable power PPAs."
    }
}

def get_market_trends() -> list[dict[str, Any]]:
    """Return active global investment themes and affected tickers."""
    trends = []
    # Load watchlist to filter compliant tickers per theme
    watchlist_path = DATA_DIR / "watchlist.json"
    watchlist_pass = set()
    if watchlist_path.exists():
        try:
            wl = json.loads(watchlist_path.read_text())
            watchlist_pass = set(wl.get("pass", []))
        except Exception:
            pass

    for key, theme in KNOWN_SECTOR_THEMES.items():
        theme_tickers = theme["tickers"]
        pass_in_theme = [t for t in theme_tickers if not watchlist_pass or t in watchlist_pass]
        trends.append({
            "key": key,
            "title": theme["title"],
            "thesis": theme["thesis"],
            "halal_tickers": pass_in_theme,
            "total_tickers": len(theme_tickers)
        })
    return trends

def load_stock_context(ticker: str) -> dict[str, Any]:
    """Gather all available data for a ticker across Layers 1-3."""
    ticker = ticker.upper()
    context: dict[str, Any] = {
        "ticker": ticker,
        "compliance": None,
        "score": None,
        "catalysts": [],
        "themes": []
    }

    # 1. Compliance
    universe_path = DATA_DIR / "universe_screen.csv"
    if universe_path.exists():
        import csv
        with open(universe_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("symbol") == ticker or row.get("ticker") == ticker:
                    context["compliance"] = {
                        "verdict": row.get("verdict"),
                        "company_name": row.get("company_name"),
                        "sic": row.get("sic"),
                        "debt_ratio": row.get("debt_ratio"),
                        "cash_ratio": row.get("cash_ratio"),
                        "non_compliant_income": row.get("non_compliant_income_ratio"),
                        "cap_tier": row.get("cap_tier")
                    }
                    break

    # 2. Score
    scores_path = DATA_DIR / "scores.json"
    if scores_path.exists():
        try:
            scores_data = json.loads(scores_path.read_text())
            if ticker in scores_data:
                context["score"] = scores_data[ticker]
        except Exception:
            pass

    # 3. Catalysts
    cat_path = DATA_DIR / "catalysts.json"
    if cat_path.exists():
        try:
            cat_data = json.loads(cat_path.read_text())
            events = cat_data.get("events", [])
            context["catalysts"] = [e for e in events if e.get("ticker") == ticker]
        except Exception:
            pass

    # 4. Global Themes
    for theme in get_market_trends():
        if ticker in theme["halal_tickers"]:
            context["themes"].append(theme["title"])

    return context

def generate_llm_text(prompt: str) -> str:
    """Call Gemini or OpenAI API via direct HTTP request."""
    cfg = get_llm_config()
    if not cfg:
        raise ValueError("No LLM API Key configured. Please set GEMINI_API_KEY or OPENAI_API_KEY in .env.")

    provider, api_key = cfg

    if provider == "gemini":
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}]
        }
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 429:
            raise RuntimeError("Gemini API Quota Exceeded (429). Please check your API key quota at https://aistudio.google.com/")
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API error ({resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Unexpected Gemini API response structure: {data}")

    elif provider == "openai":
        url = "https://api.openai.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI API error ({resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError):
            raise RuntimeError(f"Unexpected OpenAI API response structure: {data}")

    raise ValueError(f"Unsupported LLM provider: {provider}")

def generate_fallback_brief(ticker: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Generate structured fallback brief if LLM key is missing."""
    comp = ctx.get("compliance") or {}
    score = ctx.get("score") or {}
    cats = ctx.get("catalysts") or []
    themes = ctx.get("themes") or []

    company_name = comp.get("company_name", ticker)
    verdict = comp.get("verdict", "PASS")
    composite_score = score.get("composite", 75.0) if isinstance(score, dict) else score

    cat_summary = f"{len(cats)} active SEC filings/insider buys" if cats else "No recent catalysts tagged"
    theme_summary = ", ".join(themes) if themes else "General Swing Setup"

    brief_markdown = f"""### {ticker} — {company_name}
**Verdict**: `{verdict}` | **Fundamental Rank Score**: `{composite_score}/100`  
**Sector Theme**: {theme_summary}

#### 1. Swing Trade Thesis
- Strong position in {theme_summary}.
- AAOIFI Compliant (Debt Ratio: {comp.get('debt_ratio', 'N/A')}, Liquid Assets: {comp.get('cash_ratio', 'N/A')}).
- Recent Catalyst Drivers: {cat_summary}.

#### 2. Bull Case
- Solid fundamental score of {composite_score} out of 100 within the Halal investable universe.
- Sector tailwinds supporting earnings trajectory for swing horizon.

#### 3. Risk & Thesis Falsification
- Re-check AAOIFI ratio limits before earnings filings.
- Monitor insider Form 4 transactions for selling pressure.
- Thesis invalidated if price breaks support or AAOIFI verdict shifts to FAIL.
"""

    return {
        "ticker": ticker,
        "generated_at": datetime.now().isoformat(),
        "is_fallback": True,
        "content_markdown": brief_markdown,
        "context_summary": {
            "verdict": verdict,
            "score": composite_score,
            "catalyst_count": len(cats),
            "themes": themes
        }
    }

def generate_trade_brief(ticker: str, force_refresh: bool = False) -> dict[str, Any]:
    """Generate or load cached AI swing trade brief for a ticker."""
    ticker = ticker.upper()
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    brief_file = BRIEFS_DIR / f"{ticker}.json"

    if not force_refresh and brief_file.exists():
        try:
            return json.loads(brief_file.read_text())
        except Exception:
            pass

    ctx = load_stock_context(ticker)

    # Check if LLM config is available
    llm_cfg = get_llm_config()
    if not llm_cfg:
        brief = generate_fallback_brief(ticker, ctx)
        brief_file.write_text(json.dumps(brief, indent=2))
        return brief

    # Build detailed LLM prompt
    prompt = f"""You are an expert equity research analyst and swing trader specializing in Shari'ah-compliant (AAOIFI) US equities.
Generate a concise, highly actionable Swing Trading Brief for **{ticker}**.

STOCK DATA:
- Ticker: {ticker}
- Company Name: {ctx.get('compliance', {}).get('company_name', ticker)}
- Compliance Status: {ctx.get('compliance', {}).get('verdict', 'PASS')} (Debt ratio: {ctx.get('compliance', {}).get('debt_ratio')}, Cash ratio: {ctx.get('compliance', {}).get('cash_ratio')})
- Fundamental Rank Score: {ctx.get('score')} / 100
- Sector Themes: {', '.join(ctx.get('themes', [])) or 'General Equities'}
- Recent SEC Catalysts / Insider Trades:
{json.dumps(ctx.get('catalysts', []), indent=2)}

INSTRUCTIONS:
Provide a clear markdown response with the following sections:
### 1. Executive Summary & Market Theme Alignment
Explain the core swing setup and how it aligns with macro trends (e.g. Memory Chips hike, AI infrastructure, Cloud security) if applicable.

### 2. Bull Case (3 Key Catalysts/Strengths)
Bullet points explaining why this stock is attractive for a swing trade.

### 3. Key Risks & Thesis Invalidation Triggers
What specific events, price actions, or ratio breaches would invalidate this trade thesis.

### 4. Halal Compliance & Ratio Safety Audit
Brief note confirming AAOIFI compliance status.
"""

    try:
        content = generate_llm_text(prompt)
        brief = {
            "ticker": ticker,
            "generated_at": datetime.now().isoformat(),
            "is_fallback": False,
            "content_markdown": content,
            "context_summary": {
                "verdict": ctx.get('compliance', {}).get('verdict'),
                "score": ctx.get('score'),
                "catalyst_count": len(ctx.get('catalysts', [])),
                "themes": ctx.get('themes')
            }
        }
    except Exception as err:
        # Fallback if API call fails
        brief = generate_fallback_brief(ticker, ctx)
        brief["error_note"] = f"LLM generation failed ({err}); served data fallback."

    brief_file.write_text(json.dumps(brief, indent=2))
    return brief
