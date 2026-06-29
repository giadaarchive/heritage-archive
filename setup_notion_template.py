#!/usr/bin/env python3
"""
Creates the Heritage Archive Notion template from scratch.

Creates:
  1. Root page "Heritage Archive — Template"
  2. Designers database (lookup)
  3. Materials database (lookup)
  4. Tags database (Why I Own It + What I'd Change)
  5. My Wardrobe database (main items DB)
  6. My Lookbook database (OOTD diary)

Populates lookup tables with comprehensive defaults.
Adds 3 anonymized sample items and 2 sample OOTD entries.

Formulas and rollups that can't be created via API are documented
in a setup notes page inside the template.

Usage:
    python3 setup_notion_template.py
"""

import os, json, time, requests, sys
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.environ["NOTION_TOKEN"]
H = {
    "Authorization": f"Bearer {TOKEN}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def api(method, path, body=None):
    url = f"https://api.notion.com/v1/{path}"
    r = getattr(requests, method)(url, headers=H, json=body, timeout=30)
    if r.status_code not in (200, 201):
        print(f"  ERROR {r.status_code}: {r.text[:400]}")
        r.raise_for_status()
    time.sleep(0.35)
    return r.json()


def create_page(parent_id, title, is_workspace=False):
    parent = {"type": "workspace", "workspace": True} if is_workspace else {"type": "page_id", "page_id": parent_id}
    return api("post", "pages", {
        "parent": parent,
        "properties": {"title": {"title": [{"text": {"content": title}}]}},
    })


def create_db(parent_id, title, properties):
    return api("post", "databases", {
        "parent": {"type": "page_id", "page_id": parent_id},
        "title": [{"type": "text", "text": {"content": title}}],
        "properties": properties,
    })


def add_page(db_id, properties, children=None):
    body = {
        "parent": {"database_id": db_id},
        "properties": properties,
    }
    if children:
        body["children"] = children
    return api("post", "pages", body)


def title_prop(text):
    return {"title": [{"text": {"content": text}}]}


def rich_text_prop(text):
    return {"rich_text": [{"text": {"content": text}}]}


def number_prop(n):
    return {"number": n}


def date_prop(d):
    return {"date": {"start": d}}


def select_prop(name):
    return {"select": {"name": name}}


def multi_select_prop(names):
    return {"multi_select": [{"name": n} for n in names]}


def checkbox_prop(v):
    return {"checkbox": v}


def relation_prop(ids):
    return {"relation": [{"id": i} for i in ids]}


# ── Step 1: Create root page ──────────────────────────────────────────────────

TEMPLATE_ROOT_ID = "38eccd15-cda1-81e6-ba35-eeff2d382960"
root_id = TEMPLATE_ROOT_ID
print(f"Using existing template root page: {root_id}")


# ── Step 2: Create Designers database ────────────────────────────────────────

print("Creating Designers database...")
designers_db = create_db(root_id, "Designers", {
    "Brand": {"title": {}},
    "SKU Code": {"rich_text": {}},
    "Country": {"rich_text": {}},
})
designers_db_id = designers_db["id"]
print(f"  Designers DB: {designers_db_id}")


# ── Step 3: Create Materials database ────────────────────────────────────────

print("Creating Materials database...")
materials_db = create_db(root_id, "Materials", {
    "Material": {"title": {}},
    "SKU Code": {"rich_text": {}},
    "Fibre Type": {
        "select": {
            "options": [
                {"name": "Natural", "color": "green"},
                {"name": "Synthetic", "color": "blue"},
                {"name": "Precious", "color": "yellow"},
                {"name": "Mixed", "color": "gray"},
            ]
        }
    },
})
materials_db_id = materials_db["id"]
print(f"  Materials DB: {materials_db_id}")


# ── Step 4: Create Tags database ──────────────────────────────────────────────

print("Creating Tags database...")
tags_db = create_db(root_id, "Tags", {
    "Tag": {"title": {}},
    "Description": {"rich_text": {}},
    "Type": {
        "select": {
            "options": [
                {"name": "Why I Own It", "color": "green"},
                {"name": "What I'd Change", "color": "red"},
                {"name": "Both", "color": "gray"},
            ]
        }
    },
})
tags_db_id = tags_db["id"]
print(f"  Tags DB: {tags_db_id}")


# ── Step 5: Create My Wardrobe database ───────────────────────────────────────

print("Creating My Wardrobe database...")
wardrobe_db = create_db(root_id, "My Wardrobe", {
    "Name": {"title": {}},
    "SKU": {"rich_text": {}},
    "Designer": {"relation": {"database_id": designers_db_id, "single_property": {}}},
    "Material Category": {"relation": {"database_id": materials_db_id, "single_property": {}}},
    "Category": {
        "select": {
            "options": [
                {"name": "Tops"},
                {"name": "Trousers"},
                {"name": "Skirts"},
                {"name": "Dresses"},
                {"name": "Outerwear"},
                {"name": "Jumpsuits"},
                {"name": "Bags"},
                {"name": "Shoes"},
                {"name": "Jewellery"},
                {"name": "Scarves"},
                {"name": "Accessories"},
                {"name": "Lingerie"},
                {"name": "Other"},
            ]
        }
    },
    "Primary Colour": {"rich_text": {}},
    "Colour Detail": {"rich_text": {}},
    "Material": {"rich_text": {}},
    "Season": {
        "multi_select": {
            "options": [
                {"name": "SS", "color": "yellow"},
                {"name": "AW", "color": "blue"},
                {"name": "Year-round", "color": "green"},
            ]
        }
    },
    "Purchase Price": {"number": {"format": "dollar"}},
    "Retail Price (USD)": {"number": {"format": "dollar"}},
    "Additional Costs": {"number": {"format": "dollar"}},
    "Date Acquired": {"date": {}},
    "Year Made": {"date": {}},
    "Wash Method": {
        "multi_select": {
            "options": [
                {"name": "Hand wash"},
                {"name": "Machine wash"},
                {"name": "Dry clean only"},
                {"name": "Spot clean"},
                {"name": "Do not wash"},
            ]
        }
    },
    "Wash Temperature": {
        "multi_select": {
            "options": [
                {"name": "Cold"},
                {"name": "30ºC"},
                {"name": "40ºC"},
                {"name": "N/A"},
            ]
        }
    },
    "Drying": {
        "select": {
            "options": [
                {"name": "Line dry"},
                {"name": "Lay flat"},
                {"name": "Tumble dry low"},
                {"name": "Do not tumble dry"},
            ]
        }
    },
    "Storage Method": {
        "multi_select": {
            "options": [
                {"name": "Hang"},
                {"name": "Fold"},
                {"name": "Dust bag"},
                {"name": "Box"},
                {"name": "Cedar"},
            ]
        }
    },
    "Ironing": {
        "multi_select": {
            "options": [
                {"name": "Low heat"},
                {"name": "Medium heat"},
                {"name": "Steam only"},
                {"name": "Do not iron"},
            ]
        }
    },
    "Why I Own It": {"relation": {"database_id": tags_db_id, "single_property": {}}},
    "What I'd Change": {"relation": {"database_id": tags_db_id, "single_property": {}}},
    "Favourite": {"checkbox": {}},
    "No Longer Owned": {"checkbox": {}},
    "Notes": {"rich_text": {}},
})
wardrobe_db_id = wardrobe_db["id"]
print(f"  My Wardrobe DB: {wardrobe_db_id}")


# ── Step 6: Create My Lookbook database ───────────────────────────────────────

print("Creating My Lookbook database...")
lookbook_db = create_db(root_id, "My Lookbook", {
    "Name": {"title": {}},
    "Items": {"relation": {"database_id": wardrobe_db_id, "single_property": {}}},
    "Worn": {"date": {}},
    "Season": {
        "select": {
            "options": [
                {"name": "SS", "color": "yellow"},
                {"name": "AW", "color": "blue"},
            ]
        }
    },
    "OOTD Story": {"rich_text": {}},
    "Favourite": {"checkbox": {}},
})
lookbook_db_id = lookbook_db["id"]
print(f"  My Lookbook DB: {lookbook_db_id}")


# ── Step 7: Add setup notes page ──────────────────────────────────────────────

print("Creating setup notes page...")
api("post", "pages", {
    "parent": {"type": "page_id", "page_id": root_id},
    "properties": {"title": {"title": [{"text": {"content": "⚙️ Setup Notes"}}]}},
    "children": [
        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "After duplicating this template"}}]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "Two properties in My Wardrobe need to be added manually (Notion API doesn't support creating these):"}}]}},
        {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"text": {"content": "1. Fits (rollup — counts how many times each item has been worn)"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "In My Wardrobe → Add property → Rollup"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "Relation: Items (the back-relation from My Lookbook)"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "Property: Name"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "Calculate: Count all"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "Name it: Fits"}}]}},
        {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"text": {"content": "2. CPW — Cost Per Wear (formula)"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "In My Wardrobe → Add property → Formula"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "Formula: if(prop(\"Fits\") > 0, prop(\"Purchase Price\") / prop(\"Fits\"), prop(\"Purchase Price\"))"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "Name it: CPW"}}]}},
        {"object": "block", "type": "heading_3", "heading_3": {"rich_text": [{"text": {"content": "3. Last Worn (rollup)"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "In My Wardrobe → Add property → Rollup"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "Relation: Items, Property: Worn, Calculate: Latest date"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "Name it: Last Worn"}}]}},
        {"object": "block", "type": "divider", "divider": {}},
        {"object": "block", "type": "heading_2", "heading_2": {"rich_text": [{"text": {"content": "Database IDs for the bot"}}]}},
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"text": {"content": "After duplicating, find these IDs in the Notion URL for each database and give them to the bot during /register:"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "My Wardrobe ID: (found in the URL when you open My Wardrobe)"}}]}},
        {"object": "block", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"text": {"content": "My Lookbook ID: (found in the URL when you open My Lookbook)"}}]}},
    ],
})


