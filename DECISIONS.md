# Heritage Archive — Decisions & Learnings

Living document. Captures every significant decision made during build, why it was made, and what was explicitly ruled out. Update this whenever something changes.

---

## Product

### What Heritage Archive is (V1 scope)

A Telegram bot that connects to a user's own Notion workspace. They send a photo of their outfit, AI identifies the garments, matches them to their Notion collection, and logs the OOTD. Over time: cost-per-wear analytics, coverage, dead weight.

**The whole stack:** Telegram + Notion + GitHub (images) + user's AI key. No VPS. No Docker. No app install.

### What it is not in V1

- Not a web app. Not a PWA. Telegram only.
- Not a social platform. Single-user by design (multi-user only in the sense that one bot instance serves multiple private users).
- Not a bulk-import tool. Progressive cataloguing — items get added one outfit at a time as you wear them.
- Not an e-commerce recommendation engine.

### Target user

30s–40s, online, fashion-conscious, sustainability-minded. Not impulsive shoppers — they care about cost-per-wear and wardrobe efficiency. Comfortable with Notion and Telegram. Not expected to be technical.

### Why Telegram (not a web app)

- Zero install for users
- Native camera + file sharing
- Album support for multi-photo outfits
- Inline keyboard buttons work well for confirm/correct/skip flow
- Bot owner can share one bot with all beta testers — no per-user deployment
- Revisit for V2 when there's a proper web interface

### Why Notion (not SQLite or Postgres)

- Users already have it or will quickly understand it
- Provides a full-featured UI for managing the wardrobe — filters, views, gallery, relations — without us building any of that
- Relations between Wardrobe, Designers, Materials, Colours, Season = clean vocabulary without custom UI work
- The API is enough for a bot to read/write
- Limitation: rate-limited (~3-4 req/s), no formula/rollup creation via API, can't create workspace-level pages via private integrations
- Revisit if Notion API becomes a bottleneck at scale

---

## Architecture

### Multi-user model: shared bot, per-user credentials

Rejected: one bot instance per user (too much setup for non-technical testers).
Chose: one bot instance run by Lisa, credentials stored per user in `data/users.json` keyed by Telegram user ID.

