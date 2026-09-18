"""
Telegram Notification Engine for Aura Quant Trading Signals.

Formats and dispatches high-conviction trade setups to Telegram:
- Galaxy v2 (3-4 Day Mean Reversion)
- Universe AI Swing (2-5 Week Stage 2 Pullbacks)
- Market Open Morning Briefing
"""

from __future__ import annotations

import os
import requests
from typing import Any, Optional
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
PORTAL_URL = os.getenv("PORTAL_URL", "https://aura-quant-k37x.onrender.com").rstrip("/")


def send_telegram_message(
    text: str,
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    parse_mode: str = "Markdown",
    disable_web_page_preview: bool = True
) -> bool:
    """Dispatches a Markdown-formatted message to Telegram."""
    token = bot_token or TELEGRAM_BOT_TOKEN
    cid = chat_id or TELEGRAM_CHAT_ID

    if not token or not cid:
        print("  ⚠️ Telegram notification skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": cid,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": disable_web_page_preview
    }

    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200 and res.json().get("ok"):
            return True
        else:
            print(f"  ❌ Telegram API error ({res.status_code}): {res.text}")
            return False
    except Exception as e:
        print(f"  ❌ Telegram request failed: {e}")
        return False


def format_signal_alert(sig: dict[str, Any], system: str = "galaxy") -> str:
    """Formats an individual trading setup into an institutional alert card."""
    ticker = sig.get("ticker", "UNKNOWN")
    price = sig.get("price", 0.0)
    stop = sig.get("stop", 0.0)
    target = sig.get("target") or sig.get("t1", 0.0)
    stop_pct = sig.get("stop_pct", 0.0)
    target_pct = sig.get("target_pct") or sig.get("t1_pct", 0.0)
    signal_type = sig.get("signal", "Algorithmic Setup")
    segment = sig.get("segment", "")
    rr = sig.get("rr", 0.0)
    if not rr and stop_pct > 0:
        rr = target_pct / stop_pct

    is_galaxy = system.lower() == "galaxy"
    sys_tag = "GALAXY v2 (3-4D)" if is_galaxy else "UNIVERSE SWING (2-5W)"
    sys_icon = "⚡" if is_galaxy else "🚀"

    lines = [
        f"{sys_icon} *AURA QUANT SETUP ALERT*",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"*{ticker}* • `{sys_tag}`",
    ]
    if segment:
        lines.append(f"Sector: _{segment}_")

    lines.extend([
        f"Signal: *{signal_type}*",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"💵 *Entry:* `${price:.2f}` (Market Open)",
        f"🎯 *Target:* `${target:.2f}` (`+{target_pct:.1f}%`)",
        f"🛑 *Stop Loss:* `${stop:.2f}` (`-{stop_pct:.1f}%`)",
        f"⚖️ *R:R Ratio:* `1 : {rr:.1f}`",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"📱 [Open Portal to Log Trade]({PORTAL_URL})"
    ])
    return "\n".join(lines)


def notify_scanner_results(
    galaxy_setups: list[dict[str, Any]],
    universe_setups: list[dict[str, Any]],
    scan_date: str
) -> int:
    """Sends setup alerts for newly detected setups."""
    total_sent = 0
    total_setups = len(galaxy_setups) + len(universe_setups)

    if total_setups == 0:
        summary = (
            f"📡 *Aura Quant Daily Scan Completed*\n"
            f"📅 Date: `{scan_date}`\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚪ *No setups triggered today.* Discipline is edge.\n"
            f"Cash preserved for high-probability market entries."
        )
        if send_telegram_message(summary):
            return 1
        return 0

    # Summary header
    header = (
        f"🎯 *AURA QUANT SCANNER REPORT*\n"
        f"📅 Date: `{scan_date}`\n"
        f"🔥 *{total_setups} High-Conviction Setup(s) Triggered*\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )
    send_telegram_message(header)

    # Individual setup cards
    for s in galaxy_setups:
        card = format_signal_alert(s, system="galaxy")
        if send_telegram_message(card):
            total_sent += 1

    for s in universe_setups:
        card = format_signal_alert(s, system="universe")
        if send_telegram_message(card):
            total_sent += 1

    return total_sent