# ── Step 8: Populate Designers ────────────────────────────────────────────────

print("Populating Designers...")
designers = [
    # (name, sku_code, country)
    ("Hermès", "HRM", "France"),
    ("Chanel", "CHN", "France"),
    ("Christian Dior", "DOR", "France"),
    ("Louis Vuitton", "LVT", "France"),
    ("Celine", "CLN", "France"),
    ("Givenchy", "GVN", "France"),
    ("Balmain", "BLM", "France"),
    ("YSL (Saint Laurent)", "YSL", "France"),
    ("Loewe", "LOW", "Spain"),
    ("Balenciaga", "BAL", "Spain"),
    ("Prada", "PRD", "Italy"),
    ("Gucci", "GCC", "Italy"),
    ("Fendi", "FND", "Italy"),
    ("Bottega Veneta", "BTG", "Italy"),
    ("Valentino", "VAL", "Italy"),
    ("Versace", "VRS", "Italy"),
    ("Salvatore Ferragamo", "FRG", "Italy"),
    ("Max Mara", "MXM", "Italy"),
    ("Loro Piana", "LOR", "Italy"),
    ("Akris", "AKR", "Switzerland"),
    ("Burberry", "BBR", "UK"),
    ("Alexander McQueen", "MCQ", "UK"),
    ("Stella McCartney", "SMC", "UK"),
    ("Victoria Beckham", "VBK", "UK"),
    ("Ralph Lauren", "RLP", "USA"),
    ("Calvin Klein", "CLK", "USA"),
    ("Tom Ford", "TFD", "USA"),
    ("Oscar de la Renta", "ODR", "USA"),
    ("The Row", "ROW", "USA"),
    ("Totême", "TTM", "Sweden"),
    ("Issey Miyake", "IMY", "Japan"),
    ("Comme des Garçon", "CDG", "Japan"),
    ("Jil Sander", "JIL", "Germany"),
    ("Lemaire", "LMR", "France"),
    ("APC", "APC", "France"),
    ("Maje", "MJE", "France"),
    ("Zimmermann", "ZMM", "Australia"),
    ("Uniqlo", "UNQ", "Japan"),
    ("H&M", "HNM", "Sweden"),
    ("Etro", "ETR", "Italy"),
    ("Chloe", "CHL", "France"),
    ("Kiton", "KTN", "Italy"),
    ("Dolce & Gabbana", "DNG", "Italy"),
    ("Cartier", "CTR", "France"),
    ("Tiffany & Co", "TFC", "USA"),
    ("Other / Unknown", "OTH", ""),
    ("Vintage / No Label", "VTG", ""),
]

