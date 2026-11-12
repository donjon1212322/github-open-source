"""Owner decisions from docs/OWNER_RULES.md, pinned as executable invariants.

A green suite once allowed MAX_PROJECT_POSTS_PER_DAY to become 4, the dedup
cooldown to become 30 days, events rotation to drop to 200 lines and
Telegraph to be off by default without a single failure. Statement coverage
cannot encode intent; these tests do.
"""
from __future__ import annotations

import inspect
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ghos_v6 import cards, dedup, formatter, pipeline, telegram_payload
from ghos_v6.Gemini import SURFACE_KEYS, SURFACES_SCHEMA, GeminiClient
from ghos_v6.config import Config
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]
OWNER_RULES = (REPO / "docs" / "OWNER_RULES.md").read_text(encoding="utf-8")


def _ts(days_ago: int = 0) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return stamp.isoformat().replace("+00:00", "Z")


def _publish_crons_and_slots() -> dict[str, str]:
    src = (REPO / "src" / "ghos_v6" / "cli.py").read_text(encoding="utf-8")
    return dict(
        re.findall(
            r'"([^"]+)"\s*:\s*lambda:\s*publish_slot\(config, store, "([^"]+)"\)',
            src,
        )
    )


# --- three posts a day ----------------------------------------------------

def test_three_posts_per_day_is_pinned_in_code_docs_and_schedule():
    assert pipeline.MAX_PROJECT_POSTS_PER_DAY == 3
    assert "3 поста в день" in OWNER_RULES
    slots = _publish_crons_and_slots()
    assert len(slots) == 3, "exactly three project slots may exist"
    assert sorted(slots.values()) == ["us_evening", "us_midday", "us_morning"]
    workflow = (REPO / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    for cron in slots:
        assert f'cron: "{cron}"' in workflow


def test_fourth_post_in_one_day_is_refused_before_any_network_call(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a fourth post must never reach a network client")

    for name in ("collect_command", "TelegramClient", "GeminiClient", "GitHubApi"):
        if hasattr(pipeline, name):
            monkeypatch.setattr(pipeline, name, forbidden)

    store = StateStore(tmp_path)
    store.data["published"] = {
        f"owner/repo{i}": {
            "full_name": f"owner/repo{i}",
            "published_at": _ts(),
            "score": 1.0,
        }
        for i in range(3)
    }
    config = Config(telegram_bot_token="dummy-token", telegram_test_channel_id=-1009999999999)
    result = pipeline.publish_slot(config, store, "manual")
    assert result["published"] is False
    assert result["reason"] == "daily_limit_reached"
    assert result["published_today"] == 3
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        e["type"] == "publish_skipped" and e.get("reason") == "daily_limit_reached"
        for e in events
    )


# --- anti-duplication -----------------------------------------------------

def test_dedup_cooldown_is_ninety_days_and_blocks_an_early_revisit():
    assert dedup.COOLDOWN_DAYS == 90
    record = {"published_at": _ts(60), "stars": 100}
    candidate = {"full_name": "owner/repo", "stars": 260}
    assert dedup.milestone(record, candidate) == "", "60 days is inside the 90-day cooldown"
    assert dedup.check(candidate, {"published": {"owner/repo": record}})["verdict"] == "duplicate"


def test_similarity_constants_bigrams_and_short_text_guard():
    assert dedup.CONTENT_SIMILARITY == 0.55
    assert dedup.MILESTONE_STAR_GROWTH == 1.5
    assert dedup.README_FINGERPRINT_CHARS == 1200
    assert len(dedup._shingles("alpha beta gamma delta")) == 3, "word bigrams, fixed size"
    # Two shingles must not be enough for a containment verdict: the guard is 3.
    assert dedup.similarity("alpha beta gamma", "alpha beta gamma delta") == 0.0
    assert dedup.similarity("alpha beta gamma delta", "alpha beta gamma delta") == 1.0


# --- long-term state ------------------------------------------------------

def test_events_rotation_keeps_two_thousand_lines(tmp_path):
    store = StateStore(tmp_path)
    assert store.EVENTS_KEEP_LINES == 2000
    assert store.EVENTS_MAX_LINES == 5000
    (tmp_path / "events.jsonl").write_text('{"n":1}\n' * 5200, encoding="utf-8")
    store._rotate_events()
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2000


def test_compaction_constants_ttl_and_audit_event(tmp_path):
    store = StateStore(tmp_path)
    assert (store.SEEN_REPOS_TTL_DAYS, store.PUBLISHED_FULL_RETENTION_DAYS) == (180, 90)
    assert (store.HISTORY_MAX_ITEMS, store.LIST_MAX_ITEMS) == (5, 500)
    store.data["seen_repos"] = {
        "owner/recent": {"last_seen_at": _ts(100), "last_decision": "queued"},
        "owner/old": {"last_seen_at": _ts(400), "last_decision": "rejected:old"},
    }
    store.data["published"] = {
        "owner/old": {"published_at": _ts(200), "generated": {"article": "x" * 100}}
    }
    store.data["dead_letters"] = [{"i": i} for i in range(600)]
    store.save()
    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert "owner/recent" in saved["seen_repos"], "100 days survives the 180-day TTL"
    assert "owner/old" not in saved["seen_repos"]
    assert "owner/old" in saved["published"], "published keys are never deleted"
    assert len(saved["dead_letters"]) == 500
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    compacted = [e for e in events if e["type"] == "state_compacted"]
    assert len(compacted) == 1, "every compaction must leave an audit trail"
    assert compacted[0]["changed"] >= 3


# --- defaults encode the owner's decisions --------------------------------

def test_defaults_encode_owner_decisions(monkeypatch):
    for name in (
        "APP_ENV", "PUBLISH_TARGET", "GEMINI_MODEL", "FEATURE_TELEGRAPH",
        "FEATURE_DEVTO", "FEATURE_BLUESKY",
        "FEATURE_AI_BRIEF", "FEATURE_SHEETS_HANDOFF",
    ):
        monkeypatch.delenv(name, raising=False)
    cfg = Config.from_env()
    assert cfg.app_env == "test"
    assert cfg.publish_target == "test"
    assert cfg.feature_telegraph is True, "Telegraph on every post, without exception"
    assert Config().feature_telegraph is True, "the dataclass default must match from_env"
    assert not any(
        getattr(cfg, flag)
        for flag in (
            "feature_devto", "feature_bluesky",
            "feature_ai_brief", "feature_sheets_handoff",
        )
    ), "start with every optional flag off"
    assert cfg.Gemini_model == "gemini-3.5-flash-lite"
    assert Config().Gemini_model == "gemini-3.5-flash-lite"
    assert GeminiClient("dummy").model == "gemini-3.5-flash-lite"
    doctor_src = (REPO / "src" / "ghos_v6" / "doctor.py").read_text(encoding="utf-8")
    assert "model_available" in doctor_src, "a retired model must fail the health check"
    env_example = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "FEATURE_TELEGRAPH=true" in env_example
    assert "FEATURE_SHEETS_HANDOFF=false" in env_example
    assert "APP_ENV=test" in env_example and "PUBLISH_TARGET=test" in env_example
    assert not hasattr(cfg, "feature_facebook"), "no direct Facebook switch"
    assert not hasattr(cfg, "Facebook_page_access_token"), "no Page token in v6"


# --- format, links and surfaces -------------------------------------------

def test_long_text_post_with_large_preview_and_direct_links():
    assert (cards.W, cards.H) == (1200, 630), "square and portrait are rejected"
    assert formatter.MAX_VISIBLE_UTF16 == 4000
    payload = telegram_payload.build_send_message_payload(
        -1009999999999,
        "<b>A long story</b>",
        preview_url="https://i.ibb.co/abcdef/card.png",
        buttons=telegram_payload.build_buttons(
            github_url="https://github.com/owner/repo",
            telegraph_url="https://telegra.ph/owner-repo-11-03",
            homepage_url="http://bit.ly/tracked-link",
        ),
    )
    assert "photo" not in payload, "photo posts are rejected: 1024-char caption"
    assert payload["parse_mode"] == "HTML"
    options = payload["link_preview_options"]
    assert options["prefer_large_media"] is True
    assert options["show_above_text"] is True
    urls = [
        button["url"]
        for row in payload["reply_markup"]["inline_keyboard"]
        for button in row
    ]
    assert "https://github.com/owner/repo" in urls
    assert all(url.startswith("https://") for url in urls)
    assert not any("bit.ly" in url or "utm_" in url for url in urls)

    post = formatter.format_project_post(
        {
            "name": "demo",
            "full_name": "owner/demo",
            "description": "A small terminal tool for reading structured logs",
            "url": "https://github.com/owner/demo",
            "stars": 120,
            "topics": ["cli"],
        }
    )
    assert formatter.utf16_len(formatter.visible_text(post)) <= 4000
    assert "utm_" not in post and "bit.ly" not in post, "no tracking or link wrappers"


def test_telegraph_page_is_created_for_every_post():
    assert "Telegraph — на каждый пост" in OWNER_RULES
    src = inspect.getsource(pipeline.publish_slot)
    assert "config.feature_telegraph" in src
    assert "telegraph.create_page" in src


def test_crossposts_use_one_winner_per_day_and_flags_fail_closed(tmp_path):
    assert "Только лучший пост суток" in OWNER_RULES
    src = inspect.getsource(pipeline.process_crossposts)
    assert "best_of_day" in src
    assert "return record_key" in src, "the day's slot is fixed after one post"
    assert "winners = {platform: winner" in src
    result = pipeline.process_crossposts(Config(), StateStore(tmp_path))
    assert result["ok"] is True
    assert result["published"] == 0, "all flags off must not touch a network client"
    handoff = pipeline.facebook_handoff_command(Config(), StateStore(tmp_path))
    assert handoff["queued"] == 0


def test_facebook_goes_through_make_com_sheet_not_direct_page_api():
    assert "через make.com" in OWNER_RULES
    src = inspect.getsource(pipeline.facebook_handoff_command)
    assert "append_rows" in src and "row_for" in src
    assert "FacebookClient" not in src, "v6 must never publish to the Page directly"
    assert '"via": "make.com"' in src
    crossposts = inspect.getsource(pipeline.process_crossposts)
    assert "FacebookClient" not in crossposts
    assert "feature_facebook" not in crossposts


def test_seo_and_telegraph_are_steered_to_different_queries():
    assert "Английский язык, под Google" in OWNER_RULES
    assert "seo_query" in SURFACE_KEYS
    assert "telegraph_angle" in SURFACE_KEYS
    schema = json.dumps(SURFACES_SCHEMA)
    assert "seo_query" in schema and "telegraph_angle" in schema
    prompt = (REPO / "src" / "ghos_v6" / "Gemini.py").read_text(encoding="utf-8")
    assert "do not cover the same ground" in prompt
    assert "dev.to and Telegraph" in prompt
