"""Command-line entry point used by GitHub Actions."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Config
from .doctor import run_doctor
from .http import sanitize
from .pipeline import (
    _news_slot_open,
    alert_owner,
    article_of_day,
    brief_dedup_fix_command,
    cleanup_test_posts,
    close_poll_command,
    collect_command,
    digest_command,
    digest_card_fix_command,
    poll_preview_command,
    poll_replace_command,
    facebook_handoff_command,
    learn_command,
    metrics_command,
    moderation_command,
    news_command,
    news_article_command,
    quality_fix_command,
    process_crossposts,
    publish_slot,
    remove_devto_articles,
    report_command,
    update_recent_article,
    unseries_articles,
)
from .state import StateStore
from .watchdog import NEWS_TERMINAL_REASONS, watchdog_command


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ghos-v6")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Run non-destructive integration checks")
    doctor.add_argument("--hello", action="store_true", help="Send a hello to the resolved test channel")
    doctor.add_argument("--configure-bot", action="store_true", help="Set bot commands/descriptions")
    doctor.add_argument("--news-spike", action="store_true", help="Try Gemini YouTube URL input on the latest WorldofAI video")

    collect = sub.add_parser("collect", help="Collect, enrich, gate and queue candidates")
    collect.add_argument("--pages", action="append", default=[], help="Extra search page(s) to sweep")

    publish = sub.add_parser("publish", help="Publish one queued project")
    publish.add_argument("--slot", default="manual")

    sub.add_parser("crossposts", help="Process due social crossposts")
    sub.add_parser("article", help="Publish the best project of the day as an article")
    sub.add_parser("article-update", help="Re-render the latest dev.to article in place")
    unseries = sub.add_parser(
        "article-unseries", help="Detach dev.to articles from the retired series"
    )
    unseries.add_argument(
        "--ids", default="",
        help="Comma-separated dev.to article ids; state ids are added automatically",
    )

    remove = sub.add_parser("devto-remove", help="Remove legacy duplicate dev.to articles")
    remove.add_argument(
        "--ids", default="",
        help="Comma-separated dev.to article ids to delete/unpublish",
    )

    news = sub.add_parser("news", help="Publish the daily AI Brief from Telegram channels")
    sub.add_parser("news-article", help="Publish the second daily dev.to article from the AI Brief")
    sub.add_parser(
        "quality-fix",
        help="Fix the live AI Brief post and companion article in place (no duplicates)",
    )
    sub.add_parser(
        "brief-dedup-fix",
        help="Remove duplicated rumours from the live AI Brief in place (no new post)",
    )
    news.add_argument("--force", action="store_true", help="Run even if FEATURE_AI_BRIEF=false")
    news.add_argument(
        "--refresh",
        action="store_true",
        help="Regenerate the newest digest and edit the existing post in place (no duplicate)",
    )

    sub.add_parser("digest", help="Publish Sunday Top 5 and poll")
    sub.add_parser("digest-card-fix", help="Replace the live Sunday card with stars, in place")
    sub.add_parser("poll-preview", help="Dry run: show this week's adaptive poll without publishing")
    sub.add_parser("poll-replace", help="Replace today's published poll with the adaptive one, in place")
    sub.add_parser("report", help="Send the weekly owner report")
    metrics = sub.add_parser("metrics", help="Read views for recent posts from the public widget")
    metrics.add_argument("--limit", type=int, default=12)
    sub.add_parser("poll-close", help="Stop this week's poll and store the tally")
    sub.add_parser("learn", help="Update ranking multipliers from measured performance")
    fb = sub.add_parser("facebook-handoff", help="Append Facebook rows to the make.com sheet")
    fb.add_argument("--limit", type=int, default=1)
    sub.add_parser("moderation", help="Poll discussion updates and delete obvious spam")
    sub.add_parser("sources", help="Show verified daily source channels and the rejected ones")
    cleanup = sub.add_parser("cleanup", help="Delete Telegram test posts from the test channel")
    cleanup.add_argument("--confirm", action="store_true", help="actually delete")
    cleanup.add_argument("--dry-run", action="store_true", help="list what would be deleted")

    sub.add_parser("watchdog", help="Recover missed scheduled slots idempotently")
    scheduled = sub.add_parser("scheduled", help="Dispatch by the GitHub cron string")
    scheduled.add_argument("--schedule", required=True)
    return parser



# Owner 11.11: GitHub's ``schedule`` events are best-effort. On 09-10.11 the
# SAME evening slots arrived a second time 6-8 hours late (17:18 -> 00:44,
# 19:18 -> 02:11, 19:53 -> 02:26). Those late duplicates published a second
# project and a second AI Brief inside the NEXT UTC day, consumed that day's
# 3-post quota and its one-brief marker, and made the on-time evening slots
# no-ops. The minute-accurate Cloudflare Worker already dispatches every
# publishing command on time (workflow_dispatch, 0-minute start lag). A late
# native cron therefore must be IGNORED, not executed. Watchdog remains the
# recovery net for a genuinely missed slot.
SCHEDULE_TOLERANCE_MINUTES = 15.0
LATE_SCHEDULE_EXEMPT = frozenset({
    # Thursday close+learn: both commands are ISO-week idempotent, so a late
    # run is a no-op, not a duplicate post/article.
    # Owner 12.11 ("да сделай нужные правки включая фейсбук"): Facebook is NO
    # LONGER exempt. Its native cron arrived 6.5 h late on 11.11. It now has
    # a daily idempotency marker and a watchdog slot, so a late duplicate is
    # ignored exactly like every other publishing slot. Facebook itself stays
    # enabled; Make/Sheet/webhook are untouched.
    "53 18 * * 4",
})

NEWS_ALERT_REASONS = frozenset({
    "not_a_digest", "duplicate_digest", "source_unavailable", "gemini_ineligible",
})
_NEWS_SKIP_TEXT = {
    "not_a_digest": "В каналах не набралось двух разных историй — бриф не вышел, повтор вчерашнего не опубликован.",
    "duplicate_digest": "Все истории уже были в прошлых брифах — сегодня брифа нет, дубль не вышел.",
    "source_unavailable": "Каналы-источники недоступны — бриф пропущен; Watchdog сделает ещё одну попытку.",
    "gemini_ineligible": "Gemini не нашёл фактических новостей — бриф не опубликован.",
}


def _cron_values(field: str, low: int, high: int) -> list[int]:
    """Parse the small cron subset actually used in pipeline.yml."""
    try:
        if field == "*":
            return list(range(low, high + 1))
        if field.startswith("*/"):
            step = max(1, int(field[2:]))
            return list(range(low, high + 1, step))
        return [int(part) for part in field.split(",") if low <= int(part) <= high]
    except ValueError:
        return []


def _latest_expected_run(schedule: str, now: datetime) -> datetime | None:
    """Most recent UTC occurrence this cron string was supposed to fire."""
    parts = schedule.split()
    if len(parts) != 5:
        return None
    minutes = _cron_values(parts[0], 0, 59)
    hours = _cron_values(parts[1], 0, 23)
    doms = _cron_values(parts[2], 1, 31)
    months = _cron_values(parts[3], 1, 12)
    dows = _cron_values(parts[4], 0, 7)
    if not all((minutes, hours, doms, months, dows)):
        return None
    best: datetime | None = None
    for offset in range(8):
        day = (now - timedelta(days=offset)).date()
        if day.month not in months or day.day not in doms:
            continue
        # cron: 0 = Sunday; Python weekday(): 0 = Monday.
        cron_dow = (day.weekday() + 1) % 7
        if cron_dow not in dows and not (cron_dow == 0 and 7 in dows):
            continue
        for hour in hours:
            for minute in minutes:
                candidate = datetime(day.year, day.month, day.day, hour, minute,
                                     tzinfo=timezone.utc)
                if candidate <= now and (best is None or candidate > best):
                    best = candidate
    return best


def _late_schedule_minutes(schedule: str, now: datetime) -> float | None:
    expected = _latest_expected_run(schedule, now)
    if expected is None:
        return None       # unknown cron shape: do not silently drop the command
    return (now - expected).total_seconds() / 60.0


def _schedule_health(store: StateStore, now: datetime) -> tuple[dict[str, Any], dict[str, Any]]:
    health = store.data.setdefault("schedule_health", {})
    day = now.date().isoformat()
    cutoff = (now - timedelta(days=14)).date().isoformat()
    for key in [k for k in health if len(k) == 10 and k[4:5] == "-" and k < cutoff]:
        del health[key]
    return health, health.setdefault(day, {"late_ignored": 0, "skipped_brief": ""})


def _note_late_schedule(config: Config, store: StateStore, schedule: str,
                        late: float, now: datetime) -> dict[str, Any]:
    """Record the ignored late cron and tell the owner ONCE per UTC day."""
    _health, entry = _schedule_health(store, now)
    entry["late_ignored"] = int(entry.get("late_ignored") or 0) + 1
    entry["last_schedule"] = schedule
    store.event("late_schedule_ignored", schedule=schedule,
                minutes_late=round(late, 1))
    if not entry.get("notified"):
        entry["notified"] = True
        alert_owner(
            config,
            "⏰ Запоздалый GitHub-cron проигнорирован",
            "%s: опоздание %.1f ч. Слот уже отработал внешним будильником в "
            "нужную минуту; второй пост или статья не созданы." % (schedule, late / 60),
        )
    store.save()
    return {"ok": True, "published": False, "reason": "late_schedule_ignored",
            "schedule": schedule, "minutes_late": round(late, 1)}


def _record_brief_dedup_stats(store: StateStore, result: dict[str, Any],
                              moment: datetime) -> None:
    """One row per DISTINCT news outcome, including a PUBLISHED brief.

    Owner 12.11: the counter used to be written only when a brief was
    skipped, so after `3587` published on 11.11 with one duplicate removed
    the weekly report still claimed "no brief has passed the new filter yet".
    Exact repeats (the same result recorded twice) must not double count.
    """
    day = moment.date().isoformat()
    removed = int(result.get("removed") or result.get("stories_removed") or 0)
    ratio = float(result.get("duplicate_ratio") or 0)
    kept = int(result.get("stories") or 0)
    published = bool(result.get("published"))
    reason = str(result.get("reason") or ("published" if published else ""))
    fingerprint = "|".join((day, str(published), reason, str(removed),
                            f"{ratio:.4f}", str(kept)))
    stats = store.data.setdefault("brief_dedup_stats", [])
    if (stats and isinstance(stats[-1], dict)
            and stats[-1].get("fingerprint") == fingerprint):
        return
    stats.append({"date": day, "removed": removed, "ratio": ratio,
                  "kept": kept, "published": published, "reason": reason,
                  "fingerprint": fingerprint})
    del stats[:-30]


def _record_news_outcome(config: Config, store: StateStore, result: dict[str, Any],
                         *, refresh: bool = False,
                         now: datetime | None = None) -> dict[str, Any]:
    """Owner 11.11: a skipped brief must be visible, not a silent gap.

    An honest refusal (no fresh stories / source down) is a completed slot:
    the watchdog must not fire three blind retries for it. The owner gets one
    private message per day. Owner 12.11: the de-dup counters are recorded
    for EVERY non-refresh run, including a published brief, so the weekly
    report can judge the filter on 2-3 real briefs.
    """
    if refresh:
        return result
    moment = now or datetime.now(timezone.utc)
    _record_brief_dedup_stats(store, result, moment)
    reason = str(result.get("reason") or "")
    day = moment.date().isoformat()
    normalized = "already_published" if reason == "news_already_published" else reason
    if result.get("published") or not reason:
        store.save()
        return result
    if reason not in NEWS_TERMINAL_REASONS and reason not in NEWS_ALERT_REASONS:
        store.save()
        return result
    last_runs = store.data.setdefault("last_runs", {})
    previous = last_runs.get("news") or {}
    same = (str(previous.get("finished_at") or "").startswith(day)
            and str(previous.get("reason") or "") == normalized)
    if normalized in NEWS_TERMINAL_REASONS and not same:
        last_runs["news"] = {
            "finished_at": moment.isoformat().replace("+00:00", "Z"),
            "reason": normalized,
            "published": False,
        }
    _health, entry = _schedule_health(store, moment)
    entry["skipped_brief"] = normalized
    if normalized in NEWS_ALERT_REASONS and not same:
        alert_owner(config, "🛰️ AI Brief сегодня не вышел",
                    _NEWS_SKIP_TEXT.get(normalized, normalized))
    store.save()
    return result


def _run_scheduled(config: Config, store: StateStore, schedule: str, *,
                  now: datetime | None = None,
                  enforce_window: bool | None = None) -> dict[str, Any]:
    normalized = " ".join(schedule.split())
    moment = now or datetime.now(timezone.utc)
    enforce = (os.getenv("EVENT_NAME") == "schedule") if enforce_window is None else enforce_window
    if enforce and normalized not in LATE_SCHEDULE_EXEMPT:
        late = _late_schedule_minutes(normalized, moment)
        if late is not None and late > SCHEDULE_TOLERANCE_MINUTES:
            return _note_late_schedule(config, store, normalized, late, moment)
    routes: dict[str, Any] = {
        "23 4 * * *": lambda: collect_command(config, store, extra_pages=[1, 2]),
        # Three posts a day (owner decision): morning EU, evening EU, US peak.
        "13 7 * * *": lambda: publish_slot(config, store, "europe_morning"),
        "43 12 * * *": lambda: publish_slot(config, store, "europe_midday"),
        "18 17 * * *": lambda: publish_slot(config, store, "us_peak"),
        "17 0,18,19,20,21,22,23 * * *": lambda: process_crossposts(config, store),
        "33 13 * * *": lambda: article_of_day(config, store),
        # Daily since 2026-11-02: the video brief is a format with its own
        # slot, after the last project post, when the day's videos are out.
        # Owner 10.11: GitHub once delivered this cron ~7h late (02:10 UTC),
        # so the brief went out at 05:11 Minsk with yesterday's material.
        # Refuse an out-of-slot SCHEDULE run without writing a marker; the
        # 19:18 cron / 20:00 watchdog can still publish the evening brief.
        "18 19 * * *": lambda: (
            {"ok": True, "published": False, "reason": "news_before_scheduled_slot"}
            if not _news_slot_open()
            else _record_news_outcome(config, store, news_command(config, store))
        ),
        # Second dev.to article of the day (owner decision 2026-11-07).
        "53 19 * * *": lambda: news_article_command(config, store),
        # Weekly cycle: digest + poll on Sunday, poll closed on Thursday,
        # metrics every evening, learning after closing.
        "13 16 * * 0": lambda: digest_command(config, store),
        # "53 18 * * 4" is handled by ``pairs`` below (close + learn).
        "23 20 * * *": lambda: metrics_command(config, store),
        "43 16 * * 0": lambda: report_command(config, store),
        "47 */3 * * *": lambda: moderation_command(config, store),
        "23 18 * * *": lambda: facebook_handoff_command(config, store),
    }
    # Some crons must run two commands in order (close the poll, then learn
    # from the fresh tallies). Keeping them on one cron avoids a race where
    # ``learn`` reads a poll that has not been closed yet.
    pairs = {"53 18 * * 4": (close_poll_command, learn_command)}
    if normalized in pairs:
        results = [dict(func(config, store)) for func in pairs[normalized]]
        return {"ok": all(r.get("ok", True) for r in results), "steps": results}
    action = routes.get(normalized)
    if action is None:
        raise RuntimeError(f"Unknown schedule: {normalized}")
    return dict(action())


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parser().parse_args(argv)
    config = Config.from_env()
    store: StateStore | None = None
    try:
        store = StateStore(config.state_dir)
        if args.command == "doctor":
            result = run_doctor(
                config,
                configure_bot=args.configure_bot,
                hello=args.hello,
                news_spike=args.news_spike,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
            return 0 if result.get("ok") else 1
        if args.command == "collect":
            result = collect_command(config, store, extra_pages=args.pages)
        elif args.command == "publish":
            result = publish_slot(config, store, args.slot)
        elif args.command == "crossposts":
            result = process_crossposts(config, store)
        elif args.command == "article":
            result = article_of_day(config, store)
        elif args.command == "article-update":
            result = update_recent_article(config, store)
        elif args.command == "article-unseries":
            result = unseries_articles(config, store, ids=args.ids)
        elif args.command == "devto-remove":
            result = remove_devto_articles(config, store, ids=args.ids)
        elif args.command == "news-article":
            result = news_article_command(config, store)
        elif args.command == "quality-fix":
            result = quality_fix_command(config, store)
        elif args.command == "brief-dedup-fix":
            result = brief_dedup_fix_command(config, store)
        elif args.command == "news":
            result = _record_news_outcome(
                config, store,
                news_command(config, store, force=args.force, refresh=args.refresh),
                refresh=args.refresh,
            )
        elif args.command == "digest":
            result = digest_command(config, store)
        elif args.command == "digest-card-fix":
            result = digest_card_fix_command(config, store)
        elif args.command == "poll-preview":
            result = poll_preview_command(config, store)
        elif args.command == "poll-replace":
            result = poll_replace_command(config, store)
        elif args.command == "report":
            result = report_command(config, store)
        elif args.command == "metrics":
            result = metrics_command(config, store, limit=args.limit)
        elif args.command == "poll-close":
            result = close_poll_command(config, store)
        elif args.command == "learn":
            result = learn_command(config, store)
        elif args.command == "facebook-handoff":
            result = facebook_handoff_command(config, store, limit=args.limit)
        elif args.command == "moderation":
            result = moderation_command(config, store)
        elif args.command == "watchdog":
            result = watchdog_command(config, store)
        elif args.command == "cleanup":
            result = cleanup_test_posts(
                config,
                store,
                confirm=args.confirm,
                dry_run=args.dry_run,
            )
        elif args.command == "sources":
            from .sources import REJECTED_SOURCES, VERIFIED_DAILY_SOURCES

            result = {
                "ok": True,
                "daily": [
                    {
                        "handle": source.handle,
                        "posts_per_day": source.posts_per_day,
                        "github_links": source.github_links,
                        "github_posts": source.github_posts,
                        "checked_on": source.checked_on,
                        "note": source.note,
                    }
                    for source in VERIFIED_DAILY_SOURCES
                ],
                "rejected": REJECTED_SOURCES,
            }
        elif args.command == "scheduled":
            result = _run_scheduled(config, store, args.schedule)
        else:  # pragma: no cover - argparse prevents this
            raise RuntimeError(f"Unhandled command {args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        # A skipped slot (e.g. gemini_unavailable) is not success: the
        # workflow must see a non-zero exit and alert the owner.
        if isinstance(result, dict) and result.get("ok") is False:
            return 1
        return 0
    except Exception as exc:  # noqa: BLE001
        message = sanitize(f"{type(exc).__name__}: {exc}")[:1200]
        logging.error("Command failed: %s", message)
        if store is not None:
            try:
                store.event("command_failed", command=args.command, error=message)
                store.save()
            except Exception:
                logging.exception("Failed to persist command failure")
        alert_owner(config, "GitHub Open Source v6 failure", f"{args.command}: {message}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