for name, sku, country in designers:
    add_page(designers_db_id, {
        "Brand": title_prop(name),
        "SKU Code": rich_text_prop(sku),
        "Country": rich_text_prop(country),
    })
    print(f"  + Designer: {name}")

print(f"  {len(designers)} designers added.")


# ── Step 9: Populate Materials ────────────────────────────────────────────────

print("Populating Materials...")
materials = [
    ("Cashmere", "CAS", "Natural"),
    ("Wool", "WOL", "Natural"),
    ("Merino Wool", "MER", "Natural"),
    ("Cotton", "COT", "Natural"),
    ("Linen", "LIN", "Natural"),
    ("Silk", "SLK", "Natural"),
    ("Mulberry Silk", "MUL", "Natural"),
    ("Denim", "DEN", "Natural"),
    ("Leather (cowhide)", "LEA", "Natural"),
    ("Suede", "SDE", "Natural"),
    ("Velvet", "VLV", "Natural"),
    ("Alpaca", "ALP", "Natural"),
    ("Mohair", "MOH", "Natural"),
    ("Lace", "LCE", "Natural"),
    ("Tweed", "TWD", "Natural"),
    ("Canvas", "CVS", "Natural"),
    ("Polyester", "PLY", "Synthetic"),
    ("Nylon", "NYL", "Synthetic"),
    ("Viscose / Rayon", "RAY", "Synthetic"),
    ("Spandex / Elastane", "SPA", "Synthetic"),
    ("Acrylic", "ACR", "Synthetic"),
    ("Gold", "XAU", "Precious"),
    ("Silver", "XAG", "Precious"),
    ("Platinum", "XPT", "Precious"),
    ("Diamond", "DIA", "Precious"),
    ("Pearl", "PRL", "Precious"),
    ("Mixed / Blended", "MIX", "Mixed"),
    ("Other", "OTH", "Mixed"),
]

