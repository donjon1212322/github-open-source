"""Owner 10.11: "исправь раз и навсегда, чтобы не было дублей".

Post 3579 (09.11) and 3581 (10.11) printed the same five stories. Root cause:
the Telegram path rebuilt its video_id daily and had no inter-day story
memory. This module covers the two barriers — exclude recent cluster keys
BEFORE generation, and refuse a mostly-duplicate brief AFTER generation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from ghos_v6 import pipeline
from ghos_v6.brief_memory import (
    _similar,
    duplicate_story_ratio,
    recent_cluster_keys,
    remember_brief,
    story_tokens,
)
from ghos_v6.state import StateStore
from ghos_v6.telegram_discovery import build_digest_material

DAY1 = datetime(2026, 11, 9, 19, 19, tzinfo=timezone.utc)
DAY2 = datetime(2026, 11, 10, 19, 19, tzinfo=timezone.utc)

# Verbatim story titles from live posts 3579 and 3581.
STORIES_3579 = [
    {"title": "Claude Fable 5.2 Released",
     "what_happened": ["Anthropic released Claude Fable 5.2 with a 20 percent price reduction."]},
    {"title": "OpenAI Teases ModRetro Chromatic",
     "what_happened": ["OpenAI teased the ModRetro Chromatic handheld game console."]},
    {"title": "AI Design Skills Collection",
     "what_happened": ["A new open-source collection of design instructions for AI coding agents."]},
    {"title": "REA Binary and App Reconstruction Agent",
     "what_happened": ["An agent-based tool called REA was released on GitHub."]},
    {"title": "T-Investments MCP Trading",
     "what_happened": ["T-Investments opened a Model Context Protocol server for trading."]},
]
STORIES_3581 = [
    {"title": "Claude Fable 5.2 Arrives with Lower Prices and Faster Speeds",
     "what_happened": ["Anthropic released Claude Fable 5.2, priced at $8 per million input tokens."]},
    {"title": "OpenAI Launches ModRetro Chromatic Codex Edition",
     "what_happened": ["OpenAI released the ModRetro Chromatic in a Codex Edition for $200."]},
    {"title": "Ripple-65B and Arete-27B Lead Open Source Updates",
     "what_happened": ["Ricursive released Ripple-65B-Preview, a 65-billion parameter dense model."]},
    {"title": "AI Design Skills for Coding Agents",
     "what_happened": ["An open-source collection of design instructions called AI Design Skills."]},
    {"title": "Trading via Model Context Protocol in T-Investments",
     "what_happened": ["T-Investments opened a Model Context Protocol server for AI agents."]},
]


class _Store:
    def __init__(self):
        self.data = {}


def _post(channel: str, pid: str, text: str, dt: str = "2026-11-10T12:00:00") -> str:
    return (
        f'<div class="tgme_widget_message" data-post="{channel}/{pid}">'
        f'<time datetime="{dt}">{dt}</time>'
        f'<div class="tgme_widget_message_text">{text}</div>'
        f'<span class="tgme_widget_message_views">1.4K</span></div>'
    )


def _fetcher(pages: dict[str, str]):
    return lambda url: pages.get(url.rsplit("/", 1)[-1], "")


def test_rewording_does_not_change_the_story_identity():
    left = story_tokens(STORIES_3579[0]["title"], STORIES_3579[0]["what_happened"])
    right = story_tokens(STORIES_3581[0]["title"], STORIES_3581[0]["what_happened"])
    assert _similar(left, right), (left, right)
    assert _similar(
        story_tokens(STORIES_3579[1]["title"]),
        story_tokens(STORIES_3581[1]["title"]),
    ) is True


def test_different_stories_are_not_mistaken_for_duplicates():
    assert _similar(story_tokens("Ripple 65B Preview Released"),
                    story_tokens("T-Investments MCP Trading")) is False


def test_yesterday_digest_is_recognised_as_mostly_duplicate():
    store = _Store()
    remember_brief(store, ["story:claude-fable", "story:modretro"], STORIES_3579, now=DAY1)
    ratio = duplicate_story_ratio(store, STORIES_3581, now=DAY2)
    assert ratio >= 0.5, ratio  # 4 of 5 stories are repeats


def test_same_day_refresh_is_never_blocked():
    store = _Store()
    remember_brief(store, [], STORIES_3579, now=DAY2)
    assert duplicate_story_ratio(store, STORIES_3579, now=DAY2) == 0.0


def test_memory_expires_and_is_bounded():
    store = _Store()
    remember_brief(store, ["story:a"], STORIES_3579, now=DAY1)
    later = DAY2 + timedelta(days=6)
    assert recent_cluster_keys(store, now=later) == set()
    assert duplicate_story_ratio(store, STORIES_3581, now=later) == 0.0


def test_discovery_excludes_previously_printed_clusters():
    pages = {
        "neuro_channel": _post("neuro_channel", "1", "Claude Fable 5.2 pricing update"),
        "data_secrets": _post("data_secrets", "2", "Ripple 65B Preview weights are live"),
    }
    fresh = build_digest_material(now=DAY2, fetcher=_fetcher(pages),
                                  channels=tuple(pages))
    keys = [c["key"] for c in fresh["clusters"]] + list(fresh["rumour_keys"])
    assert keys, "fixture must produce clusters"
    excluded = build_digest_material(now=DAY2, fetcher=_fetcher(pages),
                                     channels=tuple(pages), exclude_keys=set(keys))
    assert excluded["clusters"] == []
    assert excluded["stats"]["excluded"] == len(set(keys))
    assert excluded["rumours"] == []
    assert excluded["rumour_keys"] == []


def test_news_command_refuses_a_duplicate_digest(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    remember_brief(store, ["story:claude-fable"], STORIES_3579, now=DAY1)
    # Freeze "now" to DAY2 for the duplicate-ratio window.
    class FixedDateTime:
        @staticmethod
        def now(tz=None):
            return DAY2

    monkeypatch.setattr(pipeline, "datetime", FixedDateTime)
    monkeypatch.setattr(pipeline, "build_digest_material", lambda **kw: {
        "entry": {"video_id": "tg-test", "description": "material"},
        "primary": {"text": "material"},
        "rumours": [], "clusters": [{"key": "story:new"}],
    })
    monkeypatch.setattr(pipeline, "GeminiClient", lambda *a, **kw: object())
    monkeypatch.setattr(pipeline, "TelegramClient", lambda *a, **kw: object())
    monkeypatch.setattr(pipeline, "synthesize_brief_telegram",
                        lambda *a, **kw: {"stories": STORIES_3581,
                                          "unconfirmed_claims": [], "eligible": True})
    config = SimpleNamespace(
        feature_ai_brief=True, feature_telegram_discovery=True,
        require=lambda *_a: None, telegram_chat_id="@GitHub_Open_Source",
        telegram_bot_token="dummy", Gemini_api_key="dummy", Gemini_model="x",
        telegram_owner_chat_id=None,
    )
    result = pipeline.news_command(config, store)
    assert result["reason"] == "duplicate_digest", result
    assert result["duplicate_ratio"] >= 0.5
    assert result["published"] is False


def test_news_command_publishes_when_stories_are_new(monkeypatch, tmp_path):
    """A genuinely new digest must NOT be blocked by the memory."""
    store = StateStore(tmp_path)
    remember_brief(store, ["story:old"], STORIES_3579, now=DAY1)
    new_stories = [
        {"title": "Qwen4 Sparse Agent Toolkit", "what_happened": ["Alibaba released a sparse agent toolkit."]},
        {"title": "Llama 5 Mini On-Device", "what_happened": ["Meta shipped a small on-device model."]},
    ]
    monkeypatch.setattr(pipeline, "build_digest_material", lambda **kw: {
        "entry": {"video_id": "tg-test", "description": "material"},
        "primary": {"text": "material"}, "rumours": [],
        "clusters": [{"key": "story:fresh"}],
    })
    monkeypatch.setattr(pipeline, "GeminiClient", lambda *a, **kw: object())
    monkeypatch.setattr(pipeline, "TelegramClient", lambda *a, **kw: object())
    monkeypatch.setattr(pipeline, "synthesize_brief_telegram",
                        lambda *a, **kw: {"stories": new_stories,
                                          "unconfirmed_claims": [], "eligible": False,
                                          "rejection_reason": "no_candidates"})
    config = SimpleNamespace(
        feature_ai_brief=True, feature_telegram_discovery=True,
        require=lambda *_a: None, telegram_chat_id="@GitHub_Open_Source",
        telegram_bot_token="dummy", Gemini_api_key="dummy", Gemini_model="x",
        telegram_owner_chat_id=None,
    )
    result = pipeline.news_command(config, store)
    assert result["reason"] != "duplicate_digest", result
