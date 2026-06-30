"""
Per-user collection cache.

Each user gets their own cache file: data/cache/{user_id}.json
Cache is refreshed if older than CACHE_TTL_HOURS (default 12h).
"""

import json, time, threading
from pathlib import Path
from config import CACHE_DIR, CACHE_TTL_HOURS
import notion

_refresh_locks: dict[str, threading.Lock] = {}
_refresh_in_progress: set[str] = set()

# SKU category code → clothing type labels (for matching AI descriptions)
SKU_CAT_LABELS = {
    "TOP": ["top", "t-shirt", "tee", "shirt", "blouse", "tank", "camisole"],
    "SHR": ["shirt", "button-down", "blouse", "top"],
    "KNT": ["knit", "sweater", "jumper", "pullover", "knitwear", "cardigan"],
    "TRS": ["trousers", "pants", "jeans", "slacks", "chinos", "wide-leg"],
    "SKT": ["skirt", "midi skirt", "mini skirt", "maxi skirt"],
    "DRS": ["dress", "gown", "shift", "wrap dress", "midi dress"],
    "OTW": ["jacket", "blazer", "coat", "outerwear", "cardigan", "bomber", "overcoat", "trench"],
    "SHO": ["shoes", "boots", "heels", "loafers", "sneakers", "flats", "mules", "sandals"],
    "BAG": ["bag", "purse", "tote", "clutch", "handbag", "shoulder bag", "crossbody"],
    "SCF": ["scarf", "silk scarf", "neckerchief"],
    "ACC": ["jewelry", "necklace", "earrings", "bracelet", "ring", "belt", "hat", "accessory"],
    "JMP": ["jumpsuit", "playsuit", "romper", "overalls"],
}


def _cache_file(user_id: int) -> Path:
    return CACHE_DIR / f"{user_id}.json"


def _designer_cache_file(user_id: int) -> Path:
    return CACHE_DIR / f"{user_id}_designers.json"


def _lookup_cache_file(user_id: int, name: str) -> Path:
    return CACHE_DIR / f"{user_id}_{name}.json"


def _get_lock(user_id: int) -> threading.Lock:
    key = str(user_id)
    if key not in _refresh_locks:
        _refresh_locks[key] = threading.Lock()
    return _refresh_locks[key]


def refresh(cfg: dict, user_id: int) -> list[dict]:
    """Fetch from Notion, resolve designer names, write cache. Returns item list."""
    print(f"[cache] refreshing for user {user_id}...", flush=True)
    items = notion.fetch_all_items(cfg)

    # Load known designer mappings
    designer_file = _designer_cache_file(user_id)
    known = {}
    if designer_file.exists():
        try:
            known = json.loads(designer_file.read_text())
        except Exception:
            pass

    known = notion.resolve_designer_names(cfg, items, known)
    designer_file.write_text(json.dumps(known, ensure_ascii=False))

    # Resolve colour relation IDs → colour name strings (new template schema)
    colour_file = _lookup_cache_file(user_id, "colours")
    known_colours = {}
    if colour_file.exists():
        try:
            known_colours = json.loads(colour_file.read_text())
        except Exception:
            pass
    known_colours = notion.resolve_lookup_names(cfg, items, "colour_ids", "colour", known_colours)
    colour_file.write_text(json.dumps(known_colours, ensure_ascii=False))

    # Build wear-frequency map from OOTD history
    try:
        ootd_entries = notion.fetch_ootd_entries(cfg, limit=200)
        wear_counts: dict[str, int] = {}
        for entry in ootd_entries:
            for iid in entry.get("item_ids", []):
                # Notion IDs may come with or without dashes — normalise
                iid_norm = iid.replace("-", "")
                wear_counts[iid_norm] = wear_counts.get(iid_norm, 0) + 1
        for item in items:
            iid_norm = item["id"].replace("-", "")
            item["recent_wears"] = wear_counts.get(iid_norm, 0)
        print(f"[cache] user {user_id}: wear counts from {len(ootd_entries)} OOTDs", flush=True)
    except Exception as e:
        print(f"[cache] ootd history fetch failed: {e}", flush=True)
        for item in items:
            item["recent_wears"] = 0

    cache = {"fetched_at": time.time(), "items": items}
    _cache_file(user_id).write_text(json.dumps(cache, ensure_ascii=False))
    _refresh_in_progress.discard(str(user_id))
    print(f"[cache] user {user_id}: {len(items)} items cached", flush=True)
    return items


def _background_refresh(cfg: dict, user_id: int):
    key = str(user_id)
    with _get_lock(user_id):
        if key in _refresh_in_progress:
            return
        _refresh_in_progress.add(key)
    t = threading.Thread(target=refresh, args=(cfg, user_id), daemon=True)
    t.start()


def load(cfg: dict, user_id: int, force: bool = False) -> list[dict]:
    """
    Load user's catalog. Returns immediately with stale data if cache exists,
    triggers background refresh. Blocks on first load.
    """
    if force:
        return refresh(cfg, user_id)

    f = _cache_file(user_id)
    if f.exists():
        try:
            cache = json.loads(f.read_text())
            items = cache.get("items", [])
            age_h = (time.time() - cache.get("fetched_at", 0)) / 3600
            if age_h < CACHE_TTL_HOURS:
                return items
            _background_refresh(cfg, user_id)
            return items
        except Exception:
            pass

    return refresh(cfg, user_id)


def search(query_type: str, query_colour: str, items: list[dict], max_results: int = 40) -> list[dict]:
    """
    Score catalog items by type/colour match and wear history.
    Falls back to the full catalog sorted by wear frequency when keyword
    scoring is thin — ensures items without SKUs always reach the AI.
    """
    qt = query_type.lower()
    qc = query_colour.lower()

    matching_cats = set()
    for cat, labels in SKU_CAT_LABELS.items():
        if any(label in qt for label in labels):
            matching_cats.add(cat)
        if any(word in label for word in qt.split() for label in labels):
            matching_cats.add(cat)

    scored = []
    for item in items:
        score = 0
        name_l = item["name"].lower()
        colour_l = item.get("colour", "").lower()

        # SKU category match
        if item.get("sku_cat") in matching_cats:
            score += 3

        # Type keywords in item name
        for word in qt.split():
            if len(word) > 3 and word in name_l:
                score += 2

        # Colour keywords
        for word in qc.split():
            if len(word) > 3 and word in colour_l:
                score += 2
            if len(word) > 3 and word in name_l:
                score += 1

        # Wear-history bonus (capped at 4 points)
        score += min(item.get("recent_wears", 0), 4)

        scored.append((score, item))

    # Sort by score descending
    scored.sort(key=lambda x: -x[0])

    # Always send at least half max_results to the AI even when keyword score = 0
    # so items without SKUs are never silently dropped.
    strong = [(s, it) for s, it in scored if s > 0]
    if len(strong) < max_results // 2:
        # Pad with most-worn items not already in strong
        strong_ids = {it["id"] for _, it in strong}
        pad = [(s, it) for s, it in scored if it["id"] not in strong_ids]
        strong = strong + pad

    return [item for _, item in strong[:max_results]]
