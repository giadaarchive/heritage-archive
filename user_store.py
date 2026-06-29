"""
Per-user credential store.

Each registered user has:
  notion_token       — Notion integration token (secret_...)
  collection_db_id   — Notion wardrobe items database ID
  ootd_db_id         — Notion OOTD/lookbook database ID
  ai_provider        — "anthropic" | "openai" | "openrouter"
  ai_key             — their personal API key for that provider (optional if bot owner supplies one)
  github_token       — GitHub PAT for image hosting (optional)
  github_repo        — "owner/repo" for collection images (optional)
  always_worn        — list of {id, name} items added to every OOTD
  registered_at      — ISO date string

Stored in data/users.json. Bot-owner is the only admin.
"""

import json
from pathlib import Path
from config import DATA_DIR

USERS_FILE = DATA_DIR / "users.json"


def _load() -> dict:
    if USERS_FILE.exists():
        try:
            return json.loads(USERS_FILE.read_text())
        except Exception:
            return {}
    return {}


def _save(users: dict):
    USERS_FILE.write_text(json.dumps(users, indent=2, ensure_ascii=False))


def get(user_id: int) -> dict | None:
    return _load().get(str(user_id))


def save(user_id: int, cfg: dict):
    users = _load()
    users[str(user_id)] = cfg
    _save(users)


def update(user_id: int, **kwargs):
    """Update individual fields without overwriting the whole record."""
    users = _load()
    key = str(user_id)
    if key not in users:
        users[key] = {}
    users[key].update(kwargs)
    _save(users)


def is_registered(user_id: int) -> bool:
    cfg = get(user_id)
    if not cfg:
        return False
    return bool(cfg.get("notion_token") and cfg.get("collection_db_id") and cfg.get("ootd_db_id"))


def all_users() -> dict:
    return _load()


# ── Always-worn helpers ───────────────────────────────────────────────────────

def get_always_worn(user_id: int) -> list[dict]:
    cfg = get(user_id) or {}
    return cfg.get("always_worn", [])


def set_always_worn(user_id: int, items: list[dict]):
    update(user_id, always_worn=items)


# ── Registration state machine ────────────────────────────────────────────────
# Tracks where each user is in the /register wizard.
# user_id → {"step": str, "partial": dict}

_reg_state: dict[int, dict] = {}

REG_STEPS = [
    "notion_token",
    "collection_db_id",
    "ootd_db_id",
    "ai_provider",   # shown as inline buttons
    "ai_key",
    "github",        # shown as inline buttons (yes/skip)
    "github_token",
    "github_repo",
    "done",
]


def reg_get(user_id: int) -> dict | None:
    return _reg_state.get(user_id)


def reg_set(user_id: int, step: str, partial: dict | None = None):
    _reg_state[user_id] = {"step": step, "partial": partial or {}}


def reg_clear(user_id: int):
    _reg_state.pop(user_id, None)
