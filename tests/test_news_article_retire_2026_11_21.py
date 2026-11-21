"""Owner 21.11: a finished day must retire the news-article slot.

On 20.11 the AI Brief was terminally skipped (duplicate digest), and
news_article_command answered "brief_not_from_today" three times without
writing its daily marker - so the watchdog kept dispatching the slot until
its cap (20:30, 21:30, 22:01, capped 23:00). A merely late brief (19.11,
recovered at 20:01Z) must stay retryable; a day that ended without a brief
must retire the slot on a fact, and a force-published late brief must still
get its companion article.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ghos_v6 import pipeline, watchdog
from ghos_v6.config import Config
from ghos_v6.state import StateStore


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _yesterday() -> str:
    return datetime.fromtimestamp(
        datetime.now(timezone.utc).timestamp() - 86400, timezone.utc
    ).date().isoformat()


def _store_with_duplicate_digest_day(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path)
    store.data = {
        "news_topics": [
            {
                "headline": "Yesterday's model watch",
                "published_at": f"{_yesterday()}T19:19:00Z",
                "article_markdown": "",
            }
        ],
        "last_runs": {
            "news": {
                "finished_at": f"{_today()}T19:19:08Z",
                "reason": "duplicate_digest",
                "published": False,
            }
        },
    }
    return store


def _events(tmp_path: Path) -> list[dict]:
    path = tmp_path / "events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_day_without_a_brief_retires_the_slot(tmp_path):
    store = _store_with_duplicate_digest_day(tmp_path)
    config = Config(dev_api_key="x", feature_devto=True)

    result = pipeline.news_article_command(config, store)

    assert result["ok"] is True
    assert result["reason"] == "brief_not_from_today"
    assert result.get("retired") is True
    marker = store.data["last_runs"]["news-article"]
    assert str(marker["finished_at"]).startswith(_today())
    assert marker["published"] is False
    retired = [e for e in _events(tmp_path) if e.get("type") == "news_article_retired"]
    assert retired and retired[-1]["news_reason"] == "duplicate_digest"
    # the whole point: the watchdog must see the slot as finished
    assert watchdog._has_run(store, "news-article", datetime.now(timezone.utc).date())

    # and a second dispatch the same day is an idempotent no-op
    again = pipeline.news_article_command(config, store)
    assert again["reason"] == "news_article_already_done"


def test_a_late_brief_stays_retryable(tmp_path):
    # 19.11 shape: the news slot has NOT concluded (transient Gemini skip
    # leaves no marker), so the article must not retire itself.
    store = StateStore(tmp_path)
    store.data = {
        "news_topics": [
            {"headline": "Yesterday's model watch",
             "published_at": f"{_yesterday()}T19:19:00Z", "article_markdown": ""}
        ],
        "last_runs": {},
    }
    result = pipeline.news_article_command(Config(dev_api_key="x", feature_devto=True), store)
    assert result["reason"] == "brief_not_from_today"
    assert "retired" not in result
    assert "news-article" not in (store.data.get("last_runs") or {})
    assert not watchdog._has_run(store, "news-article", datetime.now(timezone.utc).date())


def test_no_topics_at_all_stays_retryable(tmp_path):
    store = StateStore(tmp_path)
    store.data = {"news_topics": [], "last_runs": {}}
    result = pipeline.news_article_command(Config(dev_api_key="x", feature_devto=True), store)
    assert result["reason"] == "no_brief_today"
    assert "news-article" not in (store.data.get("last_runs") or {})


def test_a_force_published_late_brief_still_gets_its_article(monkeypatch, tmp_path):
    # The day was retired, then the owner force-published a brief after all.
    store = _store_with_duplicate_digest_day(tmp_path)
    store.data["last_runs"]["news-article"] = {
        "finished_at": f"{_today()}T20:35:00Z",
        "reason": "brief_not_from_today",
        "published": False,
    }
    store.data["news_topics"].append(
        {
            "headline": "The late force-published model watch",
            "published_at": f"{_today()}T21:40:00Z",
            "article_markdown": "## What moved\n\nA full Gemini-written long read.",
            "article_title": "The late model watch",
        }
    )

    monkeypatch.setattr(pipeline, "_ensure_devto_cover", lambda config, topic: "https://example.com/cover.jpg")

    class FakeDevTo:
        def __init__(self, key):
            pass

        def publish_markdown(self, title, markdown, *, tags=(), cover_url=""):
            return {"id": 424242, "url": "https://dev.to/githubopensource/the-late-model-watch"}

    monkeypatch.setattr(pipeline, "DevToClient", FakeDevTo)

    result = pipeline.news_article_command(Config(dev_api_key="x", feature_devto=True), store)

    assert result["ok"] is True
    assert result["published"] is True
    assert result["url"] == "https://dev.to/githubopensource/the-late-model-watch"
    marker = store.data["last_runs"]["news-article"]
    assert marker["published"] is True
    assert marker["devto_id"] == 424242
