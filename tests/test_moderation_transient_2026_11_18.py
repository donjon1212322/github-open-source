"""Owner 18.11: the 00:47Z moderation run failed the whole workflow.

Actions run 44210707855 (18.11 00:47:32Z, workflow_dispatch) exited 1 and
emailed the owner because Telegram answered ``502 Bad Gateway`` to
``getUpdates``. The next run 25 minutes later read the queue fine, so the
outage was temporary. Moderation is read-only housekeeping on a cron: a
transient Telegram outage must be a quiet retryable skip (same policy as
the Gemini 503 fix from 13.11), never a failed workflow and an owner email.
A real error (401 Unauthorized) must still raise and stay visible.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ghos_v6 import pipeline
from ghos_v6.config import Config
from ghos_v6.state import StateStore
from ghos_v6.telegram import TelegramError

SPAM_TEXT = "crypto giveaway https://a.example https://b.example https://c.example"


class _DownTelegram:
    """getUpdates fails with the given error; nothing else is called."""

    def __init__(self, token, error):
        self.error = error

    def api(self, method, payload=None):
        assert method == "getUpdates", f"unexpected call: {method}"
        raise self.error


class _FlakyTelegram:
    """One normal batch (with spam), then getUpdates goes down."""

    def __init__(self, error):
        self.error = error
        self.calls = 0
        self.deleted: list[int] = []

    def api(self, method, payload=None):
        if method == "getUpdates":
            self.calls += 1
            if self.calls == 1:
                # A full 100-update batch: the loop must come back for a
                # second read, and that second read is the one that fails.
                batch = [
                    {
                        "update_id": i,
                        "message": {
                            "chat": {"id": -100, "type": "supergroup"},
                            "from": {"id": 42},
                            "message_id": i,
                            "text": "plain chat message",
                        },
                    }
                    for i in range(1, 100)
                ]
                batch.append(
                    {
                        "update_id": 100,
                        "message": {
                            "chat": {"id": -100, "type": "supergroup"},
                            "from": {"id": 42},
                            "message_id": 1000,
                            "text": SPAM_TEXT,
                        },
                    }
                )
                return batch
            raise self.error
        if method == "getChatMember":
            return {"status": "member"}
        raise AssertionError(f"unexpected moderation call: {method}")

    def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)


def _events(tmp_path: Path) -> list[dict]:
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_bad_gateway_getupdates_is_a_quiet_retryable_skip(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    store.data = {"published": {}, "last_runs": {}}
    monkeypatch.setattr(
        pipeline, "TelegramClient",
        lambda token: _DownTelegram(
            token, TelegramError("Telegram getUpdates failed: Bad Gateway",
                                 error_code=502)))

    result = pipeline.moderation_command(Config(telegram_bot_token="x"), store)

    assert result["ok"] is True, "a transient outage must not fail the workflow"
    assert result["reason"] == "moderation_telegram_unavailable"
    assert result["deleted"] == 0
    assert any(e.get("type") == "moderation_skipped" for e in _events(tmp_path))


def test_network_failure_getupdates_is_a_quiet_retryable_skip(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    store.data = {"published": {}, "last_runs": {}}
    monkeypatch.setattr(
        pipeline, "TelegramClient",
        lambda token: _DownTelegram(
            token,
            TelegramError("Telegram getUpdates network failure: ConnectionError")))

    result = pipeline.moderation_command(Config(telegram_bot_token="x"), store)

    assert result["ok"] is True
    assert result["reason"] == "moderation_telegram_unavailable"
    assert any(e.get("type") == "moderation_skipped" for e in _events(tmp_path))


def test_an_unauthorized_moderation_error_still_raises(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    store.data = {"published": {}, "last_runs": {}}
    monkeypatch.setattr(
        pipeline, "TelegramClient",
        lambda token: _DownTelegram(
            token, TelegramError("Telegram getUpdates failed: Unauthorized",
                                 error_code=401)))

    with pytest.raises(TelegramError):
        pipeline.moderation_command(Config(telegram_bot_token="x"), store)
    assert not any(e.get("type") == "moderation_skipped" for e in _events(tmp_path))


def test_a_500_without_matching_text_is_still_retryable(monkeypatch, tmp_path):
    # "Internal Server Error" matches none of the wording checks; only the
    # 5xx status code itself marks this outage as temporary.
    store = StateStore(tmp_path)
    store.data = {"published": {}, "last_runs": {}}
    monkeypatch.setattr(
        pipeline, "TelegramClient",
        lambda token: _DownTelegram(
            token, TelegramError("Telegram getUpdates failed: Internal Server Error",
                                 error_code=500)))

    result = pipeline.moderation_command(Config(telegram_bot_token="x"), store)

    assert result["ok"] is True
    assert result["reason"] == "moderation_telegram_unavailable"
    assert any(e.get("type") == "moderation_skipped" for e in _events(tmp_path))


def test_a_mid_queue_outage_keeps_the_offset_and_the_deletion(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    store.data = {"published": {}, "last_runs": {}}
    flaky = _FlakyTelegram(
        TelegramError("Telegram getUpdates failed: Bad Gateway", error_code=502))
    monkeypatch.setattr(pipeline, "TelegramClient", lambda token: flaky)

    result = pipeline.moderation_command(Config(telegram_bot_token="x"), store)

    assert result["ok"] is True
    assert result["deleted"] == 1
    assert result["reason"] == "moderation_telegram_unavailable"
    assert flaky.deleted == [1000], "the spam message before the outage is deleted"
    # The processed batch must not be read again on the next run.
    assert store.data["last_runs"]["telegram_update_offset"] == 100