Each user has their own:
- Notion workspace (token + DB IDs)
- AI provider + key (fallback to bot owner's key during beta)
- Collection cache JSON (`data/cache/{user_id}.json`)
- Corrections SQLite DB (`data/corrections/{user_id}.db`)
- Designer name cache (`data/cache/{user_id}_designers.json`)
- Colour name cache (`data/cache/{user_id}_colours.json`)

### AI matching: two-model pipeline

1. **Vision model** (Claude Sonnet / GPT-4o / Qwen-VL) — identify every garment visible in the photo. Returns list of {type, colour, description}.
2. **Text model** (Claude Haiku / GPT-4o-mini / Qwen-72B) — for each identified garment, rank the top catalog candidates by how well they match the description.

Kept separate because:
- Vision is expensive — only run once per image
- Text ranking is cheap — run once per garment
- The two-model approach lets you swap cheaper text models without losing visual quality

**Corrections learning (Level 1 + 2):**
- Level 1: exact image hash replay — same photo never costs AI calls twice
- Level 2b: type+colour boosting — if you corrected "navy blazer" before, that item gets confidence boosted to 0.95 on future navy blazer sightings

### Progressive cataloguing (not bulk import)

Rejected bulk import (photo grid → AI cleanup) for V1. Reasons:
- Adds significant complexity for a one-time flow
- Most users won't do it — they just start logging outfits
- The bot already handles "item not found → create draft entry" on the fly
- At 3–4 outfits/week, most of a wardrobe gets catalogued within a few months naturally

### Bot owner as shared AI key provider (beta)

During beta, Lisa's AI keys are the fallback. Users can optionally provide their own. This keeps friction low for testers while costs stay manageable (10 users × light usage = negligible).

### Image hosting

Two-tier:
1. GitHub repo (user provides token + repo name) — permanent, owned, free
2. freeimage.host — anonymous, zero setup, links may expire

Default: freeimage.host during beta. Power users set up GitHub.

---

## Notion Schema

### Current My Wardrobe schema (as of June 2026)

| Property | Type | Notes |
|---|---|---|
| Name | title | Item name |
| SKU | rich_text | Format: BRAND-CAT-MAT-YY-### |
| Category | select | Tops/Trousers/Skirts/Dresses/Outerwear/Jumpsuits/Bags/Shoes/Jewellery/Scarves/Accessories/Lingerie/Other |
| Designer | relation → Designers | Lookup table |
| Material Category | relation → Materials | Lookup table |
| Material | rich_text | Free-form material notes |
| Colour | relation → Colours | Lookup table (45 colours + families) |
| Season | relation → Season | Lookup table (SS/AW/Year-round/Resort) |
| Purchase Price | number (dollar) | What you paid |
| Retail Price (USD) | number (dollar) | Original retail for CPW context |
| Additional Costs | number (dollar) | Alterations, repairs, cleaning |
| Date Acquired | date | |
| Year Made | date | Vintage reference |
| Favourite | checkbox | |
| No Longer Owned | checkbox | Soft-delete |
| Notes | rich_text | Free-form |
| Fits | rollup | Manual setup required — API limitation |
| CPW | formula | Manual setup required — API limitation |

### What was removed and why

**Care/storage fields removed (Wash Method, Storage Method, Drying, Ironing, Wash Temperature):**
Too much friction for V1. Nobody adds this data. Adds cognitive load with no immediate payoff. Can be added back later if users ask.

**Tags database removed (Why I Own It / What I'd Change as relations):**
The relation-based approach requires users to know tag vocabulary and click through to a separate DB. Adds setup complexity. No payoff at V1 scale (need 6+ months of tagged data before it's meaningful). Removed entirely. Could come back as a simple multi_select directly on the item in V2.

**Primary Colour (rich_text) → Colour (relation):**
Rich text is inconsistent — everyone writes "Navy Blue" differently. Relation to a Colours lookup table enforces vocabulary. Bot can also resolve colour names for AI matching.

**Season (multi_select) → Season (relation):**
Same logic as colour. Standard vocabulary. Cleaner analytics.

### Lookup databases

All four lookup databases are **pre-populated** during `/register`:
- **Designers**: 47 entries (Hermès → Vintage/No Label), with SKU code and country
- **Materials**: 28 entries (Cashmere → Other), with Fibre Type (Natural/Synthetic/Precious/Mixed)
- **Colours**: 45 entries (Black → Multi/Print), with Family and Hex code
- **Season**: 4 entries (SS, AW, Year-round, Resort), with hemisphere note

Users never build these from scratch. They just pick from dropdowns.

### What the Notion API cannot do (limitations)

- **Cannot create workspace-level pages** — private integrations can only create pages inside pages/databases they have access to. Workaround: user shares one blank page with the integration, bot creates everything inside it.
- **Cannot create rollup properties** — `Fits` (wear count) and `Last Worn` must be added manually. Documented in the ⚙️ Setup Notes page created inside the workspace.
- **Cannot create formula properties** — `CPW` must be added manually.
- **Cannot change a property's type** — to convert rich_text → relation, you must delete the old property (loses its data) and create a new one. Do this in a single PATCH call: set old to null, add new with new name. If same name needed, use two calls.
- **Cannot delete databases** — only archive them via PATCH `{archived: true}`.
- **Token format**: new integrations use `ntn_` prefix; old ones use `secret_`. Both still work.
- **Rate limiting**: ~0.3s delay between calls is safe. Full workspace creation (6 DBs + ~125 lookup entries) takes ~45–60 seconds.

---

## Registration Flow

### What changed and why

**Old flow (rejected):**
1. Paste Notion token
2. Paste Wardrobe database ID (from URL — confusing, error-prone)
3. Paste Lookbook database ID
4. Pick AI provider
5. Paste AI key
6. GitHub yes/no
7. (If yes) GitHub token + repo

The DB ID steps were the biggest source of friction. Users had to know what a database URL looks like, extract a 32-character ID, paste it correctly. Non-technical users couldn't do this reliably.

**New flow:**
1. Create Notion integration (link provided in bot message) → share one page → paste token
2. Bot calls Notion search API, finds the shared page automatically
3. Bot creates all 6 databases with full schema and lookup tables (~45s)
4. Pick AI provider (or skip — use bot's shared key)
5. Paste AI key (or skip)
6. GitHub yes/no
7. (If yes) GitHub token + repo

**Key insight**: if you auto-create the workspace, you also own the DB IDs. Zero information needs to come from the user about Notion structure.

### Multi-page handling

If the integration has access to more than one page (user accidentally shared multiple), the bot shows inline buttons — one per page — for the user to pick. Max 8 shown.

### Admin registration (`/admin_add`)

Updated to take a page ID instead of DB IDs:
`/admin_add <user_id> <notion_token> <page_id> [provider] [key]`

Bot creates the workspace, stores the resulting DB IDs, registers the user. They receive a "You're all set" message on their next /start.

---

## Analytics

### Sustainability score formula

`score = coverage_pts + cpw_pts + deadweight_pts` (max 100)

- **Coverage (40 pts)**: % of wardrobe worn at least once. 40 pts at 100%, scales linearly.
- **CPW efficiency (30 pts)**: average cost-per-wear. 30 pts at CPW ≤ $10, 0 pts at CPW > $100, linear in between.
- **Dead weight (30 pts)**: % never worn. 30 pts at 0%, 0 pts at 30%+.

### What the bot reports with `/stats`

- Coverage % (worn at least once / total)
- Average CPW across items with prices
- Top 5 most-worn items (by count)
- Never-worn items (count + list)
- Items not worn in 12 months
- Health score 0–100 with progress bar

---

## Code structure

| File | What it does |
|---|---|
| `bot.py` | Telegram bot, all handlers, registration wizard, session state |
| `notion.py` | All Notion API reads/writes, parameterised by user cfg |
| `workspace_setup.py` | One-time workspace bootstrap — creates all 6 DBs from scratch |
| `catalog.py` | Per-user collection cache with TTL + background refresh |
| `vision.py` | Two-model AI pipeline: identify items → match to catalog |
| `ai_client.py` | Provider abstraction (Anthropic / OpenAI / OpenRouter) |
| `corrections.py` | Per-user SQLite: exact image replay + type/colour boosting |
| `analytics.py` | CPW, coverage, dead weight — compute + format for Telegram |
| `user_store.py` | Per-user credential + config store (JSON), registration state machine |
| `config.py` | Global env vars: bot token, owner ID, fallback AI keys, paths |

### Key design rule

Every function in `notion.py` and `analytics.py` takes a `cfg` dict (the user's credential store entry). No hardcoded IDs. No global state. This is what makes multi-user work.

---

## What needs to happen next

**Immediately (before first beta tester):**
- Push repo to GitHub so testers can clone it
- Add `Fits` rollup and `CPW` formula to the live template workspace manually
- Run a real end-to-end test with a real bot token

**Before open-source:**
- Replace `setup_notion_template.py` (old, hardcoded) with `workspace_setup.py` as the canonical path — or delete the old file
- Decide on Notion OAuth vs integration token — OAuth removes the "create integration" step but requires a web server for the callback
- Bulk add flow: camera roll scan → identify items → batch-add drafts to Notion
- `/add` command: manually add an item from Telegram without a photo

---

## Future ideas (not scoped, in no particular order)

Everything below is explicitly out of V1 scope. Captured here so the ideas don't get lost. Prioritise based on what beta testers ask for first.

### Item depth

- **Care instructions** — Wash method, wash temperature, drying, storage (hang/fold/dust bag), ironing. Removed from V1 for friction reasons. Add back as a separate "care card" view in Notion, filled in optionally.
- **Why I Own It** — Structured tags: quality material, timeless silhouette, investment piece, sentimental, gifted, rare find, vintage provenance, travel-worthy, craftsmanship. Useful for understanding purchase patterns over time.
- **What I'd Change** — Tags for friction or regret: sizing wrong, doesn't integrate with wardrobe, wrong colour, wrong fabric for use case, price not justified, logo fatigue. Pairs with Why I Own It for a complete item debrief.
- **Condition tracking** — Current condition (mint / excellent / good / fair / worn out). Useful for knowing when something needs repair, cleaning, or retirement.
- **Provenance / heritage notes** — AI-generated notes on the brand, the piece, the era it was made. Already built for askgiada; bring into Heritage Archive for the wardrobe context.
- **Repair log** — Date, what was repaired, cost, where (tailor, cobbler, at-home). Feeds into true total cost of ownership.
- **Alteration notes** — What was altered (hemmed, taken in, relined) and by whom.
- **Authentication** — For high-value pieces: serial numbers, authentication certificates, provenance photos. Stored in Notion, referenced in the item record.

### Wardrobe intelligence

- **Outfit story generation** — After logging an OOTD, AI writes a short editorial caption (100–150 words) and adds it to the Lookbook entry. Already built for personal Substack; adapt for multi-user.
- **Style pattern analysis** — What colours, categories, designers do you reach for most? What's your actual colour palette vs. what you think it is? Weekly/monthly breakdown.
- **Personal style identification** — Based on wear history and item metadata, infer a style profile: minimalist / classic / eclectic / maximalist / etc. Not a label — a description of patterns.
- **Seasonal wardrobe review** — At the start of each season, bot prompts: "You haven't worn 23 items since last AW. Here are the 5 you're least likely to wear again. Want to review them?"
- **Capsule suggestions** — From your existing wardrobe, identify a 10-piece capsule that covers the most outfit combinations. Show the math.
- **Dead weight report** — Monthly summary of items not worn in 12+ months. Suggested actions: sell, donate, move to archive, restyle.
- **Sell suggestions** — Items where the resale value is still meaningful and the CPW is high. Suggested with price range from resale comps.

### Styling

- **How to style a specific piece** — User asks "how do I style this camel coat?" → AI generates 5 outfit ideas using pieces already in their wardrobe. Cross-references catalog, stays within what they own.
- **Outfit gap analysis** — "You have a great selection of tops but no occasion trousers. These 3 gaps explain why 40% of your dresses never get worn."
- **Occasion dressing** — User types "I need an outfit for a garden wedding" → bot proposes 3 outfits from existing items with styling notes.
- **Trend matching** — Current season trends (AW/SS) matched against what you already own. "This season's big colour is chocolate brown. You have 4 pieces. Here's how to use them."
- **How-to-style templates** — Recurring outfit formulas (e.g. "silk blouse + tailored trouser + loafer") mapped to items in your specific wardrobe.
- **New item integration** — When a new item is added: "This piece works with 12 things you already own. Here are the 3 best combinations."

### Social / publishing

- **OOTD publishing** — Export an outfit log entry as a formatted post (Substack, Ghost, Markdown). AI writes the story. User edits and publishes.
- **Wardrobe public profile** — Opt-in shareable version of your wardrobe stats: coverage %, CPW, style breakdown. No item details unless explicitly shared.
- **Style evolution timeline** — Year-by-year view of how your wardrobe and wearing patterns have changed. Visual and narrative.

### Data and ownership

- **Full export** — Everything in CSV, JSON, or Markdown. Items, OOTD log, corrections, analytics history.
- **Import from other apps** — Stylebook, Whering, Cladwell — if they export, Heritage Archive should be able to import.
- **Price history** — Track what you paid, what retail was, what it's worth now (resale comps). Total portfolio value.

### Technical / infrastructure

- **Notion OAuth** — Replace manual integration token with OAuth flow. User clicks "Connect Notion," authorises, done. Requires a web server for the callback but eliminates the integration creation step entirely.
- **Web UI** — Minimal interface for viewing wardrobe, browsing OOTD history, and accessing analytics outside Telegram.
- **SQLite backend** — For users who don't want Notion. Same interface, local storage. Full data ownership, no API rate limits.
- **Ollama support** — Local vision model (LLaVA or Qwen-VL) for users who want outfit identification without sending photos to any external API. Zero marginal cost, lower accuracy.
- **Bulk import** — Camera roll scan → identify items in multiple photos → batch-add drafts. The biggest onboarding accelerator.
