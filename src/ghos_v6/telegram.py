"""Telegram Bot API client."""
from __future__ import annotations

from typing import Any, Sequence

import requests

import time

from .http import sanitize
from .telegram_payload import (
    build_buttons,
    build_edit_message_payload,
    build_send_message_payload,
)

API_BASE = "https://api.telegram.org"


class TelegramError(RuntimeError):
    def __init__(self, description: str, *, error_code: int | None = None):
        super().__init__(sanitize(description))
        self.description = sanitize(description)
        self.error_code = error_code


def is_fatal_error(exc: Exception) -> bool:
    """True when retrying the same candidate cannot succeed.

    401/403 mean the bot is not allowed in the chat; "chat not found" means
    the target is gone; a network failure/timeout may or may not have sent
    the message. In all three cases the slot must abort, not silently drop
    the candidate and move on.
    """
    code = getattr(exc, "error_code", None)
    text = str(exc).lower()
    return code in {401, 403} or any(
        term in text
        for term in ("chat not found", "network failure", "timeout", "connection")
    )


class TelegramClient:
    def __init__(self, bot_token: str):
        self.bot_token = bot_token.strip()
        self.session = requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.bot_token)

    def _post(self, url: str, **kwargs: Any) -> requests.Response:
        """POST once, and once more after a 429 with Telegram's retry_after."""
        response = self.session.post(url, **kwargs)
        if response.status_code == 429:
            try:
                body = response.json()
                retry_after = int(((body or {}).get("parameters") or {}).get("retry_after") or 1)
            except ValueError:
                retry_after = 1
            time.sleep(min(retry_after, 30))
            response = self.session.post(url, **kwargs)
        return response

    def api(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        if not self.configured:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
        try:
            response = self._post(
                f"{API_BASE}/bot{self.bot_token}/{method}",
                json=payload or {},
                timeout=45,
            )
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise TelegramError(f"Telegram {method} network failure: {type(exc).__name__}") from exc
        if not body.get("ok"):
            raise TelegramError(
                f"Telegram {method} failed: {body.get('description') or 'unknown error'}",
                error_code=body.get("error_code"),
            )
        return body.get("result")

    def get_me(self) -> dict[str, Any]:
        return dict(self.api("getMe") or {})

    def get_updates(self, *, limit: int = 100) -> list[dict[str, Any]]:
        result = self.api("getUpdates", {"timeout": 0, "limit": limit})
        return list(result or [])

    def get_chat(self, chat_id: int | str) -> dict[str, Any]:
        return dict(self.api("getChat", {"chat_id": chat_id}) or {})

    def discover_ids(self) -> dict[str, Any]:
        """Discover owner/channel IDs from recent bot updates.

        Expected owner actions have already happened: /start in a private
        chat, bot added to the test channel, and one message in that
        channel. Updates are retained by Telegram for a limited time.
        """
        updates = self.get_updates(limit=100)
        owners: dict[int, str] = {}
        channels: dict[int, str] = {}
        groups: dict[int, str] = {}
        for update in updates:
            for key in ("message", "channel_post", "edited_channel_post", "my_chat_member", "chat_member"):
                item = update.get(key)
                if not isinstance(item, dict):
                    continue
                chat = item.get("chat") or {}
                chat_id = chat.get("id")
                chat_type = str(chat.get("type") or "")
                title = str(chat.get("title") or chat.get("username") or "")
                if isinstance(chat_id, int):
                    if chat_type == "channel":
                        channels[chat_id] = title
                    elif chat_type in {"group", "supergroup"}:
                        groups[chat_id] = title
                    elif chat_type == "private":
                        user = item.get("from") or {}
                        if user.get("id") and not user.get("is_bot"):
                            owners[int(user["id"])] = str(user.get("username") or user.get("first_name") or "")
                user = item.get("from") or {}
                if user.get("id") and not user.get("is_bot") and chat_type == "private":
                    owners[int(user["id"])] = str(user.get("username") or user.get("first_name") or "")
        return {
            "updates_count": len(updates),
            "owner_candidates": owners,
            "channel_candidates": channels,
            "group_candidates": groups,
        }

    def send_message_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        return dict(self.api("sendMessage", payload) or {})

    def send_text(
        self,
        chat_id: int | str,
        text_html: str,
        *,
        preview_url: str = "",
        buttons: Sequence[Sequence[dict[str, str]]] = (),
        disable_notification: bool = False,
    ) -> dict[str, Any]:
        payload = build_send_message_payload(
            chat_id,
            text_html,
            preview_url=preview_url,
            buttons=buttons,
            disable_notification=disable_notification,
        )
        try:
            return self.send_message_payload(payload)
        except TelegramError as exc:
            description = exc.description.lower()
            if preview_url and any(word in description for word in ("webpage", "curl", "preview", "wrong file")):
                payload["link_preview_options"] = {"is_disabled": True}
                return self.send_message_payload(payload)
            raise

    def edit_text(
        self,
        chat_id: int | str,
        message_id: int,
        text_html: str,
        *,
        preview_url: str = "",
    ) -> dict[str, Any]:
        """Fix an already published brief in place instead of posting a duplicate."""
        payload = build_edit_message_payload(
            chat_id, message_id, text_html, preview_url=preview_url
        )
        result = self.api("editMessageText", payload)
        return dict(result) if isinstance(result, dict) else {"message_id": message_id}

    def send_project(
        self,
        chat_id: int | str,
        text_html: str,
        *,
        preview_url: str,
        github_url: str = "",
        telegraph_url: str = "",
        homepage_url: str = "",
        include_buttons: bool = False,
    ) -> dict[str, Any]:
        # Owner decision (2026-11-04, option A): channel posts carry NO
        # inline keyboard. Telegram replaces its native "Leave a comment"
        # footer with any custom reply_markup, so buttons had to go. The
        # very same links are already rendered in the post text.
        return self.send_text(
            chat_id,
            text_html,
            preview_url=preview_url,
            buttons=(
                build_buttons(
                    github_url=github_url,
                    telegraph_url=telegraph_url,
                    homepage_url=homepage_url,
                )
                if include_buttons
                else ()
            ),
        )

    def send_photo(
        self,
        chat_id: int | str,
        photo: bytes,
        caption_html: str,
        *,
        buttons: Sequence[Sequence[dict[str, str]]] = (),
        filename: str = "card.png",
    ) -> dict[str, Any]:
        """Upload the card as the post itself (multipart, no external host).

        Prefer this when a public image host is unavailable: Telegram takes
        the bytes directly, so the card cannot silently degrade to a
        GitHub OpenGraph preview the way a link-preview post does.
        """
        if not self.configured:
            raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
        import json as _json

        from .upload import to_telegram_jpeg

        # Encode once, ourselves: Telegram recompresses whatever it stores,
        # and hairlines are the first casualty when it chooses the quality.
        if photo.startswith(b"\x89PNG"):
            import io as _io

            from .images import open_safe_image
            photo = to_telegram_jpeg(open_safe_image(_io.BytesIO(photo)))

        data = {"chat_id": str(chat_id), "caption": caption_html, "parse_mode": "HTML"}
        if buttons:
            data["reply_markup"] = _json.dumps({"inline_keyboard": list(buttons)})
        files = {"photo": (filename.rsplit(".", 1)[0] + ".jpg", photo, "image/jpeg")}
        try:
            response = self._post(
                f"{API_BASE}/bot{self.bot_token}/sendPhoto",
                data=data,
                files=files,
                timeout=90,
            )
            body = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise TelegramError(f"Telegram sendPhoto network failure: {type(exc).__name__}") from exc
        if not body.get("ok"):
            raise TelegramError(
                f"Telegram sendPhoto failed: {body.get('description') or 'unknown error'}",
                error_code=body.get("error_code"),
            )
        return dict(body.get("result") or {})

    def send_owner(self, owner_chat_id: int | None, text_html: str) -> dict[str, Any] | None:
        if not owner_chat_id:
            return None
        return self.send_text(owner_chat_id, text_html)

    def send_poll(
        self,
        chat_id: int | str,
        question: str,
        options: list[str],
        *,
        open_period: int | None = None,
    ) -> dict[str, Any]:
        clean_options = [str(option)[:100] for option in options if str(option).strip()][:10]
        if len(clean_options) < 2:
            raise TelegramError("A poll needs at least two options")
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "question": str(question)[:300],
            "options": clean_options,
            "is_anonymous": str(chat_id).strip().startswith(("@", "-100")),
            "allows_multiple_answers": False,
        }
        if open_period:
            # Telegram caps open_period at 10 days; auto-close keeps the
            # tally readable even if the closing run is late.
            payload["open_period"] = max(300, min(int(open_period), 10 * 24 * 3600))
        return dict(self.api("sendPoll", payload) or {})

    def stop_poll(self, chat_id: int | str, message_id: int) -> dict[str, Any]:
        return dict(self.api("stopPoll", {"chat_id": chat_id, "message_id": message_id}) or {})

    def delete_message(self, chat_id: int | str, message_id: int) -> bool:
        return bool(self.api("deleteMessage", {"chat_id": chat_id, "message_id": message_id}))

    def configure_bot_profile(self) -> dict[str, Any]:
        """Set commands/descriptions that the Bot API allows a bot to own."""
        results: dict[str, Any] = {}
        commands = [
            {"command": "start", "description": "About GitHub Open Source automation"},
            {"command": "status", "description": "Publishing system status"},
            {"command": "latest", "description": "Latest published open-source projects"},
        ]
        results["commands"] = self.api("setMyCommands", {"commands": commands})
        try:
            results["description"] = self.api(
                "setMyDescription",
                {
                    "description": (
                        "Automation bot for GitHub Open Source: curated projects, "
                        "deep dives, digests and community polls."
                    )
                },
            )
        except TelegramError as exc:
            results["description_error"] = exc.description
        try:
            results["short_description"] = self.api(
                "setMyShortDescription",
                {"short_description": "Curated open-source projects and deep dives."},
            )
        except TelegramError as exc:
            results["short_description_error"] = exc.description
        return results
