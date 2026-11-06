"""End-to-end autonomous publishing pipeline."""
from __future__ import annotations

import html
import logging
import re

import requests
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import dedup, insights, polls
from .cards import render_ai_brief_card, render_digest_card
from .config import Config, telegram_message_link
from .devto_cover import build_devto_cover
from .content_adapters import build_crosspost_text, image_alt_text, public_telegram_link
from .formatter import format_project_post
from .gate import GateConfig, evaluate
from .Gemini import GeminiClient, generate_project_content
from .rate_limit import GeminiBudget
from .http import download_bytes, sanitize
from .media import choose_visual
from .media_host import ImgBBMediaHost
from .upload import shrink_for_web_preview
from .news import (
    NewsSourceError,
    brief_model_names,
    brief_stories,
    card_hook_text,
    extract_primary_urls,
    fetch_primary_text,
    fetch_worldofai_entries,
    format_ai_brief,
    select_latest_news_entry,
    synthesize_brief,
)
from .platforms import BlueskyClient, DevToClient, PlatformError
from .rank import rank_candidates, select_candidate
from .sources import GitHubApi, collect_candidates, enrich_candidate, enrich_candidates
from .state import StateStore, iso_now
from .telegram import TelegramClient, is_fatal_error
from .telegraph import TelegraphClient

logger = logging.getLogger(__name__)


