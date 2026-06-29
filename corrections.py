"""
Per-user corrections database (SQLite).

Each user gets: data/corrections/{user_id}.db

Two learning modes:
  1. Exact image hash match → skip AI, replay prior decisions
  2. Item type + colour → boost previously-approved items to top of candidates
"""

import sqlite3, hashlib, json
from pathlib import Path
from config import CORRECTIONS_DIR


def _db_path(user_id: int) -> Path:
    return CORRECTIONS_DIR / f"{user_id}.db"


def _conn(user_id: int):
    c = sqlite3.connect(_db_path(user_id))
    c.row_factory = sqlite3.Row
    return c


def init(user_id: int):
    with _conn(user_id) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS corrections (
                id                  INTEGER PRIMARY KEY,
                image_hash          TEXT NOT NULL,
                item_type           TEXT NOT NULL,
                item_colour         TEXT,
                visual_description  TEXT,
                ai_top_id           TEXT,
                ai_top_name         TEXT,
                correct_id          TEXT NOT NULL,
                correct_name        TEXT NOT NULL,
                wear_date           TEXT,
                ts                  REAL DEFAULT (unixepoch())
            );
            CREATE INDEX IF NOT EXISTS idx_type_colour ON corrections(item_type, item_colour);
            CREATE INDEX IF NOT EXISTS idx_image_hash  ON corrections(image_hash);

            CREATE TABLE IF NOT EXISTS image_sessions (
                image_hash  TEXT NOT NULL,
                wear_date   TEXT,
                decisions   TEXT NOT NULL,
                ts          REAL DEFAULT (unixepoch()),
                PRIMARY KEY (image_hash, wear_date)
            );
        """)


def image_hash(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


def save_decisions(user_id: int, img_hash: str, wear_date: str, decisions: list[dict]):
    init(user_id)
    session_decisions = []
    with _conn(user_id) as c:
        for d in decisions:
            c.execute("""
                INSERT INTO corrections
                    (image_hash, item_type, item_colour, visual_description,
                     ai_top_id, ai_top_name, correct_id, correct_name, wear_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                img_hash,
                d.get("item_type", ""),
                d.get("item_colour", ""),
                d.get("visual_description", ""),
                d.get("ai_top_id"),
                d.get("ai_top_name"),
                d["correct_id"],
                d["correct_name"],
                wear_date,
            ))
            session_decisions.append({
                "item_type": d.get("item_type", ""),
                "correct_id": d["correct_id"],
                "correct_name": d["correct_name"],
            })
        c.execute(
            "INSERT OR REPLACE INTO image_sessions (image_hash, wear_date, decisions) VALUES (?, ?, ?)",
            (img_hash, wear_date, json.dumps(session_decisions)),
        )


def lookup_image(user_id: int, img_hash: str) -> list[dict] | None:
    init(user_id)
    with _conn(user_id) as c:
        row = c.execute(
            "SELECT decisions FROM image_sessions WHERE image_hash = ? ORDER BY ts DESC LIMIT 1",
            (img_hash,)
        ).fetchone()
    return json.loads(row["decisions"]) if row else None


def lookup_type_colour(user_id: int, item_type: str, item_colour: str, limit: int = 5) -> list[dict]:
    init(user_id)
    tl = item_type.lower()
    cl = item_colour.lower() if item_colour else ""
    with _conn(user_id) as c:
        rows = c.execute("""
            SELECT correct_id, correct_name, COUNT(*) as cnt
            FROM corrections
            WHERE lower(item_type) LIKE ? AND lower(item_colour) LIKE ?
            GROUP BY correct_id ORDER BY cnt DESC LIMIT ?
        """, (f"%{tl}%", f"%{cl}%", limit)).fetchall()
        if not rows and tl:
            rows = c.execute("""
                SELECT correct_id, correct_name, COUNT(*) as cnt
                FROM corrections WHERE lower(item_type) LIKE ?
                GROUP BY correct_id ORDER BY cnt DESC LIMIT ?
            """, (f"%{tl}%", limit)).fetchall()
    return [{"correct_id": r["correct_id"], "correct_name": r["correct_name"], "count": r["cnt"]} for r in rows]


def stats(user_id: int) -> dict:
    init(user_id)
    with _conn(user_id) as c:
        total    = c.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
        images   = c.execute("SELECT COUNT(*) FROM image_sessions").fetchone()[0]
        corrected = c.execute(
            "SELECT COUNT(*) FROM corrections WHERE ai_top_id != correct_id AND ai_top_id IS NOT NULL"
        ).fetchone()[0]
    return {"total_decisions": total, "unique_outfits": images, "corrections_made": corrected}
