# Heritage Archive — Issues & Fixes Log

Running record of bugs, root causes, and fixes. Add new entries at the top.

---

## 2026-07-01 — Search returns no results for multi-word queries

**Symptom:** Typing "Akris Silk" in search returns nothing even though there are Akris items with silk fabric.

**Root cause:** Search in `bot.py handle_text` did a single full-string substring match: `"akris silk" in item["name"].lower()`. If "Akris" is in the `designer` field and "Silk" is in the item name, the full string "akris silk" doesn't appear in any single field — so nothing matches.

**Fix:** Tokenise the query and require every token to appear somewhere in the combined haystack of name + sku + colour + designer + type + category. "Akris Silk" → ["akris", "silk"] — both must appear anywhere across all fields.

**File:** `bot.py` → `handle_text()` search block (~line 1370)

---

## 2026-07-01 — Buttons dead after bot restart (primary bug)

**Symptom:** User sends a photo, bot identifies items and sends review cards with Approve/Skip buttons. Bot restarts (crash, Mac sleep, pkill). User taps a button → nothing happens or "Session expired."

**Root cause:** `_sessions` is a plain in-memory Python dict. It is wiped every time the process restarts. The inline keyboard buttons remain in Telegram's UI indefinitely, but the session data they depend on is gone.

**Fix:** Persist sessions to disk in `data/sessions.json` on every meaningful mutation. Load on startup. Images stored as base64 so the full session is portable. Key save points:
- After session creation (`_sessions[user_id] = {...}`)
- In `_show_next_item()` before sending the next card (captures every decision update)
- After every `_sessions.pop()` (clears the file entry)
- After extra-item append in `handle_add_pick_callback`

**Files:** `bot.py` → `_save_sessions()`, `_load_sessions()`, `_SESSION_FILE`; called in `main()`, `_run_ai_inner()`, `_show_next_item()`, `handle_callback()`, `_do_confirm()`

---

## 2026-07-01 — Kimi 401 logged on every single AI text call

**Symptom:** Log fills with `[ai] Kimi auth failed (...), falling back to Anthropic` on every outfit identification. Adds ~1–2s latency per call and hides real errors in noise.

**Root cause:** Kimi key (`sk-HhbeI...`) is expired/invalid. `_text_provider_and_key()` still returns "kimi" because `BOT_KIMI_KEY` is set in config. Every `text_call()` tries Kimi, gets 401, then falls back to Anthropic — repeating this on every call.

**Fix:** Added module-level `_kimi_dead = False` flag in `ai_client.py`. First 401 sets it True. All subsequent calls skip Kimi entirely and go straight to Anthropic without attempting the request.

**File:** `ai_client.py` → `text_call()` (~line 79)

**Remaining:** The Kimi key should be removed from `.env` / `config.py` or replaced with a valid one to clean this up permanently. The flag is a runtime workaround; it resets on each bot restart.

---

## 2026-06-29 — Multiple bot instances running simultaneously

**Symptom:** Buttons clickable but nothing happens. Session state correct in one process but button tap routes to a different process with an empty `_sessions`.

**Root cause:** Running `python3 bot.py` multiple times (or re-running after a crash without killing the old process) creates multiple polling instances. Telegram distributes updates across them. Process A holds the session; Process B receives the callback — `_sessions.get(user_id)` returns None.

**Fix:** Pidfile at `bot.pid`. On startup, `_acquire_pidfile()` reads the existing PID, sends SIGTERM to it, waits 1s, then writes the new PID. `atexit` removes the file on clean exit.

**File:** `bot.py` → `_acquire_pidfile()`, `_PIDFILE`, called in `main()`

---

## 2026-06-29 — NetworkError (ConnectError, ReadError) not retried on download

**Symptom:** Photo sent to bot → bot silently fails with `[photo] error: NetworkError('httpx.ConnectError: ')` → user sees no response, no status message.

**Root cause:** `_download()` only caught `TimedOut` for retry. `NetworkError` subclasses (`ConnectError`, `ReadError`) caused immediate failure and propagated up — before any status message was sent.

**Fix:** Changed except clause from `except TimedOut` to `except (TimedOut, NetworkError)`. Retries increased from 3 to 4. Wait time is `2 * attempt` seconds (2, 4, 6, 8s).

**File:** `bot.py` → `_download()` (~line 725)

---

## 2026-06-28 — Silent exceptions in callback handlers

**Symptom:** Error occurs inside a callback (e.g. `handle_setdate_callback`) but user sees nothing — no error message, no response.

**Root cause:** python-telegram-bot v20+ swallows exceptions in handlers when no error handler is registered. Exceptions in `_run_ai_and_review` were discarded silently.

**Fix (two-part):**
1. Wrapped `_run_ai_inner` in try/except inside `_run_ai_and_review`, with explicit `_safe_msg_edit(msg, f"⚠️ Error: {e}")` on failure.
2. Registered a PTB error handler via `app.add_error_handler(_error_handler)` in `main()`.

**File:** `bot.py` → `_run_ai_and_review()`, `main()`

---

## 2026-06-28 — "Message is not modified" BadRequest crash

**Symptom:** Bot crashes with `telegram.error.BadRequest: Message is not modified` when trying to update a status message with identical text.

**Root cause:** `msg.edit_text(text)` raises `BadRequest` if the new text is exactly the same as the current text. This happened when the AI status update produced the same string twice.

**Fix:** Added `_safe_msg_edit()` helper that catches `BadRequest` and silently ignores it when the error message contains "not modified". All `msg.edit_text()` calls inside `_run_ai_inner` replaced with `_safe_msg_edit()`.

**File:** `bot.py` → `_safe_msg_edit()`

---

## 2026-06-27 — Kimi auth failure blocked item matching

**Symptom:** Bot identifies item types from photo (e.g. "white fitted cotton crew neck") but never matches them to specific items in the collection.

**Root cause:** `text_call()` with provider="kimi" returned 401 on every call. The exception was not caught at the caller level, so `match_item()` in `vision.py` returned empty results silently. Bot showed the description but no match candidates.

**Fix:** Added try/except in `text_call()` around the Kimi path. On 401/authentication errors, falls back to Anthropic automatically. (Later made permanent with `_kimi_dead` flag — see entry above.)

**File:** `ai_client.py` → `text_call()`

---

## Notes

- **Kimi key:** The bot config still has a dead Kimi key. Replace it with a valid one at platform.moonshot.cn or remove `BOT_KIMI_KEY` from `.env` entirely to route all text calls directly to Anthropic.
- **Bot runs on Mac:** Any Mac sleep or network drop can kill the process. If this becomes a recurring issue, consider running on a always-on VPS or using `launchd` to auto-restart on crash.
- **Sessions file:** `data/sessions.json` — only contains in-progress review sessions. Safe to delete if sessions need to be reset.
