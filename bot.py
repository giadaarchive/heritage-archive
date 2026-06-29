#!/usr/bin/env python3
"""
Heritage Archive — Telegram bot for wardrobe logging.

Send an outfit photo → AI identifies what you're wearing →
confirm matches → logged to your Notion wardrobe.

Commands:
  /start          — welcome / check registration
  /register       — connect your Notion workspace and AI key
  /date YYYY-MM-DD — override date for next outfit
  /refresh        — force-reload your collection cache
  /stats          — wardrobe analytics (coverage, CPW, dead weight)
  /always         — manage items added to every OOTD automatically
  /help           — command list
"""

import os, sys, asyncio, base64, hashlib, traceback, json
from io import BytesIO
from datetime import date, timedelta
from html import escape
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, NetworkError as TgNetworkError
from PIL import Image
from PIL.ExifTags import TAGS

import config
import user_store
import catalog as catalog_mod
import corrections
import vision as vision_mod
import notion
import analytics
import workspace_setup
import ideas as ideas_mod


# ── Utilities ─────────────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    return escape(str(text))


def _notion_url(item: dict) -> str:
    return f"https://www.notion.so/{item['id'].replace('-', '')}"


def _resize(image_bytes: bytes, max_dim: int = config.VISION_MAX_DIM) -> bytes:
    img = Image.open(BytesIO(image_bytes))
    img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _exif_date(image_bytes: bytes) -> str | None:
    try:
        img = Image.open(BytesIO(image_bytes))
        exif = img.getexif()
        for tag_id, value in (exif or {}).items():
            if TAGS.get(tag_id) == "DateTimeOriginal":
                from datetime import datetime
                return datetime.strptime(value, "%Y:%m:%d %H:%M:%S").date().isoformat()
    except Exception:
        pass
    return None


async def _safe_answer(query) -> bool:
    try:
        await query.answer()
        return True
    except BadRequest as e:
        if "query" in str(e).lower():
            return False
        raise


async def _safe_reply(message, text: str, **kwargs):
    try:
        return await message.reply_text(text, **kwargs)
    except TgNetworkError:
        await asyncio.sleep(2)
        return await message.reply_text(text, **kwargs)


async def _safe_edit(query, text: str, **kwargs) -> bool:
    try:
        await query.edit_message_text(text, **kwargs)
        return True
    except BadRequest as e:
        if "not modified" in str(e).lower():
            return True
        raise


def _require_registration(user_id: int) -> str | None:
    """Returns error message if user is not registered, else None."""
    if not user_store.is_registered(user_id):
        return (
            "You're not set up yet. Run /register to connect your Notion workspace.\n\n"
            "If you've been added by the bot owner, they'll send you a link to your Notion template first."
        )
    return None


# ── Session state ─────────────────────────────────────────────────────────────
# user_id → {all_images, img_hash, date, season, results, decisions, ...}
_sessions: dict[int, dict] = {}
_date_override: dict[int, str] = {}
_pending: dict[int, dict] = {}          # images waiting for date picker
_media_groups: dict[str, dict] = {}     # album buffers


# ── Registration wizard ───────────────────────────────────────────────────────

