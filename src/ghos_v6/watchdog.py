"""Detect missed GitHub cron slots and recover them idempotently.

GitHub delayed 34 of 42 scheduled events on 05.11. A watchdog cannot make
GitHub punctual, but it can notice the gap, alert the owner and dispatch the
missed command without losing it to the concurrency queue.

Owner decision (2026-11-06):
- ``HTTP 204`` from ``workflow_dispatch`` is NOT proof that the slot ran. On
  05.11 the accepted run was cancelled before any job started because the
  watchdog shared ``state-writer`` with the pipeline. A marker is now
  ``verified`` only when the corresponding fact appears in ``state``.
- ``article`` is monitored too; the article cron was silently dropped on 05.11.
- Days are still counted in UTC on purpose: GitHub cron is UTC and the owner
  forbade ``timezone: "Europe/Minsk"``. Switching the 3-posts/day limit to
  Minsk time would be a separate, explicit decision.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Config
from .state import StateStore
from .telegram import TelegramClient

logger = logging.getLogger(__name__)

# key -> (UTC deadline hour, minute) -> workflow_dispatch inputs
SLOTS: tuple[tuple[str, tuple[int, int], dict[str, str]], ...] = (
    ("collect", (5, 30), {"command": "collect"}),
    ("publish:europe_morning", (8, 30), {"command": "publish", "slot": "europe_morning"}),
    ("publish:europe_midday", (13, 30), {"command": "publish", "slot": "europe_midday"}),
    ("article", (14, 0), {"command": "article"}),
    ("publish:us_peak", (18, 0), {"command": "publish", "slot": "us_peak"}),
    ("news", (20, 0), {"command": "news"}),
)

# A dispatched but unverified slot may be retried, but never in a blind series.
DISPATCH_COOLDOWN_SECONDS = 1800.0        # 30 min between attempts for one slot
ARTICLE_RECOVERY_WINDOW_HOURS = 6.0       # do not backfill yesterday's article


def _date_of(value: Any):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _datetime_of(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _stamp(moment: datetime) -> str:
    """Use the injected clock so cooldown tests stay deterministic."""
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _devto_published_at(record: dict[str, Any]) -> Any:
    return ((record.get("articles") or {}).get("devto") or {}).get("published_at")


def _has_run(store: StateStore, key: str, today) -> bool:
    """Proof the slot actually completed, not merely that dispatch was accepted."""
    if key == "news":
        return any(_date_of(n.get("published_at")) == today
                   for n in (store.data.get("news_topics") or []))
    if key == "article":
        return any(_date_of(_devto_published_at(r)) == today
                   for r in (store.data.get("published") or {}).values())
    return _date_of((store.data.get("last_runs", {}).get(key) or {}).get("finished_at")) == today


def _today_project_count(store: StateStore, today) -> int:
    """article_of_day() needs a project published on this UTC day."""
    return sum(1 for r in (store.data.get("published") or {}).values()
               if _date_of(r.get("published_at")) == today)


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
    changed = False
    for old in [k for k in marks if not str(k).startswith(str(today))]:
        del marks[old]
        changed = True   # persist the cleanup of yesterday's unverified marks

    missed: list[str] = []
    dispatched: list[str] = []
    verified: list[str] = []
    failed: list[str] = []

    for key, (hour, minute), inputs in SLOTS:
        deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        mark = f"{today}:{key}"

        if _has_run(store, key, today):
            # The slot really finished: retire any unverified marker.
            prev = marks.get(mark)
            if prev and not prev.get("verified"):
                prev["verified"] = True
                prev["verified_at"] = _stamp(now)
                changed = True
                store.event("watchdog_verified", slot=key)
                verified.append(key)
            continue
        if now < deadline:
            continue
        if key == "article":
            # No project today => article_of_day() would return no_projects_today.
            # Do not spam a green dispatch that cannot publish anything.
            if not _today_project_count(store, today):
                continue
            if now > deadline + timedelta(hours=ARTICLE_RECOVERY_WINDOW_HOURS):
                continue

        missed.append(key)
        prev = marks.get(mark)
        if prev:
            started = _datetime_of(prev.get("at")) or now
            age = (now - started).total_seconds()
            if age < DISPATCH_COOLDOWN_SECONDS:
                # Already tried recently; wait for state to confirm instead of
                # firing a blind series of retries.
                continue

        ok, detail = _dispatch(repo, inputs, token)
        marks[mark] = {
            "at": _stamp(now),
            "dispatched": ok,
            "verified": False,          # HTTP 204 is not execution proof
            "detail": detail[:200],
            "attempts": int(prev.get("attempts", 1) if prev else 0) + 1,
        }
        changed = True
        (dispatched if ok else failed).append(key)
        store.event("watchdog_recovered" if ok else "watchdog_dispatch_failed",
                    slot=key, detail=detail[:200], verified=False,
                    attempts=marks[mark]["attempts"])
        logger.info("watchdog %s: dispatch ok=%s %s (unverified)", key, ok, detail)

        if config.telegram_owner_chat_id and config.telegram_bot_token:
            try:
                TelegramClient(config.telegram_bot_token).send_text(
                    config.telegram_owner_chat_id,
                    "⚠️ <b>Пропущен слот</b> <code>%s</code>\n"
                    "Повтор: %s. Подтверждения в state пока нет; "
                    "проверь Actions, если слот не появился."
                    % (key, "dispatch принят (HTTP 204)" if ok else "не удался"),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("watchdog alert failed: %s", type(exc).__name__)

    if missed or changed:
        store.save()
    return {"ok": True, "checked": len(SLOTS), "missed": missed,
            "dispatched": dispatched, "verified": verified, "failed": failed}