for name, sku, fibre_type in materials:
    add_page(materials_db_id, {
        "Material": title_prop(name),
        "SKU Code": rich_text_prop(sku),
        "Fibre Type": select_prop(fibre_type),
    })

print(f"  {len(materials)} materials added.")


# ── Step 10: Populate Tags ────────────────────────────────────────────────────

print("Populating Tags...")
tags = [
    # (tag, description, type)
    ("quality-material", "Premium fabric or construction that justifies the cost", "Why I Own It"),
    ("timeless-silhouette", "A cut or shape that won't date", "Why I Own It"),
    ("versatile", "Works across multiple occasions, seasons, or styling approaches", "Why I Own It"),
    ("investment-piece", "Cost-per-wear logic — price high, but used often enough to justify", "Why I Own It"),
    ("love-the-designer", "Specific affinity for the house or designer's vision", "Why I Own It"),
    ("brand-legacy", "Heritage, craft history, or cultural significance of the brand", "Why I Own It"),
    ("rare-find", "Limited edition, discontinued, or hard to source", "Why I Own It"),
    ("vintage-provenance", "Age and history adds to the piece's value", "Why I Own It"),
    ("colour", "The specific colour is exactly right and hard to find elsewhere", "Why I Own It"),
    ("sentimental", "Emotional connection — gift, memory, or milestone", "Why I Own It"),
    ("travel-worthy", "Packs well, works across climates, good for travel", "Why I Own It"),
    ("30-plus-wears", "Already has high wear history — proven earner", "Why I Own It"),
    ("brand-discovery", "Introduced me to a designer I didn't know before", "Why I Own It"),
    ("craftsmanship", "Exceptional construction detail, finish, or technique", "Why I Own It"),
    ("gifted", "Received as a gift — not purchased", "Why I Own It"),
    ("size-wrong", "Cut or sizing doesn't work for my body", "What I'd Change"),
    ("doesnt-fit-my-wardrobe", "Doesn't integrate with the rest of what I own", "What I'd Change"),
    ("doesnt-fit-my-style", "Aesthetic doesn't match how I dress", "What I'd Change"),
    ("have-better-in-wardrobe", "Already own something that does this job better", "What I'd Change"),
    ("have-equivalent-in-wardrobe", "Already own something similar enough", "What I'd Change"),
    ("wrong-colour", "The colour doesn't work for me or my wardrobe", "What I'd Change"),
    ("wrong-fabric-for-use-case", "Material isn't right for the climate or occasion I'd wear it", "What I'd Change"),
    ("derivative-design", "Feels like a copy of something better", "What I'd Change"),
    ("too-common-silhouette", "Seen everywhere — lacks distinction", "What I'd Change"),
    ("price", "Not worth the asking price for what it is", "What I'd Change"),
    ("logo-fatigue", "Too much visible branding or logomania", "What I'd Change"),
    ("loud-branding", "Brand presence overwhelms the piece itself", "What I'd Change"),
    ("condition", "Signs of wear that affect how I feel wearing it", "What I'd Change"),
    ("misleading-material-claim", "Fabric or quality doesn't match what was advertised", "What I'd Change"),
    ("natural-patina", "Ages beautifully — develops character with wear", "Both"),
    ("pattern-integrity", "Pattern, print, or weave has visual coherence and intention", "Both"),
]

