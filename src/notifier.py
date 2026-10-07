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

# Load environment variables from .env if python-dotenv is installed, or parse manually
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    env_file = Path(__file__).parent.parent / ".env"
    if env_file.exists():
        with open(env_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip("'\"")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8598127712:AAHDz8HNum6QT9G9aA4OOvW050TeWO_0u8Y")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "5847323936")
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


def format_signal_alert(sig: dict[str, Any], system: str = "galaxy", scan_date: str = "") -> str:
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

    # Layer 2 Priority resolution
    meta = sig.get("metadata")
    if isinstance(meta, str):
        try:
            import json
            meta = json.loads(meta)
        except Exception:
            meta = {}
    elif not isinstance(meta, dict):
        meta = {}

    priority_rank = sig.get("priority_rank") or meta.get("priority_rank")
    rs_63 = sig.get("rs_63") if sig.get("rs_63") is not None else meta.get("rs_63")
    bench = sig.get("bench") or meta.get("bench")

    lines = [
        f"{sys_icon} *AURA QUANT SETUP ALERT*",
        f"━━━━━━━━━━━━━━━━━━━━━",
        f"*{ticker}* • `{sys_tag}`",
    ]

    if not is_galaxy and priority_rank:
        if priority_rank == 1:
            lines.append("⭐ *PRIORITY #1 • HIGH ALPHA LEADER*")
        elif priority_rank == 2:
            lines.append("🔹 *PRIORITY #2*")
        else:
            lines.append(f"▫️ *PRIORITY #{priority_rank}*")

    if scan_date:
        lines.append(f"📅 *Date:* `{scan_date}`")
    if segment:
        lines.append(f"Sector: _{segment}_")

    if not is_galaxy and rs_63 is not None:
        bench_str = f" vs {bench}" if bench else ""
        rs_float = float(rs_63)
        rs_sign = "+" if rs_float >= 0 else ""
        lines.append(f"📊 *Relative Strength:* `{rs_sign}{rs_float:.1f}%{bench_str}`")

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

    # Individual setup cards - Galaxy first
    for s in galaxy_setups:
        card = format_signal_alert(s, system="galaxy", scan_date=scan_date)
        if send_telegram_message(card):
            total_sent += 1

    # Universe setups sorted by Priority #1 first
    def _u_rank(x):
        r = x.get("priority_rank")
        if r is None and isinstance(x.get("metadata"), dict):
            r = x["metadata"].get("priority_rank")
        rs = x.get("rs_63") or 0.0
        return (0 if r == 1 else (r if r is not None else 999), -float(rs))

    sorted_universe = sorted(universe_setups, key=_u_rank)
    for s in sorted_universe:
        card = format_signal_alert(s, system="universe", scan_date=scan_date)
        if send_telegram_message(card):
            total_sent += 1

    return total_sent
