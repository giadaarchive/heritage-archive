"""
Unified AI client — routes to Anthropic, OpenAI, or OpenRouter.

Usage:
    from ai_client import text_call, vision_call

    text = text_call(cfg, system="...", user="...", max_tokens=800)
    text = vision_call(cfg, system="...", user_text="...", image_b64="...", max_tokens=1000)

cfg is the user's credential dict from user_store. If the user has no personal AI key,
falls back to the bot-owner key from config.py.
"""

import os, sys
from config import BOT_ANTHROPIC_KEY, BOT_OPENAI_KEY, BOT_OPENROUTER_KEY, BOT_KIMI_KEY

KIMI_BASE_URL = "https://api.moonshot.cn/v1"

DEFAULT_VISION_MODEL = {
    "anthropic":  "claude-sonnet-4-6",
    "openai":     "gpt-4o",
    "openrouter": "qwen/qwen2.5-vl-72b-instruct",
}

DEFAULT_TEXT_MODEL = {
    "anthropic":  "claude-haiku-4-5-20251001",
    "openai":     "gpt-4o-mini",
    "openrouter": "qwen/qwen-2.5-72b-instruct",
    "kimi":       "moonshot-v1-32k",
}


def _provider_and_key(cfg: dict) -> tuple[str, str]:
    """Resolve vision provider + key from user cfg."""
    provider = cfg.get("ai_provider", "anthropic")
    key = cfg.get("ai_key", "")
    if not key:
        if provider == "anthropic":
            key = BOT_ANTHROPIC_KEY
        elif provider == "openai":
            key = BOT_OPENAI_KEY
        elif provider == "openrouter":
            key = BOT_OPENROUTER_KEY
    if not key:
        raise EnvironmentError(
            f"No API key for provider '{provider}'. Set one in /register or ask the bot owner."
        )
    return provider, key


def _text_provider_and_key(cfg: dict) -> tuple[str, str]:
    """
    Resolve text/matching provider + key.
    If the user has a kimi_key set, use Kimi for text (cheaper).
    Otherwise fall back to the same provider used for vision.
    """
    kimi_key = cfg.get("kimi_key", "") or BOT_KIMI_KEY
    if kimi_key:
        return "kimi", kimi_key
    return _provider_and_key(cfg)


def _anthropic_client(key: str):
    import anthropic
    return anthropic.Anthropic(api_key=key)


def _openai_client(key: str, base_url: str | None = None):
    from openai import OpenAI
    return OpenAI(api_key=key, base_url=base_url)


# ── Text call ─────────────────────────────────────────────────────────────────

def text_call(cfg: dict, system: str, user: str, max_tokens: int, model: str | None = None) -> str:
    provider, key = _text_provider_and_key(cfg)
    model = model or DEFAULT_TEXT_MODEL.get(provider, DEFAULT_TEXT_MODEL["anthropic"])

    if provider == "kimi":
        client = _openai_client(key, base_url=KIMI_BASE_URL)
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content.strip()

    if provider == "anthropic":
        client = _anthropic_client(key)
        msg = client.messages.create(
            model=model, max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return msg.content[0].text.strip()

    elif provider in ("openai", "openrouter"):
        base_url = "https://openrouter.ai/api/v1" if provider == "openrouter" else None
        client = _openai_client(key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return resp.choices[0].message.content.strip()

    raise ValueError(f"Unknown provider: {provider}")


# ── Vision call ───────────────────────────────────────────────────────────────

def vision_call(cfg: dict, system: str, user_text: str, image_b64: str, max_tokens: int, model: str | None = None) -> str:
    provider, key = _provider_and_key(cfg)
    model = model or DEFAULT_VISION_MODEL[provider]

    if provider == "anthropic":
        import anthropic
        client = _anthropic_client(key)
        msg = client.messages.create(
            model=model, max_tokens=max_tokens,
            system=system,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": image_b64}},
                    {"type": "text", "text": user_text},
                ],
            }],
        )
        return msg.content[0].text.strip()

    elif provider in ("openai", "openrouter"):
        base_url = "https://openrouter.ai/api/v1" if provider == "openrouter" else None
        client = _openai_client(key, base_url=base_url)
        content = [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            {"type": "text", "text": user_text},
        ]
        resp = client.chat.completions.create(
            model=model, max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        )
        return resp.choices[0].message.content.strip()

    raise ValueError(f"Unknown provider: {provider}")
