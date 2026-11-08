"""Command-line entry point used by GitHub Actions."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any

from .config import Config
from .doctor import run_doctor
from .http import sanitize
from .pipeline import (
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
from .watchdog import watchdog_command


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


def _run_scheduled(config: Config, store: StateStore, schedule: str) -> dict[str, Any]:
    normalized = " ".join(schedule.split())
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
        "18 19 * * *": lambda: news_command(config, store),
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
            result = news_command(config, store, force=args.force, refresh=args.refresh)
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
