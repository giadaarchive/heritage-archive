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
        ootd_entries = notion.fetch_ootd_entries(cfg, limit=700)
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


# Synonym expansions for type matching — maps vision words → catalog SKU label words
_TYPE_SYNONYMS = {
    "crew neck": ["knit", "sweater", "pullover"],
    "crewneck": ["knit", "sweater", "pullover"],
    "t-shirt": ["top", "tee"],
    "tee": ["top", "t-shirt"],
    "slacks": ["trousers", "pants"],
    "blazer": ["jacket"],
    "coat": ["jacket", "outerwear"],
    "loafers": ["shoes"],
    "heels": ["shoes"],
    "mules": ["shoes"],
    "sandals": ["shoes"],
    "tote": ["bag"],
    "clutch": ["bag"],
    "crossbody": ["bag"],
}


def search(query_type: str, query_colour: str, items: list[dict], max_results: int = 50) -> list[dict]:
    """
    Score catalog items by type/colour match and wear history.
    Always surfaces top-worn items from matching categories so the AI
    always has strong historical candidates to rank.
    """
    qt = query_type.lower()
    qc = query_colour.lower()

    # Expand query with synonyms
    expanded_words = set(qt.split())
    for phrase, syns in _TYPE_SYNONYMS.items():
        if phrase in qt:
            expanded_words.update(syns)

    matching_cats = set()
    for cat, labels in SKU_CAT_LABELS.items():
        if any(label in qt for label in labels):
            matching_cats.add(cat)
        if any(word in label for word in expanded_words for label in labels):
            matching_cats.add(cat)

    scored = []
    for item in items:
        score = 0
        name_l = item["name"].lower()
        colour_l = item.get("colour", "").lower()
        wears = item.get("recent_wears", 0)

        # SKU category match — strong signal
        if item.get("sku_cat") in matching_cats:
            score += 5

        # Type keywords (expanded) in item name
        for word in expanded_words:
            if len(word) > 3 and word in name_l:
                score += 3

        # Colour match
        for word in qc.split():
            if len(word) > 3 and word in colour_l:
                score += 2
            if len(word) > 3 and word in name_l:
                score += 1

        # Wear-history: heavily weighted — worn items are almost certainly in the collection
        score += min(wears, 10)  # up to 10 points (was capped at 4)

        scored.append((score, item))

    scored.sort(key=lambda x: -x[0])

    # Always guarantee the top 15 most-worn items from matching categories are included
    # so historically frequent items are never absent from the AI's candidate list.
    top_worn_in_cat = sorted(
        [it for it in items if it.get("sku_cat") in matching_cats],
        key=lambda it: -it.get("recent_wears", 0),
    )[:15]

    result_ids: dict[str, dict] = {}
    for _, it in scored[:max_results]:
        result_ids[it["id"]] = it
    for it in top_worn_in_cat:
        if it["id"] not in result_ids:
            result_ids[it["id"]] = it

    # Re-sort final pool by score so AI sees best candidates first
    id_to_score = {it["id"]: s for s, it in scored}
    final = sorted(result_ids.values(), key=lambda it: -id_to_score.get(it["id"], 0))
    return final[:max_results]