tag_ids = {}
for tag, desc, tag_type in tags:
    page = add_page(tags_db_id, {
        "Tag": title_prop(tag),
        "Description": rich_text_prop(desc),
        "Type": select_prop(tag_type),
    })
    tag_ids[tag] = page["id"]

print(f"  {len(tags)} tags added.")


# ── Step 11: Add sample wardrobe items ────────────────────────────────────────

print("Adding sample wardrobe items...")

sample_items = [
    {
        "Name": "White Cotton T-Shirt",
        "SKU": "OTH-TOP-COT-20-001",
        "Category": "Tops",
        "Primary Colour": "White",
        "Material": "Cotton",
        "Season": ["SS", "Year-round"],
        "Purchase Price": 35.0,
        "Wash Method": ["Machine wash"],
        "Wash Temperature": ["30ºC"],
        "Drying": "Line dry",
        "Storage Method": ["Fold"],
        "Favourite": False,
        "Notes": "Sample item — edit with your actual piece details",
        "why_tags": ["versatile", "30-plus-wears"],
    },
    {
        "Name": "Cashmere Crew-Neck Sweater",
        "SKU": "OTH-KNT-CAS-19-001",
        "Category": "Tops",
        "Primary Colour": "Camel",
        "Material": "Cashmere",
        "Season": ["AW"],
        "Purchase Price": 280.0,
        "Retail Price (USD)": 390.0,
        "Wash Method": ["Hand wash"],
        "Wash Temperature": ["Cold"],
        "Drying": "Lay flat",
        "Storage Method": ["Fold", "Cedar"],
        "Favourite": True,
        "Notes": "Sample item — edit with your actual piece details",
        "why_tags": ["quality-material", "timeless-silhouette", "investment-piece"],
    },
    {
        "Name": "Black Wide-Leg Trousers",
        "SKU": "OTH-TRS-WOL-22-001",
        "Category": "Trousers",
        "Primary Colour": "Black",
        "Material": "Wool blend",
        "Season": ["AW", "Year-round"],
        "Purchase Price": 190.0,
        "Retail Price (USD)": 250.0,
        "Date Acquired": "2022-09-01",
        "Wash Method": ["Dry clean only"],
        "Wash Temperature": ["N/A"],
        "Drying": "Line dry",
        "Storage Method": ["Hang"],
        "Favourite": True,
        "Notes": "Sample item — edit with your actual piece details",
        "why_tags": ["versatile", "timeless-silhouette", "30-plus-wears"],
    },
]

