"""
Two-step AI outfit matching.

Step 1 — identify_items: vision model identifies every visible garment.
Step 2 — match_item: text model ranks catalog candidates for each identified item.

Uses ai_client for provider-agnostic calls (Anthropic / OpenAI / OpenRouter).
"""

import json, re
import ai_client
import catalog as catalog_mod
import corrections

IDENTIFY_SYSTEM = """\
You are an expert fashion stylist. Analyze this outfit photo and identify every visible clothing item and accessory the person is wearing.

For each item return:
- type: the specific garment type (e.g. "blazer", "straight-leg trousers", "leather tote bag", "silk scarf", "ankle boots")
- colour: primary colour description (e.g. "navy", "camel", "off-white", "dark wash")
- description: one precise sentence describing the item (silhouette, material, key details)

Also classify the overall outfit season:
- "SS" (Spring/Summer): lighter fabrics, bare skin, brighter or pastel tones, fewer layers, linen/cotton/silk
- "AW" (Autumn/Winter): heavier fabrics, knitwear, outerwear, darker tones, more layers, wool/cashmere/leather

Return a JSON object with keys "season" and "items". No markdown, no explanation.
Example:
{"season": "AW", "items": [
  {"type": "double-breasted blazer", "colour": "navy", "description": "Navy wool double-breasted blazer with peak lapels"},
  {"type": "straight-leg trousers", "colour": "ivory", "description": "Ivory wool straight-leg trousers with a high waist"}
]}
"""

MATCH_SYSTEM = """\
You are a luxury wardrobe curator. Match a described garment to the closest items in the user's personal collection.

Rules:
- Items with higher "worn" counts are much more likely to be the correct match — always rank them higher
- Match on garment type first, then colour, then material, then brand
- Treat these as equivalent: crew neck = round neck = knitwear = pullover = sweater; \
cotton = lightweight knit; trousers = pants = slacks; jacket = blazer; tote = bag
- A partial brand or name match is very strong evidence even if description differs
- ALWAYS return the top 3 candidates — never return an empty list. \
If uncertain, still rank them and give honest confidence scores (minimum 0.15)

Return ONLY a raw JSON object — no markdown, no code fences.
{"matches": [
  {"candidate_index": 0, "confidence": 0.92, "reasoning": "Kiton knit pullover, white, high-wear item"},
  {"candidate_index": 2, "confidence": 0.35, "reasoning": "Also a white knit but less worn"}
]}
"""


def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```\s*$', '', raw)
    return json.loads(raw.strip())


def _format_candidates(candidates: list[dict]) -> str:
    lines = []
    for i, c in enumerate(candidates):
        designer = c.get("designer", "")
        colour = c.get("colour", "")
        sku = c.get("sku", "")
        wears = c.get("recent_wears", 0)
        worn_tag = f" | worn {wears}x" if wears else ""
        lines.append(f"{i}. {c['name']} | {designer} | {colour} | {sku}{worn_tag}")
    return "\n".join(lines)


def identify_items(cfg: dict, image_b64: str) -> tuple[list[dict], str]:
    """
    Step 1: vision model identifies all visible items and classifies season.
    Returns (items, season).
    """
    try:
        raw = ai_client.vision_call(
            cfg,
            system=IDENTIFY_SYSTEM,
            user_text="Identify every item in this outfit photo and classify the season.",
            image_b64=image_b64,
            max_tokens=1000,
        )
        parsed = _parse_json(raw)
        items = parsed.get("items", [])
        season = parsed.get("season", "SS")
        if season not in ("SS", "AW"):
            season = "SS"
        return items, season
    except Exception as e:
        print(f"[vision] identify_items failed: {e}")
        return [], "SS"


def match_item(cfg: dict, identified: dict, candidates: list[dict]) -> list[dict]:
    """
    Step 2: text model ranks catalog candidates for one identified item.
    Returns [{item, confidence, reasoning}].
    """
    if not candidates:
        return []

    user_text = (
        f"Item to match: {identified['description']}\n"
        f"Type: {identified['type']}, Colour: {identified['colour']}\n\n"
        f"Candidates from collection:\n{_format_candidates(candidates)}"
    )

    try:
        raw = ai_client.text_call(cfg, system=MATCH_SYSTEM, user=user_text, max_tokens=400)
        ranked = _parse_json(raw).get("matches", [])
    except Exception as e:
        print(f"[vision] match_item failed: {e}")
        return []

    results = []
    for m in ranked:
        idx = m.get("candidate_index")
        if idx is not None and 0 <= idx < len(candidates):
            results.append({
                "item": candidates[idx],
                "confidence": m.get("confidence", 0.0),
                "reasoning": m.get("reasoning", ""),
            })
    return results


def run_matching(cfg: dict, user_id: int, image_b64: str, items_catalog: list[dict], img_hash: str) -> dict:
    """
    Full pipeline: identify → filter → match. Includes learning from corrections.

    Returns {
        "season": str,
        "results": [{
            "identified": {type, colour, description},
            "top_matches": [{item, confidence, reasoning}],
            "status": "matched" | "ambiguous" | "unidentified",
            "from_memory": bool
        }]
    }
    """
    # Level 1: exact image replay
    prior = corrections.lookup_image(user_id, img_hash)
    if prior:
        print(f"[memory] exact image match — replaying {len(prior)} decisions")
        results = []
        for d in prior:
            item = next((c for c in items_catalog if c["id"] == d["correct_id"]), None)
            if not item:
                continue
            results.append({
                "identified": {"type": d["item_type"], "colour": "", "description": f"Previously identified as {d['correct_name']}"},
                "top_matches": [{"item": item, "confidence": 1.0, "reasoning": "From your correction history"}],
                "status": "matched",
                "from_memory": True,
            })
        if results:
            return {"season": "SS", "results": results}

    # Level 2: fresh AI identification
    identified_items, season = identify_items(cfg, image_b64)
    if not identified_items:
        return {"season": "SS", "results": []}

    results = []
    for item in identified_items:
        candidates = catalog_mod.search(item["type"], item["colour"], items_catalog, max_results=50)

        # Inject prior corrections for this type+colour
        prior_for_type = corrections.lookup_type_colour(user_id, item["type"], item["colour"])
        if prior_for_type:
            prior_ids = {p["correct_id"] for p in prior_for_type}
            priority = [c for c in candidates if c["id"] in prior_ids]
            rest     = [c for c in candidates if c["id"] not in prior_ids]
            candidates = priority + rest

        top_matches = match_item(cfg, item, candidates) if candidates else []

        # Boost confidence for previously confirmed items
        if top_matches and prior_for_type:
            prior_ids = {p["correct_id"] for p in prior_for_type}
            if top_matches[0]["item"]["id"] in prior_ids:
                top_matches[0] = dict(top_matches[0],
                    confidence=max(top_matches[0]["confidence"], 0.95),
                    reasoning=top_matches[0]["reasoning"] + " [confirmed by your history]")

        if top_matches:
            status = "matched" if top_matches[0]["confidence"] >= 0.65 else "ambiguous"
        else:
            status = "unidentified"

        results.append({
            "identified": item,
            "top_matches": top_matches,
            "status": status,
            "from_memory": False,
        })

    return {"season": season, "results": results}
