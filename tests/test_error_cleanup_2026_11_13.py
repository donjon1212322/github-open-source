"""Owner 13.11: seven failure emails on 12.11 were recoverable conditions.

Diagnosis from the Actions logs:

* five ``poll-close`` runs (18:53, 19:00, 20:00, 20:30, 23:54Z) returned
  ``stop_poll_failed`` because Telegram had ALREADY auto-closed poll 3574:
  its 96 h ``open_period`` expired at 18:09Z, 44 minutes before the Thursday
  close cron. The watchdog kept retrying a slot that could never succeed.
* two ``news`` runs (19:18, 20:00Z) died on a Gemini 503 "high demand" that
  escaped to cli.main -> exit 1 -> owner alert + GitHub failure email. The
  watchdog published the brief at 20:32Z and the article at 21:01Z anyway.
* every no-change run logged a bogus "Artifact not found" error annotation.

These regressions pin the fix: permanent poll states are honest no-ops,
transient model outages are quiet retryable skips, and real bugs still raise.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ghos_v6 import cli, pipeline, polls, watchdog
from ghos_v6.config import Config
from ghos_v6.http import HttpError
from ghos_v6.state import StateStore, iso_week_key
from ghos_v6.telegram import TelegramError

REPO = Path(__file__).resolve().parents[1]
UTC = timezone.utc


# --- weekly poll lifetime -------------------------------------------------

def test_weekly_poll_outlives_the_thursday_close_cron():
    # Sunday digest 16:13Z, Thursday poll-close 18:53Z. 96 h expired at
    # Thursday 16:13Z (too early); 120 h reaches Friday 16:13Z.
    sunday = datetime(2026, 11, 15, 16, 13, tzinfo=UTC)
    thursday_close = datetime(2026, 11, 19, 18, 53, tzinfo=UTC)
    assert sunday + timedelta(hours=polls.DEFAULT_WEEK_POLL_HOURS) > (
        thursday_close + timedelta(hours=1))
    assert polls.DEFAULT_WEEK_POLL_HOURS <= polls.MAX_POLL_DAYS * 24


def test_poll_already_closed_wording_is_recognised():
    assert pipeline._poll_already_closed(
        "Telegram stopPoll failed: Bad Request: poll can't be stopped")
    assert pipeline._poll_already_closed("poll has already been closed")
    assert not pipeline._poll_already_closed("Telegram stopPoll failed: network failure")
    assert not pipeline._poll_already_closed("Bad Request: message not found")


def _poll_store(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path)
    store.data["active_poll"] = {
        "message_id": 3574,
        "chat_id": "@GitHub_Open_Source",
        "question": "Which of these would save you time this week?",
        "option_keys": ["job_agent-skills", "job_avatar"],
        "repos": {},
    }
    return store


def test_already_closed_poll_finishes_the_week_without_a_failure(monkeypatch, tmp_path):
    store = _poll_store(tmp_path)

    def already_closed(*args, **kwargs):
        raise TelegramError(
            "Telegram stopPoll failed: Bad Request: poll can't be stopped",
            error_code=400)

    monkeypatch.setattr(polls, "close_poll", already_closed)
    config = Config(telegram_bot_token="dummy", telegram_test_channel_id=-1009999999999)

    result = pipeline.close_poll_command(config, store)

    assert result["ok"] is True
    assert result["closed"] is False
    assert result["reason"] == "poll_already_closed"
    marker = store.data["last_runs"]["poll-close"]
    assert marker["week"] == iso_week_key()
    assert marker["reason"] == "poll_already_closed"
    assert store.data["active_poll"] == {}, "a closed poll must not stay active"
    assert store.data.get("poll_history", []) == [], "never invent votes"
    events = [json.loads(line) for line in
              (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    assert any(e.get("type") == "poll_close_skipped" for e in events)
    # The ISO-week marker stops the watchdog and the late Thursday cron.
    again = pipeline.close_poll_command(config, store)
    assert again["reason"] == "poll-close_already_done"


def test_a_real_stop_poll_failure_is_still_visible(monkeypatch, tmp_path):
    store = _poll_store(tmp_path)

    def network_down(*args, **kwargs):
        raise TelegramError("Telegram stopPoll failed: network failure")

    monkeypatch.setattr(polls, "close_poll", network_down)
    config = Config(telegram_bot_token="dummy", telegram_test_channel_id=-1009999999999)

    result = pipeline.close_poll_command(config, store)

    assert result["ok"] is False
    assert result["reason"] == "stop_poll_failed"
    assert "poll-close" not in store.data.get("last_runs", {}), (
        "a genuine failure must stay retryable")
    assert store.data["active_poll"]["message_id"] == 3574


# --- transient Gemini outages --------------------------------------------

def _brief_config() -> Config:
    return Config(telegram_bot_token="dummy", telegram_test_channel_id=-1009999999999,
                  Gemini_api_key="dummy", feature_ai_brief=True,
                  feature_telegram_discovery=True)


def _wire_brief(monkeypatch, store: StateStore, exc: BaseException) -> None:
    monkeypatch.setattr(
        pipeline, "build_digest_material",
        lambda exclude_keys=None: {
            "entry": {"video_id": "tg-20261112-test"},
            "primary": {},
            "clusters": [{"key": "model-releases"}],
            "rumours": [],
        })

    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(pipeline, "synthesize_brief_telegram", boom)


def test_high_demand_gemini_is_a_quiet_retryable_skip(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    _wire_brief(monkeypatch, store, HttpError(
        503, "Gemini generateContent failed: This model is currently "
             "experiencing high demand. Spikes in demand are usually temporary."))

    result = pipeline.news_command(_brief_config(), store)

    assert result["ok"] is True, "a transient outage must not fail the workflow"
    assert result["published"] is False
    assert result["reason"] == "brief_gemini_unavailable"
    assert store.data.get("news_topics", []) == []
    assert "news" not in store.data.get("last_runs", {}), (
        "no marker: the watchdog must recover the slot later")
    events = [json.loads(line) for line in
              (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
              if line.strip()]
    assert any(e.get("type") == "news_skipped" and
               e.get("reason") == "brief_gemini_unavailable" for e in events)


def test_a_non_transient_gemini_error_still_raises(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    _wire_brief(monkeypatch, store, HttpError(400, "invalid responseSchema"))

    with pytest.raises(HttpError):
        pipeline.news_command(_brief_config(), store)


def test_companion_article_rebuild_is_quiet_on_high_demand(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    today = datetime.now(UTC).date().isoformat()
    store.data["news_topics"] = [{
        "published_at": f"{today}T20:32:00Z",
        "brief": {"stories": [{"name": "Gemini 4"}]},
    }]

    def boom(*args, **kwargs):
        raise HttpError(503, "high demand")

    monkeypatch.setattr(pipeline, "synthesize_news_article", boom)
    config = Config(dev_api_key="dummy", Gemini_api_key="dummy", feature_devto=True)

    result = pipeline.news_article_command(config, store)

    assert result["ok"] is True
    assert result["published"] is False
    assert result["reason"] == "article_gemini_unavailable"
    assert "news-article" not in store.data.get("last_runs", {})


def test_devto_transient_publish_is_quiet_not_a_failed_workflow(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    today = datetime.now(UTC).date().isoformat()
    store.data["news_topics"] = [{
        "published_at": f"{today}T20:32:00Z",
        "article_markdown": "## A heading\n\nSome long read text.",
        "article_title": "Model watch",
        "brief": {"stories": []},
    }]
    monkeypatch.setattr(pipeline, "_ensure_devto_cover", lambda *a, **k: "")

    class DownDevTo:
        def __init__(self, *args, **kwargs):
            pass

        def publish_markdown(self, *args, **kwargs):
            raise HttpError(503, "Service temporarily unavailable")

    monkeypatch.setattr(pipeline, "DevToClient", DownDevTo)
    config = Config(dev_api_key="dummy", feature_devto=True)

    result = pipeline.news_article_command(config, store)

    assert result["ok"] is True
    assert result["reason"] == "article_gemini_unavailable"
    assert "news-article" not in store.data.get("last_runs", {})


def test_skip_reasons_are_retryable_and_silent():
    assert pipeline.BRIEF_GEMINI_SKIP not in watchdog.NEWS_TERMINAL_REASONS
    assert pipeline.BRIEF_GEMINI_SKIP not in cli.NEWS_ALERT_REASONS
    assert pipeline.ARTICLE_GEMINI_SKIP not in watchdog.NEWS_TERMINAL_REASONS


# --- workflow: no bogus artifact error ------------------------------------

def test_commit_state_checks_the_artifact_not_a_fragile_job_output():
    """14.11: a failed `run` job may expose no `needs.run.outputs`.

    The state writer must ALWAYS start, then ask the Artifacts API whether
    an upload really happened. Only then may it download and commit. Both
    guarded steps must keep the condition (count == 2), and the lookup needs
    `actions: read` permission.
    """
    text = (REPO / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    assert "needs.run.outputs.state_changed" not in text
    assert "if: always()\n    runs-on: ubuntu-latest" in text
    guard = "if: ${{ always() && steps.state_artifact.outputs.found == 'true' }}"
    assert text.count(guard) == 2
    assert "id: state_artifact" in text
    assert "actions: read" in text
    assert "GITHUB_RUN_ID" in text
    assert "/artifacts?per_page=100" in text
    assert "found = True" in text, "API failure must fall back to a download"
