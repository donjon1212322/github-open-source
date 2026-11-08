"""Owner 08.11, second duplicate incident: post 3575 printed the same
Anthropic rumour twice, and 3568 had already printed it the day before.

The first fix compared literal strings, so a re-post with one extra word
("multiplayer") produced a different key. These regressions lock in the
general fix: semantic de-duplication, clean channel text and cross-day memory.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ghos_v6 import pipeline
from ghos_v6.config import Config
from ghos_v6.news import format_ai_brief_digest
from ghos_v6.rumours import (
    clean_rumour_text,
    dedupe_claims,
    filter_recent_rumours,
    remember_rumours,
)
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]

# Verbatim from state.brief of Telegram post 3575 (08.11 19:19 UTC).
LIVE_CLAIMS = [
    "Anthropic is working on a collaborative multiplayer mode for Claude Code Projects.",
    "Tibo's tweets hinted at an upcoming Codex limits reset.",
    "Anthropic is working on a collaborative mode for Claude Code Projects. "
    "> Recent activity on the Projects page summarises the latest threads. "
    "> \"We don't have multiplayer yet,\" shared Lydia Hallie from Anthropic "
    "yesterday. Yet 👀 Anthropic is working on a collaborative mode for "
    "Claude Code Projects. > Recent activity on the Projects page summarises "
    "the latest threads. > \"We don't have multiplayer ye",
]
YESTERDAY_CLAIM = "Anthropic is working on a collaborative mode for Claude Code Projects."


class _Store:
    def __init__(self):
        self.data = {}


def _brief(claims: list[str]) -> dict:
    return {
        "headline": "Three new models and a 65B open weights release dropped on Tuesday.",
        "narrative_hook": "Three model releases showed up in multiple channels this week, "
                          "but the thing I can't stop thinking about is a 65-billion "
                          "parameter model trained by a",
        "hook": "Three model releases showed up in multiple channels this week.",
        "discussion_question": "How are you currently handling persistent memory?",
        "models": ["Ripple 65B Preview", "Inkling 2 Small"],
        "stories": [
            {"title": "Ripple 65B Preview Released",
             "bullets": ["Ricursive released Ripple 65B Preview, a dense 65-billion "
                         "parameter model trained from scratch on 12 trillion tokens."],
             "status": "official_release", "basis": "the model release is live on OpenRouter"},
            {"title": "Thinking Machines Launches Inkling 2 Small",
             "bullets": ["Thinking Machines released Inkling 2 Small with 17 billion "
                         "active parameters out of 594 billion."],
             "status": "official_release", "basis": "the official announcement is up"},
        ],
        "unconfirmed_claims": list(claims),
    }


def test_live_3575_claims_collapse_to_two_distinct_rumours():
    claims = dedupe_claims(LIVE_CLAIMS)
    assert len(claims) == 2, claims
    assert sum("Anthropic" in c for c in claims) == 1
    assert any("Tibo" in c for c in claims)
    assert all(">" not in c and "👀" not in c and "ye" != c[-2:] for c in claims)


def test_raw_repost_chain_is_reduced_to_one_clean_sentence():
    cleaned = clean_rumour_text(LIVE_CLAIMS[2])
    assert cleaned == "Anthropic is working on a collaborative mode for Claude Code Projects."
    assert "Recent activity" not in cleaned
    assert "Lydia" not in cleaned


def test_digest_renders_the_anthropic_rumour_only_once():
    text = format_ai_brief_digest(_brief(LIVE_CLAIMS))
    assert text.count("Anthropic is working on a collaborative") == 1
    assert "Tibo" in text
    assert "&gt; Recent activity" not in text
    assert "Rumours we're keeping an eye on" in text


def test_different_short_rumours_are_not_collapsed():
    claims = dedupe_claims([
        "Tibo's tweets hinted at an upcoming Codex limits reset.",
        "US AI market impacted by recent election results regarding regulations.",
    ])
    assert len(claims) == 2


def test_previous_day_rumour_cannot_return_even_when_rewritten(monkeypatch):
    store = _Store()
    yesterday = datetime(2026, 11, 7, 19, 19, tzinfo=timezone.utc)
    today = datetime(2026, 11, 8, 19, 19, tzinfo=timezone.utc)
    remember_rumours(store, [YESTERDAY_CLAIM], now=yesterday)
    # "multiplayer" is a new word, but it is the same story.
    kept = filter_recent_rumours(store, LIVE_CLAIMS[:2], now=today)
    assert len(kept) == 1
    assert "Tibo" in kept[0]
    assert "Anthropic" not in kept[0]


def test_same_day_refresh_does_not_empty_the_rumour_block():
    store = _Store()
    now = datetime(2026, 11, 8, 19, 19, tzinfo=timezone.utc)
    remember_rumours(store, LIVE_CLAIMS[:2], now=now)
    kept = filter_recent_rumours(store, LIVE_CLAIMS[:2], now=now)
    assert len(kept) == 2, "today's own keys must not filter today's refresh"


def test_memory_expires_after_three_days_and_stays_bounded():
    store = _Store()
    old = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    now = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    remember_rumours(store, [YESTERDAY_CLAIM], now=old)
    kept = filter_recent_rumours(store, [YESTERDAY_CLAIM], now=now)
    assert kept, "a week-old rumour may legitimately return"
    assert "rumour_memory" in store.data


def test_replace_rumour_block_keeps_everything_else_byte_identical():
    before = format_ai_brief_digest(_brief(LIVE_CLAIMS))
    claims = dedupe_claims(LIVE_CLAIMS)
    after = pipeline.replace_rumour_block(before, claims)
    assert after is not None
    assert after.count("Anthropic is working on a collaborative") == 1
    assert "Ripple 65B Preview Released" in after
    assert "How are you currently handling persistent memory?" in after
    assert "📣" in after
    # Only the rumour lines changed.
    head_b = before.split("<b>◻️ Rumours", 1)[0]
    head_a = after.split("<b>◻️ Rumours", 1)[0]
    assert head_b == head_a


def test_brief_dedup_fix_edits_in_place_without_new_post(monkeypatch, tmp_path):
    calls: list[tuple] = []
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLISH_TARGET", "main")
    monkeypatch.setenv("TELEGRAM_MAIN_CHANNEL_ID", "@GitHub_Open_Source")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(pipeline, "_live_brief_hook", lambda channel, mid: "Recovered hook")
    monkeypatch.setattr(
        pipeline, "TelegramClient",
        lambda token: type("T", (), {
            "edit_text": lambda self, chat, mid, text, preview_url="": (
                calls.append((chat, mid, text, preview_url)), {"message_id": mid})[1]
        })(),
    )
    store = StateStore(tmp_path)
    store.data["news_topics"] = [{
        "telegram_message_id": 3575,
        "image_url": "https://i.ibb.co/example.png",
        "brief": _brief(LIVE_CLAIMS),
    }]
    result = pipeline.brief_dedup_fix_command(Config.from_env(), store)
    assert result["ok"] is True and result["edited"] is True
    assert result["before"] == 3 and result["after"] == 2
    assert len(calls) == 1, "exactly one in-place edit, never a new post"
    assert calls[0][1] == 3575
    assert calls[0][3] == "https://i.ibb.co/example.png", "card preview preserved"
    assert calls[0][2].count("Anthropic is working on a collaborative") == 1
    assert store.data["news_topics"][0]["post_text"] == calls[0][2]
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(e.get("type") == "brief_rumours_deduped" for e in events)


def test_cli_and_workflow_whitelist_the_new_command():
    cli = (REPO / "src/ghos_v6/cli.py").read_text(encoding="utf-8")
    wf = (REPO / ".github/workflows/pipeline.yml").read_text(encoding="utf-8")
    assert 'sub.add_parser(\n        "brief-dedup-fix"' in cli
    assert "brief-dedup-fix" in wf
    assert "quality-fix|brief-dedup-fix" in wf


def test_news_command_filters_and_remembers(monkeypatch):
    source = (REPO / "src/ghos_v6/pipeline.py").read_text(encoding="utf-8")
    assert "filter_recent_rumours(\n        store, brief.get(\"unconfirmed_claims\")" in source
    assert "remember_rumours(store, brief.get(\"unconfirmed_claims\"))" in source
    assert '"post_text": text' in source
