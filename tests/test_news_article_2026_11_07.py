"""Regression: the SECOND dev.to article is a full Gemini-written long read.

Owner correction 07.11.2026: "разве не Gemini формирует статью? статья должна
быть полноценной многословной и профессионально сверстана, с дружелюбной
подачей, возможны эмодзи". The first implementation shipped a hard-coded
~100-word stub with a Python list repr. Never let that come back.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ghos_v6 import pipeline, watchdog
from ghos_v6.config import Config
from ghos_v6.news import NEWS_ARTICLE_SCHEMA, NEWS_ARTICLE_STYLE, synthesize_news_article
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]


def _long_markdown() -> str:
    head = (
        "> **TL;DR:** Three confirmed model updates, a couple of odd little "
        "tools and one rumour we are definitely not calling a release.\n\n"
        "## What actually moved today\n\n"
        "It was a quiet-looking day that was not actually quiet. 🧠\n\n"
        "## The model watch\n\n"
        "### GPT-6 Cyber\n\n"
        "The evidence level is thin, so treat this as unconfirmed.\n\n"
        "| Confirmed | Not confirmed |\n| --- | --- |\n| Inkling 2 Small | GPT-6.2 Astra |\n\n"
        "## Rumours, clearly labelled\n\n"
        "Not confirmed: a Friday launch date.\n\n"
        "## Odd finds and small tools\n\n"
        "- A tiny VRAM htop clone ✨\n\n"
        "## What I would try this weekend\n\n"
        "Re-run one eval with the new context limit.\n\n"
        "## Where this leaves us\n\n"
        "Which of these would you put in a weekend eval first?\n"
    )
    filler = "\n\n".join(
        f"Paragraph {i}: a concrete, friendly explanation of what changed, "
        "why a developer would care and what still looks uncertain."
        for i in range(70)
    )
    return head + filler


class FakeGemini:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.prompt = ""
        self.kwargs: dict[str, object] = {}

    def generate_json(self, prompt: str, **kwargs: object) -> dict[str, object]:
        self.prompt = prompt
        self.kwargs = kwargs
        return self.payload


def test_article_prompt_demands_a_full_friendly_professional_long_read():
    assert "1000-1500 words" in NEWS_ARTICLE_STYLE
    assert "TL;DR" in NEWS_ARTICLE_STYLE
    assert "emoji" in NEWS_ARTICLE_STYLE
    assert "friendly and conversational" in NEWS_ARTICLE_STYLE
    assert 'no "Sources"' in NEWS_ARTICLE_STYLE
    assert "Python list or dict repr" in NEWS_ARTICLE_STYLE
    assert "Rumours stay ONLY" in NEWS_ARTICLE_STYLE
    assert NEWS_ARTICLE_SCHEMA["required"] == ["article_title", "article_markdown"]


def test_synthesize_news_article_uses_gemini_and_returns_normalised_markdown():
    fake = FakeGemini(
        {
            "article_title": "Model watch, odd tools and one stubborn rumour",
            "article_markdown": "# Draft title\n\n" + _long_markdown(),
        }
    )
    brief = {
        "headline": "Inkling 2 Small and GPT-6 Cyber lead the model watch",
        "models": ["Inkling 2 Small", "GPT-6 Cyber"],
        "stories": [
            {
                "title": "Inkling 2 Small",
                "models": ["Inkling 2 Small"],
                "what_happened": ["A small reasoning model card appeared."],
                "why_it_matters": "Cheaper evals.",
                "status": "official_release",
                "basis": "model card published",
            }
        ],
        "unconfirmed_claims": ["Astra on Friday"],
    }
    result = synthesize_news_article(brief, fake)
    assert result["article_title"] == "Model watch, odd tools and one stubborn rumour"
    assert "Draft title" not in result["article_markdown"], "leading H1 is stripped"
    assert "## The model watch" in result["article_markdown"]
    assert "| Confirmed |" in result["article_markdown"]
    assert "Inkling 2 Small" in fake.prompt
    assert "Astra on Friday" in fake.prompt
    assert fake.kwargs["schema"] is NEWS_ARTICLE_SCHEMA
    assert fake.kwargs["max_output_tokens"] == 16384
    assert "['" not in result["article_markdown"], "no Python list repr"


def _wire(monkeypatch) -> tuple[list[dict[str, str]], Config]:
    captured: list[dict[str, str]] = []

    def fake_publish(self, title, body, *, tags=(), cover_url=""):
        captured.append(
            {"title": title, "body": body, "tags": list(tags), "cover_url": cover_url}
        )
        return {"id": 987654, "url": "https://dev.to/githubopensource/full-long-read-1"}

    monkeypatch.setattr(
        "ghos_v6.platforms.DevToClient.publish_markdown", fake_publish
    )
    monkeypatch.setenv("FEATURE_DEVTO", "true")
    monkeypatch.setenv("DEV_API_KEY", "test-dev-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    return captured, Config.from_env()


def _topic(day: str, *, with_article: bool = True) -> dict[str, object]:
    return {
        "published_at": f"{day}T19:19:00Z",
        "headline": "Inkling 2 Small and GPT-6 Cyber lead the model watch",
        "topic_key": "model-watch",
        "image_url": "https://i.ibb.co/card-d.png",
        "article_title": "A real Gemini-written title" if with_article else "",
        "article_markdown": _long_markdown() if with_article else "",
        "brief": {
            "headline": "Inkling 2 Small and GPT-6 Cyber lead the model watch",
            "models": ["Inkling 2 Small", "GPT-6 Cyber"],
            "stories": [
                {
                    "title": "Inkling 2 Small",
                    "bullets": ["A model card appeared."],
                    "why": "Cheaper evals.",
                    "status": "official_release",
                    "basis": "model card published",
                }
            ],
            "unconfirmed_claims": ["Astra on Friday"],
        },
    }


def test_article_slot_publishes_the_stored_gemini_markdown(monkeypatch, tmp_path):
    captured, cfg = _wire(monkeypatch)
    day = datetime.now(timezone.utc).date().isoformat()
    store = StateStore(tmp_path)
    store.data["news_topics"] = [_topic(day)]

    result = pipeline.news_article_command(cfg, store)

    assert result["published"] is True
    assert result["words"] > 400
    assert captured[0]["title"] == "A real Gemini-written title"
    assert captured[0]["body"] == _long_markdown()
    assert "This is a standalone companion" not in captured[0]["body"]
    assert "## Status" not in captured[0]["body"]
    assert "['" not in captured[0]["body"]
    assert captured[0]["cover_url"] == "https://i.ibb.co/card-d.png"
    marker = store.data["last_runs"]["news-article"]
    assert marker["published"] is True and marker["words"] > 400
    assert "article" not in store.data["last_runs"], "project article untouched"

    again = pipeline.news_article_command(cfg, store)
    assert again["reason"] == "news_article_already_done"
    assert len(captured) == 1, "no duplicate dev.to article"


def test_missing_markdown_is_rebuilt_by_gemini_not_by_a_template(monkeypatch, tmp_path):
    captured, cfg = _wire(monkeypatch)
    day = datetime.now(timezone.utc).date().isoformat()
    store = StateStore(tmp_path)
    store.data["news_topics"] = [_topic(day, with_article=False)]

    def fake_rebuild(brief, client):
        assert "Inkling 2 Small" in str(brief)
        return {"article_title": "Rebuilt by Gemini", "article_markdown": _long_markdown()}

    monkeypatch.setattr(pipeline, "synthesize_news_article", fake_rebuild)
    result = pipeline.news_article_command(cfg, store)

    assert result["published"] is True
    assert captured[0]["title"] == "Rebuilt by Gemini"
    assert captured[0]["body"] == _long_markdown()
    assert store.data["news_topics"][-1]["article_markdown"] == _long_markdown()


def test_stale_brief_never_publishes_the_article(monkeypatch, tmp_path):
    captured, cfg = _wire(monkeypatch)
    store = StateStore(tmp_path)
    store.data["news_topics"] = [_topic("2026-11-06")]
    result = pipeline.news_article_command(cfg, store)
    assert result["reason"] == "brief_not_from_today"
    assert captured == []


def test_watchdog_covers_news_article_but_waits_for_the_brief(monkeypatch, tmp_path):
    calls: list[str] = []

    def fake_dispatch(repo, inputs, token):
        calls.append(inputs["command"])
        return True, "HTTP 204"

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setattr(watchdog, "_dispatch", fake_dispatch)
    monkeypatch.setattr(watchdog, "_notify", lambda config, text: False)

    store = StateStore(tmp_path)
    now = datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc)
    first = watchdog.watchdog_command(Config(), store, now=now)
    # 05.11 is Thursday, so the weekly poll-close slot is checked too.
    assert first["checked"] == len(watchdog.SLOTS) + 1 == 8
    assert "news-article" not in calls, "no blind dispatch before the brief exists"

    store.data["news_topics"] = [{"published_at": "2026-11-05T19:20:00Z"}]
    calls.clear()
    second = watchdog.watchdog_command(Config(), store, now=now)
    assert "news-article" in calls
    assert "news-article" in second["dispatched"]


def test_brief_is_not_published_twice_on_a_double_trigger(monkeypatch, tmp_path):
    day = datetime.now(timezone.utc).date().isoformat()
    store = StateStore(tmp_path)
    store.data["news_topics"] = [{"published_at": f"{day}T19:19:00Z"}]

    def must_not_fetch(**_kwargs):
        raise AssertionError("no source fetch on a duplicate trigger")

    monkeypatch.setattr(pipeline, "build_digest_material", must_not_fetch)
    cfg = Config.from_env()
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    cfg = Config.from_env()
    result = pipeline.news_command(cfg, store, force=True)
    assert result["reason"] == "news_already_published"