_NOTION_INSTRUCTIONS = (
    "<b>Welcome to Heritage Archive.</b>\n\n"
    "I'll build your entire Notion workspace automatically — no template to copy, "
    "no database IDs to find.\n\n"
    "<b>Two quick steps in Notion:</b>\n\n"
    "1. Go to <a href=\"https://www.notion.so/my-integrations\">notion.so/my-integrations</a>\n"
    "   → New integration → name it <i>Heritage Archive</i> → Submit\n"
    "   → Copy the token (starts with <code>ntn_</code>)\n\n"
    "2. In Notion, create a blank page (name it anything — \"Heritage Archive\" works).\n"
    "   Open it → click <b>···</b> top right → <b>Connections</b> → add <i>Heritage Archive</i>\n\n"
    "Then paste the integration token here:"
)


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_store.is_registered(user_id):
        await update.message.reply_text(
            "You're already set up. Send a photo to log an outfit, or /stats to see your analytics.\n\n"
            "To start over: /register_reset",
        )
        return
    user_store.reg_set(user_id, "notion_token", {})
    await update.message.reply_text(
        _NOTION_INSTRUCTIONS,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def cmd_register_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_store.reg_clear(user_id)
    existing = user_store.get(user_id)
    if existing:
        import copy
        cfg = copy.copy(existing)
        cfg["notion_token"] = ""
        cfg["collection_db_id"] = ""
        cfg["ootd_db_id"] = ""
        user_store.save(user_id, cfg)
    user_store.reg_set(user_id, "notion_token", {})
    await update.message.reply_text(
        _NOTION_INSTRUCTIONS,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def _run_workspace_setup(
    user_id: int, reply_msg, status_msg, token: str, page_id: str, page_title: str, partial: dict
):
    """
    Run workspace creation in a thread executor with live progress updates.
    reply_msg — message object used to send the follow-up AI provider prompt.
    status_msg — message object to edit with progress lines.
    """
    loop = asyncio.get_event_loop()
    lines = [f"Building your Heritage Archive workspace on <b>{_esc(page_title)}</b>...\n"]

    async def update_status(new_line: str):
        lines.append(new_line)
        try:
            await status_msg.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)
        except Exception:
            pass

    errors = []

    def on_progress(msg: str):
        asyncio.run_coroutine_threadsafe(update_status(msg), loop).result(timeout=10)

    def do_create():
        try:
            return workspace_setup.create_user_workspace(token, page_id, on_progress)
        except Exception as e:
            errors.append(e)
            return None

    result = await loop.run_in_executor(None, do_create)

    if errors or result is None:
        await status_msg.edit_text(
            f"⚠️ Workspace setup failed: {errors[0] if errors else 'unknown error'}\n\n"
            "Check that your token is valid and the page is shared with the integration, "
            "then try /register_reset to start over."
        )
        return

    partial["notion_token"] = token
    partial["collection_db_id"] = result["collection_db_id"]
    partial["ootd_db_id"] = result["ootd_db_id"]
    user_store.reg_set(user_id, "ai_provider", partial)

    buttons = [
        [InlineKeyboardButton("Anthropic (Claude) — recommended", callback_data="reg_provider:anthropic")],
        [InlineKeyboardButton("OpenAI (GPT-4o)", callback_data="reg_provider:openai")],
        [InlineKeyboardButton("OpenRouter (multi-model)", callback_data="reg_provider:openrouter")],
        [InlineKeyboardButton("Skip — use the bot's key", callback_data="reg_provider:bot")],
    ]
    await reply_msg.reply_text(
        "✅ <b>Workspace ready.</b> 6 databases created in your Notion.\n\n"
        "Which AI provider do you want for outfit identification?\n\n"
        "<i>Skip to use the bot's shared key (may have usage limits during beta).</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def handle_registration_text(update: Update, context: ContextTypes.DEFAULT_TYPE, reg: dict):
    user_id = update.effective_user.id
    step = reg["step"]
    partial = reg["partial"]
    text = update.message.text.strip()

    if step == "notion_token":
        if not (text.startswith("ntn_") or text.startswith("secret_")):
            await update.message.reply_text(
                "That doesn't look like a Notion integration token.\n"
                "It should start with <code>ntn_</code> (or <code>secret_</code> for older tokens).\n\n"
                "Make sure you're copying the <b>Internal Integration Token</b>, not a page URL.",
                parse_mode=ParseMode.HTML,
            )
            return

        # Find pages accessible to this integration
        status = await update.message.reply_text("Connecting to Notion...")
        pages = await asyncio.get_event_loop().run_in_executor(
            None, workspace_setup.find_accessible_pages, text
        )

        if not pages:
            await status.edit_text(
                "I connected to Notion, but couldn't find any pages shared with this integration.\n\n"
                "Go to your blank Heritage Archive page in Notion → click <b>···</b> → "
                "<b>Connections</b> → add your integration. Then try again.",
                parse_mode=ParseMode.HTML,
            )
            return

        if len(pages) == 1:
            page = pages[0]
            await status.edit_text(
                f"Found page: <b>{_esc(page['title'])}</b>\n\nSetting up your workspace...",
                parse_mode=ParseMode.HTML,
            )
            await _run_workspace_setup(user_id, update.message, status, text, page["id"], page["title"], partial)

        else:
            # Multiple pages — let user choose
            partial["notion_token"] = text
            partial["_page_candidates"] = pages
            user_store.reg_set(user_id, "notion_page", partial)
            buttons = [
                [InlineKeyboardButton(p["title"][:60], callback_data=f"reg_page:{p['id']}")]
                for p in pages[:8]
            ]
            await status.edit_text(
                "I found multiple pages. Which one should I build your wardrobe inside?",
                reply_markup=InlineKeyboardMarkup(buttons),
            )

    elif step == "ai_key":
        partial["ai_key"] = text
        user_store.reg_set(user_id, "github", partial)
        buttons = [
            [InlineKeyboardButton("Yes, use GitHub for photo storage", callback_data="reg_github:yes")],
            [InlineKeyboardButton("Skip — use free image hosting", callback_data="reg_github:skip")],
        ]
        await update.message.reply_text(
            "<b>Almost done.</b> Where should outfit photos be stored?\n\n"
            "GitHub (recommended) — photos are permanent in your own repo.\n"
            "Free hosting — anonymous, no account needed, but links may expire.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    elif step == "github_token":
        partial["github_token"] = text
        user_store.reg_set(user_id, "github_repo", partial)
        await update.message.reply_text(
            "Token saved. Send your repository name:\n"
            "<code>username/repo-name</code>",
            parse_mode=ParseMode.HTML,
        )

    elif step == "github_repo":
        if "/" not in text:
            await update.message.reply_text("Format: <code>username/repo-name</code>", parse_mode=ParseMode.HTML)
            return
        partial["github_repo"] = text
        await _finish_registration(update, user_id, partial)


def _format_db_id(raw: str) -> str:
    """Format 32-char ID into dashed Notion UUID."""
    r = raw.replace("-", "")
    return f"{r[:8]}-{r[8:12]}-{r[12:16]}-{r[16:20]}-{r[20:]}"


async def _finish_registration(update_or_query, user_id: int, partial: dict):
    partial.setdefault("always_worn", [])
    partial.setdefault("registered_at", date.today().isoformat())
    partial.pop("_page_candidates", None)
    user_store.save(user_id, partial)
    user_store.reg_clear(user_id)

    msg = (
        "✅ <b>You're all set!</b>\n\n"
        "Send me an outfit photo to log your first OOTD.\n\n"
        "Your Notion workspace has:\n"
        "• <b>My Wardrobe</b> — add your items here\n"
        "• <b>My Lookbook</b> — outfit log, filled by the bot\n"
        "• Designers, Materials, Colours, Season lookup tables\n\n"
        "One thing to add manually in Notion (the API can't do it):\n"
        "In <b>My Wardrobe</b> → add a <b>Rollup</b> property called <b>Fits</b> "
        "counting entries from the Items back-relation. See the ⚙️ Setup Notes page in your workspace.\n\n"
        "/help — all commands"
    )

    if hasattr(update_or_query, 'message'):
        await update_or_query.message.reply_text(msg, parse_mode=ParseMode.HTML)
    else:
        await update_or_query.edit_message_text(msg, parse_mode=ParseMode.HTML)


# ── Admin: register another user ──────────────────────────────────────────────

async def cmd_admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /admin_add <user_id> <notion_token> <page_id_or_url> [ai_provider] [ai_key]
    Bot owner only. Creates the workspace for a user and registers them.
    page_id_or_url: the Notion page the integration has access to.
    """
    caller_id = update.effective_user.id
    if caller_id != config.BOT_OWNER_ID:
        await update.message.reply_text("This command is for the bot owner only.")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Usage: /admin_add <user_id> <notion_token> <notion_page_id> [ai_provider] [ai_key]\n\n"
            "notion_page_id: the 32-char ID of the page shared with the integration."
        )
        return

    target_id = int(args[0])
    token = args[1]
    raw_page = args[2].replace("-", "")
    page_id = _format_db_id(raw_page)

    status = await update.message.reply_text(f"Setting up workspace for user {target_id}...")

    errors = []
    result = None
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: workspace_setup.create_user_workspace(token, page_id)
        )
    except Exception as e:
        errors.append(e)

    if errors or result is None:
        await status.edit_text(f"Setup failed: {errors[0] if errors else 'unknown error'}")
        return

    cfg = {
        "notion_token": token,
        "collection_db_id": result["collection_db_id"],
        "ootd_db_id": result["ootd_db_id"],
        "ai_provider": args[3] if len(args) > 3 else "anthropic",
        "ai_key": args[4] if len(args) > 4 else "",
        "always_worn": [],
        "registered_at": date.today().isoformat(),
    }
    user_store.save(target_id, cfg)
    await status.edit_text(
        f"✅ User {target_id} registered. Workspace created.\n"
        f"Wardrobe DB: <code>{result['collection_db_id']}</code>",
        parse_mode=ParseMode.HTML,
    )


# ── Core commands ─────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_store.is_registered(user_id):
        cfg = user_store.get(user_id)
        items = catalog_mod.load(cfg, user_id)
        await update.message.reply_text(
            f"<b>Heritage Archive</b>\n\n"
            f"Your wardrobe: <b>{len(items)} items</b>.\n\n"
            "Send an outfit photo to log today's look.\n"
            "/stats — wardrobe analytics\n"
            "/always — manage daily items\n"
            "/help — all commands",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            "<b>Heritage Archive</b>\n\n"
            "Track what you own, log what you wear, understand your style.\n\n"
            "Run /register to connect your Notion workspace and get started.",
            parse_mode=ParseMode.HTML,
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    is_owner = update.effective_user.id == config.BOT_OWNER_ID
    owner_cmds = (
        "\n\n<b>Owner only</b>\n"
        "/idea &lt;text&gt; — save a future idea\n"
        "/ideas — view all saved ideas\n"
        "/idea remove &lt;id&gt; — remove an idea"
    ) if is_owner else ""
    await update.message.reply_text(
        "<b>Heritage Archive commands</b>\n\n"
        "📸 <b>Send a photo</b> — log today's outfit\n\n"
        "/date YYYY-MM-DD — set date for next outfit\n"
        "/refresh — reload your wardrobe from Notion\n"
        "/stats — coverage, CPW, dead weight, health score\n"
        "/always — items always added to every OOTD\n"
        "/register — connect Notion + AI provider\n"
        "/help — this message"
        + owner_cmds,
        parse_mode=ParseMode.HTML,
    )


async def cmd_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if err := _require_registration(user_id):
        await update.message.reply_text(err)
        return
    args = context.args
    if not args:
        current = _date_override.get(user_id, "not set (uses photo EXIF or today)")
        await update.message.reply_text(f"Date override: {current}\n\nUsage: /date YYYY-MM-DD")
        return
    try:
        date.fromisoformat(args[0])
        _date_override[user_id] = args[0]
        await update.message.reply_text(f"Date set to <b>{args[0]}</b>. Send your photo.", parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text("Invalid date. Use: /date YYYY-MM-DD")


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if err := _require_registration(user_id):
        await update.message.reply_text(err)
        return
    cfg = user_store.get(user_id)
    msg = await update.message.reply_text("Refreshing your wardrobe from Notion...")
    try:
        items = catalog_mod.refresh(cfg, user_id)
        await msg.edit_text(f"Done. {len(items)} items loaded.")
    except Exception as e:
        await msg.edit_text(f"Refresh failed: {e}")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if err := _require_registration(user_id):
        await update.message.reply_text(err)
        return
    cfg = user_store.get(user_id)
    msg = await update.message.reply_text("Computing your stats...")
    try:
        items = catalog_mod.load(cfg, user_id)
        data = analytics.compute(cfg, items)
        text = analytics.format_stats_message(data)
        await msg.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        await msg.edit_text(f"Stats failed: {e}")
        print(traceback.format_exc(), file=sys.stderr)


# ── Always-worn management ────────────────────────────────────────────────────

async def cmd_always(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if err := _require_registration(user_id):
        await update.message.reply_text(err)
        return
    cfg = user_store.get(user_id)
    args = context.args

    if not args or args[0] == "list":
        items = user_store.get_always_worn(user_id)
        if not items:
            await update.message.reply_text(
                "No always-worn items set.\n\nUse /always add <search> to add one.\nE.g. /always add cartier necklace"
            )
            return
        lines = ["<b>Always-worn (added to every OOTD):</b>\n"]
        for i, aw in enumerate(items, 1):
            url = f"https://www.notion.so/{aw['id'].replace('-', '')}"
            lines.append(f'{i}. <a href="{url}">{_esc(aw["name"])}</a>')
        lines.append("\n/always add &lt;search&gt;\n/always remove &lt;name&gt;\n/always clear")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return

    if args[0] == "clear":
        user_store.set_always_worn(user_id, [])
        await update.message.reply_text("Always-worn list cleared.")
        return

    if args[0] == "add":
        query = " ".join(args[1:]).lower()
        if not query:
            await update.message.reply_text("Usage: /always add <search>")
            return
        catalog = catalog_mod.load(cfg, user_id)
        matches = [c for c in catalog if query in c["name"].lower() or query in c.get("designer", "").lower()][:6]
        if not matches:
            await update.message.reply_text(f"No items found for '{query}'.")
            return
        buttons = [
            [InlineKeyboardButton(f"{m['name'][:40]}", callback_data=f"always_add:{m['id']}")]
            for m in matches
        ]
        await update.message.reply_text("Select item:", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if args[0] == "remove":
        query = " ".join(args[1:]).lower()
        current = user_store.get_always_worn(user_id)
        new_list = [i for i in current if query not in i["name"].lower()]
        removed = len(current) - len(new_list)
        user_store.set_always_worn(user_id, new_list)
        await update.message.reply_text(f"Removed {removed} item(s).")
        return

    await update.message.reply_text("Usage: /always | /always add <search> | /always remove <name> | /always clear")


# ── Ideas / future backlog ────────────────────────────────────────────────────

async def cmd_idea(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /idea <text>          — add a new idea
    /idea list            — same as /ideas
    /idea remove <id>     — remove idea by number
    """
    user_id = update.effective_user.id
    if user_id != config.BOT_OWNER_ID:
        return
    args = context.args

    if not args or args[0] == "list":
        await update.message.reply_text(ideas_mod.format_list(), parse_mode=ParseMode.HTML)
        return

    if args[0] == "remove":
        if len(args) < 2 or not args[1].isdigit():
            await update.message.reply_text("Usage: /idea remove <id>")
            return
        removed = ideas_mod.remove(int(args[1]))
        await update.message.reply_text("Removed." if removed else "ID not found.")
        return

    text = " ".join(args)
    idea = ideas_mod.add(text)
    total = len(ideas_mod.all_ideas())
    await update.message.reply_text(
        f"💡 Saved: <i>{_esc(idea['text'])}</i>\n<code>#{idea['id']}</code>  ·  {total} idea{'s' if total != 1 else ''} total",
        parse_mode=ParseMode.HTML,
    )


async def cmd_ideas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != config.BOT_OWNER_ID:
        return
    await update.message.reply_text(ideas_mod.format_list(), parse_mode=ParseMode.HTML)


async def _send_ideas_digest(context: ContextTypes.DEFAULT_TYPE):
    """Daily check: send bi-weekly ideas digest to bot owner when due."""
    if not ideas_mod.due_for_digest():
        return
    all_i = ideas_mod.all_ideas()
    lines = ["<b>Heritage Archive — ideas digest</b>\n"]
    for idea in all_i:
        lines.append(f"• [{idea['id']}] {idea['text']}")
    lines.append(f"\n<i>{len(all_i)} idea{'s' if len(all_i) != 1 else ''} · /ideas to view · /idea remove &lt;id&gt; to clear</i>")
    await context.bot.send_message(
        chat_id=config.BOT_OWNER_ID,
        text="\n".join(lines),
        parse_mode=ParseMode.HTML,
    )
    ideas_mod.mark_digest_sent()


# ── Photo handling ────────────────────────────────────────────────────────────

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if err := _require_registration(user_id):
        await update.message.reply_text(err)
        return
    try:
        await _handle_photo_inner(update, context)
    except Exception as e:
        print(f"[photo] error: {e!r}\n{traceback.format_exc()}", flush=True)
        try:
            await update.effective_message.reply_text(f"⚠️ Error: {e}")
        except Exception:
            pass


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if err := _require_registration(user_id):
        await update.message.reply_text(err)
        return
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        return
    raw = await _download(doc.file_id, context, "document")
    await _route_image(update, context, raw)


async def _handle_photo_inner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = await _download(update.message.photo[-1].file_id, context, "photo")
    await _route_image(update, context, raw)


async def _download(file_id: str, context: ContextTypes.DEFAULT_TYPE, label: str, retries: int = 3) -> bytes:
    from telegram.error import TimedOut
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            f = await context.bot.get_file(file_id)
            buf = BytesIO()
            await f.download_to_memory(buf)
            return buf.getvalue()
        except TimedOut as e:
            last_err = e
            await asyncio.sleep(2 * attempt)
    raise last_err


async def _route_image(update: Update, context: ContextTypes.DEFAULT_TYPE, raw: bytes):
    user_id = update.effective_user.id
    mgid = update.message.media_group_id

    if mgid:
        if mgid not in _media_groups:
            task = asyncio.create_task(_flush_album(mgid, context))
            _media_groups[mgid] = {"update": update, "all_images": [raw], "task": task}
        else:
            _media_groups[mgid]["all_images"].append(raw)
    else:
        session = _sessions.get(user_id)
        if session:
            session["all_images"].append(raw)
            n = len(session["all_images"])
            await update.message.reply_text(f"📸 Photo {n} added to your current outfit.")
        else:
            await _process_images(update, context, [raw])


async def _flush_album(media_group_id: str, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(config.ALBUM_COLLECT_SECS)
    data = _media_groups.pop(media_group_id, None)
    if data:
        await _process_images(data["update"], context, data["all_images"])


async def _process_images(update: Update, context: ContextTypes.DEFAULT_TYPE, all_images: list[bytes]):
    user_id = update.effective_user.id
    primary = all_images[0]
    img_hash = hashlib.sha256(primary).hexdigest()
    n = len(all_images)
    label = f"📸 {n} photo{'s' if n > 1 else ''} received."

    if user_id in _date_override:
        outfit_date = _date_override.pop(user_id)
        msg = await _safe_reply(update.message,
            f"{label}\n📅 <b>{outfit_date}</b> <i>(set manually)</i>",
            parse_mode=ParseMode.HTML)
        await _run_ai_and_review(update, context, all_images, img_hash, outfit_date, status_msg=msg)
        return

    exif_date = _exif_date(primary)
    if exif_date:
        msg = await _safe_reply(update.message,
            f"{label}\n📅 <b>{exif_date}</b> <i>(from photo metadata)</i>",
            parse_mode=ParseMode.HTML)
        await _run_ai_and_review(update, context, all_images, img_hash, exif_date, status_msg=msg)
    else:
        _pending[user_id] = {"all_images": all_images, "img_hash": img_hash}
        today = date.today()
        options = [
            (today.isoformat(), "Today"),
            ((today - timedelta(days=1)).isoformat(), "Yesterday"),
            ((today - timedelta(days=2)).isoformat(), (today - timedelta(days=2)).strftime("%a %-d %b")),
            ((today - timedelta(days=3)).isoformat(), (today - timedelta(days=3)).strftime("%a %-d %b")),
        ]
        buttons = [
            [InlineKeyboardButton(lbl, callback_data=f"setdate:{iso}")]
            for iso, lbl in options
        ] + [[InlineKeyboardButton("📅 Pick another date", callback_data="setdate:pick")]]

        await _safe_reply(update.message,
            f"{label}\n⚠️ <i>No date in metadata — send as a <b>File</b> next time to preserve it.</i>\n\n<b>When was this worn?</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
            disable_web_page_preview=True,
        )


async def _run_ai_and_review(update, context, all_images, img_hash, outfit_date, status_msg=None):
    user_id = update.effective_user.id
    cfg = user_store.get(user_id)
    eff_msg = update.effective_message

    msg = status_msg or await eff_msg.reply_text("🔍 Identifying items...")
    await msg.edit_text(f"📅 <b>{outfit_date}</b>\n\n🔍 Identifying items...", parse_mode=ParseMode.HTML)

    try:
        catalog = catalog_mod.load(cfg, user_id)
    except Exception as e:
        await msg.edit_text(f"Failed to load your wardrobe: {e}")
        return

    primary_resized = _resize(all_images[0])
    image_b64 = base64.standard_b64encode(primary_resized).decode()

    try:
        match_output = await asyncio.get_event_loop().run_in_executor(
            None, vision_mod.run_matching, cfg, user_id, image_b64, catalog, img_hash
        )
    except Exception as e:
        await msg.edit_text(f"AI matching failed: {e}")
        print(traceback.format_exc(), file=sys.stderr)
        return

    results = match_output["results"]
    season = match_output["season"]

    if not results:
        await msg.edit_text("Could not identify any items. Try a clearer photo.")
        return

    always_decisions = _build_always_worn_decisions(user_store.get_always_worn(user_id), catalog)

    _sessions[user_id] = {
        "all_images": all_images,
        "img_hash": img_hash,
        "date": outfit_date,
        "season": season,
        "results": results,
        "decisions": [None] * len(results),
        "always_worn_decisions": always_decisions,
        "extra_items": [],
    }

    matched = sum(1 for r in results if r["status"] == "matched")
    ambiguous = sum(1 for r in results if r["status"] == "ambiguous")
    unidentified = sum(1 for r in results if r["status"] == "unidentified")
    photo_note = f" · {len(all_images)} photos" if len(all_images) > 1 else ""

    await msg.edit_text(
        f"📅 <b>{outfit_date}</b>{photo_note} · <b>{season}</b>\n"
        f"Found <b>{len(results)} item(s)</b> — {matched} matched, {ambiguous} maybe, {unidentified} unknown.",
        parse_mode=ParseMode.HTML,
    )
    await _show_next_item(eff_msg, _sessions[user_id], user_id)


def _build_always_worn_decisions(always_worn: list[dict], catalog: list[dict]) -> list[dict]:
    decisions = []
    for aw in always_worn:
        item = next((c for c in catalog if c["id"] == aw["id"]), None)
        if item:
            decisions.append({"action": "approved", "item_id": aw["id"], "item_name": aw["name"], "always_worn": True})
    return decisions


# ── Item review cards ─────────────────────────────────────────────────────────

def _status_emoji(status: str) -> str:
    return {"matched": "✅", "ambiguous": "⚠️", "unidentified": "❓"}.get(status, "•")


def _build_item_card(result: dict, idx: int, total: int, decision: dict | None = None) -> tuple[str, InlineKeyboardMarkup]:
    ident = result["identified"]
    top = result["top_matches"]
    emoji = _status_emoji(result["status"])
    memory_tag = " 🧠" if result.get("from_memory") else ""

    lines = [f"<b>Item {idx + 1} of {total}</b> {emoji}{memory_tag}"]
    lines.append(f"<i>{_esc(ident['type'].title())} — {_esc(ident['colour'])}</i>")
    lines.append(_esc(ident['description'][:120]))
    lines.append("")

    if top:
        best = top[0]
        item = best["item"]
        conf = int(best["confidence"] * 100)
        url = _notion_url(item)
        lines.append(f"<b>Best match ({conf}%):</b>")
        lines.append(f'<a href="{url}">{_esc(item["name"])}</a>')
        if item.get("designer"):
            lines.append(f'  {_esc(item["designer"])}')
        if item.get("colour"):
            lines.append(f'  {_esc(item["colour"])}')
        lines.append(f'  <code>{_esc(item["sku"])}</code>')
        if len(top) > 1:
            alt = top[1]
            alt_url = _notion_url(alt["item"])
            lines.append(f'\n<i>Alt ({int(alt["confidence"]*100)}%): <a href="{alt_url}">{_esc(alt["item"]["name"])}</a></i>')
    else:
        lines.append("<i>No match found in your wardrobe.</i>")

    if decision:
        action = decision["action"]
        if action == "approved":
            lines.append("\n✅ <b>Approved</b>")
        elif action == "skipped":
            lines.append("\n⏭️ <b>Skipped</b>")
        elif action == "changed":
            lines.append(f"\n✏️ Changed to: {_esc(decision.get('item_name', '?'))}")
        elif action == "new_item":
            lines.append(f"\n➕ New item: {_esc(decision.get('item_name', '?'))}")

    text = "\n".join(lines)
    buttons = []

    if not decision:
        if top:
            buttons.append([
                InlineKeyboardButton("✅ Correct", callback_data=f"approve:{idx}"),
                InlineKeyboardButton("⏭️ Skip", callback_data=f"skip:{idx}"),
            ])
            if len(top) > 1:
                buttons.append([InlineKeyboardButton(
                    f"↕️ Use: {top[1]['item']['name'][:28]}",
                    callback_data=f"alt:{idx}",
                )])
        else:
            buttons.append([
                InlineKeyboardButton("⏭️ Skip", callback_data=f"skip:{idx}"),
                InlineKeyboardButton("➕ Add to wardrobe", callback_data=f"new_item:{idx}"),
            ])
        buttons.append([InlineKeyboardButton("🔍 Search wardrobe", callback_data=f"search:{idx}")])

    return text, InlineKeyboardMarkup(buttons) if buttons else InlineKeyboardMarkup([])


def _build_summary(session: dict) -> str:
    results = session["results"]
    decisions = session["decisions"]
    always = session.get("always_worn_decisions", [])

    approved = [(r, d) for r, d in zip(results, decisions) if d and d["action"] in ("approved", "changed", "new_item")]
    skipped = [r for r, d in zip(results, decisions) if d and d["action"] == "skipped"]

    season_tag = f" · <b>{session.get('season', '')}</b>" if session.get("season") else ""
    lines = [f"<b>Ready to log</b>{season_tag}\n"]

    for d in always:
        url = f"https://www.notion.so/{d['item_id'].replace('-', '')}"
        lines.append(f'💍 <a href="{url}">{_esc(d["item_name"])}</a> <i>(daily)</i>')

    for r, d in approved:
        item_name = d.get("item_name") or (r["top_matches"][0]["item"]["name"] if r["top_matches"] else "?")
        item_id = d.get("item_id", "")
        if item_id:
            url = f"https://www.notion.so/{item_id.replace('-', '')}"
            prefix = "➕" if d["action"] == "new_item" else "✅"
            lines.append(f'{prefix} <a href="{url}">{_esc(item_name)}</a>')
        else:
            lines.append(f"✅ {_esc(item_name)}")

    for r in skipped:
        lines.append(f"⏭️ <i>{_esc(r['identified']['type'])} — skipped</i>")

    for extra in session.get("extra_items", []):
        url = f"https://www.notion.so/{extra['item_id'].replace('-', '')}"
        lines.append(f'➕ <a href="{url}">{_esc(extra["item_name"])}</a>')

    total = len(always) + len(approved) + len(session.get("extra_items", []))
    lines.append(f"\n📅 {session['date']} · {total} item(s) to log")

    return "\n".join(lines)


async def _show_next_item(message, session: dict, user_id: int):
    results = session["results"]
    decisions = session["decisions"]

    for idx in range(len(results)):
        if decisions[idx] is None:
            text, keyboard = _build_item_card(results[idx], idx, len(results))
            try:
                await _safe_reply(message, text, parse_mode=ParseMode.HTML,
                                  reply_markup=keyboard, disable_web_page_preview=True)
            except TgNetworkError:
                _sessions.pop(user_id, None)
            return

    # All decided — show summary
    summary = _build_summary(session)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Log to Notion", callback_data="confirm"),
         InlineKeyboardButton("🗑️ Cancel", callback_data="cancel")],
        [InlineKeyboardButton("➕ Add missing item", callback_data="add_item")],
    ])
    await _safe_reply(message, summary, parse_mode=ParseMode.HTML,
                      reply_markup=keyboard, disable_web_page_preview=True)


# ── Callback handlers ─────────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)
    user_id = update.effective_user.id
    data = query.data

    # Registration callbacks
    if data.startswith("reg_page:"):
        page_id = data.split(":", 1)[1]
        reg = user_store.reg_get(user_id)
        if not reg:
            await _safe_edit(query, "Registration session expired. Run /register again.")
            return
        partial = reg["partial"]
        token = partial["notion_token"]
        candidates = partial.get("_page_candidates", [])
        page_title = next((p["title"] for p in candidates if p["id"] == page_id), "your page")
        await _safe_edit(query, f"Setting up workspace on <b>{_esc(page_title)}</b>...", parse_mode=ParseMode.HTML)
        await _run_workspace_setup(user_id, query.message, query.message, token, page_id, page_title, partial)
        return

    if data.startswith("reg_provider:"):
        provider = data.split(":", 1)[1]
        reg = user_store.reg_get(user_id)
        if not reg:
            await _safe_edit(query, "Registration session expired. Run /register again.")
            return
        reg["partial"]["ai_provider"] = provider
        if provider == "bot":
            reg["partial"]["ai_key"] = ""
            user_store.reg_set(user_id, "github", reg["partial"])
            buttons = [
                [InlineKeyboardButton("Yes, use GitHub for photo storage", callback_data="reg_github:yes")],
                [InlineKeyboardButton("Skip — use free image hosting", callback_data="reg_github:skip")],
            ]
            await _safe_edit(query,
                "Using the bot's shared AI key.\n\n"
                "Where should outfit photos be stored?\n\n"
                "GitHub — permanent, in your own repo.\n"
                "Free hosting — no account needed.",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        else:
            user_store.reg_set(user_id, "ai_key", reg["partial"])
            provider_names = {"anthropic": "Anthropic", "openai": "OpenAI", "openrouter": "OpenRouter"}
            await _safe_edit(query, f"{provider_names[provider]} selected.\n\nSend me your API key:")
        return

    if data.startswith("reg_github:"):
        choice = data.split(":", 1)[1]
        reg = user_store.reg_get(user_id)
        if not reg:
            await _safe_edit(query, "Registration session expired. Run /register again.")
            return
        if choice == "skip":
            await _finish_registration(query, user_id, reg["partial"])
        else:
            user_store.reg_set(user_id, "github_token", reg["partial"])
            await _safe_edit(query,
                "Send me your GitHub personal access token (needs repo write permission).\n\n"
                "Get one at github.com → Settings → Developer settings → Personal access tokens"
            )
        return

    # Always-worn add callback
    if data.startswith("always_add:"):
        item_id = data.split(":", 1)[1]
        cfg = user_store.get(user_id)
        catalog = catalog_mod.load(cfg, user_id)
        item = next((c for c in catalog if c["id"] == item_id), None)
        if not item:
            await _safe_edit(query, "Item not found. Try /refresh then retry.")
            return
        current = user_store.get_always_worn(user_id)
        if any(i["id"] == item_id for i in current):
            await _safe_edit(query, f"{item['name']} is already in your always-worn list.")
            return
        current.append({"id": item_id, "name": item["name"]})
        user_store.set_always_worn(user_id, current)
        url = _notion_url(item)
        await _safe_edit(query,
            f'💍 Added: <a href="{url}">{_esc(item["name"])}</a>\n\nAdded to every OOTD automatically.',
            parse_mode=ParseMode.HTML)
        return

    # Date picker callback
    if data.startswith("setdate:"):
        await handle_setdate_callback(update, context)
        return

    # Pick callback (search result selected)
    if data.startswith("pick:"):
        await handle_pick_callback(update, context)
        return

    # Add item from results
    if data.startswith("add_pick:"):
        await handle_add_pick_callback(update, context)
        return

    # Session callbacks
    session = _sessions.get(user_id)

    if data == "confirm":
        await _do_confirm(query, session, user_id)
        return

    if data == "cancel":
        _sessions.pop(user_id, None)
        await _safe_edit(query, "Cancelled.")
        return

    if data == "add_item":
        if not session:
            await _safe_edit(query, "Session expired. Send a new photo.")
            return
        session["adding_item"] = True
        await _safe_edit(query, "➕ <b>Add item</b>\n\nType keywords to search your wardrobe:", parse_mode=ParseMode.HTML)
        return

    if data == "add_cancel":
        if session:
            session.pop("adding_item", None)
        await _safe_edit(query, "Cancelled.")
        return

    if not session:
        await _safe_edit(query, "Session expired. Send a new photo.")
        return

    # Item decisions
    parts = data.split(":", 1)
    action, idx_str = parts[0], parts[1]
    idx = int(idx_str)
    result = session["results"][idx]

    if action == "approve":
        if not result["top_matches"]:
            await query.answer("No match to approve.", show_alert=True)
            return
        item = result["top_matches"][0]["item"]
        session["decisions"][idx] = {"action": "approved", "item_id": item["id"], "item_name": item["name"]}
        text, _ = _build_item_card(result, idx, len(session["results"]), session["decisions"][idx])
        await _safe_edit(query, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        await _show_next_item(query.message, session, user_id)

    elif action == "alt":
        if len(result["top_matches"]) < 2:
            return
        item = result["top_matches"][1]["item"]
        session["decisions"][idx] = {"action": "changed", "item_id": item["id"], "item_name": item["name"]}
        text, _ = _build_item_card(result, idx, len(session["results"]), session["decisions"][idx])
        await _safe_edit(query, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        await _show_next_item(query.message, session, user_id)

    elif action == "skip":
        session["decisions"][idx] = {"action": "skipped", "item_id": None}
        text, _ = _build_item_card(result, idx, len(session["results"]), session["decisions"][idx])
        await _safe_edit(query, text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        await _show_next_item(query.message, session, user_id)

    elif action == "search":
        session["searching_for_idx"] = idx
        await _safe_edit(query,
            f"🔍 <b>Search your wardrobe</b> for item {idx + 1}:\n"
            f"<i>{_esc(result['identified']['description'][:100])}</i>\n\n"
            "Type keywords (name, colour, brand, SKU):",
            parse_mode=ParseMode.HTML,
        )

    elif action == "new_item":
        # Not in wardrobe — prompt to add
        session["adding_new_for_idx"] = idx
        ident = result["identified"]
        draft_name = f"{ident['colour'].title()} {ident['type'].title()}"
        session[f"draft_name_{idx}"] = draft_name
        await _safe_edit(query,
            f"➕ <b>Add to your wardrobe</b>\n\n"
            f"Identified: <i>{_esc(ident['description'][:120])}</i>\n\n"
            f"I'll create a draft entry. What's the name of this piece?\n"
            f"(or send the suggested name: <code>{_esc(draft_name)}</code>)",
            parse_mode=ParseMode.HTML,
        )


async def handle_setdate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)
    user_id = update.effective_user.id
    pending = _pending.get(user_id)
    if not pending:
        await _safe_edit(query, "Session expired. Send the photo again.")
        return

    chosen = query.data.split(":", 1)[1]
    if chosen == "pick":
        _pending[user_id]["waiting_for_date_text"] = True
        await _safe_edit(query, "Type the date: <b>YYYY-MM-DD</b>", parse_mode=ParseMode.HTML)
        return

    _pending.pop(user_id, None)
    await _run_ai_and_review(update, context, pending["all_images"], pending["img_hash"], chosen, status_msg=query.message)


async def handle_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)
    user_id = update.effective_user.id
    session = _sessions.get(user_id)
    if not session:
        return
    parts = query.data.split(":", 2)
    idx = int(parts[1])
    item_id = parts[2]
    cfg = user_store.get(user_id)
    catalog = catalog_mod.load(cfg, user_id)
    item = next((i for i in catalog if i["id"] == item_id), None)
    item_name = item["name"] if item else item_id
    session["decisions"][idx] = {"action": "changed", "item_id": item_id, "item_name": item_name}
    session.pop("searching_for_idx", None)
    await _safe_edit(query, f"✏️ Set to: <b>{_esc(item_name)}</b>", parse_mode=ParseMode.HTML)
    await _show_next_item(query.message, session, user_id)


async def handle_add_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await _safe_answer(query)
    user_id = update.effective_user.id
    session = _sessions.get(user_id)
    if not session:
        return
    item_id = query.data.split(":", 1)[1]
    cfg = user_store.get(user_id)
    catalog = catalog_mod.load(cfg, user_id)
    item = next((i for i in catalog if i["id"] == item_id), None)
    item_name = item["name"] if item else item_id
    session.setdefault("extra_items", []).append({"item_id": item_id, "item_name": item_name})
    session.pop("adding_item", None)
    url = f"https://www.notion.so/{item_id.replace('-', '')}"
    await _safe_edit(query, f'➕ Added: <a href="{url}">{_esc(item_name)}</a>', parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    summary = _build_summary(session)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Log to Notion", callback_data="confirm"),
         InlineKeyboardButton("🗑️ Cancel", callback_data="cancel")],
        [InlineKeyboardButton("➕ Add missing item", callback_data="add_item")],
    ])
    await query.message.reply_text(summary, parse_mode=ParseMode.HTML, reply_markup=keyboard, disable_web_page_preview=True)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text input: registration wizard, search queries, date input, new item names."""
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # Registration wizard
    reg = user_store.reg_get(user_id)
    if reg:
        await handle_registration_text(update, context, reg)
        return

    # Pending date input
    pending = _pending.get(user_id)
    if pending and pending.get("waiting_for_date_text"):
        try:
            date.fromisoformat(text)
        except ValueError:
            await update.message.reply_text("Invalid format. Use YYYY-MM-DD:")
            return
        _pending.pop(user_id, None)
        msg = await update.message.reply_text(f"📅 <b>{text}</b>\n\n🔍 Identifying items...", parse_mode=ParseMode.HTML)
        await _run_ai_and_review(update, context, pending["all_images"], pending["img_hash"], text, status_msg=msg)
        return

    session = _sessions.get(user_id)
    if not session:
        return

    # New item name input
    if "adding_new_for_idx" in session:
        idx = session.pop("adding_new_for_idx")
        item_name = text or session.get(f"draft_name_{idx}", "New item")
        cfg = user_store.get(user_id)
        try:
            page_id = notion.create_item(cfg, title=item_name)
            session["decisions"][idx] = {"action": "new_item", "item_id": page_id, "item_name": item_name}
            url = f"https://www.notion.so/{page_id.replace('-', '')}"
            await update.message.reply_text(
                f'➕ Created: <a href="{url}">{_esc(item_name)}</a>\n\n'
                f'<i>Edit the full details in Notion later (price, brand, care info).</i>',
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
            await _show_next_item(update.message, session, user_id)
        except Exception as e:
            await update.message.reply_text(f"Failed to create item: {e}")
        return

    # Search query
    if "searching_for_idx" not in session and not session.get("adding_item"):
        return

    cfg = user_store.get(user_id)
    catalog = catalog_mod.load(cfg, user_id)
    q = text.lower()
    matches = [
        i for i in catalog
        if q in i["name"].lower() or q in i["sku"].lower()
        or q in i.get("colour", "").lower() or q in i.get("designer", "").lower()
    ][:8]

    if not matches:
        await update.message.reply_text(f"No results for '{_esc(text)}'. Try different keywords.")
        return

    adding = session.get("adding_item", False)
    idx = session.get("searching_for_idx")

    buttons = []
    for m in matches:
        label = f"{m['name'][:30]} | {m.get('designer', '')[:12]} | {m.get('colour', '')[:10]}"
        cb = f"add_pick:{m['id']}" if adding else f"pick:{idx}:{m['id']}"
        buttons.append([InlineKeyboardButton(label, callback_data=cb)])

    if adding:
        buttons.append([InlineKeyboardButton("✖️ Cancel", callback_data="add_cancel")])
    else:
        buttons.append([InlineKeyboardButton("⏭️ Skip this item", callback_data=f"skip:{idx}")])

    await update.message.reply_text(
        f"Results for <b>{_esc(text)}</b>:",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=True,
    )


# ── Confirm + write to Notion ─────────────────────────────────────────────────

async def _do_confirm(query, session: dict, user_id: int):
    if not session:
        await _safe_edit(query, "Session expired.")
        return

    cfg = user_store.get(user_id)
    always = session.get("always_worn_decisions", [])
    decisions = session["decisions"]

    always_ids = [d["item_id"] for d in always if d.get("item_id")]
    reviewed_ids = [
        d["item_id"] for d in decisions
        if d and d["action"] in ("approved", "changed", "new_item") and d.get("item_id")
    ]
    extra_ids = [e["item_id"] for e in session.get("extra_items", []) if e.get("item_id")]

    seen = set(always_ids)
    item_ids = always_ids[:]
    for iid in reviewed_ids + extra_ids:
        if iid not in seen:
            item_ids.append(iid)
            seen.add(iid)

    if not item_ids:
        await _safe_edit(query, "No items approved. Nothing to log.")
        _sessions.pop(user_id, None)
        return

    # Save corrections
    results = session.get("results", [])
    records = []
    for r, d in zip(results, decisions):
        if d and d["action"] in ("approved", "changed") and d.get("item_id"):
            ident = r["identified"]
            ai_top = r["top_matches"][0]["item"] if r.get("top_matches") else None
            records.append({
                "item_type": ident.get("type", ""),
                "item_colour": ident.get("colour", ""),
                "visual_description": ident.get("description", ""),
                "ai_top_id": ai_top["id"] if ai_top else None,
                "ai_top_name": ai_top["name"] if ai_top else None,
                "correct_id": d["item_id"],
                "correct_name": d["item_name"],
            })
    if records:
        corrections.save_decisions(user_id, session.get("img_hash", ""), session["date"], records)

    all_images = session.get("all_images", [])
    n = len(all_images)
    await _safe_edit(query, f"⏳ Uploading {n} photo{'s' if n > 1 else ''} and logging to Notion...")

    loop = asyncio.get_event_loop()
    image_urls = []
    for i, img_bytes in enumerate(all_images):
        suffix = f"_{i+1}" if n > 1 else ""
        url = await loop.run_in_executor(None, notion.host_image, cfg, img_bytes, session["date"], suffix)
        if url:
            image_urls.append(url)

    try:
        page_id = await loop.run_in_executor(
            None, notion.create_ootd_entry,
            cfg, session["date"], item_ids,
            image_urls if image_urls else None,
            session.get("season"),
        )
        clean_id = page_id.replace("-", "")
        notion_url = f"https://www.notion.so/{clean_id}"
        img_note = f"📷 {len(image_urls)} photo{'s' if len(image_urls) > 1 else ''}" if image_urls else "📷 Image upload failed"
        await query.message.reply_text(
            f'✅ <a href="{notion_url}">OOTD {session["date"]}</a> logged.\n'
            f'{img_note} · {len(item_ids)} item(s)',
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        await query.message.reply_text(f"⚠️ Failed to create Notion entry: {e}")
        print(traceback.format_exc(), file=sys.stderr)
    finally:
        _sessions.pop(user_id, None)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not config.TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set. Add it to your .env file.", file=sys.stderr)
        sys.exit(1)

    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("register_reset", cmd_register_reset))
    app.add_handler(CommandHandler("admin_add", cmd_admin_add))
    app.add_handler(CommandHandler("date", cmd_date))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("always", cmd_always))
    app.add_handler(CommandHandler("idea", cmd_idea))
    app.add_handler(CommandHandler("ideas", cmd_ideas))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    app.add_handler(CallbackQueryHandler(handle_callback))

    # Bi-weekly ideas digest — fires every 14 days, daily check at 09:00
    if app.job_queue and config.BOT_OWNER_ID:
        import datetime as dt
        app.job_queue.run_daily(
            _send_ideas_digest,
            time=dt.time(hour=9, minute=0),
            days=(0, 1, 2, 3, 4, 5, 6),
            name="ideas_digest_check",
        )

    print("Heritage Archive bot running...", flush=True)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
