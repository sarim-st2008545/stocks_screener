"""
Configuration and Environment loader for the Halal Stock Screener.
Loads variables from .env, config.json, or system environment.
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Automatically load .env file from project root
BASE_DIR = Path(__file__).parent
ENV_FILE = BASE_DIR / ".env"
CONFIG_FILE = BASE_DIR / "config.json"

if ENV_FILE.exists():
    load_dotenv(ENV_FILE)

def get_config(key: str, default: str | None = None) -> str | None:
    """Retrieve config value checking OS environment first, then config.json."""
    val = os.environ.get(key)
    if val:
        return val.strip()
    
    # Try lowercase/uppercase in config.json
    if CONFIG_FILE.exists():
        try:
            cfg = json.loads(CONFIG_FILE.read_text())
            if key in cfg and cfg[key]:
                return str(cfg[key]).strip()
            key_lower = key.lower()
            if key_lower in cfg and cfg[key_lower]:
                return str(cfg[key_lower]).strip()
        except Exception:
            pass
            
    return default

def get_telegram_config() -> tuple[str, str] | None:
    """Get Telegram Bot Token and Chat ID."""
    token = get_config("TELEGRAM_BOT_TOKEN")
    chat = get_config("TELEGRAM_CHAT_ID")
    if token and chat:
        return token, chat
    return None

def get_llm_config() -> tuple[str, str] | None:
    """
    Get (provider, api_key) for LLM generation.
    Supports GEMINI_API_KEY and OPENAI_API_KEY.
    """
    provider = (get_config("LLM_PROVIDER") or "auto").lower()
    gemini_key = get_config("GEMINI_API_KEY")
    openai_key = get_config("OPENAI_API_KEY")

    if provider == "gemini" and gemini_key:
        return "gemini", gemini_key
    elif provider == "openai" and openai_key:
        return "openai", openai_key
    
    # Auto-detect
    if gemini_key:
        return "gemini", gemini_key
    if openai_key:
        return "openai", openai_key
        
    return None
