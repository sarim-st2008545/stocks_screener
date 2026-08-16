"""
Interactive Telegram Bot listener and notification manager.

Responds to commands:
  /start, /help    - Welcome message and list of commands
  /screen TICKER   - Halal AAOIFI ratio check
  /top             - Top ranked fundamental Halal stocks
  /catalysts       - Form 4 insider buying and 8-K filings
  /trends          - Global investment trends (Memory Chips, AI, etc.)
  /brief TICKER    - AI swing trade brief
"""

from __future__ import annotations

import json
import time
import requests
from typing import Any

from config_loader import get_telegram_config
from universe import DATA_DIR
from briefs import get_market_trends, generate_trade_brief, load_stock_context

def send_telegram_message(text: str) -> bool:
    """Send text message to configured Telegram chat."""
    cfg = get_telegram_config()
    if not cfg:
        print("[Telegram] Unconfigured - set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return False
    token, chat_id = cfg
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"[Telegram] Failed to send: {e}")
        return False

def handle_telegram_command(text: str) -> str:
    """Process incoming command and return Markdown response."""
    parts = text.strip().split()
    if not parts:
        return "Please send a valid command. Type /help for options."

    cmd = parts[0].lower()
    arg = parts[1].upper() if len(parts) > 1 else ""

    if cmd in ("/start", "/help"):
        return (
            "🤖 *Halal Swing Trading Assistant Bot*\n\n"
            "Commands:\n"
            "• `/screen TICKER` — AAOIFI Halal compliance & ratio audit\n"
            "• `/top` — Top ranked fundamental Halal swing stocks\n"
            "• `/catalysts` — Form 4 insider buying & 8-K filings\n"
            "• `/trends` — Global sector hikes (Memory Chips, AI, etc.)\n"
            "• `/brief TICKER` — AI swing trade brief & bull/bear analysis\n"
        )

    elif cmd == "/screen":
        if not arg:
            return "Usage: `/screen TICKER` (e.g. `/screen AAPL`)"
        ctx = load_stock_context(arg)
        comp = ctx.get("compliance")
        if not comp:
            return f"❌ Symbol *{arg}* not found in universe screen. Run `/screen` after universe sweep."
        
        verdict_icon = "✅" if comp.get("verdict") == "PASS" else "⚠️" if comp.get("verdict") == "REVIEW" else "❌"
        return (
            f"{verdict_icon} *{arg} — {comp.get('company_name', arg)}*\n"
            f"Verdict: `{comp.get('verdict')}` | Cap Tier: `{comp.get('cap_tier')}`\n\n"
            f"📊 *AAOIFI Ratios*:\n"
            f"• Debt / Cap: `{comp.get('debt_ratio', 'N/A')}` (Limit < 30%)\n"
            f"• Cash / Cap: `{comp.get('cash_ratio', 'N/A')}` (Limit < 30%)\n"
            f"• Impure Income: `{comp.get('non_compliant_income', 'N/A')}` (Limit < 5%)\n"
        )

    elif cmd == "/top":
        scores_path = DATA_DIR / "scores.json"
        if not scores_path.exists():
            return "No scoring data available. Run fundamental scoring first."
        try:
            scores_data = json.loads(scores_path.read_text())
            top_names = sorted(scores_data.items(), key=lambda x: x[1].get("composite", 0), reverse=True)[:10]
            lines = ["⭐ *Top 10 Halal Fundamental Stocks*\n"]
            for idx, (t, s) in enumerate(top_names, 1):
                comp_val = s.get("composite", 0)
                lines.append(f"{idx}. *{t}* — Score: `{comp_val:.1f}`/100")
            return "\n".join(lines)
        except Exception as e:
            return f"Error loading scores: {e}"

    elif cmd == "/catalysts":
        cat_path = DATA_DIR / "catalysts.json"
        if not cat_path.exists():
            return "No active catalysts found."
        try:
            cat_data = json.loads(cat_path.read_text())
            events = cat_data.get("events", [])[:8]
            if not events:
                return "No recent catalysts recorded."
            lines = ["🔥 *Active Watchlist Catalysts*\n"]
            for ev in events:
                kind = ev.get("kind", "").upper()
                t = ev.get("ticker")
                hl = ev.get("headline")
                sc = ev.get("score")
                lines.append(f"• *{t}* (`{kind}`) Score: `{sc}`\n  {hl}")
            return "\n\n".join(lines)
        except Exception as e:
            return f"Error loading catalysts: {e}"

    elif cmd == "/trends":
        trends = get_market_trends()
        lines = ["📈 *Global Investment Sector Trends*\n"]
        for tr in trends:
            lines.append(
                f"🔥 *{tr['title']}*\n"
                f"{tr['thesis']}\n"
                f"Halal Tickers: {', '.join(tr['halal_tickers'])}\n"
            )
        return "\n".join(lines)

    elif cmd == "/brief":
        if not arg:
            return "Usage: `/brief TICKER` (e.g. `/brief MU`)"
        send_telegram_message(f"⌛ Generating AI Swing Brief for *{arg}*...")
        brief = generate_trade_brief(arg)
        content = brief.get("content_markdown", "")
        # Truncate if long for Telegram (4000 char max)
        if len(content) > 3800:
            content = content[:3800] + "\n\n*(Truncated due to length)*"
        return content

    return "Unknown command. Type /help for available commands."

def poll_telegram_updates(once: bool = False):
    """Poll Telegram getUpdates and answer commands."""
    cfg = get_telegram_config()
    if not cfg:
        print("[Telegram Listener] Disabled — missing bot token / chat id.")
        return

    token, allowed_chat = cfg
    offset = 0
    url = f"https://api.telegram.org/bot{token}/getUpdates"

    print(f"[Telegram Listener] Polling for updates (Chat ID: {allowed_chat})...")
    
    while True:
        try:
            resp = requests.get(url, params={"offset": offset, "timeout": 5}, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for update in data.get("result", []):
                    offset = max(offset, update["update_id"] + 1)
                    msg = update.get("message", {})
                    chat_id = str(msg.get("chat", {}).get("id", ""))
                    text = msg.get("text", "")
                    
                    # Security check: only respond to owner's chat id if specified
                    if allowed_chat and chat_id != allowed_chat:
                        print(f"Ignored message from unauthorized chat: {chat_id}")
                        continue
                        
                    if text.startswith("/"):
                        print(f"[Telegram Cmd] Received: {text}")
                        reply = handle_telegram_command(text)
                        send_telegram_message(reply)

        except Exception as e:
            print(f"[Telegram Poll Error]: {e}")

        if once:
            break
        time.sleep(2)

if __name__ == "__main__":
    poll_telegram_updates(once=True)
