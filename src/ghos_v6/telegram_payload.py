"""v6 — publish via Telegram Bot API (instead of a Telethon user account).

A BotFather bot added as channel admin is free and least-privilege:
leaking its token can be revoked in one tap, unlike a user session
string which hands over the whole personal account.

The post is sent as a *text message* (4,096-char budget) whose link
preview is enlarged and shown above the text — visually equivalent to
a photo post, without the 1,024-char caption cap:
https://core.telegram.org/bots/api#linkpreviewoptions
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any, Callable, Sequence
from urllib.parse import urlparse

API_BASE = "https://api.telegram.org"


def _is_https_url(value: str) -> bool:
    """Telegram rejects the whole sendMessage when one button URL is invalid."""
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and not any(char.isspace() for char in value)
    )


def build_buttons(
    *,
    github_url: str = "",
    telegraph_url: str = "",
    homepage_url: str = "",
) -> list[list[dict[str, str]]]:
    row: list[dict[str, str]] = []
    if _is_https_url(github_url):
        row.append({"text": "🔗 GitHub", "url": github_url})
    if _is_https_url(telegraph_url):
        row.append({"text": "📖 Full breakdown", "url": telegraph_url})
    if _is_https_url(homepage_url) and homepage_url != github_url:
        row.append({"text": "🌐 Homepage", "url": homepage_url})
    return [row] if row else []


def build_send_message_payload(
    chat_id: str | int,
    text_html: str,
    *,
    preview_url: str = "",
    buttons: Sequence[Sequence[dict[str, str]]] = (),
    disable_notification: bool = False,
) -> dict[str, Any]:
    """Build a ``sendMessage`` payload. Pure function — unit-testable."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text_html,
        "parse_mode": "HTML",
        "disable_notification": disable_notification,
    }
    if _is_https_url(preview_url):
        payload["link_preview_options"] = {
            "url": preview_url,
            "prefer_large_media": True,
            "show_above_text": True,
        }
    else:
        payload["link_preview_options"] = {"is_disabled": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [list(r) for r in buttons]}
    return payload


PostFn = Callable[[str, dict[str, Any]], dict[str, Any]]


def _urllib_post(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed host)
        return json.loads(resp.read().decode("utf-8"))


def publish_message(
    bot_token: str,
    payload: dict[str, Any],
    *,
    post_fn: PostFn = _urllib_post,
) -> dict[str, Any]:
    """POST the payload; raises RuntimeError on any failure (never silent)."""
    if not bot_token.strip():
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    result = post_fn(f"{API_BASE}/bot{bot_token}/sendMessage", payload)
    if not result.get("ok"):
        raise RuntimeError(f"Telegram sendMessage failed: {result.get('description') or result}")
    return result["result"]
