"""Owner-reported incidents, 2026-11-04.

1. "сбой и ошибка, новость не вышла": YouTube's public RSS returned 404
   (it does that for *every* channel during its recurring outages), and the
   exception failed the whole scheduled workflow. The source is now
   layered and an upstream outage is a recorded skip, not a red run.
2. "карточка загрузилась дефолтной гитхаба": ImgBB answered a transient
   503, Telegraph upload 400, and the code substituted GitHub's generic
   OpenGraph image. Never do that again; retry transient failures instead.
3. "3 поста на dev.to за день, 2 про один репозиторий": article_of_day
   walked every one of today's posts and had no idea about articles created
   by the pre-migration repository on the same dev.to account.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from ghos_v6 import news, pipeline
from ghos_v6.media_host import ImgBBMediaHost, MediaHostError
from ghos_v6.state import StateStore
from ghos_v6.news import NewsSourceError, parse_channel_page

REPO = Path(__file__).resolve().parents[1]


class _Resp:
    def __init__(self, status_code, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")


# ---------------------------------------------------------------------------
# 1. News source outage


_CHANNEL_HTML = """
<html><script>
ytInitialData = {"contents":{}};
{"lockupViewModel":{"contentId":"ABCDEFGHIJK","contentType":"LOCKUP_CONTENT_TYPE_VIDEO",
 "metadata":{"lockupMetadataViewModel":{"title":{"content":"New GPT release \\u0026 dev tools"},
 "metadata":{"contentMetadataViewModel":{"metadataRows":[{"metadataParts":[
 {"text":{"content":"150K"}},{"text":{"content":"18h ago"}}]}]}}}}}},
{"lockupViewModel":{"contentId":"LMNOPQRSTUV","contentType":"LOCKUP_CONTENT_TYPE_VIDEO",
 "metadata":{"lockupMetadataViewModel":{"title":{"content":"Older roundup"},
 "metadata":{"contentMetadataViewModel":{"metadataRows":[{"metadataParts":[
 {"text":{"content":"90K"}},{"text":{"content":"9d ago"}}]}]}}}}}}
