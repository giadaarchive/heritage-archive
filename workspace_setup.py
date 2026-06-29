"""
Notion workspace bootstrapper.

Called during /register to create the full Heritage Archive schema
in a user's Notion workspace from scratch.

find_accessible_pages(token) → list of {id, title} pages the integration can see.
create_user_workspace(token, root_page_id, on_progress) → {collection_db_id, ootd_db_id}.
"""

import time
import requests


def _h(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }


def _api(token: str, method: str, path: str, body=None, delay: float = 0.3) -> dict:
    r = getattr(requests, method)(
        f"https://api.notion.com/v1/{path}",
        headers=_h(token), json=body, timeout=30,
    )
    r.raise_for_status()
    time.sleep(delay)
    return r.json()


def _create_db(token: str, parent_id: str, title: str, props: dict) -> dict:
    return _api(token, "post", "databases", {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": props,
    })


def _add_page(token: str, db_id: str, props: dict) -> dict:
    return _api(token, "post", "pages", {
        "parent": {"database_id": db_id},
        "properties": props,
    }, delay=0.2)


def _t(text: str) -> dict:
    return {"title": [{"text": {"content": text}}]}


def _rt(text: str) -> dict:
    return {"rich_text": [{"text": {"content": text}}]}


def _sel(name: str) -> dict:
    return {"select": {"name": name}}


# ── Public API ────────────────────────────────────────────────────────────────

def find_accessible_pages(token: str) -> list[dict]:
    """
    Returns all pages the integration can access, as list of {id, title}.
    The user must have shared at least one page with the integration.
    """
    try:
        r = requests.post(
            "https://api.notion.com/v1/search",
            headers=_h(token),
            json={"filter": {"value": "page", "property": "object"}, "page_size": 20},
            timeout=20,
        )
        r.raise_for_status()
        pages = []
        for result in r.json().get("results", []):
            if result.get("object") != "page":
                continue
            # Skip pages that are rows inside a database — only show top-level pages
            parent = result.get("parent", {})
            if parent.get("type") == "database_id":
                continue
            props = result.get("properties", {})
            title_prop = next((v for v in props.values() if v.get("type") == "title"), None)
            title = ""
            if title_prop:
                title = "".join(t.get("plain_text", "") for t in title_prop.get("title", []))
            pages.append({"id": result["id"], "title": title or "(untitled)"})
        return pages
    except Exception:
        return []


