"""
Global bot configuration — read once from environment on startup.
Per-user credentials (Notion tokens, AI keys) live in user_store.py.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Required ──────────────────────────────────────────────────────────────────

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# ── Optional: bot-owner AI key (used when user hasn't set their own) ─────────

BOT_ANTHROPIC_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
BOT_OPENAI_KEY       = os.environ.get("OPENAI_API_KEY", "")
BOT_OPENROUTER_KEY   = os.environ.get("OPENROUTER_API_KEY", "")
BOT_KIMI_KEY         = os.environ.get("KIMI_API_KEY", "")

# Telegram user ID of the bot owner (can register other users via /admin_add)
BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0"))

# ── Runtime paths ─────────────────────────────────────────────────────────────

DATA_DIR      = Path(__file__).parent / "data"
CACHE_DIR     = DATA_DIR / "cache"
CORRECTIONS_DIR = DATA_DIR / "corrections"

DATA_DIR.mkdir(exist_ok=True)
CACHE_DIR.mkdir(exist_ok=True)
CORRECTIONS_DIR.mkdir(exist_ok=True)

# ── Tuning ────────────────────────────────────────────────────────────────────

CACHE_TTL_HOURS   = 12
ALBUM_COLLECT_SECS = 3      # seconds to wait for album siblings before processing
VISION_MAX_DIM    = 1600    # resize outfit photos before sending to vision model