def _parse_iso(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _today_records(store: StateStore, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    result = []
    for record in store.published_records():
        published = _parse_iso(record.get("published_at"))
        if published and published.date() == now.date():
            result.append(record)
    return result


def _recent_records(store: StateStore, days: int = 7) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    records = []
    for record in store.published_records():
        published = _parse_iso(record.get("published_at"))
        if published and published >= cutoff:
            records.append(record)
    return sorted(records, key=lambda item: str(item.get("published_at") or ""), reverse=True)


def _safe_image_stem(candidate: dict[str, Any]) -> str:
    """Filesystem-safe stem for an uploaded preview filename."""
    raw = str(candidate.get("full_name") or candidate.get("name") or "project")
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-").lower() or "project"


def _host_local_image(
    config: Config,
    data: bytes,
    *,
    filename: str,
    content_type: str = "image/png",
) -> tuple[str, str]:
    """Host a rendered card and return a public URL ('' on failure).

    Order: ImgBB (owner's required host, real ``image/*`` content type,
    what Telegram previews and Telegraph ``<img>`` tags need) -> legacy
    Telegraph upload. GitHub release assets are *not* in the chain: they
    answer ``application/octet-stream`` with ``content-disposition:
    attachment`` and Telegram drops the preview without an error.
    Callers decide their own final fallback; this helper never
    silently substitutes a different image.
    """
    if not data:
        return "", ""
    # ImgBB is the owner's required host: it serves a real ``image/png``
    # content-type, which is what makes Telegram render a large preview.
    # GitHub release assets answer ``application/octet-stream`` with
    # ``content-disposition: attachment`` and Telegram silently drops the
    # preview, so it must never be the quiet default.
    imgbb = ImgBBMediaHost(getattr(config, "imgbb_api_key", ""))
    if imgbb.configured:
        try:
            return imgbb.upload_image(data, filename=filename, content_type=content_type), "imgbb"
        except Exception as exc:  # noqa: BLE001 - next host is the fallback
            logger.warning(
                "ImgBB upload failed; trying Telegraph as the last host: %s",
                sanitize(str(exc))[:240],
            )
    else:
        logger.error(
            "IMGBB_API_KEY is empty: cards cannot be hosted on ImgBB, so the "
            "post will go out without its preview. Set the secret before publishing."
        )
    # GitHub release assets serve ``application/octet-stream`` with
    # ``content-disposition: attachment``; Telegram silently drops the
    # preview for those, so that host is no longer a preview candidate.
    try:
        return (
            TelegraphClient(config.telegraph_access_token).upload_image(
                data, filename=filename, content_type=content_type
            ),
            "telegraph",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegraph image upload failed: %s", sanitize(str(exc))[:240])
        return "", ""


def alert_owner(config: Config, title: str, details: str = "") -> None:
    if not config.telegram_bot_token or not config.telegram_owner_chat_id:
        return
    try:
        text = f"<b>{html.escape(title)}</b>"
        if details:
            text += f"\n{html.escape(sanitize(details)[:1200])}"
        TelegramClient(config.telegram_bot_token).send_owner(config.telegram_owner_chat_id, text)
    except Exception:  # noqa: BLE001 - alerting must not mask the original failure
        logger.exception("Owner alert failed")


def collect_command(
    config: Config,
    store: StateStore,
    *,
    extra_pages: list[int] | None = None,
) -> dict[str, Any]:
    started = iso_now()
    candidates = collect_candidates(config, extra_pages=extra_pages or [])
    suppressed = store.data.get("suppressed") or {}
    candidates = [
        candidate
        for candidate in candidates
        if store.repo_key(candidate) not in suppressed
        and (
            not store.is_published(candidate)
            or dedup.check(candidate, store.data)["verdict"] == "revisit"
        )
    ]
    candidates = rank_candidates(candidates)
    api = GitHubApi(config.GitHub_token)
    enriched = enrich_candidates(api, candidates, max_enrich=90)

    gate_cfg = GateConfig()
    passing: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
    for candidate in enriched:
        # Keep the committed queue reasonably small; Gemini gets enough
        # README context without storing whole repositories in git.
        readme = str(candidate.get("readme") or "")
        # The README is used by the gate right here and nowhere else: the
        # publish slot re-fetches it before generation. Keeping 14 KB per
        # queued candidate put ~700 KB into a file that is committed on
        # every run, so only a short excerpt survives into state.
        candidate["readme_len"] = len(readme)
        candidate["readme"] = ""
        candidate["readme_excerpt"] = readme[:1_200]
        decision = evaluate(candidate, gate_cfg)
        candidate["is_hidden_gem"] = decision.is_hidden_gem
        candidate["is_dual_use"] = decision.is_dual_use
        if decision.ok:
            passing.append(candidate)
            store.mark_seen(candidate, "queued")
        else:
            reason = ",".join(decision.reasons) or "unknown"
            rejection_counts.update(decision.reasons or ["unknown"])
            store.mark_seen(candidate, f"rejected:{reason}")

    passing = rank_candidates(passing)
    # The queue is a buffer, not a warehouse: three slots a day means a
    # week of fuel is ~21 posts. Anything beyond that is inventory that
    # gets stale (stars change, projects get archived) and bloats the
    # committed state file. 28 = 9 days at the current cadence.
    queue = passing[:28]
    store.set_queue(queue)
    store.data.setdefault("last_runs", {})["collect"] = {
        "started_at": started,
        "finished_at": iso_now(),
        "discovered": len(candidates),
        "enriched": len(enriched),
        "passed_gate": len(passing),
        "queued": len(queue),
        "rejections": dict(rejection_counts),
    }
    store.event(
        "collect_completed",
        discovered=len(candidates),
        enriched=len(enriched),
        passed_gate=len(passing),
        queued=len(queue),
        rejections=dict(rejection_counts),
    )
    store.save()
    return {
        "ok": True,
        "discovered": len(candidates),
        "enriched": len(enriched),
        "passed_gate": len(passing),
        "queued": len(queue),
        "top": [candidate.get("full_name") for candidate in queue[:5]],
        "rejections": dict(rejection_counts),
    }


def _public_record_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "repo_id",
        "id",
        "name",
        "full_name",
        "url",
        "description",
        "homepage",
        "language",
        "stars",
        "forks",
        "open_issues",
        "contributors_count",
        "topics",
        "license",
        "created_at",
        "pushed_at",
        "updated_at",
        "sources",
        "hn_points",
        "score",
        "is_hidden_gem",
        "is_dual_use",
        "media_urls",
    )
    return {key: candidate.get(key) for key in keep if key in candidate}


def _article_markdown(record_candidate: dict[str, Any], generated: dict[str, Any]) -> str:
    """Telegraph wrapper: standfirst + snapshot, then the long breakdown.

    The article is part of the Telegram funnel: a small follow line up
    top and a clear discussion/follow CTA at the end, not repo links
    only.
    """
    markdown = str(generated.get("article_markdown") or "").strip()
    lines = markdown.splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        markdown = "\n".join(lines[1:]).strip()
    hook = str(generated.get("hook") or record_candidate.get("description") or "").strip()
    lic = record_candidate.get("license")
    license_name = ""
    if isinstance(lic, dict):
        license_name = str(lic.get("spdx_id") or lic.get("name") or "")
    elif lic:
        license_name = str(lic)

    snapshot_lines: list[str] = []
    if record_candidate.get("full_name"):
        snapshot_lines.append(f"- **Repository:** {record_candidate['full_name']}")
    if record_candidate.get("language"):
        snapshot_lines.append(f"- **Language:** {record_candidate['language']}")
    if license_name:
        snapshot_lines.append(f"- **License:** {license_name}")
    try:
        snapshot_lines.append(
            f"- **Stars:** {int(record_candidate.get('stars') or 0):,}".replace(",", " ")
        )
    except (TypeError, ValueError):
        pass
    try:
        if record_candidate.get("forks") is not None:
            snapshot_lines.append(
                f"- **Forks:** {int(record_candidate.get('forks') or 0):,}".replace(",", " ")
            )
    except (TypeError, ValueError):
        pass

    intro = ""
    if hook:
        intro += f"**{hook}**\n\n"
    if snapshot_lines:
        intro += "**Repository snapshot**\n\n" + "\n".join(snapshot_lines) + "\n\n"
    intro += "*Daily open-source picks: [GitHub Open Source on Telegram](https://t.me/GitHub_Open_Source)*\n\n"

    links = [
        "- [Discuss this pick and get daily finds on Telegram](https://t.me/GitHub_Open_Source)",
        f"- [GitHub repository]({record_candidate.get('url')})",
    ]
    if record_candidate.get("homepage"):
        links.append(f"- [Project homepage]({record_candidate['homepage']})")
    return (
        intro
        + markdown
        + "\n\n## Continue exploring\n\n"
        + "\n".join(links)
        + "\n\n**Get the next pick in Telegram:** "
        "[Follow GitHub Open Source](https://t.me/GitHub_Open_Source)"
        + "\n\n*Curated from the repository README and metadata. "
        "No hands-on test is claimed; evaluate it in a sandbox before production use.*\n"
    )

MAX_PROJECT_POSTS_PER_DAY = 3


def _crosspost_due(now: datetime | None = None) -> datetime:
    """Crossposts wait until the day's last project slot.

    A crosspost sent 30 minutes after the morning post would crown the
    morning post before the midday/evening projects exist. 18:00 UTC is after
    the 17:15 slot, so the winner is chosen from the whole day.
    """
    now = now or datetime.now(timezone.utc)
    due = now + timedelta(minutes=30)
    if due.hour < 18:
        due = due.replace(hour=18, minute=0, second=0, microsecond=0)
    return due


def publish_slot(config: Config, store: StateStore, slot: str = "manual") -> dict[str, Any]:
    config.require("TELEGRAM_BOT_TOKEN")
    chat_id = config.telegram_chat_id
    # The three-a-day limit lived only in the cron. A manual run, a retried
    # job or a fourth slot added later would have posted a fourth project and
    # nobody would have noticed until the channel looked wrong. The limit is
    # the owner's rule, so it belongs in code.
    already_today = len(_today_records(store))
    if already_today >= MAX_PROJECT_POSTS_PER_DAY:
        store.event(
            "publish_skipped",
            slot=slot,
            reason="daily_limit_reached",
            published_today=already_today,
        )
        store.save()
        return {
            "ok": True,
            "published": False,
            "reason": "daily_limit_reached",
            "slot": slot,
            "published_today": already_today,
        }
    if not store.data.get("queue"):
        collect_command(config, store)

    telegram = TelegramClient(config.telegram_bot_token)
    Gemini = GeminiClient(config.Gemini_api_key, config.Gemini_model,
                      budget=GeminiBudget(store),
                      fallback_model="gemini-3.1-flash-lite",
                      http_retries=0, retry_post=False)
    api = GitHubApi(config.GitHub_token)
    gate_cfg = GateConfig()
    # One Gemini call per published post, never per queued candidate: the
    # queue is ranked deterministically, so generating copy for projects that
    # may never be posted would burn quota for nothing. The cap also bounds
    # the worst case (a run of rejected candidates) to 4 calls, not 10.
    max_attempts = 4
    attempts = 0

    while attempts < max_attempts:
        attempts += 1
        published_today = _today_records(store)
        candidate = select_candidate(
            list(store.data.get("queue") or []),
            published_today,
            store.data.get("value_model"),
        )
        if candidate is None:
            store.event("publish_skipped", slot=slot, reason="no_candidate")
            store.save()
            return {"ok": True, "published": False, "reason": "no_candidate", "slot": slot}

        key = store.repo_key(candidate)
        try:
            candidate = enrich_candidate(api, dict(candidate), force=True)
            candidate["readme"] = str(candidate.get("readme") or "")[:20_000]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Selected candidate refresh failed for %s: %s", key, type(exc).__name__)

        verdict = dedup.check(candidate, store.data)
        if verdict["verdict"] == "duplicate":
            store.pop_queue_candidate(key)
            store.mark_seen(candidate, f"duplicate:{verdict['reason']}")
            store.event("publish_candidate_duplicate", repo=key, reason=verdict["reason"])
            store.save()
            continue
        if verdict["verdict"] == "revisit":
            candidate["revisit_of"] = key
            candidate["revisit_reason"] = verdict["reason"]

        pre_decision = evaluate(candidate, gate_cfg)
        if not pre_decision.ok:
            store.pop_queue_candidate(key)
            store.mark_seen(candidate, "rejected:" + ",".join(pre_decision.reasons))
            store.event("publish_candidate_rejected", repo=key, reasons=pre_decision.reasons, stage="pre_llm")
            store.save()
            continue
        candidate["is_hidden_gem"] = pre_decision.is_hidden_gem
        candidate["is_dual_use"] = pre_decision.is_dual_use

        generated = generate_project_content(Gemini, candidate)
        if not generated.get("llm_used"):
            # No model, no verdict. Retry once, but a project the model keeps
    # failing on must not block the queue forever.
            failures = store.data.setdefault("publish_failures", {})
            count = int(failures.get(key, 0)) + 1
            failures[key] = count
            if count >= 2:
                store.pop_queue_candidate(key)
                store.mark_seen(candidate, "gemini_failed_twice")
                store.add_dead_letter(
                    {
                        "repo": key,
                        "platform": "gemini",
                        "error": "gemini_unavailable twice",
                        "created_at": iso_now(),
                        "note": "removed from queue after 2 model failures",
                    }
                )
                store.event("publish_candidate_dead", repo=key, reason="gemini_failed_twice", failures=count)
                store.save()
                return {
                    "ok": False,
                    "published": False,
                    "reason": "gemini_failed_twice",
                    "repo": key,
                    "candidate_removed": True,
                }
            store.event("publish_skipped", slot=slot, reason="gemini_unavailable",
                        repo=key, failures=count,
                        gemini_error_class=str(generated.get("error_class") or "unknown"))
            store.save()
            return {
                "ok": False,
                "published": False,
                "reason": "gemini_unavailable",
                "repo": key,
                "queue_unchanged": True,
                "failures": count,
            }
        candidate["safety"] = generated.get("safety")
        post_decision = evaluate(candidate, gate_cfg, require_safety=True)
        candidate["is_dual_use"] = bool(
            post_decision.is_dual_use or generated.get("safety") == "dual_use"
        )
        candidate["is_hidden_gem"] = post_decision.is_hidden_gem
        if not post_decision.ok:
            store.pop_queue_candidate(key)
            store.mark_seen(candidate, "rejected:" + ",".join(post_decision.reasons))
            store.event(
                "publish_candidate_rejected",
                repo=key,
                reasons=post_decision.reasons,
                stage="post_llm",
                safety_reason=generated.get("safety_reason") or "",
            )
            store.save()
            continue

        issue_number = len(store.published_records()) + 1
        visual = choose_visual(candidate, generated, issue_number=issue_number)
        telegraph = TelegraphClient(config.telegraph_access_token)
        image_url = visual.url
        effective_visual_kind = visual.kind
        visual_host = "readme" if visual.kind == "readme" else ""
        if visual.kind == "readme" and visual.data:
            # A README image used to be passed through as the vendor's own
            # URL. That makes the post depend on a third-party host staying
            # up and on its hotlink policy, and it leaves nothing on ImgBB —
            # while cross-posts and the article both reuse image_url. Host a
            # shrunk copy on ImgBB; keep the original URL if the upload
            # fails so a good picture is never lost to a hosting error.
            hosted, hosted_by = _host_local_image(
                config,
                shrink_for_web_preview(visual.data),
                filename=_safe_image_stem(candidate) + "-readme-preview.jpg",
                content_type="image/jpeg",
            )
            if hosted:
                image_url = hosted
                visual_host = hosted_by
        if visual.kind == "card" and visual.data:
            hosted, hosted_by = _host_local_image(
                config,
                # Preview crawlers need a small, real image/* file; the
                # PNG stays the master for Telegraph and dev.to covers.
                shrink_for_web_preview(visual.data),
                filename=(visual.local_path.stem if visual.local_path else "cover") + "-preview.jpg",
                content_type="image/jpeg",
            )
            if hosted:
                image_url = hosted
                visual_host = hosted_by
            else:
                # Owner decision (2026-11-04): a failed image host must never
                # degrade to GitHub's generic repository card. Keep no image
                # and let our own Telegraph page be the link preview instead.
                image_url = ""
                effective_visual_kind = "text"
                visual_host = "no_preview"
                store.event("card_host_failed", repo=key, fallback="no_preview")

        telegraph_url = ""
        article_markdown = _article_markdown(candidate, generated)
        if config.feature_telegraph and telegraph.configured:
            try:
                page = telegraph.create_page(
                    title=str(generated.get("article_title") or candidate.get("name") or "Project breakdown"),
                    markdown=article_markdown,
                    image_url=image_url,
                    image_caption=str(candidate.get("full_name") or ""),
                )
                telegraph_url = str(page.get("url") or "")
            except Exception as exc:  # noqa: BLE001
                logger.warning("Telegraph publish failed; continuing: %s", sanitize(str(exc))[:250])
                store.event("telegraph_failed", repo=key, error=sanitize(str(exc))[:250])

        format_input = dict(candidate)
        format_input.update(
            {
                "hook": generated.get("hook"),
                "readme_summary": generated.get("summary"),
                "primary_use_case": generated.get("summary"),
                "key_features": generated.get("key_features"),
                "who_for": generated.get("who_for"),
                "why_now": generated.get("why_now"),
                "verdict": generated.get("verdict"),
                "quick_start": generated.get("quick_start"),
                "discussion_question": generated.get("discussion_question"),
                "category": generated.get("category") or candidate.get("category"),
                "is_dual_use": candidate.get("is_dual_use"),
                # The editorial history is what makes the post a story, not
        # a spec sheet. It was generated and paid for; pass it through.
                "scene": generated.get("scene"),
                "tension": generated.get("tension"),
                "turn": generated.get("turn"),
            }
        )
        text_html = format_project_post(format_input, telegraph_url=telegraph_url)
        # The owner's format: the long post is the message itself, and the
        # rendered card rides on top as a large link preview. A photo post
        # cannot carry long text at all (caption ceiling is 1024 chars), so
        # publishing as a photo would silently cost the story.
        # Never point the preview at the repository URL: Telegram would
        # unfold GitHub's default OpenGraph image, which the owner rejected.
        preview_url = image_url or telegraph_url
        try:
            sent = telegram.send_project(
                chat_id,
                text_html,
                preview_url=preview_url,
                github_url=str(candidate.get("url") or ""),
                telegraph_url=telegraph_url,
                homepage_url=str(candidate.get("homepage") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - one bad candidate must not block the queue
            error = sanitize(str(exc))[:400]
            if is_fatal_error(exc):
                # 401/403, chat not found, network failure/timeout: abort the
                # slot and keep the candidate. The owner must fix the config;
                # dropping the project would lose it silently.
                if "timeout" in error.lower() or "connection" in error.lower() or "network" in error.lower():
                    store.data.setdefault("suppressed", {})[key] = {
                        "reason": "telegram_uncertain",
                        "at": iso_now(),
                        "error": error,
                    }
                store.event("publish_failed", repo=key, error=error, candidate_kept=True, fatal=True)
                store.save()
                logger.warning("Publish aborted for %s; candidate kept: %s", key, error)
                return {
                    "ok": False,
                    "published": False,
                    "reason": "telegram_failed",
                    "repo": key,
                    "candidate_kept": True,
                    "error": error,
                }
            # A message-level error (bad URL, caption too long): remove this
            # candidate and record it for a human.
            store.pop_queue_candidate(key)
            store.mark_seen(candidate, "publish_error")
            store.add_dead_letter(
                {
                    "repo": key,
                    "platform": "telegram",
                    "error": error,
                    "created_at": iso_now(),
                    "telegraph_url": telegraph_url,
                    "note": "removed from queue; may or may not have been sent",
                }
            )
            store.event("publish_failed", repo=key, error=error, candidate_removed=True)
            store.save()
            logger.warning("Publish failed for %s; candidate removed: %s", key, error)
            continue
        store.event("published_as_text", repo=key, preview_host=visual_host or "none")
        message_id = int(sent.get("message_id") or 0)
        due = _crosspost_due()
        record = _public_record_candidate(candidate)
        record.update(
            {
                "generated": generated,
                "category": generated.get("category") or "",
                "published_at": iso_now(),
                "slot": slot,
                "telegram_chat_id": chat_id,
                "telegram_message_id": message_id,
                "telegram_link": telegram_message_link(
                    chat_id,
                    message_id,
                    main=config.is_main_target,
                ),
                "telegraph_url": telegraph_url,
                "image_url": image_url,
                "visual_kind": effective_visual_kind,
                "description_fp": dedup.description_fingerprint(str(candidate.get("description") or "")),
                "readme_fp": dedup.readme_fingerprint(dedup.readme_for_fingerprint(candidate)),
                "revisit_of": candidate.get("revisit_of") or "",
                "revisit_reason": candidate.get("revisit_reason") or "",
                "visual_host": visual_host,
                "card_style": visual.card_style,
                "crossposts": {
                    "bluesky": {"status": "pending" if config.feature_bluesky else "disabled", "due_at": due.isoformat(), "attempts": 0},
                },
                "articles": {},
            }
        )
        if not config.is_main_target:
            # state.json is public. Never commit the private test-channel id
            # or its t.me/c link; the operator can find the post in Telegram.
            record["telegram_chat_id"] = "test"
            record["telegram_link"] = ""
        store.record_published(record)
        store.mark_seen(record, "published")
        # A successful publish must clear the retry counter; otherwise the
        # next transient failure on a revisit would dead-letter the repo.
        store.data.get("publish_failures", {}).pop(key, None)
        store.data.setdefault("last_runs", {})[f"publish:{slot}"] = {
            "finished_at": iso_now(),
            "repo": key,
            "telegram_message_id": message_id,
        }
        store.save()
        return {
            "ok": True,
            "published": True,
            "repo": key,
            "telegram_message_id": message_id,
            "telegram_link": record["telegram_link"],
            "telegraph_url": telegraph_url,
            "visual": visual.kind,
            "llm_used": bool(generated.get("llm_used")),
            "slot": slot,
        }

    store.event("publish_skipped", slot=slot, reason="all_candidates_rejected")
    store.save()
    return {"ok": True, "published": False, "reason": "all_candidates_rejected", "slot": slot}


def _download_record_image(record: dict[str, Any]) -> tuple[bytes, str]:
    image_url = str(record.get("image_url") or "")
    if not image_url.startswith("https://"):
        return b"", "image/png"
    try:
        data = download_bytes(image_url, max_bytes=5_000_000)
    except Exception:
        return b"", "image/png"
    lower = image_url.lower()
    if lower.endswith((".jpg", ".jpeg")):
        return data, "image/jpeg"
    if lower.endswith(".gif"):
        return data, "image/gif"
    if lower.endswith(".webp"):
        return data, "image/webp"
    return data, "image/png"


def _ensure_devto_cover(config: Config, record: dict[str, Any]) -> str:
    """Host a 1000x420 cover so dev.to's fixed fit=cover cannot cut the card.

    The Telegram/Bluesky ``image_url`` stays untouched: it is a 1200x630
    card, which is correct there. Only dev.to needs its own aspect ratio.
    """
    existing = str(record.get("devto_cover_url") or "")
    if existing.startswith("https://"):
        return existing
    data, _content_type = _download_record_image(record)
    if not data:
        return ""
    try:
        cover = build_devto_cover(data)
    except Exception as exc:  # noqa: BLE001 - fall back to the plain card
        logger.warning("dev.to cover render failed: %s", sanitize(str(exc))[:200])
        return ""
    url, _host = _host_local_image(
        config,
        cover,
        filename=_safe_image_stem(record) + "-devto-cover.png",
        content_type="image/png",
    )
    if url:
        record["devto_cover_url"] = url
    return url


def process_crossposts(config: Config, store: StateStore) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    summary = {"ok": True, "processed": 0, "published": 0, "failed": 0, "dead": 0}
    clients: dict[str, Any] = {}

    def client_for(platform: str):
        if platform not in clients:
            if platform == "bluesky":
                clients[platform] = BlueskyClient(config.bluesky_handle, config.bluesky_app_password)
        return clients.get(platform)

    enabled = {
        "bluesky": config.feature_bluesky,
    }

    def best_of_day() -> str:
        """The day's single winner, fixed as soon as one post has gone out.

        Owner decision: three project posts a day all going to Bluesky,
        Bluesky and Facebook meant 63 cross-posts a week for 21 projects.
        That is blanket publishing, which the multi-platform agent warns
        against. Only one post per day leaves Telegram; the rest stay in the
        channel. dev.to already worked this way.

        The winner is chosen from *all* of today's posts, not only from the
        ones that still have a pending cross-post. And once any post has
        already been published to a platform today, the day's winner is
        fixed: a later, higher-scoring post must not take a second slot.
        """
        today = datetime.now(timezone.utc).date().isoformat()
        best_key = ""
        best_score = float("-inf")
        for record_key, record in (store.data.get("published") or {}).items():
            published_at = _parse_iso(record.get("published_at"))
            if not published_at or published_at.date().isoformat() != today:
                continue
            for info in (record.get("crossposts") or {}).values():
                if info.get("status") != "published":
                    continue
                crosspost_at = _parse_iso(info.get("published_at"))
                if crosspost_at and crosspost_at.date().isoformat() == today:
                    # The day's slot is used. Keep the same winner for every
                    # platform so a second post cannot sneak in later.
                    return record_key
            score = float(record.get("score") or 0)
            if score > best_score:
                best_score, best_key = score, record_key
        return best_key

    winner = best_of_day()
    winners = {platform: winner for platform, is_on in enabled.items() if is_on}

    for key, record in list(store.data.get("published", {}).items()):
        generated = record.get("generated") or {}
        is_winner = any(winners.get(platform) == key for platform in enabled if enabled[platform])
        if not is_winner:
            # Mark the losers without downloading their image: the archive
            # grows forever and the crosspost runs every 30 minutes.
            for platform, status_info in list((record.get("crossposts") or {}).items()):
                if platform not in enabled or not enabled[platform]:
                    continue
                if status_info.get("status") in {"pending", "failed"}:
                    status_info["status"] = "held"
                    status_info["held_reason"] = "not_best_of_day"
            continue
        image_data, content_type = _download_record_image(record)
        for platform, status_info in list((record.get("crossposts") or {}).items()):
            if platform not in enabled:
                continue
            if not enabled[platform]:
                if status_info.get("status") == "pending":
                    status_info["status"] = "disabled"
                continue
            if status_info.get("status") not in {"pending", "failed"}:
                continue
            due = _parse_iso(status_info.get("due_at"))
            if due and due > now:
                continue
            summary["processed"] += 1
            try:
                client = client_for(platform)
                # Native copy per surface: limits, link placement, tags
                # and tone differ deliberately.  Private test-channel
                # t.me/c links are never used in public social copy.
                text = build_crosspost_text(platform, record, generated)
                result = client.post(
                    text,
                    url="",
                    image_data=image_data,
                    content_type=content_type,
                    image_alt=image_alt_text(record, generated),
                    idempotency_key=f"{key}:{platform}",
                )
                result_url = str(result.get("url") or "")
                status_info.update(
                    {"status": "published", "published_at": iso_now(), "url": result_url, "error": ""}
                )
                store.event("crosspost_published", repo=key, platform=platform, url=result_url)
                summary["published"] += 1
            except (requests.Timeout, requests.ConnectionError) as exc:
                # The request may have reached the platform. Retrying can
                # create a duplicate, so park it for a human instead.
                error = sanitize(f"{type(exc).__name__}: {exc}")[:400]
                status_info["status"] = "uncertain"
                status_info["error"] = error
                status_info["attempts"] = int(status_info.get("attempts") or 0) + 1
                store.add_dead_letter(
                    {
                        "repo": key,
                        "platform": platform,
                        "attempts": status_info["attempts"],
                        "error": error,
                        "created_at": iso_now(),
                        "note": "timeout: may have been published; verify before retrying",
                    }
                )
                store.event("crosspost_uncertain", repo=key, platform=platform, error=error)
                summary["failed"] += 1
                continue
            except Exception as exc:  # noqa: BLE001
                attempts = int(status_info.get("attempts") or 0) + 1
                error = sanitize(str(exc))[:400]
                status_info["attempts"] = attempts
                status_info["error"] = error
                if attempts >= 3:
                    status_info["status"] = "dead"
                    store.add_dead_letter(
                        {
                            "repo": key,
                            "platform": platform,
                            "attempts": attempts,
                            "error": error,
                            "created_at": iso_now(),
                        }
                    )
                    summary["dead"] += 1
                else:
                    status_info["status"] = "failed"
                    delay = [30, 120, 360][min(attempts - 1, 2)]
                    status_info["due_at"] = (now + timedelta(minutes=delay)).isoformat()
                store.event("crosspost_failed", repo=key, platform=platform, attempts=attempts, error=error)
                summary["failed"] += 1
    store.save()
    return summary


def _devto_duplicate_url(owned: list[dict[str, Any]], record: dict[str, Any]) -> str:
    """Find an article on the dev.to account that already covers this repo.

    ``owned`` is fetched ONCE per article run, before any expensive work.
    dev.to's own index is a cheap GET; a cover upload and a publish POST
    consume real service quota. State alone is not enough: pre-migration
    posts live on the same dev.to account but not in this repository's state,
    which is exactly how the Horizon duplicate happened.
    """
    full_name = str(record.get("full_name") or "")
    if "/" not in full_name:
        return ""
    needle = f"https://github.com/{full_name}".lower()
    for item in owned:
        blob = " ".join(
            str(item.get(k) or "") for k in ("body_markdown", "canonical_url", "title", "url")
        ).lower()
        if needle in blob:
            return str(item.get("url") or "")
    return ""


def _load_devto_index(client: DevToClient) -> list[dict[str, Any]]:
    """One cheap GET per run. A broken index must not fail the whole article."""
    try:
        return list(client.my_articles(per_page=100) or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning("dev.to article index unavailable: %s", sanitize(str(exc))[:180])
        return []


def _duplicate_cached_today(record: dict[str, Any], day: str) -> bool:
    """A duplicate confirmed earlier today needs no second dev.to request."""
    info = (record.get("articles") or {}).get("devto_duplicate") or {}
    parsed = _parse_iso(info.get("checked_at"))
    return bool(parsed and parsed.date().isoformat() == day)


def _article_slot_done(store: StateStore, result: dict[str, Any]) -> None:
    """Record that the article command ran for this UTC day, whatever the verdict.

    Owner decision (2026-11-06): an honest refusal (duplicate, no candidates,
    already published) is a completed slot, not a missed one. Without this
    fact the watchdog kept dispatching ``article`` every 30 minutes on 06.11
    and escalated to a warning on every repeated attempt.
    """
    store.data.setdefault("last_runs", {})["article"] = {
        "finished_at": iso_now(),
        "reason": result.get("reason") or ("published" if result.get("published") else "done"),
        "repo": result.get("repo", ""),
        "url": result.get("url", ""),
        "published": bool(result.get("published")),
    }
    store.save()


def article_of_day(config: Config, store: StateStore) -> dict[str, Any]:
    # Owner decision (2026-11-04): one article per UTC day. The old loop
    # walked every one of today's posts and published a second article when
    # the cron ran after a manual dispatch.
    day = datetime.now(timezone.utc).date().isoformat()
    for existing in (store.data.get("published") or {}).values():
        info = (existing.get("articles") or {}).get("devto") or {}
        if not info or info.get("skipped"):
            continue
        published_at = _parse_iso(info.get("published_at"))
        if published_at and published_at.date().isoformat() == day:
            store.event("article_skipped", reason="already_published_today")
            result = {"ok": True, "published": False, "reason": "already_published_today"}
            _article_slot_done(store, result)
            return result

    # Best score first: if the top project is already covered on dev.to, fall
    # through to the next one instead of returning an honest but empty refusal.
    today = sorted(_today_records(store), key=lambda item: float(item.get("score") or 0), reverse=True)
    if not today:
        result = {"ok": True, "published": False, "reason": "no_projects_today"}
        _article_slot_done(store, result)
        return result
    if not config.feature_devto:
        result = {"ok": True, "published": False, "reason": "feature_devto_disabled"}
        _article_slot_done(store, result)
        return result

    candidates = [r for r in today if not (r.get("articles") or {}).get("devto")]
    if not candidates:
        result = {"ok": True, "published": False, "reason": "already_published"}
        _article_slot_done(store, result)
        return result

    # Cheap duplicate check FIRST, cached for today, one dev.to GET per run.
    # Never spend ImgBB cover uploads or dev.to publish quota on a repo that
    # is already covered, and never repeat the index lookup for a cached dupe.
    unchecked = [r for r in candidates if not _duplicate_cached_today(r, day)]
    client = DevToClient(config.dev_api_key) if unchecked else None
    owned = _load_devto_index(client) if client is not None else []
    duplicates: list[dict[str, str]] = []

    for record in candidates:
        key = store.repo_key(record)
        if _duplicate_cached_today(record, day):
            duplicates.append({"repo": key, "existing_url": "cached"})
            continue
        duplicate = _devto_duplicate_url(owned, record)
        if duplicate:
            store.event("article_skipped_duplicate", repo=key, existing_url=duplicate)
            record.setdefault("articles", {})["devto_duplicate"] = {
                "url": duplicate, "checked_at": iso_now(),
            }
            duplicates.append({"repo": key, "existing_url": duplicate})
            store.save()
            continue  # next-best project, not an empty return
        publisher = client or DevToClient(config.dev_api_key)
        _ensure_devto_cover(config, record)
        published = publisher.publish_article(record, record.get("generated") or {})
        record.setdefault("articles", {})["devto"] = {
            "id": published.get("id"),
            "url": published.get("url") or "",
            "published_at": iso_now(),
        }
        store.event("article_published", repo=key, platform="devto",
                    url=published.get("url") or "")
        result = {"ok": True, "published": True, "repo": key,
                  "url": published.get("url") or "",
                  "skipped_duplicates": len(duplicates)}
        _article_slot_done(store, result)
        return result

    result = {
        "ok": True, "published": False, "reason": "duplicate_repo_on_devto",
        "duplicates": len(duplicates),
        "repo": duplicates[0]["repo"] if duplicates else "",
        "existing_url": duplicates[-1]["existing_url"] if duplicates else "",
    }
    store.event("article_skipped_all_duplicates", count=len(duplicates))
    _article_slot_done(store, result)
    return result


def update_recent_article(config: Config, store: StateStore) -> dict[str, Any]:
    """Re-render and PUT the most recent dev.to article in place.

    Used when the article renderer changes: the owner sees the fix on the
    already published URL instead of a second, duplicate article.
    """
    config.require("DEV_API_KEY")
    candidates = [
        record
        for record in (store.data.get("published") or {}).values()
        if (record.get("articles") or {}).get("devto", {}).get("id")
    ]
    if not candidates:
        return {"ok": True, "updated": 0, "reason": "no_devto_articles"}
    record = max(candidates, key=lambda item: str(item.get("published_at") or ""))
    key = store.repo_key(record)
    info = record["articles"]["devto"]
    client = DevToClient(config.dev_api_key)
    try:
        owned = client.my_articles()
    except Exception as exc:  # noqa: BLE001 - diagnostics must not kill the update
        logger.warning("dev.to article list unavailable: %s", sanitize(str(exc))[:200])
        owned = []
    article_id, id_source = client.resolve_article_id(
        owned, str(info.get("url") or ""), info.get("id") or 0
    )
    _ensure_devto_cover(config, record)
    try:
        result = client.update_article(
            article_id, record, record.get("generated") or {}
        )
        action = "updated"
    except PlatformError as exc:
        # The article can disappear (moderation, manual deletion, a stale
        # id). A 404 must not strand the fix: publish one fresh, correctly
        # formatted article instead of failing or duplicating a live post.
        if "404" not in str(exc):
            raise
        result = client.publish_article(record, record.get("generated") or {})
        action = "created"
    info["id"] = result.get("id") or info.get("id")
    info["updated_at"] = iso_now()
    info["url"] = str(result.get("url") or info.get("url") or "")
    store.event(
        "article_updated" if action == "updated" else "article_recreated",
        repo=key, url=info["url"],
    )
    store.save()
    return {
        "ok": True, "updated": 1 if action == "updated" else 0,
        "created": 1 if action == "created" else 0,
        "action": action, "repo": key, "url": info["url"],
        "owned_articles": len(owned), "id_source": id_source,
    }


def unseries_articles(config: Config, store: StateStore, ids: str = "") -> dict[str, Any]:
    """Detach dev.to articles from the retired legacy series.

    The owner wants independent posts, not a daily growing series. Explicit
    ids cover legacy articles from the pre-migration account; ids recorded in
    state are added automatically. PUT with ``series: None`` is idempotent.
    """
    config.require("DEV_API_KEY")
    raw = [part.strip() for part in re.split(r"[\s,]+", str(ids or "")) if part.strip()]
    known = [
        str(info.get("id"))
        for record in (store.data.get("published") or {}).values()
        for info in [(record.get("articles") or {}).get("devto") or {}]
        if info.get("id")
    ]
    targets: list[str] = []
    for value in raw + known:
        if not re.fullmatch(r"\d{1,12}", value):
            raise ValueError(f"invalid dev.to article id: {value!r}")
        if value not in targets:
            targets.append(value)
    client = DevToClient(config.dev_api_key)
    cleared: list[str] = []
    skipped: list[str] = []
    for article_id in targets:
        try:
            client.clear_series(article_id)
            cleared.append(article_id)
        except PlatformError as exc:
            if "404" not in str(exc):
                raise
            skipped.append(article_id)
    store.event("devto_series_cleared", ids=cleared, cleared=len(cleared))
    store.save()
    return {"ok": True, "cleared": len(cleared), "ids": cleared, "missing": skipped}


def remove_devto_articles(config: Config, store: StateStore, ids: str = "") -> dict[str, Any]:
    """Remove legacy duplicate articles from the dev.to account.

    Owner decision (2026-11-04): delete the older of the two Horizon
    articles. An id that is still referenced by our state is protected:
    the live pipeline must never lose the article it links to.
    """
    config.require("DEV_API_KEY")
    raw = [part.strip() for part in re.split(r"[\s,]+", str(ids or "")) if part.strip()]
    protected = {
        str(info.get("id"))
        for record in (store.data.get("published") or {}).values()
        for info in [(record.get("articles") or {}).get("devto") or {}]
        if info.get("id")
    }
    targets: list[str] = []
    blocked: list[str] = []
    for value in raw:
        if not re.fullmatch(r"\d{1,12}", value):
            raise ValueError(f"invalid dev.to article id: {value!r}")
        if value in protected:
            blocked.append(value)
        elif value not in targets:
            targets.append(value)
    client = DevToClient(config.dev_api_key)
    removed: list[dict[str, str]] = []
    missing: list[str] = []
    for article_id in targets:
        try:
            how = client.remove_article(article_id)
            removed.append({"id": article_id, "action": how})
        except PlatformError as exc:
            if "404" not in str(exc):
                raise
            missing.append(article_id)
    store.event("devto_articles_removed", ids=[item["id"] for item in removed], blocked=blocked)
    store.save()
    return {"ok": True, "removed": removed, "missing": missing, "blocked": blocked}


def news_command(
    config: Config,
    store: StateStore,
    *,
    force: bool = False,
    refresh: bool = False,
) -> dict[str, Any]:
    """Owner decision (2026-11-04): ONE brief per newest video, as a DIGEST.

    Freshness (today or yesterday), no duplicate video/topic, Gemini cuts
    every sponsor read, extracts ALL model stories with exact technical
    names, and the post carries no source attribution.

    ``refresh`` regenerates the newest brief and EDITS the already published
    channel message in place. Improving a weak post must never create a
    duplicate of the same video.
    """
    if not config.feature_ai_brief and not force:
        return {"ok": True, "published": False, "reason": "feature_ai_brief_disabled"}
    config.require("TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY")
    chat_id = config.telegram_chat_id
    # Owner decision (2026-11-05): the AI Brief's YouTube video never had
    # capacity problems. Keep the original plain client: no shared TPM
    # budget, no 70 s heavy-call gap, no spare-model fallback.
    client = GeminiClient(config.Gemini_api_key, config.Gemini_model)
    telegram = TelegramClient(config.telegram_bot_token)
    try:
        entries = fetch_worldofai_entries()
    except NewsSourceError as exc:
        reason = sanitize(str(exc))[:300]
        store.event("news_skipped", reason="source_unavailable", error=reason)
        store.save()
        alert_owner(config, "AI Brief: источник недоступен", reason)
        logger.warning("AI Brief source unavailable; skipping: %s", reason)
        return {"ok": True, "published": False, "reason": "source_unavailable", "error": reason}

    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    seen_video_ids: set[str] = set()
    recent_topic_keys: set[str] = set()
    topics = store.data.get("news_topics") or []
    for item in topics:
        if item.get("video_id"):
            seen_video_ids.add(str(item["video_id"]))
        published = _parse_iso(item.get("published_at"))
        if item.get("topic_key") and published and published >= cutoff:
            recent_topic_keys.add(str(item["topic_key"]))

    # A refresh deliberately bypasses deduplication; freshness still applies.
    entry, skip_reason = select_latest_news_entry(
        entries,
        set() if refresh else seen_video_ids,
        set() if refresh else recent_topic_keys,
    )
    if entry is None:
        store.event("news_skipped", reason=skip_reason, entries=len(entries))
        store.save()
        return {"ok": True, "published": False, "reason": skip_reason, "entries": len(entries)}

    existing: dict[str, Any] | None = None
    if refresh:
        existing = next(
            (
                item
                for item in topics
                if isinstance(item, dict)
                and str(item.get("video_id")) == str(entry.get("video_id"))
            ),
            None,
        )

    primary: dict[str, str] = {}
    for url in extract_primary_urls(entry):
        primary = fetch_primary_text(url) or {}
        if primary:
            break
    try:
        brief = synthesize_brief(entry, primary, client, use_video=True)
        video_input = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gemini YouTube input failed; retrying text-only: %s", type(exc).__name__)
        brief = synthesize_brief(entry, primary, client, use_video=False)
        video_input = False
    if not brief.get("eligible"):
        reason = str(brief.get("rejection_reason") or "gemini_ineligible")[:80]
        store.event("news_skipped", reason=reason, video_id=entry.get("video_id"))
        store.save()
        return {"ok": True, "published": False, "reason": reason}

    names = brief_model_names(brief)
    stories = brief_stories(brief)
    # The exact failure the owner reported on 04.11.2026: a 33-minute
    # round-up compressed into one policy story. A brief with a single
    # story is not a digest; do not publish it at all.
    if len(stories) < 2:
        store.event("news_skipped", reason="not_a_digest", video_id=entry.get("video_id"))
        store.save()
        return {"ok": True, "published": False, "reason": "not_a_digest", "stories": len(stories)}

    topic_key = re.sub(r"[^a-z0-9]+", "-", str(brief.get("topic_key") or "").lower()).strip("-")
    # No YouTube link preview: it would attribute the source, which the
    # owner explicitly does not want. Only our own card is ever attached.
    preview_url = ""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            # The card uses the HOOK, not the dry summary, and knows how
            # many stories it represents (owner feedback 04.11.2026).
            standfirst = card_hook_text(brief)
            card_path = render_ai_brief_card(
                str(brief.get("headline") or "AI model watch"),
                standfirst,
                " · ".join(names[:2]),
                Path(tmp) / "ai-brief.png",
                stories=len(stories),
            )
            uploaded, _hosted_by = _host_local_image(
                config, card_path.read_bytes(), filename=card_path.name
            )
            if uploaded:
                preview_url = uploaded
    except Exception as exc:  # noqa: BLE001
        logger.warning("AI Brief Glass Aurora card failed; sending text only: %s", type(exc).__name__)

    text = format_ai_brief(brief)
    edited = False
    message_id: Any = None
    old_message_id = int((existing or {}).get("telegram_message_id") or 0)
    if refresh and old_message_id:
        try:
            result = telegram.edit_text(chat_id, old_message_id, text, preview_url=preview_url)
            message_id = int(result.get("message_id") or old_message_id)
            edited = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("AI Brief in-place edit failed; post left intact: %s", type(exc).__name__)
            return {
                "ok": False,
                "published": False,
                "reason": "edit_failed",
                "error": type(exc).__name__,
            }
    else:
        sent = telegram.send_text(chat_id, text, preview_url=preview_url)
        message_id = sent.get("message_id")

    record = {
        "video_id": entry.get("video_id"),
        "topic_key": topic_key,
        "headline": brief.get("headline") or "",
        "official_url": brief.get("official_url") or "",
        "video_url": entry.get("url") or "",
        "published_at": iso_now(),
        "telegram_message_id": message_id,
        "image_url": preview_url,
        "Gemini_video_input": video_input,
        "stories": len(stories),
        "models": names[:12],
        "statuses": [
            str(item.get("status") or "") for item in (brief.get("stories") or []) if isinstance(item, dict)
        ][:12],
    }
    if existing is not None:
        existing.update(record)
    else:
        store.data.setdefault("news_topics", []).append(record)
    store.event(
        "ai_brief_republished" if edited else "ai_brief_published",
        video_id=entry.get("video_id"),
        topic=topic_key,
        official_url=record["official_url"],
        Gemini_video_input=video_input,
        stories=len(stories),
        models=len(names),
    )
    store.save()
    return {
        "ok": True,
        "published": True,
        "edited": edited,
        "headline": record["headline"],
        "telegram_message_id": message_id,
        "Gemini_video_input": video_input,
        "sourced": bool(brief.get("sourced")),
        "video_id": record["video_id"],
        "stories": len(stories),
        "models": len(names),
    }


def digest_command(config: Config, store: StateStore) -> dict[str, Any]:
    config.require("TELEGRAM_BOT_TOKEN")
    chat_id = config.telegram_chat_id
    records = sorted(_recent_records(store, days=7), key=lambda item: float(item.get("score") or 0), reverse=True)[:5]
    if not records:
        return {"ok": True, "published": False, "reason": "no_records"}
    telegram = TelegramClient(config.telegram_bot_token)
    image_url = ""
    with tempfile.TemporaryDirectory() as tmp:
        path = render_digest_card(records, Path(tmp) / "sunday-digest.png")
        image_url, _hosted_by = _host_local_image(
            config, path.read_bytes(), filename=path.name
        )
    lines = ["📰 <b>The Open Source Daily — Top 5 of the Week</b>", ""]
    for index, record in enumerate(records, 1):
        name = html.escape(str(record.get("name") or record.get("full_name") or "Project"))
        link = public_telegram_link(record) or str(record.get("url") or "")
        title = f'<a href="{html.escape(link, quote=True)}">{name}</a>' if link.startswith("https://") else name
        generated = record.get("generated") or {}
        hook = html.escape(str(generated.get("hook") or record.get("description") or "")[:220])
        try:
            stars = f" ⭐ {int(record.get('stars') or 0):,}".replace(",", " ")
        except (TypeError, ValueError):
            stars = ""
        lines.append(f"{index}. <b>{title}</b>")
        detail = " — ".join(part for part in [stars.strip(), hook] if part)
        if detail:
            lines.append(f"   {detail}")
        lines.append("")
    lines += [
        "──────────────",
        "↗️ <b>Forward this Top 5</b> to a teammate who should see it.",
        "🗳️ Vote in the poll below: which one deserves Project of the Week?",
        '📣 <a href="https://t.me/GitHub_Open_Source"><b>@GitHub_Open_Source</b></a> — daily open-source picks',
    ]
    sent = telegram.send_text(chat_id, "\n".join(lines), preview_url=image_url)
    poll = None
    poll_labels = {
        str(record.get("name") or record.get("full_name") or "Project"): str(record.get("full_name") or "")
        for record in records
    }
    try:
        poll = polls.send_week_poll(
            telegram,
            chat_id,
            "Which project should win Project of the Week?",
            list(poll_labels),
            hours=polls.DEFAULT_WEEK_POLL_HOURS,
        )
        store.data["active_poll"] = {
            # state.json is public: never store the private test-channel id.
            # close_poll_command resolves the real chat from the config.
            "chat_id": chat_id if config.is_main_target else "test",
            "message_id": (poll or {}).get("message_id"),
            "question": "Which project should win Project of the Week?",
            "repos": poll_labels,
            "opened_at": iso_now(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Digest poll failed: %s", sanitize(str(exc))[:200])
    store.event(
        "digest_published",
        telegram_message_id=sent.get("message_id"),
        poll_message_id=(poll or {}).get("message_id"),
        repos=[record.get("full_name") for record in records],
    )
    store.save()
    return {"ok": True, "published": True, "telegram_message_id": sent.get("message_id"), "count": len(records)}


def _channel_handle(config: Config) -> str:
    """Public handle usable by the embed widget, or ``""``.

    The widget can only read *public* channels. A private test channel has
    ids that collide with the main channel's post numbers, so measuring a
    test-channel post against the main channel would attach somebody else's
    views to our record. Refusing to measure is the only safe answer.
    """
    if not config.is_main_target:
        return ""
    raw = str(config.telegram_main_channel_id or "").strip().lstrip("@")
    if raw.startswith("-") or raw.isdigit():
        return "GitHub_Open_Source"
    return raw


def metrics_command(config: Config, store: StateStore, *, limit: int = 12) -> dict[str, Any]:
    """Read views for recent posts from the public widget (no credentials).

    Telegram gives nobody a client-side session here, so this is the only
    free way to know whether a card worked. Runs are idempotent.
    """
    channel = _channel_handle(config)
    if not channel:
        store.event("metrics_skipped", reason="channel_not_public")
        store.save()
        return {
            "ok": True,
            "checked": 0,
            "updated": 0,
            "channel": "",
            "reason": "channel_not_public",
            "note": "Views are only readable for public channels; the test channel stays unmeasured.",
        }
    # Project picks only. AI Briefs live in news_topics and are deliberately
    # excluded: a brief is a news item with no project to rank, so a high or
    # low view count there says nothing about which repositories to pick.
    # Mixing them in would drag the value model towards whatever news
    # happened to be loud that week.
    records = sorted(
        (r for r in store.published_records() if r.get("telegram_message_id")),
        key=lambda r: str(r.get("published_at") or ""),
        reverse=True,
    )[:limit]
    updated = 0
    for record in records:
        stats = insights.fetch_post_stats(channel, int(record["telegram_message_id"]))
        if not stats.get("ok"):
            continue
        history = record.setdefault("views_history", [])
        history.append({"at": stats["fetched_at"], "views": stats["views"], "reactions": stats.get("reactions", 0)})
        del history[:-8]  # keep the last eight samples per post
        record["views"] = stats["views"]
        record["reactions"] = stats.get("reactions", 0)
        record["views_age_hours"] = round(insights.age_hours(record.get("published_at")), 1)
        record["views_at_24h"] = round(
            insights.views_at_reference(stats["views"], record["views_age_hours"])
        )
        record["value_score"] = insights.value_score(record, median_views=insights.median_views(store.published_records()))
        updated += 1
    store.event("metrics_collected", posts=len(records), updated=updated, channel=channel)
    store.save()
    return {"ok": True, "checked": len(records), "updated": updated, "channel": channel}


def close_poll_command(config: Config, store: StateStore) -> dict[str, Any]:
    """Close this week's poll and store the tally — the raw taste signal."""
    config.require("TELEGRAM_BOT_TOKEN")
    poll = store.data.get("active_poll") or {}
    if not poll.get("message_id"):
        return {"ok": True, "closed": False, "reason": "no_active_poll"}
    telegram = TelegramClient(config.telegram_bot_token)
    try:
        poll_chat_id = poll.get("chat_id")
        if not poll_chat_id or poll_chat_id == "test":
            poll_chat_id = config.telegram_chat_id
        tally = polls.close_poll(telegram, poll_chat_id, int(poll["message_id"]))
    except Exception as exc:  # noqa: BLE001
        logger.warning("stopPoll failed: %s", sanitize(str(exc))[:200])
        return {"ok": False, "closed": False, "reason": "stop_poll_failed"}
    entries = []
    for option in tally.get("options") or []:
        entries.append(
            {
                "label": option["text"],
                "votes": option["votes"],
                "share": option["share"],
                "repo": (poll.get("repos") or {}).get(option["text"], ""),
            }
        )
    store.data.setdefault("poll_history", []).append(
        {
            "closed_at": iso_now(),
            "question": tally.get("question") or poll.get("question") or "",
            "total_voters": tally.get("total_voters") or 0,
            "options": entries,
        }
    )
    winner = polls.winner({"options": entries})
    store.data["active_poll"] = {}
    store.event("poll_closed", total_voters=tally.get("total_voters"), winner=(winner or {}).get("label"))
    store.save()
    return {"ok": True, "closed": True, "winner": winner, "options": entries, "total_voters": tally.get("total_voters")}


def learn_command(config: Config, store: StateStore) -> dict[str, Any]:
    """Turn measured performance into deterministic ranking multipliers.

    Deliberately simple and reviewable: for each language and topic we
    compare the median value score of posts that had it against the overall
    median, clamp the multiplier to 0.85..1.15, and write it to state so
    the next selection can use it. No model, no hidden weights.
    """
    records = [r for r in store.published_records() if r.get("views") is not None]
    # 5 posts is not a sample: with the observed spread (sd≈8 on a median of
    # 49) five posts give a ±14% confidence interval, wider than any effect
    # we could act on. The floor is 20 measured posts.
    if len(records) < insights.MIN_MEASURED_POSTS:
        return {
            "ok": True,
            "learned": False,
            "reason": "not_enough_samples",
            "samples": len(records),
            "needed": insights.MIN_MEASURED_POSTS,
        }
    overall = insights.median_views(records)
    for record in records:
        # Recompute from views so `learn` works even if the daily metrics run
        # missed a day; age normalisation is applied inside the score.
        record["value_score"] = insights.value_score(record, median_views=overall)
    scores = sorted(float(r["value_score"]) for r in records)
    median_score = scores[len(scores) // 2]

    def multiplier(groups: list[str]) -> float:
        if not groups:
            return 1.0
        selected = [float(r["value_score"]) for r in records if set(groups) & set(r.get("_groups") or [])]
        # A group needs its own sample, not three posts that happen to share
        # a topic. Below the floor the honest multiplier is 1.0 (no opinion).
        if len(selected) < insights.MIN_GROUP_POSTS:
            return 1.0
        selected.sort()
        local = selected[len(selected) // 2]
        return round(max(0.85, min(1.15, 1.0 + (local - median_score) / max(median_score, 1.0) * 0.5)), 3)

    for record in records:
        record["_groups"] = [str(record.get("language") or "").lower()] + [
            str(t).lower() for t in (record.get("topics") or [])
        ]
    languages = {lang for r in records for lang in [str(r.get("language") or "").lower()] if lang}
    topics = {str(t).lower() for r in records for t in (r.get("topics") or [])}
    model = {
        "updated_at": iso_now(),
        "samples": len(records),
        "median_score": round(median_score, 2),
        "language_multipliers": {lang: multiplier([lang]) for lang in sorted(languages)},
        "topic_multipliers": {topic: multiplier([topic]) for topic in sorted(topics)},
    }
    for record in records:
        record.pop("_groups", None)
    store.data["value_model"] = model
    store.event("value_model_updated", samples=model["samples"], median=model["median_score"])
    store.save()
    return {"ok": True, "learned": True, **model}


def facebook_handoff_command(config: Config, store: StateStore, *, limit: int = 1) -> dict[str, Any]:
    """Queue Facebook posts for make.com by appending rows to a spreadsheet.

    This is the bridge to the chain that already works: Google service
    account → Sheet → make.com → Page. v6 supplies the rows; make.com keeps
    owning the publish, retries and scheduling.

    Rows are only appended when ``FEATURE_SHEETS_HANDOFF`` is on and both
    Sheets settings exist. Everything is logged, and a Sheets outage never
    stops the Telegram pipeline.
    """
    from .content_adapters import build_Facebook_text, facebook_media_url
    from .sheets import append_rows, row_for

    if not config.feature_sheets_handoff:
        return {"ok": True, "queued": 0, "reason": "feature_disabled"}

    # The handoff takes the best post of the day, not the oldest. The old
    # implementation sorted by published_at ascending and queued the first
    # row, so the best project of the day could be skipped for days.
    today = datetime.now(timezone.utc).date().isoformat()
    candidates: list[tuple[float, dict[str, Any]]] = []
    for record in store.published_records():
        if (record.get("crossposts") or {}).get("facebook_sheet", {}).get("status") == "queued":
            continue
        published_at = _parse_iso(record.get("published_at"))
        if not published_at or published_at.date().isoformat() != today:
            continue
        candidates.append((float(record.get("score") or 0), record))
    if not candidates:
        return {"ok": True, "queued": 0, "reason": "nothing_pending"}
    candidates.sort(key=lambda item: item[0], reverse=True)
    selected = [record for _, record in candidates[:limit]]
    rows = []
    for record in selected:
        generated = record.get("generated") or {}
        text = build_Facebook_text(record, generated)
        rows.append(row_for(record, text, media_url=facebook_media_url(record)))
    result = append_rows(
        config.sheets_spreadsheet_id,
        rows,
        credentials_json=config.sheets_credentials_json,
        range_name=config.sheets_range,
    )
    if not result.get("ok"):
        store.event("facebook_handoff_failed", error=result.get("error"), rows=len(rows))
        store.save()
        return {"ok": False, "queued": 0, "error": result.get("error")}

    for record in selected:
        crossposts = record.setdefault("crossposts", {})
        crossposts["facebook_sheet"] = {"status": "queued", "at": iso_now(), "via": "make.com"}
    store.event("facebook_handoff_queued", rows=len(rows), range=result.get("range", ""))
    store.save()
    return {"ok": True, "queued": len(rows), "range": result.get("range", ""), "projects": [r.get("full_name") for r in selected]}


def report_command(config: Config, store: StateStore) -> dict[str, Any]:
    records = _recent_records(store, days=7)
    top = sorted(records, key=lambda item: float(item.get("score") or 0), reverse=True)[:3]
    languages = Counter(str(record.get("language") or "Unknown") for record in records)
    sources = Counter(source for record in records for source in (record.get("sources") or []))
    lines = [
        "<b>GitHub Open Source v6 — weekly report</b>",
        "",
        f"Projects published (7d): <b>{len(records)}</b>",
        f"Queued now: <b>{len(store.data.get('queue') or [])}</b>",
        f"Dead letters: <b>{len(store.data.get('dead_letters') or [])}</b>",
        f"AI Briefs recorded: <b>{len(store.data.get('news_topics') or [])}</b>",
    ]
    if top:
        lines += ["", "<b>Top by editorial score</b>"]
        for index, record in enumerate(top, 1):
            lines.append(
                f"{index}. {html.escape(str(record.get('name') or record.get('full_name') or ''))} — {float(record.get('score') or 0):.1f}"
            )
    if languages:
        lines += ["", "<b>Languages</b>: " + ", ".join(f"{k} {v}" for k, v in languages.most_common(6))]
    if sources:
        lines.append("<b>Sources</b>: " + ", ".join(f"{k} {v}" for k, v in sources.most_common(6)))
    text = "\n".join(lines)
    sent = None
    if config.telegram_bot_token and config.telegram_owner_chat_id:
        sent = TelegramClient(config.telegram_bot_token).send_owner(config.telegram_owner_chat_id, text)
    store.event("weekly_report", sent=bool(sent), projects_7d=len(records))
    store.save()
    return {"ok": True, "sent": bool(sent), "projects_7d": len(records), "text": text}


_SPAM_RE = re.compile(
    r"(crypto giveaway|airdrop claim|double your|investment profit|casino|betting bonus|"
    r"earn \$|work from home.*\$|telegram premium free|seed phrase)",
    re.I,
)


def _moderation_ignored_sender(sender: dict[str, Any]) -> bool:
    """Service senders are never moderated.

    Messages forwarded from the channel arrive from Telegram's service user
    777000 with ``is_automatic_forward`` set. They are not a bot in the
    ``is_bot`` sense, so the old check deleted the channel's own posts.
    """
    return bool(
        sender.get("is_bot")
        or sender.get("is_automatic_forward")
        or sender.get("id") == 777000
    )


def moderation_command(config: Config, store: StateStore) -> dict[str, Any]:
    config.require("TELEGRAM_BOT_TOKEN")
    telegram = TelegramClient(config.telegram_bot_token)
    last_offset = int(store.data.setdefault("last_runs", {}).get("telegram_update_offset") or 0)
    offset = last_offset + 1 if last_offset else None
    deleted = 0
    seen = 0
    max_update_id = last_offset
    # getUpdates returns at most 100 updates. A busy discussion group can
    # produce more than that between two runs; loop until the queue is empty
    # (bounded) instead of silently dropping the tail.
    for _ in range(20):
        payload: dict[str, Any] = {"timeout": 0, "limit": 100}
        if offset:
            payload["offset"] = offset
        updates = list(telegram.api("getUpdates", payload) or [])
        if not updates:
            break
        seen += len(updates)
        for update in updates:
            update_id = int(update.get("update_id") or 0)
            max_update_id = max(max_update_id, update_id)
            message = update.get("message") or update.get("edited_message") or {}
            if message.get("is_automatic_forward"):
                continue
            chat = message.get("chat") or {}
            if chat.get("type") not in {"group", "supergroup"}:
                continue
            sender = message.get("from") or {}
            if _moderation_ignored_sender(sender):
                continue
            text = str(message.get("text") or message.get("caption") or "")
            urls = re.findall(r"https?://|t\.me/", text, flags=re.I)
            suspicious = bool(_SPAM_RE.search(text)) or len(urls) >= 3
            if not suspicious:
                continue
            try:
                member = telegram.api(
                    "getChatMember",
                    {"chat_id": chat.get("id"), "user_id": sender.get("id")},
                )
                if (member or {}).get("status") in {"creator", "administrator"}:
                    continue
                telegram.delete_message(int(chat["id"]), int(message["message_id"]))
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                logger.info("Moderation delete skipped: %s", sanitize(str(exc))[:160])
        batch_max = max(int(update.get("update_id") or 0) for update in updates)
        max_update_id = max(max_update_id, batch_max)
        offset = batch_max + 1
        if len(updates) < 100:
            break
    store.data.setdefault("last_runs", {})["telegram_update_offset"] = max_update_id
    store.event("moderation_completed", updates=seen, deleted=deleted)
    store.save()
    return {"ok": True, "updates": seen, "deleted": deleted}


def cleanup_test_posts(
    config: Config,
    store: StateStore,
    *,
    confirm: bool = False,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete the Telegram test posts created during the publication test.

    Only the private test channel is touched. The state projection is kept as
    the audit trail; this command removes the messages, not the history.
    """
    config.require("TELEGRAM_BOT_TOKEN")
    if config.telegram_test_channel_id is None:
        return {"ok": False, "reason": "TELEGRAM_TEST_CHANNEL_ID is not set"}
    records = [
        record
        for record in store.published_records()
        if record.get("telegram_message_id")
    ]
    if dry_run or not confirm:
        return {
            "ok": True,
            "dry_run": True,
            "would_delete": len(records),
            "message_ids": [r.get("telegram_message_id") for r in records],
        }
    telegram = TelegramClient(config.telegram_bot_token)
    deleted: list[int] = []
    for record in records:
        message_id = int(record.get("telegram_message_id") or 0)
        try:
            telegram.delete_message(config.telegram_test_channel_id, message_id)
            deleted.append(message_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("cleanup delete failed: %s", sanitize(str(exc))[:200])
    store.event("test_posts_cleaned", deleted=len(deleted), requested=len(records))
    store.save()
    return {"ok": True, "deleted": len(deleted), "message_ids": deleted}