def create_user_workspace(token: str, root_page_id: str, on_progress=None) -> dict:
    """
    Creates the full Heritage Archive schema in the user's Notion workspace.

    Creates: Designers, Materials, Colours, Season lookup DBs
             + My Wardrobe + My Lookbook
    Populates all lookup tables with defaults.

    Returns {collection_db_id, ootd_db_id}.
    on_progress(step: str) is called with plain-text status updates.
    """
    def progress(msg: str):
        if on_progress:
            on_progress(msg)

    # ── Lookup: Designers ─────────────────────────────────────────────────────
    progress("Creating Designers database...")
    designers_db = _create_db(token, root_page_id, "Designers", {
        "Brand": {"title": {}},
        "SKU Code": {"rich_text": {}},
        "Country": {"rich_text": {}},
    })
    designers_db_id = designers_db["id"]

    designers = [
        ("Hermès", "HRM", "France"), ("Chanel", "CHN", "France"),
        ("Christian Dior", "DOR", "France"), ("Louis Vuitton", "LVT", "France"),
        ("Celine", "CLN", "France"), ("Givenchy", "GVN", "France"),
        ("Balmain", "BLM", "France"), ("YSL (Saint Laurent)", "YSL", "France"),
        ("Loewe", "LOW", "Spain"), ("Balenciaga", "BAL", "Spain"),
        ("Prada", "PRD", "Italy"), ("Gucci", "GCC", "Italy"),
        ("Fendi", "FND", "Italy"), ("Bottega Veneta", "BTG", "Italy"),
        ("Valentino", "VAL", "Italy"), ("Versace", "VRS", "Italy"),
        ("Salvatore Ferragamo", "FRG", "Italy"), ("Max Mara", "MXM", "Italy"),
        ("Loro Piana", "LOR", "Italy"), ("Akris", "AKR", "Switzerland"),
        ("Burberry", "BBR", "UK"), ("Alexander McQueen", "MCQ", "UK"),
        ("Stella McCartney", "SMC", "UK"), ("Victoria Beckham", "VBK", "UK"),
        ("Ralph Lauren", "RLP", "USA"), ("Calvin Klein", "CLK", "USA"),
        ("Tom Ford", "TFD", "USA"), ("Oscar de la Renta", "ODR", "USA"),
        ("The Row", "ROW", "USA"), ("Totême", "TTM", "Sweden"),
        ("Issey Miyake", "IMY", "Japan"), ("Comme des Garçons", "CDG", "Japan"),
        ("Jil Sander", "JIL", "Germany"), ("Lemaire", "LMR", "France"),
        ("APC", "APC", "France"), ("Maje", "MJE", "France"),
        ("Zimmermann", "ZMM", "Australia"), ("Uniqlo", "UNQ", "Japan"),
        ("H&M", "HNM", "Sweden"), ("Etro", "ETR", "Italy"),
        ("Chloé", "CHL", "France"), ("Kiton", "KTN", "Italy"),
        ("Dolce & Gabbana", "DNG", "Italy"), ("Cartier", "CTR", "France"),
        ("Tiffany & Co", "TFC", "USA"),
        ("Other / Unknown", "OTH", ""), ("Vintage / No Label", "VTG", ""),
    ]
    progress(f"Adding {len(designers)} designers...")
    for name, sku, country in designers:
        _add_page(token, designers_db_id, {
            "Brand": _t(name), "SKU Code": _rt(sku), "Country": _rt(country),
        })

    # ── Lookup: Materials ─────────────────────────────────────────────────────
    progress("Creating Materials database...")
    materials_db = _create_db(token, root_page_id, "Materials", {
        "Material": {"title": {}},
        "SKU Code": {"rich_text": {}},
        "Fibre Type": {
            "select": {"options": [
                {"name": "Natural", "color": "green"},
                {"name": "Synthetic", "color": "blue"},
                {"name": "Precious", "color": "yellow"},
                {"name": "Mixed", "color": "gray"},
            ]}
        },
    })
    materials_db_id = materials_db["id"]

    materials = [
        ("Cashmere", "CAS", "Natural"), ("Wool", "WOL", "Natural"),
        ("Merino Wool", "MER", "Natural"), ("Cotton", "COT", "Natural"),
        ("Linen", "LIN", "Natural"), ("Silk", "SLK", "Natural"),
        ("Mulberry Silk", "MUL", "Natural"), ("Denim", "DEN", "Natural"),
        ("Leather", "LEA", "Natural"), ("Suede", "SDE", "Natural"),
        ("Velvet", "VLV", "Natural"), ("Alpaca", "ALP", "Natural"),
        ("Mohair", "MOH", "Natural"), ("Lace", "LCE", "Natural"),
        ("Tweed", "TWD", "Natural"), ("Canvas", "CVS", "Natural"),
        ("Polyester", "PLY", "Synthetic"), ("Nylon", "NYL", "Synthetic"),
        ("Viscose / Rayon", "RAY", "Synthetic"), ("Spandex / Elastane", "SPA", "Synthetic"),
        ("Acrylic", "ACR", "Synthetic"), ("Gold", "XAU", "Precious"),
        ("Silver", "XAG", "Precious"), ("Platinum", "XPT", "Precious"),
        ("Diamond", "DIA", "Precious"), ("Pearl", "PRL", "Precious"),
        ("Mixed / Blended", "MIX", "Mixed"), ("Other", "OTH", "Mixed"),
    ]
    progress(f"Adding {len(materials)} materials...")
    for name, sku, fibre in materials:
        _add_page(token, materials_db_id, {
            "Material": _t(name), "SKU Code": _rt(sku), "Fibre Type": _sel(fibre),
        })

    # ── Lookup: Colours ───────────────────────────────────────────────────────
    progress("Creating Colours database...")
    colours_db = _create_db(token, root_page_id, "Colours", {
        "Colour": {"title": {}},
        "Family": {
            "select": {"options": [
                {"name": "Neutral", "color": "gray"},
                {"name": "Warm", "color": "orange"},
                {"name": "Cool", "color": "blue"},
                {"name": "Earth", "color": "brown"},
                {"name": "Dark", "color": "default"},
                {"name": "Light", "color": "yellow"},
                {"name": "Multi", "color": "pink"},
            ]}
        },
        "Hex": {"rich_text": {}},
    })
    colours_db_id = colours_db["id"]

    colours = [
        ("Black", "Dark", "#000000"), ("White", "Light", "#FFFFFF"),
        ("Off-white", "Light", "#FAF9F6"), ("Ivory", "Light", "#FFFFF0"),
        ("Cream", "Light", "#FFFDD0"), ("Ecru", "Light", "#C2B280"),
        ("Grey", "Neutral", "#808080"), ("Light grey", "Neutral", "#D3D3D3"),
        ("Charcoal", "Dark", "#36454F"), ("Navy", "Cool", "#000080"),
        ("Midnight blue", "Cool", "#191970"), ("Cobalt", "Cool", "#0047AB"),
        ("Sky blue", "Cool", "#87CEEB"), ("Powder blue", "Cool", "#B0E0E6"),
        ("Teal", "Cool", "#008080"), ("Forest green", "Cool", "#228B22"),
        ("Sage", "Earth", "#B2AC88"), ("Olive", "Earth", "#808000"),
        ("Khaki", "Earth", "#C3B091"), ("Camel", "Earth", "#C19A6B"),
        ("Tan", "Earth", "#D2B48C"), ("Beige", "Earth", "#F5F5DC"),
        ("Sand", "Earth", "#C2B280"), ("Brown", "Earth", "#964B00"),
        ("Chocolate", "Earth", "#7B3F00"), ("Burgundy", "Warm", "#800020"),
        ("Wine", "Warm", "#722F37"), ("Red", "Warm", "#FF0000"),
        ("Rust", "Warm", "#B7410E"), ("Terracotta", "Warm", "#E2725B"),
        ("Burnt orange", "Warm", "#CC5500"), ("Orange", "Warm", "#FFA500"),
        ("Coral", "Warm", "#FF7F50"), ("Blush", "Warm", "#DE5D83"),
        ("Pink", "Warm", "#FFC0CB"), ("Hot pink", "Warm", "#FF69B4"),
        ("Rose", "Warm", "#FF007F"), ("Mauve", "Warm", "#E0B0FF"),
        ("Lavender", "Cool", "#E6E6FA"), ("Purple", "Cool", "#800080"),
        ("Plum", "Cool", "#8E4585"), ("Gold", "Warm", "#FFD700"),
        ("Champagne", "Light", "#F7E7CE"), ("Silver", "Neutral", "#C0C0C0"),
        ("Multi / Print", "Multi", ""),
    ]
    progress(f"Adding {len(colours)} colours...")
    for name, family, hex_val in colours:
        _add_page(token, colours_db_id, {
            "Colour": _t(name), "Family": _sel(family), "Hex": _rt(hex_val),
        })

    # ── Lookup: Season ────────────────────────────────────────────────────────
    progress("Creating Season database...")
    season_db = _create_db(token, root_page_id, "Season", {
        "Season": {"title": {}},
        "Hemisphere Note": {"rich_text": {}},
    })
    season_db_id = season_db["id"]

    for name, note in [
        ("SS", "Spring/Summer — lighter fabrics, warmer months"),
        ("AW", "Autumn/Winter — heavier fabrics, cooler months"),
        ("Year-round", "Works across all seasons"),
        ("Resort", "Holiday / cruise season — lightweight year-round"),
    ]:
        _add_page(token, season_db_id, {
            "Season": _t(name), "Hemisphere Note": _rt(note),
        })

    # ── My Wardrobe ───────────────────────────────────────────────────────────
    progress("Creating My Wardrobe database...")
    wardrobe_db = _create_db(token, root_page_id, "My Wardrobe", {
        "Name": {"title": {}},
        "SKU": {"rich_text": {}},
        "Category": {
            "select": {"options": [
                {"name": "Tops"}, {"name": "Trousers"}, {"name": "Skirts"},
                {"name": "Dresses"}, {"name": "Outerwear"}, {"name": "Jumpsuits"},
                {"name": "Bags"}, {"name": "Shoes"}, {"name": "Jewellery"},
                {"name": "Scarves"}, {"name": "Accessories"},
                {"name": "Lingerie"}, {"name": "Other"},
            ]}
        },
        "Designer": {"relation": {
            "database_id": designers_db_id,
            "type": "single_property", "single_property": {},
        }},
        "Material Category": {"relation": {
            "database_id": materials_db_id,
            "type": "single_property", "single_property": {},
        }},
        "Material": {"rich_text": {}},
        "Colour": {"relation": {
            "database_id": colours_db_id,
            "type": "single_property", "single_property": {},
        }},
        "Season": {"relation": {
            "database_id": season_db_id,
            "type": "single_property", "single_property": {},
        }},
        "Purchase Price": {"number": {"format": "dollar"}},
        "Retail Price (USD)": {"number": {"format": "dollar"}},
        "Additional Costs": {"number": {"format": "dollar"}},
        "Date Acquired": {"date": {}},
        "Year Made": {"date": {}},
        "Favourite": {"checkbox": {}},
        "No Longer Owned": {"checkbox": {}},
        "Notes": {"rich_text": {}},
    })
    wardrobe_db_id = wardrobe_db["id"]

    # ── My Lookbook ───────────────────────────────────────────────────────────
    progress("Creating My Lookbook database...")
    lookbook_db = _create_db(token, root_page_id, "My Lookbook", {
        "Name": {"title": {}},
        "Items": {"relation": {
            "database_id": wardrobe_db_id,
            "type": "single_property", "single_property": {},
        }},
        "Worn": {"date": {}},
        "Season": {
            "select": {"options": [
                {"name": "SS", "color": "yellow"},
                {"name": "AW", "color": "blue"},
                {"name": "Year-round", "color": "green"},
                {"name": "Resort", "color": "orange"},
            ]}
        },
        "OOTD Story": {"rich_text": {}},
        "Favourite": {"checkbox": {}},
    })
    lookbook_db_id = lookbook_db["id"]

    # ── Setup notes page ──────────────────────────────────────────────────────
    progress("Adding setup notes...")
    _api(token, "post", "pages", {
        "parent": {"type": "page_id", "page_id": root_page_id},
        "properties": {"title": {"title": [{"text": {"content": "⚙️ Setup Notes"}}]}},
        "children": [
            {"object": "block", "type": "heading_2", "heading_2": {
                "rich_text": [{"text": {"content": "Two properties to add manually in Notion"}}]}},
            {"object": "block", "type": "paragraph", "paragraph": {
                "rich_text": [{"text": {"content": "The Notion API can't create rollups or formulas. Add these once:"}}]}},
            {"object": "block", "type": "heading_3", "heading_3": {
                "rich_text": [{"text": {"content": "Fits (rollup — counts wears per item)"}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {
                "rich_text": [{"text": {"content": "My Wardrobe → Add property → Rollup"}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {
                "rich_text": [{"text": {"content": "Relation: Items (back-relation from My Lookbook) · Property: Name · Calculate: Count all"}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {
                "rich_text": [{"text": {"content": "Name it: Fits"}}]}},
            {"object": "block", "type": "heading_3", "heading_3": {
                "rich_text": [{"text": {"content": "CPW — Cost Per Wear (formula)"}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {
                "rich_text": [{"text": {"content": 'My Wardrobe → Add property → Formula'}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {
                "rich_text": [{"text": {"content": 'if(prop("Fits") > 0, prop("Purchase Price") / prop("Fits"), prop("Purchase Price"))'}}]}},
            {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {
                "rich_text": [{"text": {"content": "Name it: CPW"}}]}},
        ],
    })

    progress("Done.")
    return {
        "collection_db_id": wardrobe_db_id,
        "ootd_db_id": lookbook_db_id,
    }
