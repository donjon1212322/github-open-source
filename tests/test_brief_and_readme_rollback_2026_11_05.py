"""Owner rollback (2026-11-05): the AI Brief keeps its original Gemini path.

The owner reported no problems with YouTube video input, so the shared TPM
budget, the 70 s heavy-call gap and the 503 spare-model fallback must NOT
apply to ``news_command()``. The README prompt limits also return to 10K/20K.
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from ghos_v6 import pipeline
from ghos_v6.Gemini import GeminiClient, _readme_for_prompt
from ghos_v6.news import NewsSourceError
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]


def _news_config() -> SimpleNamespace:
    return SimpleNamespace(
        feature_ai_brief=True,
        require=lambda *a: None,
        telegram_bot_token="x",
        Gemini_api_key="x",
        Gemini_model="gemini-3.5-flash-lite",
        telegram_chat_id="@GitHub_Open_Source",
        telegram_owner_chat_id=None,
    )


def test_readme_prompt_limits_are_restored() -> None:
    assert inspect.signature(_readme_for_prompt).parameters["limit"].default == 10_000
    source = (REPO / "src" / "ghos_v6" / "pipeline.py").read_text(encoding="utf-8")
    start = source.index("def publish_slot(")
    body = source[start:source.index("\ndef news_command(", start)]
    assert '[:20_000]' in body
    assert '[:6_000]' not in body


def test_news_command_uses_the_plain_original_client(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    class _FakeClient:
        def __init__(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr(pipeline, "GeminiClient", _FakeClient)
    monkeypatch.setattr(pipeline, "TelegramClient", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "alert_owner", lambda *a, **k: None)
    monkeypatch.setattr(
        pipeline,
        "fetch_worldofai_entries",
        lambda: (_ for _ in ()).throw(NewsSourceError("stop after client construction")),
    )

    config = _news_config()
    result = pipeline.news_command(config, StateStore(tmp_path))

    assert result["reason"] == "source_unavailable"
    assert captured["args"] == (config.Gemini_api_key, config.Gemini_model)
    # 08.11 free-tier audit: the ONLY allowed extra is the daily RPD counter.
    # The owner-approved plain client still has no TPM window, no 70 s heavy
    # gap, no spare-model fallback and no retry overrides.
    kwargs = captured["kwargs"]
    assert set(kwargs) == {"daily_cap"}
    assert type(kwargs["daily_cap"]).__name__ == "GeminiDailyCap"
    assert all(key not in kwargs for key in
               ("budget", "fallback_model", "http_retries", "retry_post"))


def test_plain_client_keeps_the_original_http_retry_policy() -> None:
    client = GeminiClient("dummy")
    assert client.http_retries == 1
    assert client.retry_post is True
    assert client.budget is None
    assert client.fallback_model is None


def test_publish_slot_still_uses_the_shared_budget_and_fallback() -> None:
    source = (REPO / "src" / "ghos_v6" / "pipeline.py").read_text(encoding="utf-8")
    start = source.index("def publish_slot(")
    body = source[start:source.index("\ndef news_command(", start)]
    assert "GeminiBudget(store)" in body
    assert 'fallback_model="gemini-3.1-flash-lite"' in body
    assert "http_retries=0" in body and "retry_post=False" in body
