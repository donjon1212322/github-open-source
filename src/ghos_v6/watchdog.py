"""Detect missed GitHub cron slots and recover them idempotently.

GitHub delayed 34 of 42 scheduled events on 05.11. A watchdog cannot make
GitHub punctual, but it can notice the gap, alert the owner and dispatch the
missed command without losing it to the concurrency queue.

Owner decisions (2026-11-06):
- ``HTTP 204`` from ``workflow_dispatch`` is NOT proof that the slot ran. On
  05.11 the accepted run was cancelled before any job started because the
  watchdog shared ``state-writer`` with the pipeline. A marker is now
  ``verified`` only when the corresponding fact appears in ``state``.
- ``article`` is monitored too; the article cron was silently dropped on 05.11.
- Days are still counted in UTC on purpose: GitHub cron is UTC and the owner
  forbade ``timezone: "Europe/Minsk"``. Switching the 3-posts/day limit to
  Minsk time would be a separate, explicit decision.
- Notifications must not cry wolf. On 06.11 two alarming "missed slot" messages
  were sent for dispatches that completed minutes later, and no closing message
  ever arrived. A first successful dispatch is now silent; the owner hears
  only when a dispatch fails, when an unverified slot needs a second attempt,
  or when state confirms the slot was restored (a closing "recovered" note).
- GitHub dropped whole batches of ``schedule`` events on 06.11 (07:27, 09:27,
  11:27, 13:27 Minsk and three hours with no run at all). A second cron tick
  and a ``repository_dispatch`` entry point let an external free clock start
  this workflow through the API instead of GitHub's best-effort scheduler.
  See ``tools/external_tick/README.md``.
- An honest refusal is a completed slot. ``article_of_day()`` now records its
  verdict in ``last_runs.article.finished_at`` (published, all duplicates, no
  projects), so a duplicate no longer triggers repeated dispatches.
- Publish slots are idempotent. ``publish_slot()`` refuses a second run of a
  slot already finished this UTC day (a late GitHub cron) and records
  ``daily_limit_reached`` as a finished slot, so the watchdog never retries a
  quota-closed ``publish:us_peak`` through the night.
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
from .state import StateStore, iso_week_key
from .telegram import TelegramClient

logger = logging.getLogger(__name__)

# key -> (UTC deadline hour, minute) -> workflow_dispatch inputs
SLOTS: tuple[tuple[str, tuple[int, int], dict[str, str]], ...] = (
    ("collect", (5, 30), {"command": "collect"}),
    ("publish:europe_morning", (8, 30), {"command": "publish", "slot": "europe_morning"}),
    ("publish:europe_midday", (13, 30), {"command": "publish", "slot": "europe_midday"}),
    ("article", (14, 0), {"command": "article"}),
    ("publish:us_peak", (18, 0), {"command": "publish", "slot": "us_peak"}),
    # Owner 12.11: Facebook no longer waits for the late native GitHub cron.
    # facebook_handoff_command() writes a daily marker, so this dispatch and
    # an on-time cron cannot double a Sheet row. The live Worker is NOT
    # redeployed (no Cloudflare token in this session): the watchdog ticks at
    # 18:27/18:30 are the exact-minute recovery. Keep Facebook ENABLED.
    ("facebook-handoff", (18, 30), {"command": "facebook-handoff"}),
    ("news", (20, 0), {"command": "news"}),
    # 2026-11-07: the second dev.to article. Its own marker lives in
    # last_runs["news-article"], so _has_run() verifies it generically.
    ("news-article", (20, 30), {"command": "news-article"}),
    # Owner 12.11: 11.11 20:23Z metrics dispatch was startup_failure, no
    # runner, no marker, and the late native cron was correctly ignored.
    # metrics_command() now writes a daily marker after a real measurement;
    # without it this slot recovers the lost views snapshot on the next tick.
    ("metrics", (20, 30), {"command": "metrics"}),
)

# Owner 08.11: weekly slots used to be the ONLY ones with no safety net,
# because a second trigger would create a duplicate digest/poll. The weekly
# commands now write an ISO-week marker in ``last_runs`` (see pipeline.py),
# so they are idempotent and CAN be recovered. Weekday uses Python's
# convention: Monday=0 ... Sunday=6 (worker.js uses JS getUTCDay: Sunday=0).
WEEKLY_SLOTS: tuple[tuple[str, int, tuple[int, int], dict[str, str]], ...] = (
    ("digest", 6, (16, 20), {"command": "digest"}),          # cron 16:13Z Sun
    ("report", 6, (16, 50), {"command": "report"}),          # cron 16:43Z Sun
    ("poll-close", 3, (19, 0), {"command": "poll-close"}),   # cron 18:53Z Thu
)

# A dispatched but unverified slot may be retried, but never in a blind series.
DISPATCH_COOLDOWN_SECONDS = 1800.0        # 30 min between attempts for one slot
ARTICLE_RECOVERY_WINDOW_HOURS = 6.0       # do not backfill yesterday's article

# Long-term free-tier guard (08.11 audit): a slot that never writes its state
# marker must not be re-dispatched every 30 minutes forever. 48 blind retries
# a day would burn Actions runs, Gemini RPD and Telegram/dev.to quotas without
# ever fixing the underlying problem. Three attempts per slot per day, and a
# hard ceiling on the whole day's recovery traffic.
MAX_ATTEMPTS_PER_SLOT = 3
MAX_DAILY_DISPATCHES = 24

# Owner 11.11: an AI Brief that is honestly refused (only repeated stories,
# fewer than two stories, feature off, already published) IS a completed
# slot. Without this the watchdog would dispatch the news command up to three
# more times for a deliberate no-post day, and the owner would never learn
# why the brief did not appear. Transient failures (source_unavailable) are
# deliberately NOT terminal: one recovery attempt is still useful.
NEWS_TERMINAL_REASONS = frozenset({
    "published", "refreshed", "already_published", "news_already_published",
    "not_a_digest", "duplicate_digest", "feature_ai_brief_disabled",
    "gemini_ineligible",
})


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


def _dispatch_allowed(marks: dict[str, Any], mark: str, today) -> tuple[bool, str]:
    """Bound recovery traffic even when state never confirms a slot."""
    prev = marks.get(mark)
    tries = int((prev or {}).get("attempts", 0) or 0) if isinstance(prev, dict) else 0
    if tries >= MAX_ATTEMPTS_PER_SLOT:
        return False, "slot"
    used = sum(
        int((value or {}).get("attempts", 0) or 0)
        for key, value in marks.items()
        if str(key).startswith(str(today)) and isinstance(value, dict)
    )
    if used >= MAX_DAILY_DISPATCHES:
        return False, "day"
    return True, ""


def _has_run(store: StateStore, key: str, today) -> bool:
    """Proof the slot actually completed, not merely that dispatch was accepted."""
    if key == "news":
        if any(_date_of(n.get("published_at")) == today
               for n in (store.data.get("news_topics") or [])):
            return True
        marker = (store.data.get("last_runs") or {}).get("news") or {}
        return (_date_of(marker.get("finished_at")) == today
                and str(marker.get("reason") or "") in NEWS_TERMINAL_REASONS)
    if key == "article":
        if any(_date_of(_devto_published_at(r)) == today
               for r in (store.data.get("published") or {}).values()):
            return True
        # article_of_day() records the whole command's verdict for the day,
        # including an honest "all candidates are duplicates" refusal. Without
        # this a duplicate looked like a missed slot on 06.11 and triggered
        # repeated warnings every 30 minutes.
        done = (store.data.get("last_runs", {}).get("article") or {}).get("finished_at")
        return _date_of(done) == today
    return _date_of((store.data.get("last_runs", {}).get(key) or {}).get("finished_at")) == today


def _has_run_weekly(store: StateStore, key: str, now: datetime) -> bool:
    """Weekly proof: the ISO-week marker, not an HTTP 204."""
    done = (store.data.get("last_runs") or {}).get(key) or {}
    return str(done.get("week") or "") == iso_week_key(now)


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


def _notify(config: Config, text_html: str) -> bool:
    """Best-effort owner notice. Never let Telegram failure break the check."""
    if not (config.telegram_owner_chat_id and config.telegram_bot_token):
        return False
    try:
        TelegramClient(config.telegram_bot_token).send_text(
            config.telegram_owner_chat_id, text_html)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("watchdog alert failed: %s", type(exc).__name__)
        return False


def _note_dispatch_cap(config: Config, store: StateStore, cap_state: dict[str, Any],
                       scope: str, slot: str, alerts: list[str]) -> None:
    # One audit event per capped slot per day, not one per watchdog tick:
    # 72 ticks/day would otherwise flood events.jsonl and push real history
    # out of the 2 000-line rotation window.
    slots = cap_state.setdefault("slots", [])
    if slot not in slots:
        slots.append(slot)
        store.event("watchdog_dispatch_capped", slot=slot, scope=scope,
                    max_attempts=MAX_ATTEMPTS_PER_SLOT,
                    max_daily=MAX_DAILY_DISPATCHES)
    if cap_state.get("notified"):
        return
    cap_state["notified"] = True
    message = (
        "⚠️ <b>Рековери остановлено по лимиту</b>\n"
        "Слот <code>%s</code> не подтвердился в state после %d попыток "
        "(дневной предел — %d). Повторы прекращены до конца суток, "
        "проверь Actions вручную." % (slot, MAX_ATTEMPTS_PER_SLOT, MAX_DAILY_DISPATCHES)
    )
    if _notify(config, message):
        alerts.append(message)


def watchdog_command(config: Config, store: StateStore, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    today = now.date()
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or config.GitHub_token
    repo = os.getenv("GITHUB_REPOSITORY") or ""
    last = store.data.setdefault("last_runs", {})
    marks: dict[str, Any] = last.setdefault("watchdog_dispatches", {})
    caps: dict[str, Any] = last.setdefault("watchdog_caps", {})
    changed = False
    for old in [k for k in marks if not str(k).startswith(str(today))]:
        del marks[old]
        changed = True   # persist the cleanup of yesterday's unverified marks
    for old in [k for k in caps if k != str(today)]:
        del caps[old]
        changed = True
    if str(today) not in caps:
        caps[str(today)] = {"notified": False}
        changed = True
    cap_state = caps[str(today)]

    missed: list[str] = []
    dispatched: list[str] = []
    verified: list[str] = []
    failed: list[str] = []
    alerts: list[str] = []

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
                message = ("✅ <b>Слот восстановлен</b> <code>%s</code>\n"
                           "Результат подтверждён в state; больше действий не требуется." % key)
                if _notify(config, message):
                    alerts.append(message)
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

        if key == "news-article" and not _has_run(store, "news", today):
            # The article needs today's brief. Never fire a blind dispatch
            # before the AI Brief has actually been published.
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

        allowed, cap_scope = _dispatch_allowed(marks, mark, today)
        if not allowed:
            _note_dispatch_cap(config, store, cap_state, cap_scope, key, alerts)
            continue

        ok, detail = _dispatch(repo, inputs, token)
        attempts = int(prev.get("attempts", 1) if prev else 0) + 1
        marks[mark] = {
            "at": _stamp(now),
            "dispatched": ok,
            "verified": False,          # HTTP 204 is not execution proof
            "detail": detail[:200],
            "attempts": attempts,
        }
        changed = True
        (dispatched if ok else failed).append(key)
        store.event("watchdog_recovered" if ok else "watchdog_dispatch_failed",
                    slot=key, detail=detail[:200], verified=False,
                    attempts=attempts)
        logger.info("watchdog %s: dispatch ok=%s %s (unverified)", key, ok, detail)

        # Silence is the success case: the first accepted dispatch is not a
        # problem yet, and a "Recovered" note arrives once state confirms it.
        # Alert only on a real failure or on a second attempt without proof.
        if not ok:
            message = ("⚠️ <b>Пропущен слот</b> <code>%s</code>\n"
                       "Повтор <b>не удался</b> (%s). Проверь Actions."
                       % (key, detail[:200]))
        elif attempts >= 2:
            message = ("⚠️ <b>Пропущен слот</b> <code>%s</code>\n"
                       "Повторная попытка №%d запущена, но подтверждения в state "
                       "всё ещё нет. Проверь Actions." % (key, attempts))
        else:
            message = ""
        if message and _notify(config, message):
            alerts.append(message)

    # Weekly recovery: only on the matching UTC weekday, and only when the
    # ISO-week marker is absent. A late native cron and this dispatch can
    # both fire in the same week - the command's guard makes the second a
    # harmless no-op, so no duplicate digest/poll/report is possible.
    weekly_active = [slot for slot in WEEKLY_SLOTS if slot[1] == now.weekday()]
    for key, _weekday, (hour, minute), inputs in weekly_active:
        deadline = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        mark = f"{today}:{key}"
        if _has_run_weekly(store, key, now):
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
        missed.append(key)
        prev = marks.get(mark)
        if prev:
            started = _datetime_of(prev.get("at")) or now
            if (now - started).total_seconds() < DISPATCH_COOLDOWN_SECONDS:
                continue
        allowed, cap_scope = _dispatch_allowed(marks, mark, today)
        if not allowed:
            _note_dispatch_cap(config, store, cap_state, cap_scope, key, alerts)
            continue

        ok, detail = _dispatch(repo, inputs, token)
        attempts = int(prev.get("attempts", 1) if prev else 0) + 1
        marks[mark] = {
            "at": _stamp(now), "dispatched": ok, "verified": False,
            "detail": detail[:200], "attempts": attempts,
        }
        changed = True
        (dispatched if ok else failed).append(key)
        store.event("watchdog_recovered" if ok else "watchdog_dispatch_failed",
                    slot=key, detail=detail[:200], verified=False, attempts=attempts)
        if not ok:
            message = ("⚠️ <b>Пропущен слот</b> <code>%s</code>\n"
                       "Повтор <b>не удался</b> (%s). Проверь Actions."
                       % (key, detail[:200]))
            if _notify(config, message):
                alerts.append(message)

    if missed or changed:
        store.save()
    return {"ok": True, "checked": len(SLOTS) + len(weekly_active), "missed": missed,
            "dispatched": dispatched, "verified": verified, "failed": failed,
            "alerts": alerts}
