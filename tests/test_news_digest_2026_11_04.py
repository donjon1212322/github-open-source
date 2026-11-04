"""Owner feedback on the first AI Brief (2026-11-04, second pass).

The published post 3553 compressed a whole round-up into a single RSI
filing story, cited the source and lost the model names. These tests encode:
a multi-story digest, verbatim technical identifiers, no source attribution,
and an in-place fix that never creates a duplicate post.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from ghos_v6 import pipeline
from ghos_v6.formatter import CHANNEL_TAG_DIVIDER
from ghos_v6.news import (
    NEWS_SCHEMA,
    brief_model_names,
    brief_stories,
    clean_brief_headline,
    format_ai_brief,
    synthesize_brief,
)
from ghos_v6.state import StateStore
from ghos_v6.telegram_payload import build_edit_message_payload

REPO = Path(__file__).resolve().parents[1]

MODELS = (
    "Claude Fable 5.5", "Claude Opus 5.5", "Claude Mythos 5.5",
    "GPT-6 Astra", "GPT-6.1 Sol", "GPT-6.2", "Qwen 4", "Qwen 4 27B",
    "Gemini 4 Argon", "DeepSeek Harness", "LongCat-2.5", "Pulse Alpha",
    "Kentia",
)


def _roundup():
    return {
        "headline": "Claude Fable 5.5, GPT-6 Astra and Qwen 4 27B lead a packed model week",
        "summary": "A stealth checkpoint, a 6X result and an open-weight rethink landed together.",
        "models": list(MODELS[:6]),
        "stories": [
            {
                "title": "Claude Fable 5.5 appears as a stealth checkpoint",
                "models": ["Claude Fable 5.5", "Claude Opus 5.5"],
                "what_happened": [
                    "A Fable 5.5 checkpoint surfaced without an announcement.",
                    "It was compared with Claude Opus 5.5 on generated output.",
                ],
                "why_it_matters": "Re-run evals pinned to the previous checkpoint.",
                "confirmed": False,
            },
            {
                "title": "GPT-6 Astra improves another model by 6X without training",
                "models": ["GPT-6 Astra", "GPT-6.1 Sol"],
                "what_happened": ["The gain came from inference-time guidance."],
                "why_it_matters": "Eval harnesses must separate model quality from scaffolding.",
                "confirmed": False,
            },
            {
                "title": "Qwen 4 27B and the open-weight question",
                "models": ["Qwen 4", "Qwen 4 27B"],
                "what_happened": ["A possible end to open-weight releases was discussed."],
                "why_it_matters": "Self-hosting roadmaps should not assume future weights.",
                "confirmed": False,
            },
            {
                "title": "DeepSeek Harness Creator Mode and Gemini 4 Argon routing",
                "models": ["DeepSeek Harness", "Gemini 4 Argon"],
                "what_happened": ["Creator Mode ships a new agent harness."],
                "why_it_matters": "One API call now costs and returns something different.",
                "confirmed": True,
            },
            {
                "title": "LongCat-2.5, Pulse Alpha and a free Kentia checkpoint",
                "models": ["LongCat-2.5", "Pulse Alpha", "Kentia"],
                "what_happened": ["Three smaller releases landed with little documentation."],
                "why_it_matters": "Cheap fallback models are worth testing first.",
                "confirmed": False,
            },
        ],
        "why_devs": "Five model stories landed at once; pin exact names before re-running evals.",
        "confirmed": ["The releases are named in the chapter list."],
        "unconfirmed_claims": ["Release dates remain unconfirmed."],
        "topic_key": "claude-fable-5-5-gpt-6-astra-model-roundup",
        "discussion_question": "Which exact checkpoint do you move your evals to first?",
        "rejection_reason": "",
        "eligible": True,
        "official_url": "https://alignment.anthropic.com/2026/rsi",
        "official_name": "Anthropic alignment post",
        "video_url": "https://www.youtube.com/watch?v=i-wYjUriSzo",
    }


def _entry(video_id="i-wYjUriSzo", hours_ago=5, *, now=None):
    now = now or datetime.now(timezone.utc)
    published = now - timedelta(hours=hours_ago)
    return {
        "video_id": video_id,
        "title": "Anthropic's RSI Breakthrough?! + Fable 5.5, GPT-6 Astra, Qwen 4",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "published_at": published.isoformat(),
        "published": published,
        "description": "Sponsored by CodeRabbit. [Time Stamps]: 7:37 New Fable 5.5 Checkpoint",
    }


# ---------------------------------------------------------------------------
# Schema + prompt: a digest, not one story

def test_schema_requires_several_stories_with_their_own_exact_names():
    assert "stories" in NEWS_SCHEMA["required"]
    story = NEWS_SCHEMA["properties"]["stories"]["items"]
    assert "models" in story["required"]
    assert "what_happened" in story["required"]
    assert "why_it_matters" in story["required"]
    assert "models" in NEWS_SCHEMA["required"]
    assert "unconfirmed_claims" in NEWS_SCHEMA["required"]


def test_prompt_demands_the_whole_roundup_exact_names_and_no_sources():
    captured = {}

    class _Gemini:
        def generate_json(self, prompt, **kwargs):
            captured["prompt"] = prompt
            captured["schema"] = kwargs.get("schema")
            return _roundup()

    result = synthesize_brief(_entry(), {}, _Gemini(), use_video=True)
    prompt = captured["prompt"]
    for marker in (
        "DIGEST OF THE WHOLE VIDEO", "FOUR TO SEVEN", "MODEL NEWS COMES FIRST",
        "EXACT TECHNICAL NAMES ARE SACRED", "NO SOURCE ATTRIBUTION",
        "ADVERTISING MUST BE REMOVED", "Claude Fable 5.5", "LongCat-2.5",
        "AT LEAST FIVE stories", "Do not pad the list",
        "Every story title must name at least one exact technical identifier",
    ):
        assert marker in prompt, marker
    assert captured["schema"] is NEWS_SCHEMA
    assert result["eligible"] is True


# ---------------------------------------------------------------------------
# Rendering: every exact name, no sources, readable digest

def test_all_stories_and_all_exact_model_names_are_rendered():
    brief = _roundup()
    rendered = format_ai_brief(brief)
    assert len(brief_stories(brief)) == 5
    for badge in ("1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"):
        assert badge in rendered
    for name in set(brief_model_names(brief)):
        assert name in rendered, f"{name} must stay verbatim"
    assert "not_a_digest" not in rendered
    assert rendered.index("daily open-source picks") < rendered.index(CHANNEL_TAG_DIVIDER) < rendered.index(
        "#AINews #OpenSource"
    )


def test_no_source_attribution_anywhere_in_the_post():
    rendered = format_ai_brief(_roundup()).lower()
    for marker in (
        "sources", "worldofai", "world of ai", "youtube", "video-only",
        "spotted via", "official source", "primary source", "read more",
        "https://alignment.anthropic.com", "coderabbit", "sponsored",
    ):
        assert marker not in rendered, marker


def test_headline_cleanup_never_rewrites_a_technical_name():
    text = "HUGE! GPT-6 Astra and LongCat-2.5 beat Qwen 4 27B"
    cleaned = clean_brief_headline(text, preserve=MODELS)
    for name in ("GPT-6 Astra", "LongCat-2.5", "Qwen 4 27B"):
        assert name in cleaned
    assert "HUGE" not in cleaned


def test_legacy_single_story_brief_still_renders_a_valid_post():
    rendered = format_ai_brief({
        "headline": "A new model shipped",
        "what_happened": ["The model was released"],
        "why_devs": "It changes local inference",
        "confirmed": ["Release notes published"],
    })
    assert "AI Brief" in rendered
    assert CHANNEL_TAG_DIVIDER in rendered
    assert "<b>React</b>" in rendered
    assert "#AINews #OpenSource" in rendered


def test_card_and_edit_payload_carry_no_source_or_keyboard():
    cards = (REPO / "src" / "ghos_v6" / "cards.py").read_text(encoding="utf-8")
    fn = cards[cards.index("def ai_brief_card_columns("):cards.index("def render_digest_card(")]
    assert "OFFICIAL SOURCE" not in fn and "SPOTTED VIA" not in fn and "WorldofAI" not in fn
    assert "MODELS IN THIS BRIEF" in fn
    payload = build_edit_message_payload("@GitHub_Open_Source", 3553, "text")
    assert payload["message_id"] == 3553
    assert "reply_markup" not in payload
    assert payload["link_preview_options"] == {"is_disabled": True}


def test_workflow_exposes_the_refresh_switch():
    flow = (REPO / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    cli = (REPO / "src" / "ghos_v6" / "cli.py").read_text(encoding="utf-8")
    assert "refresh_news:" in flow and "REFRESH_NEWS" in flow and "--refresh" in flow
    assert '"--refresh"' in cli and "refresh=args.refresh" in cli


# ---------------------------------------------------------------------------
# Pipeline: one story is rejected, refresh edits in place

def _config():
    return SimpleNamespace(
        feature_ai_brief=True, require=lambda *a: None,
        telegram_bot_token="x", Gemini_api_key="x", Gemini_model="x",
        telegram_chat_id="@GitHub_Open_Source", telegram_owner_chat_id=None,
        imgbb_api_key="",
    )


def _wire(monkeypatch, tmp_path, *, brief=None):
    sent, edited, prompts = [], [], []

    class _Gemini:
        def __init__(self, *a, **k):
            pass

        def generate_json(self, prompt, **kwargs):
            prompts.append(prompt)
            return dict(brief if brief is not None else _roundup())

    class _Telegram:
        def __init__(self, *a, **k):
            pass

        def send_text(self, chat_id, text, **kwargs):
            sent.append({"text": text, "kwargs": kwargs})
            return {"message_id": 9000}

        def edit_text(self, chat_id, message_id, text, **kwargs):
            edited.append({"id": message_id, "text": text, "kwargs": kwargs})
            return {"message_id": message_id}

    card = tmp_path / "card.png"
    card.write_bytes(b"png")
    monkeypatch.setattr(pipeline, "GeminiClient", _Gemini)
    monkeypatch.setattr(pipeline, "TelegramClient", _Telegram)
    monkeypatch.setattr(pipeline, "fetch_worldofai_entries", lambda: [_entry()])
    monkeypatch.setattr(pipeline, "extract_primary_urls", lambda e: [])
    monkeypatch.setattr(pipeline, "render_ai_brief_card", lambda *a, **k: card)
    monkeypatch.setattr(pipeline, "_host_local_image", lambda *a, **k: ("", ""))
    return sent, edited, prompts


def test_single_story_is_not_published(monkeypatch, tmp_path):
    one = _roundup()
    one["stories"] = one["stories"][:1]
    sent, edited, _ = _wire(monkeypatch, tmp_path, brief=one)
    result = pipeline.news_command(_config(), StateStore(tmp_path))
    assert result["published"] is False
    assert result["reason"] == "not_a_digest"
    assert not sent and not edited


def test_refresh_edits_the_existing_post_instead_of_posting_a_duplicate(monkeypatch, tmp_path):
    sent, edited, _ = _wire(monkeypatch, tmp_path)
    store = StateStore(tmp_path)
    store.data["news_topics"] = [{
        "video_id": "i-wYjUriSzo",
        "topic_key": "anthropic-rsi",
        "headline": "Anthropic amends S-1 filing regarding RSI policy thresholds",
        "published_at": "2026-11-04T03:19:46Z",
        "telegram_message_id": 3553,
    }]

    result = pipeline.news_command(_config(), store, refresh=True)
    assert result["published"] is True and result["edited"] is True
    assert result["telegram_message_id"] == 3553
    assert result["stories"] == 5
    assert not sent, "a refresh must never create a second post"
    assert len(edited) == 1 and edited[0]["id"] == 3553
    assert "Claude Fable 5.5" in edited[0]["text"]
    assert "Sources" not in edited[0]["text"]
    assert len(store.data["news_topics"]) == 1
    assert store.data["news_topics"][0]["telegram_message_id"] == 3553
    assert store.data["news_topics"][0]["models"]


def test_refresh_still_refuses_a_stale_video(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    old = _entry("ANCIENT", 96, now=now)
    sent, edited, _ = _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(pipeline, "fetch_worldofai_entries", lambda: [old])
    result = pipeline.news_command(_config(), StateStore(tmp_path), refresh=True)
    assert result["published"] is False and result["reason"] == "video_too_old"
    assert not sent and not edited


def test_normal_news_still_dedupes_the_same_video(monkeypatch, tmp_path):
    sent, _, _ = _wire(monkeypatch, tmp_path)
    store = StateStore(tmp_path)
    first = pipeline.news_command(_config(), store)
    assert first["published"] is True and len(sent) == 1
    assert "https://www.youtube.com" not in sent[0]["kwargs"].get("preview_url", "")
    second = pipeline.news_command(_config(), store)
    assert second["reason"] == "news_already_published"
    assert len(sent) == 1
