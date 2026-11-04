"""Regression tests for the third audit (2026-11-02, round 3)."""
from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone

import pytest
import requests

from ghos_v6 import http, pipeline
from ghos_v6.config import Config
from ghos_v6.Gemini import deterministic_project_content
from ghos_v6.rank import score_candidate
from ghos_v6.state import StateStore


class _FakeSocial:
    def __init__(self, *args, **kwargs):
        pass

    def post(self, text, url="", image_data=b"", content_type="", image_alt="", idempotency_key=""):
        return {"url": "https://social.example/post/1"}


def _record(name, score, hours_ago):
    now = datetime.now(timezone.utc)
    # Anchor to the start of the UTC day: subtracting hours from "now" used
    # to move these records into yesterday whenever the suite ran before
    # 04:00 UTC, which made the best-of-day tests fail by wall-clock hour.
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "full_name": f"owner/{name}",
        "name": name,
        "score": score,
        "description": f"A project called {name}",
        "url": f"https://github.com/owner/{name}",
        "image_url": "",
        "published_at": (day_start + timedelta(minutes=hours_ago)).isoformat(),
        "crossposts": {
            "bluesky": {"status": "pending", "attempts": 0, "due_at": (now - timedelta(minutes=5)).isoformat()},
        },
    }


def _config():
    return Config(
        telegram_bot_token="x",
        feature_bluesky=True,
        bluesky_handle="o",
        bluesky_app_password="p",
    )


def _public_ip(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )


def test_download_bytes_follows_a_redirect(monkeypatch):
    _public_ip(monkeypatch)
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        if len(calls) == 1:
            response.status_code = 302
            response.headers["Location"] = "https://example.com/final.png"
        else:
            response.status_code = 200
            response._content = b"hello"
        response.headers["Content-Type"] = "image/png"
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", fake_get)
    data = http.download_bytes("https://example.com/start.png")
    assert data == b"hello"
    assert calls == ["https://example.com/start.png", "https://example.com/final.png"]


def test_download_bytes_refuses_private_redirect(monkeypatch):
    def fake_addrinfo(host, *args, **kwargs):
        if host == "127.0.0.1":
            return [(2, 1, 6, "", ("127.0.0.1", 443))]
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_addrinfo)
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        if len(calls) == 1:
            response.status_code = 302
            response.headers["Location"] = "https://127.0.0.1/final.png"
        else:
            response.status_code = 200
            response._content = b"secret"
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", fake_get)
    with pytest.raises(http.UnsafeUrl):
        http.download_bytes("https://example.com/start.png")


def test_later_higher_post_cannot_take_a_second_slot(monkeypatch, tmp_path):
    """Once a post has gone out today, the day's winner is fixed. A later,
    higher-scoring post must not take a second social slot."""
    monkeypatch.setattr(pipeline, "BlueskyClient", _FakeSocial)
    store = StateStore(tmp_path)
    store.data = {"published": {}}
    store.data["published"]["owner/first"] = _record("first", 50, 5)
    config = _config()

    pipeline.process_crossposts(config, store)
    assert store.data["published"]["owner/first"]["crossposts"]["bluesky"]["status"] == "published"

    # A later post with a higher score arrives; the day's slot is already used.
    store.data["published"]["owner/second"] = _record("second", 99, 1)
    pipeline.process_crossposts(config, store)
    assert store.data["published"]["owner/second"]["crossposts"]["bluesky"]["status"] == "held"
    assert store.data["published"]["owner/second"]["crossposts"]["bluesky"]["held_reason"] == "not_best_of_day"


def test_deterministic_fallback_rejects_dual_use():
    candidate = {
        "name": "Demo",
        "full_name": "owner/demo",
        "description": "A security tool",
        "readme": "A security tool. " * 60,
        "is_dual_use": True,
    }
    assert deterministic_project_content(candidate)["safety"] == "reject"


def test_rank_uses_the_full_readme_length():
    base = {
        "name": "Demo",
        "full_name": "owner/demo",
        "description": "A useful demo project",
        "license": {"spdx_id": "MIT"},
        "stars": 500,
        "forks": 20,
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2026-10-01T00:00:00Z",
        "topics": ["python"],
        "homepage": "https://example.com",
        "contributors_count": 3,
    }
    long_readme = score_candidate({**base, "readme_len": 14000, "readme_excerpt": "x" * 1200})
    short_readme = score_candidate({**base, "readme_len": 1200, "readme_excerpt": "x" * 1200})
    assert long_readme > short_readme


def test_moderation_ignores_service_senders():
    assert pipeline._moderation_ignored_sender({"is_bot": True})
    assert pipeline._moderation_ignored_sender({"is_automatic_forward": True})
    assert pipeline._moderation_ignored_sender({"id": 777000})
    assert not pipeline._moderation_ignored_sender({"id": 42})


def test_moderation_does_not_delete_channel_forwards(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    store.data = {"published": {}, "last_runs": {}}

    class FakeTelegram:
        def __init__(self, token):
            pass

        def api(self, method, payload=None):
            if method == "getUpdates":
                return [
                    {
                        "update_id": 1,
                        "message": {
                            "chat": {"id": -100, "type": "supergroup"},
                            "from": {"id": 777000, "is_automatic_forward": True},
                            "text": "https://a.example https://b.example https://c.example",
                        },
                    }
                ]
            if method == "getChatMember":
                return {"status": "member"}
            raise AssertionError(f"unexpected moderation call: {method}")

    monkeypatch.setattr(pipeline, "TelegramClient", FakeTelegram)
    config = Config(telegram_bot_token="x")
    result = pipeline.moderation_command(config, store)
    assert result["deleted"] == 0


def test_main_channel_falls_back_to_legacy_variable(monkeypatch):
    monkeypatch.setenv("MAIN_TELEGRAM_CHANNEL", "@GitHub_Open_Source")
    monkeypatch.delenv("TELEGRAM_MAIN_CHANNEL_ID", raising=False)
    config = Config.from_env()
    assert config.telegram_main_channel_id == "@GitHub_Open_Source"


def test_publish_keeps_candidate_when_gemini_unavailable(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    store.data = {
        "published": {},
        "seen_repos": {},
        "last_runs": {},
        "queue": [
            {
                "full_name": "owner/demo",
                "name": "Demo",
                "description": "A useful demo project",
                "license": {"spdx_id": "MIT"},
                "stars": 500,
                "forks": 20,
                "created_at": "2025-01-01T00:00:00Z",
                "pushed_at": "2026-10-01T00:00:00Z",
                "contributors_count": 3,
                "has_readme": True,
                "readme": "A useful demo project. " * 30,
                "topics": ["python"],
            }
        ],
    }
    config = Config(telegram_bot_token="x", telegram_test_channel_id=123)
    monkeypatch.setattr(pipeline, "TelegramClient", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "GeminiClient", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "GitHubApi", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "enrich_candidate", lambda api, candidate: candidate)
    monkeypatch.setattr(
        pipeline,
        "generate_project_content",
        lambda client, candidate: {"llm_used": False, "safety": "reject"},
    )
    result = pipeline.publish_slot(config, store, "manual")
    assert result["reason"] == "gemini_unavailable"
    assert len(store.data["queue"]) == 1
