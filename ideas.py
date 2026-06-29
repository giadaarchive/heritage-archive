"""
Future ideas store for Heritage Archive.

Simple JSON file: data/ideas.json
Each idea has an id, text, and date added.
"""

import json
import time
from datetime import date
from pathlib import Path
from config import DATA_DIR

IDEAS_FILE = DATA_DIR / "ideas.json"


def _load() -> dict:
    if IDEAS_FILE.exists():
        try:
            return json.loads(IDEAS_FILE.read_text())
        except Exception:
            pass
    return {"ideas": [], "last_digest": None, "next_id": 1}


def _save(data: dict):
    IDEAS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def add(text: str) -> dict:
    """Add a new idea. Returns the saved idea dict."""
    data = _load()
    idea = {
        "id": data["next_id"],
        "text": text.strip(),
        "added": date.today().isoformat(),
    }
    data["ideas"].append(idea)
    data["next_id"] += 1
    _save(data)
    return idea


def remove(idea_id: int) -> bool:
    """Remove an idea by ID. Returns True if found and removed."""
    data = _load()
    before = len(data["ideas"])
    data["ideas"] = [i for i in data["ideas"] if i["id"] != idea_id]
    if len(data["ideas"]) < before:
        _save(data)
        return True
    return False


def all_ideas() -> list[dict]:
    return _load()["ideas"]


def format_list() -> str:
    """Format all ideas as a plain numbered list for Telegram."""
    ideas = all_ideas()
    if not ideas:
        return "No ideas saved yet. Add one with /idea <your idea>"
    lines = ["<b>Future ideas</b>\n"]
    for idea in ideas:
        lines.append(f"• [{idea['id']}] {idea['text']}  <i>({idea['added']})</i>")
    lines.append(f"\n<i>{len(ideas)} idea{'s' if len(ideas) != 1 else ''} total</i>")
    lines.append("To remove: /idea remove &lt;id&gt;")
    return "\n".join(lines)


