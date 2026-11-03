"""Regression tests for the fourth audit (2026-11-02, round 4)."""
from __future__ import annotations


from ghos_v6 import dedup, pipeline, polls
from ghos_v6.config import Config
from ghos_v6.formatter import format_project_caption
from ghos_v6.state import StateStore
from ghos_v6.telegram_payload import build_buttons, build_send_message_payload


def test_dedup_matches_by_repo_id_after_rename():
    published = {
        "old/name": {
            "repo_id": 123,
            "description": "Old description of the project",
            "readme_fp": "",
        }
    }
    candidate = {
        "full_name": "new/name",
        "repo_id": 123,
        "description": "A completely different description",
    }
    result = dedup.check(candidate, {"published": published})
    assert result["verdict"] != "new"


def test_is_published_matches_by_repo_id(tmp_path):
    store = StateStore(tmp_path)
    store.data["published"]["old/name"] = {"repo_id": 123}
    assert store.is_published({"full_name": "new/name", "repo_id": 123})
    assert not store.is_published({"full_name": "other/name", "repo_id": 999})


def test_cli_returns_nonzero_on_failed_result(monkeypatch):
    from ghos_v6 import cli

    monkeypatch.setattr(
        cli.Config,
        "from_env",
        classmethod(lambda cls: Config(telegram_bot_token="x")),
    )
    monkeypatch.setattr(cli, "StateStore", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli,
        "publish_slot",
        lambda config, store, slot: {"ok": False, "reason": "gemini_unavailable"},
    )
    assert cli.main(["publish", "--slot", "manual"]) == 1


def test_cli_returns_zero_on_success(monkeypatch):
    from ghos_v6 import cli

    monkeypatch.setattr(
        cli.Config,
        "from_env",
        classmethod(lambda cls: Config(telegram_bot_token="x")),
    )
    monkeypatch.setattr(cli, "StateStore", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli,
        "publish_slot",
        lambda config, store, slot: {"ok": True, "published": False, "reason": "no_candidate"},
    )
    assert cli.main(["publish", "--slot", "manual"]) == 0


def test_invalid_homepage_url_is_not_a_button():
    buttons = build_buttons(
        github_url="https://github.com/owner/repo",
        homepage_url="https://exa mple.com",
    )
    labels = [btn["text"] for row in buttons for btn in row]
    assert "🌐 Homepage" not in labels
    assert "🔗 GitHub" in labels


def test_invalid_preview_url_disables_preview():
    payload = build_send_message_payload("@test", "text", preview_url="https://exa mple.com")
    assert payload["link_preview_options"] == {"is_disabled": True}


def test_caption_escapes_hook():
    project = {
        "name": "Demo",
        "hook": "<b>bold</b>",
        "description": "A useful demo project",
        "stars": 10,
        "forks": 2,
        "language": "Python",
        "license": {"spdx_id": "MIT"},
    }
    caption = format_project_caption(project)
    assert "<b>bold</b>" not in caption
    assert "&lt;b&gt;bold&lt;/b&gt;" in caption



def test_close_poll_resolves_masked_chat_id(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    store.data = {
        "published": {},
        "last_runs": {},
        "active_poll": {"chat_id": "test", "message_id": 1},
    }
    config = Config(telegram_bot_token="x", telegram_test_channel_id=123)
    monkeypatch.setattr(pipeline, "TelegramClient", lambda *args, **kwargs: None)
    captured = {}

    def fake_close(telegram, chat_id, message_id):
        captured["chat_id"] = chat_id
        return {"options": [], "total_voters": 0}

    monkeypatch.setattr(polls, "close_poll", fake_close)
    pipeline.close_poll_command(config, store)
    assert captured["chat_id"] == 123
