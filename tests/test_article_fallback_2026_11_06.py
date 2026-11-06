"""Regression: the 06.11 article incident.

The top-scoring project of the day was already covered by a pre-migration
dev.to article. ``article_of_day()`` returned an honest ``duplicate`` verdict
without trying the next-best project, and the watchdog — which only accepted a
``published[].articles.devto.published_at`` fact — kept re-dispatching the
article every 30 minutes and escalating to warnings.

Owner decision (2026-11-06): a duplicate must fall through to the next project,
the cheap dev.to index lookup happens once before any expensive cover/publish
work, and the article command records its verdict as a completed UTC-day slot.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from ghos_v6 import pipeline, watchdog
from ghos_v6.config import Config
from ghos_v6.state import StateStore


def _config():
    return SimpleNamespace(
        feature_devto=True, dev_api_key="k", require=lambda *a: None,
        telegram_bot_token="x", Gemini_api_key="x", Gemini_model="x",
        telegram_chat_id="@c", telegram_owner_chat_id=None,
    )


def _record(repo: str, score: float = 90, *, duplicate: bool = False):
    record = {
        "full_name": repo, "name": repo.split("/")[-1], "score": score,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "devto_cover_url": "https://i.ibb.co/a/cover.png",
        "generated": {"article_markdown": "## What it is\nA tool."},
    }
    if duplicate:
        record["articles"] = {"devto_duplicate": {
            "url": "https://dev.to/githubopensource/old-1abc",
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }}
    return record


class _FakeDevTo:
    instances: list = []
    owned: list = []

    def __init__(self, key):
        self.key = key
        self.published: list[str] = []
        self.index_calls = 0
        _FakeDevTo.instances.append(self)

    def my_articles(self, per_page=100):
        self.index_calls += 1
        return _FakeDevTo.owned

    def publish_article(self, record, generated):
        self.published.append(record["full_name"])
        return {"id": 99, "url": "https://dev.to/githubopensource/new-1abc"}


def _bind(monkeypatch, owned):
    _FakeDevTo.instances = []
    _FakeDevTo.owned = owned
    monkeypatch.setattr(pipeline, "DevToClient", _FakeDevTo)
    covers: list[str] = []
    monkeypatch.setattr(
        pipeline, "_ensure_devto_cover",
        lambda config, record: covers.append(record["full_name"]) or "",
    )
    return covers


def test_duplicate_falls_through_to_the_next_best_project(monkeypatch, tmp_path):
    covers = _bind(monkeypatch, [{
        "url": "https://dev.to/githubopensource/old-2n1g",
        "body_markdown": "- [GitHub](https://github.com/a/top)",
    }])
    store = StateStore(tmp_path)
    store.data["published"] = {
        "a/top": _record("a/top", 95),
        "a/mid": _record("a/mid", 90),
        "a/low": _record("a/low", 85),
    }

    result = pipeline.article_of_day(_config(), store)

    assert result["published"] is True
    assert result["repo"] == "a/mid", "next-highest score must win, not an empty refusal"
    assert result["skipped_duplicates"] == 1
    assert _FakeDevTo.instances[0].published == ["a/mid"]
    assert covers == ["a/mid"], "no ImgBB cover work for the duplicate"
    assert "devto_duplicate" in store.data["published"]["a/top"]["articles"]
    assert store.data["last_runs"]["article"]["published"] is True


def test_devto_index_is_fetched_once_per_run(monkeypatch, tmp_path):
    _bind(monkeypatch, [
        {"url": "https://dev.to/x/a-1", "body_markdown": "https://github.com/a/one"},
        {"url": "https://dev.to/x/b-1", "body_markdown": "https://github.com/a/two"},
    ])
    store = StateStore(tmp_path)
    store.data["published"] = {
        "a/one": _record("a/one", 95),
        "a/two": _record("a/two", 90),
        "a/three": _record("a/three", 85),
    }

    result = pipeline.article_of_day(_config(), store)

    assert result["published"] is True and result["repo"] == "a/three"
    assert _FakeDevTo.instances[0].index_calls == 1, "one cheap GET, not one per candidate"


def test_all_duplicates_are_recorded_as_a_finished_slot(monkeypatch, tmp_path):
    _bind(monkeypatch, [
        {"url": "https://dev.to/x/a-1", "body_markdown": "https://github.com/a/one"},
        {"url": "https://dev.to/x/b-1", "body_markdown": "https://github.com/a/two"},
    ])
    store = StateStore(tmp_path)
    store.data["published"] = {
        "a/one": _record("a/one", 95),
        "a/two": _record("a/two", 90),
    }

    result = pipeline.article_of_day(_config(), store)

    assert result["published"] is False
    assert result["reason"] == "duplicate_repo_on_devto"
    assert result["duplicates"] == 2
    assert _FakeDevTo.instances[0].published == []
    slot = store.data["last_runs"]["article"]
    assert slot["finished_at"].startswith(datetime.now(timezone.utc).date().isoformat())
    assert slot["reason"] == "duplicate_repo_on_devto"


def test_cached_duplicates_do_not_touch_devto_at_all(monkeypatch, tmp_path):
    _bind(monkeypatch, [])
    store = StateStore(tmp_path)
    store.data["published"] = {
        "a/one": _record("a/one", 95, duplicate=True),
        "a/two": _record("a/two", 90, duplicate=True),
    }

    result = pipeline.article_of_day(_config(), store)

    assert result["reason"] == "duplicate_repo_on_devto"
    assert _FakeDevTo.instances == [], "no client, no GET, no quota spent"
    assert store.data["last_runs"]["article"]["finished_at"]


def test_no_projects_today_is_also_a_completed_slot(monkeypatch, tmp_path):
    _bind(monkeypatch, [])
    store = StateStore(tmp_path)
    result = pipeline.article_of_day(_config(), store)
    assert result["reason"] == "no_projects_today"
    assert store.data["last_runs"]["article"]["finished_at"]
    assert _FakeDevTo.instances == []


def _wire(monkeypatch):
    calls: list[str] = []
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setattr(
        watchdog, "_dispatch",
        lambda repo, inputs, token: (calls.append(inputs["command"]), (True, "HTTP 204"))[1],
    )
    monkeypatch.setattr(watchdog, "_notify", lambda config, text: True)
    return calls


def test_watchdog_retires_article_when_the_command_recorded_its_verdict(monkeypatch, tmp_path):
    calls = _wire(monkeypatch)
    store = StateStore(tmp_path)
    store.data["published"] = {
        "a/one": {"published_at": "2026-11-06T10:00:00Z", "score": 95},
    }
    store.data["last_runs"] = {
        "article": {"finished_at": "2026-11-06T14:10:00Z",
                    "reason": "duplicate_repo_on_devto", "published": False},
        "watchdog_dispatches": {
            "2026-11-06:article": {"at": "2026-11-06T14:00:00Z",
                                   "dispatched": True, "verified": False, "attempts": 4},
        },
    }
    result = watchdog.watchdog_command(
        Config(), store, now=datetime(2026, 11, 6, 14, 30, tzinfo=timezone.utc))
    assert "article" not in result["missed"]
    assert "article" in result["verified"]
    assert "article" not in calls, "no blind retry after an honest refusal"


def test_watchdog_still_detects_a_genuinely_missed_article(monkeypatch, tmp_path):
    calls = _wire(monkeypatch)
    store = StateStore(tmp_path)
    store.data["published"] = {
        "a/one": {"published_at": "2026-11-06T10:00:00Z", "score": 95},
    }
    result = watchdog.watchdog_command(
        Config(), store, now=datetime(2026, 11, 6, 14, 30, tzinfo=timezone.utc))
    assert "article" in result["missed"]
    assert "article" in calls
