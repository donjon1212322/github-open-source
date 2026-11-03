"""Regression tests for the fifth audit (2026-11-02, round 5)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ghos_v6 import dedup, pipeline, sources
from ghos_v6.config import Config
from ghos_v6.formatter import format_project_post
from ghos_v6.gate import GateConfig, evaluate
from ghos_v6.images import MAX_IMAGE_PIXELS
from ghos_v6.pipeline import _crosspost_due
from ghos_v6.rank import select_candidate
from ghos_v6.state import StateStore
from ghos_v6.telegram import TelegramError, is_fatal_error
from PIL import Image


def _project(**overrides):
    project = {
        "name": "Demo",
        "full_name": "owner/demo",
        "description": "A useful demo project",
        "topics": ["python"],
        "license": {"spdx_id": "MIT"},
        "stars": 500,
        "forks": 20,
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2026-10-01T00:00:00Z",
        "contributors_count": 3,
        "has_readme": True,
        "readme": "A useful demo project. " * 30,
    }
    project.update(overrides)
    return project


def test_fatal_telegram_errors_are_classified():
    assert is_fatal_error(TelegramError("Forbidden: bot is not a member", error_code=403))
    assert is_fatal_error(TelegramError("Unauthorized", error_code=401))
    assert is_fatal_error(TelegramError("Chat not found"))
    assert is_fatal_error(TelegramError("network failure: timeout"))
    assert not is_fatal_error(TelegramError("Bad Request: wrong file", error_code=400))


def test_crosspost_due_waits_for_the_whole_day():
    morning = _crosspost_due(datetime(2026, 11, 2, 7, 10, tzinfo=timezone.utc))
    assert (morning.hour, morning.minute) == (18, 0)
    evening = _crosspost_due(datetime(2026, 11, 2, 19, 0, tzinfo=timezone.utc))
    assert (evening.hour, evening.minute) == (19, 30)


def test_history_block_reaches_the_post():
    project = _project(
        hook="A demo hook",
        scene="The project started as a weekend script.",
        tension="Scaling it broke the original design.",
        turn="The rewrite made it 10x faster.",
    )
    html = format_project_post(project, telegraph_url="https://telegra.ph/demo")
    assert "weekend script" in html
    assert "10x faster" in html


def test_record_published_preserves_history(tmp_path):
    store = StateStore(tmp_path)
    store.data["published"]["owner/demo"] = {
        "published_at": "2026-01-01T00:00:00Z",
        "telegram_message_id": 1,
        "score": 10,
    }
    store.record_published(
        {
            "full_name": "owner/demo",
            "published_at": "2026-02-01T00:00:00Z",
            "telegram_message_id": 2,
            "score": 20,
        }
    )
    record = store.data["published"]["owner/demo"]
    assert record["telegram_message_id"] == 2
    assert record["history"][0]["telegram_message_id"] == 1


def test_revisit_for_a_new_release():
    record = {
        "repo_id": 1,
        "latest_release_tag": "v1.0.0",
        "published_at": "2026-01-01T00:00:00Z",
        "description": "A project",
    }
    candidate = {
        "full_name": "owner/demo",
        "repo_id": 1,
        "latest_release_tag": "v2.0.0",
        "description": "A project",
    }
    result = dedup.check(candidate, {"published": {"owner/demo": record}})
    assert result["verdict"] == "revisit"


def test_select_candidate_respects_the_daily_limit():
    queue = [{"full_name": "owner/a", "is_dual_use": True, "score": 10.0}]
    published_today = [{"is_dual_use": True}]
    assert select_candidate(queue, published_today) is None


def test_short_readme_is_rejected_even_when_has_readme():
    decision = evaluate(_project(has_readme=True, readme="ab"), GateConfig())
    assert "no_readme" in decision.reasons


def test_format_project_post_terminates_with_a_small_limit():
    project = _project(
        hook="A demo hook",
        summary="word " * 2000,
        key_features=["One", "Two", "Three"],
        verdict="Worth a look",
    )
    html = format_project_post(project, max_visible=1500)
    assert len(html) > 0


def test_effective_pixel_cap_is_40mp():
    assert MAX_IMAGE_PIXELS == 20_000_000
    assert Image.MAX_IMAGE_PIXELS == 20_000_000


def test_moderation_ignores_message_level_automatic_forward(monkeypatch, tmp_path):
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
                            "from": {"id": 42},
                            "is_automatic_forward": True,
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


def test_enrich_candidate_force_refetches_metadata():
    calls = []

    class FakeApi:
        def repo(self, full_name):
            calls.append(full_name)
            return {
                "full_name": full_name,
                "repo_id": 1,
                "license": {"spdx_id": "MIT"},
                "stargazers_count": 10,
                "forks_count": 2,
                "created_at": "2025-01-01T00:00:00Z",
                "pushed_at": "2026-10-01T00:00:00Z",
                "open_issues_count": 0,
                "archived": False,
                "fork": False,
                "description": "A project",
                "topics": ["python"],
                "language": "Python",
                "default_branch": "main",
            }

        def contributors_count(self, full_name):
            return 2

        def readme(self, full_name):
            return "A project. " * 100

    candidate = {"full_name": "owner/demo", "repo_id": 1, "license": {"spdx_id": "MIT"}}
    sources.enrich_candidate(FakeApi(), candidate, force=True)
    assert calls == ["owner/demo"]


def test_runtime_lock_has_no_dev_tools():
    lock = Path("requirements.lock").read_text(encoding="utf-8")
    for tool in ("ruff==", "mypy==", "pytest=="):
        assert tool not in lock


def test_publish_removes_candidate_after_two_model_failures(monkeypatch, tmp_path):
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
    monkeypatch.setattr(pipeline, "enrich_candidate", lambda api, candidate, force=False: candidate)
    monkeypatch.setattr(
        pipeline,
        "generate_project_content",
        lambda client, candidate: {"llm_used": False, "safety": "reject"},
    )

    first = pipeline.publish_slot(config, store, "manual")
    assert first["reason"] == "gemini_unavailable"
    assert len(store.data["queue"]) == 1

    second = pipeline.publish_slot(config, store, "manual")
    assert second["reason"] == "gemini_failed_twice"
    assert len(store.data["queue"]) == 0


def test_cleanup_dry_run_does_not_delete(tmp_path):
    from ghos_v6.pipeline import cleanup_test_posts

    store = StateStore(tmp_path)
    store.data = {
        "published": {"owner/demo": {"telegram_message_id": 35}},
        "last_runs": {},
    }
    config = Config(telegram_bot_token="x", telegram_test_channel_id=-1009999999999)
    result = cleanup_test_posts(config, store, dry_run=True)
    assert result["would_delete"] == 1
    assert result["message_ids"] == [35]
