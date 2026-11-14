"""Integration checks for setup and CI smoke runs.

Non-destructive by default. ``--unpin-discussion`` is the ONE explicit
live change: it clears the channel's auto-pinned posts in the linked
discussion group (owner 14.11).
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from .cards import render_project_card
from .config import Config
from .Gemini import GeminiClient
from .http import sanitize
from .news import fetch_worldofai_entries
from .platforms import BlueskyClient, DevToClient
from .sources import GitHubApi
from .telegram import TelegramClient
from .telegraph import TelegraphClient


def _safe_error(exc: BaseException) -> str:
    return sanitize(f"{type(exc).__name__}: {exc}")[:500]


def _discussion_unpin_check(
    telegram: TelegramClient, me: dict[str, Any], linked: int | str | None
) -> dict[str, Any]:
    """Report bot pin rights, clear existing pins, then verify the list is empty."""
    if not linked:
        return {"ok": False, "reason": "no_linked_group"}
    try:
        before = telegram.get_chat(linked)
        member = dict(telegram.api(
            "getChatMember", {"chat_id": linked, "user_id": me.get("id")}
        ) or {})
        status = str(member.get("status") or "")
        can_pin = bool(member.get("can_pin_messages"))
        if status == "creator":
            can_pin = True  # the owner implicitly holds every admin right
        pinned_before = (before.get("pinned_message") or {}).get("message_id")
        if not can_pin:
            return {
                "ok": False, "reason": "missing_pin_rights",
                "linked_chat_id": linked, "bot_status": status,
                "can_pin_messages": can_pin, "group_title": before.get("title"),
                "group_type": before.get("type"),
            }
        telegram.api("unpinAllChatMessages", {"chat_id": linked})
        after = telegram.get_chat(linked)
        pinned_after = (after.get("pinned_message") or {}).get("message_id")
        return {
            "ok": pinned_after is None,
            "linked_chat_id": linked,
            "group_title": before.get("title"),
            "group_type": before.get("type"),
            "bot_status": status,
            "can_pin_messages": can_pin,
            "pinned_before": pinned_before,
            "pinned_after": pinned_after,
            "unpinned": pinned_before is not None and pinned_after is None,
        }
    except BaseException as exc:  # noqa: BLE001 - diagnostic only
        return {"ok": False, "error": _safe_error(exc), "linked_chat_id": linked}


def run_doctor(
    config: Config,
    *,
    configure_bot: bool = False,
    hello: bool = False,
    news_spike: bool = False,
    unpin_discussion: bool = False,
) -> dict[str, Any]:
    report: dict[str, Any] = {"ok": True, "target_locked_test": not config.is_main_target, "checks": {}}
    checks: dict[str, Any] = report["checks"]

    presence = {
        "TELEGRAM_BOT_TOKEN": bool(config.telegram_bot_token),
        "TELEGRAM_TEST_CHANNEL_ID": config.telegram_test_channel_id is not None,
        "TELEGRAM_OWNER_CHAT_ID": config.telegram_owner_chat_id is not None,
        "GH_API_TOKEN": bool(config.GitHub_token),
        "GEMINI_API_KEY": bool(config.Gemini_api_key),
        "IMGBB_API_KEY": bool(config.imgbb_api_key),
        "TELEGRAPH_ACCESS_TOKEN": bool(config.telegraph_access_token),
        "DEV_API_KEY": bool(config.dev_api_key),
        "BLUESKY_APP_PASSWORD": bool(config.bluesky_app_password),
    }
    checks["env_presence"] = presence

    telegram = TelegramClient(config.telegram_bot_token)
    try:
        me = telegram.get_me()
        checks["telegram_get_me"] = {
            "ok": True,
            "id": me.get("id"),
            "username": me.get("username"),
            "first_name": me.get("first_name"),
        }
        checks["telegram_discovery"] = telegram.discover_ids()
        # Option A (2026-11-04): native comments require a linked
        # discussion group. The Bot API cannot link one, so report it.
        linked = None
        try:
            main_chat = telegram.get_chat(config.telegram_chat_id)
            linked = main_chat.get("linked_chat_id")
            discussion: dict[str, Any] = {
                "ok": bool(linked),
                "channel_username": main_chat.get("username"),
                "linked_chat_id": linked,
            }
            if linked:
                group = telegram.get_chat(linked)
                discussion.update(
                    {
                        "group_title": group.get("title"),
                        "group_username": group.get("username"),
                        "group_type": group.get("type"),
                    }
                )
            checks["telegram_discussion"] = discussion
        except BaseException as exc:  # noqa: BLE001 - diagnostic only
            checks["telegram_discussion"] = {"ok": False, "error": _safe_error(exc)}
        if unpin_discussion:
            unpin = _discussion_unpin_check(telegram, me, linked)
            checks["discussion_unpin"] = unpin
            if not unpin.get("ok"):
                report["ok"] = False
        if configure_bot:
            checks["telegram_bot_profile"] = telegram.configure_bot_profile()
        if config.telegram_test_channel_id is not None:
            chat = telegram.get_chat(config.telegram_test_channel_id)
            member = telegram.api(
                "getChatMember",
                {"chat_id": config.telegram_test_channel_id, "user_id": me.get("id")},
            )
            checks["telegram_test_channel"] = {
                "ok": True,
                "id": chat.get("id"),
                "title": chat.get("title"),
                "bot_status": (member or {}).get("status"),
                "linked_chat_id": chat.get("linked_chat_id"),
            }
            if hello:
                sent = telegram.send_text(
                    config.telegram_test_channel_id,
                    "✅ <b>GitHub Open Source v6 doctor</b>\nBot API connection is working. Test target is locked.",
                )
                checks["telegram_hello"] = {"ok": True, "message_id": sent.get("message_id")}
    except BaseException as exc:  # noqa: BLE001
        report["ok"] = False
        checks["telegram"] = {"ok": False, "error": _safe_error(exc)}

    Gemini = GeminiClient(config.Gemini_api_key, config.Gemini_model)
    try:
        status, models = Gemini.list_models()
        # A model that Google retired must fail the health check loudly: the
        # pipeline is fail-closed without a model verdict and would otherwise
        # stop publishing silently.
        available = {
            str(item.get("name") if isinstance(item, dict) else item).removeprefix("models/")
            for item in models
        }
        model_ok = not available or config.Gemini_model in available
        checks["Gemini_models"] = {
            "ok": status == 200 and model_ok,
            "status": status,
            "count": len(models),
            "configured_model": config.Gemini_model,
            "model_available": model_ok,
        }
        if not model_ok:
            checks["Gemini_model_lifecycle"] = {
                "ok": False,
                "hint": "Set GEMINI_MODEL to a currently available model; see docs/RUNBOOK.md",
            }
        if status != 200 or not model_ok:
            report["ok"] = False
        else:
            ping = Gemini.generate_json(
                "Return a tiny health check object.",
                schema={
                    "type": "OBJECT",
                    "properties": {"status": {"type": "STRING"}},
                    "required": ["status"],
                },
                temperature=0,
            )
            checks["Gemini_generate"] = {"ok": True, "keys": sorted(ping)}
    except BaseException as exc:  # noqa: BLE001
        report["ok"] = False
        checks["Gemini"] = {"ok": False, "error": _safe_error(exc)}

    try:
        api = GitHubApi(config.GitHub_token)
        items = api.search("stars:>=100 archived:false fork:false language:Python", per_page=3)
        checks["GitHub_search"] = {"ok": bool(items), "sample_count": len(items)}
        if not items:
            report["ok"] = False
    except BaseException as exc:  # noqa: BLE001
        report["ok"] = False
        checks["GitHub_search"] = {"ok": False, "error": _safe_error(exc)}

    try:
        entries = fetch_worldofai_entries()
        latest = entries[0] if entries else {}
        checks["worldofai_rss"] = {
            "ok": bool(entries),
            "count": len(entries),
            "latest_video_id": latest.get("video_id"),
            "latest_title": latest.get("title"),
            "latest_published_at": latest.get("published_at"),
        }
        if news_spike and entries:
            try:
                spike = Gemini.generate_json(
                    "Extract up to three factual claims from this public YouTube video. "
                    "Mark every claim as unverified; do not add outside facts.",
                    schema={
                        "type": "OBJECT",
                        "properties": {
                            "claims": {"type": "ARRAY", "items": {"type": "STRING"}}
                        },
                        "required": ["claims"],
                    },
                    youtube_url=str(latest.get("url") or ""),
                    temperature=0.1,
                )
                checks["Gemini_youtube_spike"] = {
                    "ok": True,
                    "claims_count": len(spike.get("claims") or []),
                }
            except BaseException as exc:  # noqa: BLE001
                checks["Gemini_youtube_spike"] = {
                    "ok": False,
                    "fallback": "description + primary sources",
                    "error": _safe_error(exc),
                }
    except BaseException as exc:  # noqa: BLE001
        checks["worldofai_rss"] = {"ok": False, "error": _safe_error(exc)}

    if config.telegraph_access_token:
        try:
            info = TelegraphClient(config.telegraph_access_token).get_account_info()
            checks["telegraph"] = {
                "ok": True,
                "short_name": info.get("short_name"),
                "page_count": info.get("page_count"),
            }
        except BaseException as exc:  # noqa: BLE001
            checks["telegraph"] = {"ok": False, "error": _safe_error(exc)}

    if config.dev_api_key:
        try:
            me = DevToClient(config.dev_api_key).me()
            checks["devto"] = {"ok": True, "username": me.get("username"), "name": me.get("name")}
        except BaseException as exc:  # noqa: BLE001
            checks["devto"] = {"ok": False, "error": _safe_error(exc)}

    if config.bluesky_app_password:
        try:
            result = BlueskyClient(config.bluesky_handle, config.bluesky_app_password).verify()
            checks["bluesky"] = {"ok": True, **result}
        except BaseException as exc:  # noqa: BLE001
            checks["bluesky"] = {"ok": False, "error": _safe_error(exc)}

    # Facebook is published by make.com from a sheet row. v6 deliberately
    # holds no Page id/token, so there is no Graph API call to verify here.
    checks["Facebook"] = {"ok": True, "skipped": "make.com publishes from a Sheet row"}

    try:
        sample_candidate = {
            "name": "Pawnix",
            "description": "A full chess client for the terminal",
            "language": "Python",
            "stars": 1240,
            "forks": 86,
            "contributors_count": 9,
            "license": {"spdx_id": "MIT"},
            "pushed_at": "2026-10-31T00:00:00Z",
            "topics": ["cli"],
        }
        sample_generated = {
            "hook": "A full chess client that lives in your terminal.",
            "summary": "Local games, online matches, clocks and PGN export in a keyboard-first interface.",
            "category": "cli",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path, style = render_project_card(
                sample_candidate,
                sample_generated,
                Path(tmp) / "sample.png",
                issue_number=1,
            )
            checks["card_render"] = {"ok": path.exists() and path.stat().st_size > 1000, "style": style}
    except BaseException as exc:  # noqa: BLE001
        report["ok"] = False
        checks["card_render"] = {"ok": False, "error": _safe_error(exc)}

    return report
