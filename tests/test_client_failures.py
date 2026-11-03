"""Fake-client integration tests: external failures must be loud, never 'ok'.

These exercise the real code paths with network replaced by deterministic
fakes, including the Doctor health check that decides whether the pipeline
is allowed to publish at all.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ghos_v6 import http, telegram_payload
from ghos_v6.Gemini import GeminiClient
from ghos_v6 import doctor
from ghos_v6.config import Config


class _Response:
    def __init__(self, status: int, payload: dict, headers: dict | None = None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.content = b"{}"
        self.text = "{}"

    def json(self) -> dict:
        return self._payload


def test_publish_message_rejects_a_failed_telegram_response():
    calls: list[tuple[str, dict]] = []

    def fake_post(url: str, payload: dict) -> dict:
        calls.append((url, payload))
        return {"ok": False, "description": "Bad Request: chat not found"}

    with pytest.raises(RuntimeError, match="Telegram sendMessage failed"):
        telegram_payload.publish_message("dummy", {"text": "x"}, post_fn=fake_post)
    assert len(calls) == 1


def test_publish_message_returns_the_message_and_never_uses_an_empty_token():
    def fake_post(url: str, payload: dict) -> dict:
        return {"ok": True, "result": {"message_id": 42}}

    result = telegram_payload.publish_message("dummy", {"text": "x"}, post_fn=fake_post)
    assert result["message_id"] == 42

    def no_network(url: str, payload: dict) -> dict:
        raise AssertionError("network must not be called without a token")

    with pytest.raises(RuntimeError, match="not set"):
        telegram_payload.publish_message("   ", {"text": "x"}, post_fn=no_network)


def test_error_text_is_sanitized_before_it_can_reach_logs(monkeypatch):
    # Built at runtime: the repository must never contain a literal string
    # shaped like a real credential, or the CI secret scanner trips on tests.
    token = "123456:" + "A" * 35
    pat_token = "github_" + "pat_" + "11" + "A" * 30
    pem = "-----BEGIN " + "PRIVATE KEY-----secret" + "-----END " + "PRIVATE KEY-----"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    assert token not in http.sanitize(f"boom {token}")
    assert "[REDACTED]" in http.sanitize(f"boom {token}")
    assert "github_" not in http.sanitize(pat_token)
    assert '"private_key": "[REDACTED]"' in http.sanitize(
        '{"private_key": "' + pem + '"}'
    )


def test_request_json_retries_only_idempotent_methods_by_default(monkeypatch):
    calls: list[str] = []

    def fake_request(method: str, url: str, **kwargs: object) -> _Response:
        calls.append(method)
        return _Response(429 if len(calls) == 1 else 200, {"ok": True})

    monkeypatch.setattr(http.requests, "request", fake_request)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)

    status, data, _ = http.request_json("GET", "https://example.com/x", retries=2)
    assert (status, data) == (200, {"ok": True})
    assert len(calls) == 2

    calls.clear()
    status, _, _ = http.request_json("POST", "https://example.com/x", retries=2)
    assert status == 429
    assert len(calls) == 1, "POST must not be retried unless retry_post is set"


def test_network_exhaustion_raises_a_sanitized_http_error(monkeypatch):
    def failing_request(method: str, url: str, **kwargs: object):
        raise http.requests.RequestException("connection refused")

    monkeypatch.setattr(http.requests, "request", failing_request)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)
    with pytest.raises(http.HttpError, match="network error"):
        http.request_json("GET", "https://example.com/x", retries=1)


def test_gemini_lists_only_models_that_support_generate_content(monkeypatch):
    seen: dict[str, object] = {}

    def fake_request_json(method: str, url: str, **kwargs: object):
        seen["url"] = url
        return 200, {
            "models": [
                {"name": "models/gemini-3.5-flash-lite",
                 "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embedding-001",
                 "supportedGenerationMethods": ["embedContent"]},
            ]
        }, None

    monkeypatch.setattr("ghos_v6.Gemini.request_json", fake_request_json)
    status, models = GeminiClient("dummy").list_models()
    assert status == 200
    assert models == ["models/gemini-3.5-flash-lite"]
    assert seen["url"].endswith("/models")


class _FakeTelegram:
    def get_me(self) -> dict:
        return {"id": 1, "username": "owner_bot", "first_name": "Owner bot"}

    def discover_ids(self) -> dict:
        return {"chats": []}

    def get_chat(self, chat_id: object) -> dict:
        return {"id": chat_id, "title": "Test channel"}

    def api(self, method: str, payload: dict | None = None) -> dict:
        return {"status": "administrator"}

    def send_text(self, chat_id: object, text: str, **kwargs: object) -> dict:
        return {"message_id": 7}


class _FakeGemini:
    def __init__(self, available: str):
        self.available = available

    def list_models(self) -> tuple[int, list[dict]]:
        return 200, [{"name": f"models/{self.available}"}]

    def generate_json(self, prompt: str, **kwargs: object) -> dict:
        return {"status": "ok"}


class _FakeGitHubApi:
    def __init__(self, token: str):
        self.token = token

    def search(self, query: str, per_page: int = 3) -> list[dict]:
        return [{"full_name": f"owner/repo{i}"} for i in range(per_page)]


def _patch_doctor(monkeypatch, available_model: str) -> None:
    monkeypatch.setattr(doctor, "TelegramClient", lambda _token: _FakeTelegram())
    monkeypatch.setattr(
        doctor, "GeminiClient", lambda _key, _model: _FakeGemini(available_model)
    )
    monkeypatch.setattr(doctor, "GitHubApi", _FakeGitHubApi)
    monkeypatch.setattr(
        doctor, "fetch_worldofai_entries",
        lambda: [{"video_id": "v1", "title": "Launch", "url": "https://youtu.be/v1"}],
    )

    def fake_render(candidate, generated, path: Path, **kwargs):
        path.write_bytes(b"png" * 1024)
        return path, "Editorial Daily"

    monkeypatch.setattr(doctor, "render_project_card", fake_render)


def test_doctor_is_green_when_the_configured_model_is_available(monkeypatch):
    _patch_doctor(monkeypatch, "gemini-3.5-flash-lite")
    report = doctor.run_doctor(Config(GitHub_token="dummy", GitHub_repository="a/b"))
    assert report["ok"] is True
    assert report["target_locked_test"] is True
    assert report["checks"]["Gemini_models"]["model_available"] is True
    assert report["checks"]["GitHub_search"]["ok"] is True
    assert report["checks"]["card_render"]["ok"] is True


def test_doctor_fails_loudly_when_the_configured_model_was_retired(monkeypatch):
    _patch_doctor(monkeypatch, "gemini-9.0-ultra")
    report = doctor.run_doctor(Config(GitHub_token="dummy", GitHub_repository="a/b"))
    assert report["ok"] is False
    assert report["checks"]["Gemini_models"]["model_available"] is False
    assert report["checks"]["Gemini_model_lifecycle"]["ok"] is False
