"""Polls that actually produce data.

The Bot API caps a poll at 10 days and cannot create one retroactively, so
the weekly poll must be sent on schedule and closed explicitly — otherwise
``voter_count`` stays unknown and the week leaves no signal behind.
"""
from __future__ import annotations

from typing import Any

from .telegram import TelegramClient

MAX_POLL_DAYS = 10
DEFAULT_WEEK_POLL_HOURS = 96  # Sunday → Thursday; closed before the digest


def is_channel(chat_id: int | str) -> bool:
    """Channels only accept anonymous polls (Bot API rule)."""
    raw = str(chat_id).strip()
    return raw.startswith("@") or raw.startswith("-100")


def send_week_poll(
    telegram: TelegramClient,
    chat_id: int | str,
    question: str,
    options: list[str],
    *,
    hours: int = DEFAULT_WEEK_POLL_HOURS,
) -> dict[str, Any]:
    hours = max(1, min(hours, MAX_POLL_DAYS * 24))
    result = telegram.api(
        "sendPoll",
        {
            "chat_id": chat_id,
            "question": question[:300],
            "options": [o[:100] for o in options][:10],
            # Channels reject non-anonymous polls: "non-anonymous polls
            # can't be sent to channel chats". Groups may keep names.
            "is_anonymous": is_channel(chat_id),
            "allows_multiple_answers": False,
            "open_period": hours * 3600,
        },
    )
    return dict(result or {})


def close_poll(telegram: TelegramClient, chat_id: int | str, message_id: int) -> dict[str, Any]:
    """Stop a poll and read the final tally (``stopPoll``)."""
    result = telegram.api("stopPoll", {"chat_id": chat_id, "message_id": message_id}) or {}
    raw_options: list[dict[str, Any]] = result.get("options") or []
    options = [
        {
            "text": str(option.get("text") or ""),
            "votes": int(option.get("voter_count") or 0),
        }
        for option in raw_options
    ]
    total = sum(option["votes"] for option in options)  # type: ignore[misc]
    for option in options:
        option["share"] = round(option["votes"] / total, 3) if total else 0.0  # type: ignore[operator]
    return {
        "ok": True,
        "question": str(result.get("question") or ""),
        "is_closed": bool(result.get("is_closed")),
        "total_voters": int(result.get("total_voter_count") or total),
        "options": options,
    }


def winner(tally: dict[str, Any]) -> dict[str, Any] | None:
    options = [o for o in tally.get("options") or [] if o.get("votes")]
    if not options:
        return None
    return max(options, key=lambda option: option["votes"])
