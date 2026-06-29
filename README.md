# Heritage Archive

Track what you own. Log what you wear. Know your wardrobe.

A Telegram bot that connects to your Notion wardrobe database. Send an outfit photo — AI identifies what you're wearing, matches it to your collection, and logs it. Over time you get cost-per-wear, coverage analytics, and a clear picture of what you actually wear.

---

## What you need

- A Notion account (free)
- A Telegram account
- An API key from Anthropic, OpenAI, or OpenRouter
- Python 3.11+

That's it. No servers, no Docker, no VPS.

---

## Setup (10 minutes)

### 1. Duplicate the Notion template

[→ Notion template link] *(will be added when beta launches)*

Open it, click "Duplicate" in the top right, and save it to your workspace.

### 2. Create a Notion integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click "New integration"
3. Name it "Heritage Archive"
4. Copy the integration token (starts with `secret_`)
5. Open your duplicated template → click the `...` menu → "Add connections" → add your integration to both the **Wardrobe** and **OOTD** databases

### 3. Find your database IDs

Open each Notion database. The ID is in the URL:
```
https://notion.so/your-workspace/DATABASE_ID?v=...
```
The ID is the 32-character string. Copy both: the Wardrobe Items ID and the OOTD ID.

### 4. Create a Telegram bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot`
3. Follow the prompts (name it anything)
4. Copy the bot token

### 5. Install and configure

```bash
git clone https://github.com/yourusername/heritage-archive
cd heritage-archive

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env: add your TELEGRAM_BOT_TOKEN and an AI key
# Set BOT_OWNER_ID to your Telegram user ID (find it via @userinfobot)
```

### 6. Run the bot

```bash
python3 bot.py
```

### 7. Register in Telegram

Open your bot in Telegram and send `/register`. It will walk you through connecting your Notion workspace.

---

## Daily use

**Log an outfit:**
Send a photo (as a file, not compressed, to preserve the date from your camera EXIF).

The bot will:
1. Identify every visible garment using AI vision
2. Match each item to your Notion collection
3. Show you the best match — tap ✅ to confirm or 🔍 to search
4. Upload the photo and create an OOTD entry in Notion

**Log a past outfit:**
Use `/date YYYY-MM-DD` before sending the photo.

**See your analytics:**
Use `/stats` for coverage, cost-per-wear, dead weight, and your wardrobe health score.

**Add a new item while logging:**
If the AI spots something not in your wardrobe, tap ➕ to create a draft entry. Fill in the details (price, brand, care info) in Notion later.

---

## Commands

| Command | What it does |
|---------|-------------|
| `/start` | Welcome and status |
| `/register` | Connect Notion + AI provider |
| `/date YYYY-MM-DD` | Override date for next photo |
| `/refresh` | Force-reload wardrobe from Notion |
| `/stats` | Coverage, CPW, dead weight, health score |
| `/always add <search>` | Add item to every OOTD automatically |
| `/always remove <name>` | Remove from always-worn |
| `/always` | List always-worn items |
| `/help` | Command reference |

---

## Photo tips

- **Send as a File, not Photo** — Telegram compresses photos and strips EXIF (date metadata). Send as a document to preserve the date automatically.
- Full-length photo works best for identification.
- Multiple photos of one outfit? Send them all at once as an album.

---

## The AI learns

Every correction you make is remembered. If you correct the AI's match for a navy blazer, the next time it sees a navy blazer it will rank your confirmed item first. Images you log twice (same photo, different day) are replayed instantly with no AI call.

---

## Privacy

Your outfit photos are sent to your chosen AI provider (Anthropic, OpenAI, or OpenRouter) for garment identification. Their data retention policies apply. All other data (wear logs, item metadata) stays in your Notion workspace.

For full local privacy, OpenRouter supports open-source vision models you can run via Ollama.

---

## Analytics — what /stats shows

- **Coverage**: % of your wardrobe worn at least once
- **Average CPW**: cost-per-wear across all items with prices
- **Most worn**: your top 5 most-reached-for pieces
- **Never worn**: items in your wardrobe you haven't logged yet
- **Not worn in 12 months**: items that might be ready to leave
- **Health score**: 0–100 composite of coverage, CPW efficiency, and dead weight

---

## Adding items to your wardrobe

You can add items directly in Notion (using the template's layout) or by logging an outfit containing a piece not yet in your database — the bot will offer to create a draft entry on the spot.

As you log more outfits, more items get catalogued automatically. There is no need to add your entire wardrobe at once. The system tells you what percentage is catalogued so far.

---

## For the bot owner: registering beta testers

If you're running this for a small group, you can register users directly without them going through the wizard:

```
/admin_add <telegram_user_id> <notion_token> <collection_db_id> <ootd_db_id>
```

The user can then just open the bot and send a photo. No setup required on their end beyond duplicating the Notion template.

---

## Behind the Cultured

Heritage Archive is a tool under the [Behind The Cultured](https://behindthecultured.com) umbrella — a brand for people who dress with intention.