item_ids = []
for item in sample_items:
    why_tag_ids = [tag_ids[t] for t in item.pop("why_tags", []) if t in tag_ids]
    props = {
        "Name": title_prop(item["Name"]),
        "SKU": rich_text_prop(item["SKU"]),
        "Category": select_prop(item["Category"]),
        "Primary Colour": rich_text_prop(item["Primary Colour"]),
        "Material": rich_text_prop(item["Material"]),
        "Season": multi_select_prop(item["Season"]),
        "Purchase Price": number_prop(item["Purchase Price"]),
        "Wash Method": multi_select_prop(item.get("Wash Method", [])),
        "Wash Temperature": multi_select_prop(item.get("Wash Temperature", [])),
        "Storage Method": multi_select_prop(item.get("Storage Method", [])),
        "Favourite": checkbox_prop(item.get("Favourite", False)),
        "No Longer Owned": checkbox_prop(False),
        "Notes": rich_text_prop(item.get("Notes", "")),
    }
    if item.get("Retail Price (USD)"):
        props["Retail Price (USD)"] = number_prop(item["Retail Price (USD)"])
    if item.get("Date Acquired"):
        props["Date Acquired"] = date_prop(item["Date Acquired"])
    if item.get("Drying"):
        props["Drying"] = select_prop(item["Drying"])
    if why_tag_ids:
        props["Why I Own It"] = relation_prop(why_tag_ids)

    page = add_page(wardrobe_db_id, props)
    item_ids.append(page["id"])
    print(f"  + Item: {item['Name']}")


# ── Step 12: Add sample OOTD entries ─────────────────────────────────────────

print("Adding sample lookbook entries...")
sample_ootd = [
    {
        "Name": "OOTD 2024-01-15",
        "Worn": "2024-01-15",
        "Season": "AW",
        "item_indices": [1, 2],  # cashmere sweater + wide-leg trousers
        "OOTD Story": "A quiet Monday. The weight of good cashmere on a cold morning, paired with trousers that move without announcing themselves. The kind of dressing that asks nothing of the room.",
        "Favourite": True,
    },
    {
        "Name": "OOTD 2024-04-03",
        "Worn": "2024-04-03",
        "Season": "SS",
        "item_indices": [0, 2],  # white t-shirt + wide-leg trousers
        "OOTD Story": "Spring arrived without ceremony. A white t-shirt, pressed trousers, and the particular confidence of a person who has stopped trying to dress for anyone but themselves.",
        "Favourite": False,
    },
]

for ootd in sample_ootd:
    linked_ids = [item_ids[i] for i in ootd["item_indices"] if i < len(item_ids)]
    add_page(lookbook_db_id, {
        "Name": title_prop(ootd["Name"]),
        "Worn": date_prop(ootd["Worn"]),
        "Season": select_prop(ootd["Season"]),
        "Items": relation_prop(linked_ids),
        "OOTD Story": rich_text_prop(ootd["OOTD Story"]),
        "Favourite": checkbox_prop(ootd["Favourite"]),
    })
    print(f"  + OOTD: {ootd['Name']}")


# ── Done ──────────────────────────────────────────────────────────────────────

root_url = f"https://www.notion.so/{root_id.replace('-', '')}"
wardrobe_url = f"https://www.notion.so/{wardrobe_db_id.replace('-', '')}"
lookbook_url = f"https://www.notion.so/{lookbook_db_id.replace('-', '')}"

print()
print("=" * 60)
print("Heritage Archive template created.")
print()
print(f"Root page:     {root_url}")
print(f"My Wardrobe:   {wardrobe_url}")
print(f"My Lookbook:   {lookbook_url}")
print()
print("DATABASE IDs FOR THE BOT:")
print(f"  collection_db_id = {wardrobe_db_id}")
print(f"  ootd_db_id       = {lookbook_db_id}")
print()
print("NEXT STEPS:")
print("1. Open the root page in Notion and verify everything looks right")
print("2. Add 3 manual properties in My Wardrobe (see ⚙️ Setup Notes page):")
print("   - Fits (rollup — count of OOTD entries)")
print("   - CPW (formula — Purchase Price / Fits)")
print("   - Last Worn (rollup — latest Worn date)")
print("3. Share the root page as a public Notion template:")
print("   Root page → ... → Share → Publish to web → Allow duplication")
print("4. The template link is what beta testers will use to get started")
