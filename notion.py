"""
All Notion API operations, parameterised by user config.

Every function takes cfg (user dict from user_store) instead of reading global env vars.
"""

import base64, json, time, requests


def _headers(cfg: dict) -> dict:
    return {
        "Authorization": f"Bearer {cfg['notion_token']}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def _get_text(rich_text_list) -> str:
    return "".join(t.get("plain_text", "") for t in rich_text_list) if rich_text_list else ""


def _image_block(url_or_notion_id: str) -> dict:
    if url_or_notion_id.startswith("notion:"):
        fid = url_or_notion_id[7:]
        return {"object": "block", "type": "image",
                "image": {"type": "file_upload", "file_upload": {"id": fid}}}
    return {"object": "block", "type": "image",
            "image": {"type": "external", "external": {"url": url_or_notion_id}}}


# ── Collection items ──────────────────────────────────────────────────────────

def fetch_all_items(cfg: dict) -> list[dict]:
    """Fetch every item from the user's Collection DB."""
    db_id = cfg["collection_db_id"]
    headers = _headers(cfg)
    items = []
    cursor = None

    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor

        r = requests.post(
            f"https://api.notion.com/v1/databases/{db_id}/query",
            headers=headers, json=body, timeout=30,
        )
        r.raise_for_status()
        data = r.json()

        for page in data.get("results", []):
            p = page["properties"]

            # Support multiple possible title field names
            name = (
                _get_text(p.get("Name", {}).get("title", []))
                or _get_text(p.get("Second best", {}).get("title", []))
                or _get_text(p.get("Title", {}).get("title", []))
            )
            if not name:
                continue

            sku = _get_text(p.get("SKU", {}).get("rich_text", []))
            sku_parts = sku.split("-")
            sku_cat = sku_parts[1] if len(sku_parts) >= 2 else ""

            # Colour: relation in new template, rich_text in old schema
            colour_prop = p.get("Colour", {}) or {}
            if colour_prop.get("type") == "relation":
                colour_ids = [rel["id"] for rel in colour_prop.get("relation", [])]
                colour = ""  # resolved later by resolve_lookup_names
            else:
                colour_ids = []
                colour = (
                    _get_text(colour_prop.get("rich_text", []))
                    or _get_text(p.get("Primary Colour", {}).get("rich_text", []))
                )
            designer_ids = [r_["id"] for r_ in p.get("Designer", {}).get("relation", [])]

            price_prop = p.get("Purchase Price", {}) or p.get("Price (SGD)", {}) or {}
            price = None
            if price_prop.get("type") == "number":
                price = price_prop.get("number")

            fits_formula = (p.get("Fits", {}).get("formula") or {})
            fits = fits_formula.get("number") or 0

            items.append({
                "id": page["id"],
                "name": name,
                "sku": sku,
                "sku_cat": sku_cat,
                "colour": colour,
                "colour_ids": colour_ids,
                "designer_ids": designer_ids,
                "designer": "",          # resolved separately
                "price": price,
                "fits": fits,
            })

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        time.sleep(0.35)

    return items


def resolve_designer_names(cfg: dict, items: list[dict], known: dict | None = None) -> dict:
    """
    Resolve designer page IDs → names. Returns updated known dict.
    Modifies items in place to set item["designer"].
    """
    if known is None:
        known = {}
    all_ids = {did for item in items for did in item["designer_ids"]}
    unknown = all_ids - set(known)
    headers = _headers(cfg)

    for did in unknown:
        try:
            r = requests.get(f"https://api.notion.com/v1/pages/{did}", headers=headers, timeout=15)
            r.raise_for_status()
            props = r.json().get("properties", {})
            name_prop = next((v for v in props.values() if v.get("type") == "title"), None)
            if name_prop:
                known[did] = _get_text(name_prop.get("title", []))
            time.sleep(0.35)
        except Exception:
            pass

    for item in items:
        item["designer"] = ", ".join(known.get(did, "") for did in item["designer_ids"] if known.get(did))

    return known


def resolve_lookup_names(cfg: dict, items: list[dict], id_field: str,
                         name_field: str, known: dict | None = None) -> dict:
    """
    Generic resolver for relation lookup fields (Colour, Season, etc.).
    Reads page IDs from item[id_field], resolves to title strings,
    writes the joined result into item[name_field].
    Returns updated known dict {page_id: name}.
    """
    if known is None:
        known = {}
    all_ids = {pid for item in items for pid in item.get(id_field, [])}
    unknown = all_ids - set(known)
    headers = _headers(cfg)

    for pid in unknown:
        try:
            r = requests.get(f"https://api.notion.com/v1/pages/{pid}", headers=headers, timeout=15)
            r.raise_for_status()
            props = r.json().get("properties", {})
            name_prop = next((v for v in props.values() if v.get("type") == "title"), None)
            if name_prop:
                known[pid] = _get_text(name_prop.get("title", []))
            time.sleep(0.2)
        except Exception:
            pass

    for item in items:
        names = [known.get(pid, "") for pid in item.get(id_field, []) if known.get(pid)]
        if names:
            item[name_field] = ", ".join(names)

    return known


def create_item(cfg: dict, title: str, category_hint: str = "", colour: str = "", image_url: str | None = None) -> str:
    """
    Create a minimal wardrobe item entry in Notion.
    Returns the new page ID.
    """
    db_id = cfg["collection_db_id"]
    headers = _headers(cfg)

    title_key = cfg.get("title_property", "Name")

    properties = {
        title_key: {"title": [{"text": {"content": title}}]},
    }

    body: dict = {"parent": {"database_id": db_id}, "properties": properties}

    if image_url:
        body["children"] = [_image_block(image_url)]

    r = requests.post("https://api.notion.com/v1/pages", headers=headers, json=body, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


# ── OOTD entries ──────────────────────────────────────────────────────────────

def count_ootd_for_date(cfg: dict, date_str: str) -> int:
    db_id = cfg["ootd_db_id"]
    headers = _headers(cfg)
    r = requests.post(
        f"https://api.notion.com/v1/databases/{db_id}/query",
        headers=headers,
        json={"filter": {"property": "Worn", "date": {"equals": date_str}}, "page_size": 10},
        timeout=20,
    )
    if r.status_code != 200:
        return 0
    return len(r.json().get("results", []))


def create_ootd_entry(cfg: dict, date_str: str, item_ids: list[str],
                      image_urls: list[str] | None = None, season: str | None = None) -> str:
    db_id = cfg["ootd_db_id"]
    headers = _headers(cfg)

    existing = count_ootd_for_date(cfg, date_str)
    title = f"OOTD {date_str}" if existing == 0 else f"OOTD {date_str} ({existing + 1})"

    title_key = cfg.get("ootd_title_property", "Name")
    items_key = cfg.get("ootd_items_property", "Items")
    season_key = cfg.get("ootd_season_property", "Season")

    properties: dict = {
        title_key: {"title": [{"text": {"content": title}}]},
        "Worn": {"date": {"start": date_str}},
        items_key: {"relation": [{"id": pid} for pid in item_ids]},
    }
    if season in ("SS", "AW", "Year-round", "Resort"):
        properties[season_key] = {"select": {"name": season}}

    body: dict = {"parent": {"database_id": db_id}, "properties": properties}

    if image_urls:
        body["children"] = [_image_block(u) for u in image_urls]

    r = requests.post("https://api.notion.com/v1/pages", headers=headers, json=body, timeout=30)
    r.raise_for_status()
    page_id = r.json()["id"]

    # Set Style Tags relation if tag IDs are configured
    style_tag_ids = _resolve_style_tag_ids(cfg, season)
    if style_tag_ids:
        try:
            style_tags_key = cfg.get("ootd_style_tags_property", "Style Tags")
            requests.patch(
                f"https://api.notion.com/v1/pages/{page_id}",
                headers=headers,
                json={"properties": {style_tags_key: {"relation": [{"id": tid} for tid in style_tag_ids]}}},
                timeout=20,
            )
        except Exception:
            pass

    return page_id


def _resolve_style_tag_ids(cfg: dict, season: str | None) -> list[str]:
    """Return tag page IDs for the season, from user config."""
    if not season:
        return []
    key = f"style_tag_ss" if season == "SS" else f"style_tag_aw"
    tag_id = cfg.get(key, "")
    return [tag_id] if tag_id else []


def write_ootd_story(cfg: dict, page_id: str, story: str):
    """Write generated story text to the OOTD Story property."""
    headers = _headers(cfg)
    story_key = cfg.get("ootd_story_property", "OOTD Story")
    # Notion rich_text blocks max 2000 chars each
    chunks = [story[i:i+2000] for i in range(0, len(story), 2000)]
    rich_text = [{"type": "text", "text": {"content": chunk}} for chunk in chunks]
    r = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=headers,
        json={"properties": {story_key: {"rich_text": rich_text}}},
        timeout=20,
    )
    r.raise_for_status()


def fetch_ootd_entries(cfg: dict, limit: int = 500) -> list[dict]:
    """Fetch OOTD entries for analytics. Returns {id, date, item_ids}."""
    db_id = cfg["ootd_db_id"]
    headers = _headers(cfg)
    items_key = cfg.get("ootd_items_property", "Items")
    entries = []
    cursor = None
    fetched = 0

    while fetched < limit:
        body = {"page_size": min(100, limit - fetched), "sorts": [{"property": "Worn", "direction": "descending"}]}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(f"https://api.notion.com/v1/databases/{db_id}/query",
                          headers=headers, json=body, timeout=30)
        r.raise_for_status()
        data = r.json()

        for page in data.get("results", []):
            p = page["properties"]
            date_val = (p.get("Worn") or {}).get("date") or {}
            date_str = date_val.get("start", "")
            item_ids = [rel["id"] for rel in (p.get(items_key) or {}).get("relation", [])]
            entries.append({"id": page["id"], "date": date_str, "item_ids": item_ids})
            fetched += 1

        if not data.get("has_more") or fetched >= limit:
            break
        cursor = data.get("next_cursor")
        time.sleep(0.35)

    return entries


# ── Image hosting ─────────────────────────────────────────────────────────────

def host_image(cfg: dict, image_bytes: bytes, date_str: str = "", suffix: str = "") -> str | None:
    """Upload image: tries Notion Files API first, then GitHub, then freeimage.host."""
    if cfg.get("notion_token"):
        fid = _upload_notion_file(cfg["notion_token"], image_bytes, f"outfit{suffix}.jpg")
        if fid:
            return f"notion:{fid}"
    if cfg.get("github_token") and cfg.get("github_repo") and date_str:
        url = _upload_github(cfg, image_bytes, date_str, suffix)
        if url:
            return url
    return _upload_freeimage(image_bytes)


def _upload_notion_file(token: str, image_bytes: bytes, filename: str = "outfit.jpg") -> str | None:
    """Upload image via Notion Files API. Returns file_upload_id or None."""
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"}
    try:
        # Step 1: create upload session
        r = requests.post(
            "https://api.notion.com/v1/file_uploads",
            headers={**headers, "Content-Type": "application/json"},
            json={"name": filename},
            timeout=20,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        upload_url = data.get("upload_url")
        file_id = data.get("id")
        if not upload_url or not file_id:
            return None
        # Step 2: upload the bytes
        r2 = requests.post(
            upload_url,
            headers=headers,
            files={"file": (filename, image_bytes, "image/jpeg")},
            timeout=60,
        )
        if r2.status_code not in (200, 201):
            return None
        return file_id
    except Exception:
        return None


def _upload_github(cfg: dict, image_bytes: bytes, date_str: str, suffix: str) -> str | None:
    repo = cfg["github_repo"]
    token = cfg["github_token"]
    filename = f"ootd/{date_str}/outfit{suffix}.jpg"
    api_url = f"https://api.github.com/repos/{repo}/contents/{filename}"
    gh_h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    b64 = base64.b64encode(image_bytes).decode()
    existing = requests.get(api_url, headers=gh_h, timeout=15)
    body = {"message": f"outfit {date_str}", "content": b64}
    if existing.status_code == 200:
        body["sha"] = existing.json()["sha"]
    r = requests.put(api_url, headers=gh_h, json=body, timeout=30)
    if r.status_code in (200, 201):
        return f"https://raw.githubusercontent.com/{repo}/main/{filename}"
    return None


def _upload_freeimage(image_bytes: bytes) -> str | None:
    b64 = base64.b64encode(image_bytes).decode()
    r = requests.post(
        "https://freeimage.host/api/1/upload",
        data={"key": "6d207e02198a847aa98d0a2a901485a5", "source": b64, "format": "json"},
        timeout=30,
    )
    if r.status_code == 200:
        return r.json().get("image", {}).get("url")
    return None