</script></html>
"""


def test_channel_page_parser_recovers_video_identity_and_age():
    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    entries = parse_channel_page(_CHANNEL_HTML, now=now)
    assert len(entries) == 2
    assert entries[0]["video_id"] == "ABCDEFGHIJK"
    assert entries[0]["title"] == "New GPT release & dev tools"
    assert entries[0]["url"] == "https://www.youtube.com/watch?v=ABCDEFGHIJK"
    assert entries[0]["published"].date().isoformat() == "2026-11-03"


def test_rss_outage_falls_back_to_the_channel_page(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if "feeds/videos.xml" in url:
            return _Resp(404, text="<html>404</html>")
        if url.endswith("/videos"):
            return _Resp(200, text=_CHANNEL_HTML)
        return _Resp(200, text="<html>watch</html>")

    monkeypatch.setattr(news.requests, "get", fake_get)
    monkeypatch.setattr(news, "_enrich_from_watch_page", lambda entry: entry)
    entries = news.fetch_worldofai_entries()
    assert len(entries) == 2
    assert any("feeds/videos.xml" in u for u in calls)
    assert any(u.endswith("/videos") for u in calls)


def test_every_source_down_raises_one_named_error(monkeypatch):
    def fake_get(url, **kwargs):
        return _Resp(503, text="unavailable")

    monkeypatch.setattr(news.requests, "get", fake_get)
    with pytest.raises(NewsSourceError, match="WorldofAI source unavailable"):
        news.fetch_worldofai_entries()


def test_news_outage_is_a_green_skip_with_an_owner_alert(monkeypatch, tmp_path):
    monkeypatch.setattr(
        pipeline, "fetch_worldofai_entries",
        lambda: (_ for _ in ()).throw(NewsSourceError("rss 404; channel 503")),
    )
    monkeypatch.setattr(pipeline, "GeminiClient", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "TelegramClient", lambda *a, **k: None)
    alerts = []
    monkeypatch.setattr(pipeline, "alert_owner", lambda c, t, d="": alerts.append((t, d)))
    config = SimpleNamespace(
        feature_ai_brief=True, require=lambda *a: None,
        telegram_bot_token="x", Gemini_api_key="x", Gemini_model="x",
        telegram_chat_id="@c", telegram_owner_chat_id=None,
    )
    store = StateStore(tmp_path)
    result = pipeline.news_command(config, store)
    assert result["ok"] is True
    assert result["published"] is False
    assert result["reason"] == "source_unavailable"
    assert alerts, "the owner must be told the brief was skipped"
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(e.get("type") == "news_skipped" for e in events)


# ---------------------------------------------------------------------------
# 2. Card hosting


def test_imgbb_retries_a_transient_503():
    replies = iter([
        _Resp(503, text="unavailable"),
        _Resp(503, text="unavailable"),
        _Resp(200, {"success": True, "data": {"image": {"url": "https://i.ibb.co/a/c.png"}}}),
    ])
    seen = []
    host = ImgBBMediaHost("k", post_fn=lambda *a, **k: (seen.append(1), next(replies))[1],
                          sleep_fn=lambda _: None)
    assert host.upload_image(b"png") == "https://i.ibb.co/a/c.png"
    assert len(seen) == 3


def test_imgbb_does_not_retry_a_permanent_bad_key():
    seen = []
    host = ImgBBMediaHost(
        "k",
        post_fn=lambda *a, **k: (seen.append(1), _Resp(400, {"error": {"message": "Invalid key"}}))[1],
        sleep_fn=lambda _: None,
    )
    with pytest.raises(MediaHostError, match="Invalid key"):
        host.upload_image(b"png")
    assert len(seen) == 1


def test_publish_never_substitutes_githubs_default_card():
    source = (REPO / "src" / "ghos_v6" / "pipeline.py").read_text(encoding="utf-8")
    assert "opengraph.githubassets.com/1/" not in source
    assert 'fallback="no_preview"' in source
    assert "preview_url = image_url or telegraph_url" in source


# ---------------------------------------------------------------------------
# 3. dev.to duplicates


def _config():
    return SimpleNamespace(
        feature_devto=True, dev_api_key="k", require=lambda *a: None,
        telegram_bot_token="x", Gemini_api_key="x", Gemini_model="x",
        telegram_chat_id="@c", telegram_owner_chat_id=None,
    )


def _record(repo, *, published_at=None):
    return {
        "full_name": repo, "name": repo.split("/")[-1], "score": 90,
        "published_at": published_at or datetime.now(timezone.utc).isoformat(),
        "devto_cover_url": "https://i.ibb.co/a/cover.png",
        "generated": {"article_markdown": "## What it is\nA tool."},
    }


class _FakeDevTo:
    instances = []

    def __init__(self, key):
        self.key = key
        self.published = []
        _FakeDevTo.instances.append(self)

    def my_articles(self, per_page=100):
        return _FakeDevTo.owned

    def publish_article(self, record, generated):
        self.published.append(record["full_name"])
        return {"id": 99, "url": "https://dev.to/githubopensource/new-1abc"}


def _bind(monkeypatch, owned):
    _FakeDevTo.instances = []
    _FakeDevTo.owned = owned
    monkeypatch.setattr(pipeline, "DevToClient", _FakeDevTo)


def test_only_one_devto_article_per_utc_day(monkeypatch, tmp_path):
    _bind(monkeypatch, [])
    store = StateStore(tmp_path)
    store.data["published"] = {
        "a/b": _record("a/b", published_at=datetime.now(timezone.utc).isoformat()),
    }
    store.data["published"]["a/b"]["articles"] = {
        "devto": {"id": 1, "url": "https://dev.to/x/y-1abc",
                  "published_at": datetime.now(timezone.utc).isoformat()}
    }
    result = pipeline.article_of_day(_config(), store)
    assert result["reason"] == "already_published_today"
    assert _FakeDevTo.instances == []


def test_repo_already_on_devto_is_not_published_again(monkeypatch, tmp_path):
    _bind(monkeypatch, [{
        "id": 4968458,
        "url": "https://dev.to/githubopensource/minecraft-in-rust-old-4774",
        "body_markdown": "- [GitHub repository](https://github.com/horizon-rust/horizon)",
    }])
    store = StateStore(tmp_path)
    store.data["published"] = {"horizon-rust/horizon": _record("horizon-rust/horizon")}
    result = pipeline.article_of_day(_config(), store)
    assert result["reason"] == "duplicate_repo_on_devto"
    assert result["existing_url"].endswith("-4774")
    assert _FakeDevTo.instances[0].published == []
    assert "devto_duplicate" in store.data["published"]["horizon-rust/horizon"]["articles"]


def test_new_repo_still_gets_one_article(monkeypatch, tmp_path):
    _bind(monkeypatch, [])
    store = StateStore(tmp_path)
    store.data["published"] = {"a/brand-new": _record("a/brand-new")}
    result = pipeline.article_of_day(_config(), store)
    assert result["published"] is True
    assert _FakeDevTo.instances[0].published == ["a/brand-new"]
