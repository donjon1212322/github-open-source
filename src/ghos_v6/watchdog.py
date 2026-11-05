"""Detect missed GitHub cron slots and recover them idempotently.

GitHub delayed 34 of 42 scheduled events on 05.11. A watchdog cannot make
GitHub punctual, but it can notice the gap, alert the owner and dispatch the
missed command exactly once per (UTC day, slot).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from .config import Config
from .state import StateStore, iso_now
from .telegram import TelegramClient

logger = logging.getLogger(__name__)

# key -> (UTC deadline hour, minute) -> workflow_dispatch inputs
SLOTS: tuple[tuple[str, tuple[int, int], dict[str, str]], ...] = (
    ("collect", (5, 30), {"command": "collect"}),
    ("publish:europe_morning", (8, 30), {"command": "publish", "slot": "europe_morning"}),
    ("publish:europe_midday", (13, 30), {"command": "publish", "slot": "europe_midday"}),
    ("publish:us_peak", (18, 0), {"command": "publish", "slot": "us_peak"}),
    ("news", (20, 0), {"command": "news"}),
)


def _date_of(value: Any):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _has_run(store: StateStore, key: str, today) -> bool:
    if key == "news":
        return any(_date_of(n.get("published_at")) == today
                   for n in (store.data.get("news_topics") or []))
    return _date_of((store.data.get("last_runs", {}).get(key) or {}).get("finished_at")) == today


def _dispatch(repo: str, inputs: dict[str, str], token: str) -> tuple[bool, str]:
    if not repo or not token:
        return False, "no repo/token"
    url = f"https://api.github.com/repos/{repo}/actions/workflows/pipeline.yml/dispatches"
    body = json.dumps({"ref": "main", "inputs": inputs}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "ghos-v6-watchdog",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status in (200, 204), f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, type(exc).__name__


def watchdog_command(config: Config, store: StateStore, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    today = now.date()
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or config.GitHub_token
    repo = os.getenv("GITHUB_REPOSITORY") or ""
    last = store.data.setdefault("last_runs", {})
    marks: dict[str, Any] = last.setdefault("watchdog_dispatches", {})
    for old in [k for k in marks if not str(k).startswith(str(today))]:
        del marks[old]

    missed: list[str] = []
    recovered: list[str] = []
    failed: list[str] = []
    for key, (hour, minute), inputs in SLOTS:
        deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < deadline or _has_run(store, key, today):
            continue
        missed.append(key)
        mark = f"{today}:{key}"
        if mark in marks:
            continue
        ok, detail = _dispatch(repo, inputs, token)
        marks[mark] = {"at": iso_now(), "ok": ok, "detail": detail[:200]}
        (recovered if ok else failed).append(key)
        store.event("watchdog_recovered" if ok else "watchdog_dispatch_failed",
                    slot=key, detail=detail[:200])
        logger.info("watchdog %s: dispatch ok=%s %s", key, ok, detail)

        if config.telegram_owner_chat_id and config.telegram_bot_token:
            try:
                TelegramClient(config.telegram_bot_token).send_text(
                    config.telegram_owner_chat_id,
                    "⚠️ <b>Пропущен слот</b> <code>%s</code>\n"
                    "Повтор: %s. Проверь Actions, если dispatch не удался."
                    % (key, "запущен" if ok else "не удался"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("watchdog alert failed: %s", type(exc).__name__)

    if missed:
        store.save()
    return {"ok": True, "checked": len(SLOTS), "missed": missed,
            "recovered": recovered, "failed": failed}
