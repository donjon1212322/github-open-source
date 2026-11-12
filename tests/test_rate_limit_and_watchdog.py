"""Regression tests for the 05.11 TPM incident and the schedule watchdog.

Extended 06.11: an accepted ``workflow_dispatch`` (HTTP 204) is not proof of
execution, ``article`` is a monitored slot, and ``queue: max`` keeps pending
runs from being evicted by the shared ``state-writer`` group.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ghos_v6.config import Config
from ghos_v6.Gemini import GeminiClient, HttpError, classify_error
from ghos_v6.rate_limit import GeminiBudget, estimate_tokens
from ghos_v6.state import StateStore
from ghos_v6.watchdog import watchdog_command

REPO = Path(__file__).resolve().parents[1]


class _Store:
    def __init__(self):
        self.data = {}


def test_429_and_503_are_classified_separately():
    assert classify_error(HttpError(429, "You exceeded your current quota")) == "quota_429"
    assert classify_error(HttpError(503, "This model is currently experiencing high demand")) == "capacity_503"
    assert classify_error(ValueError("bad json")) == "other"


def test_video_prompt_is_estimated_as_heavy():
    assert estimate_tokens("hello", "https://youtu.be/x") > 100_000
    assert estimate_tokens("hello") < 10


def test_budget_sleeps_until_the_window_frees_up():
    clock = [1000.0]
    sleeps: list[float] = []
    def advance(delta):
        sleeps.append(delta)
        clock[0] += delta
    store = _Store()
    budget = GeminiBudget(store, sleep=advance, now=lambda: clock[0],
                          max_tokens=100, max_requests=2)
    budget.acquire(60)
    clock[0] += 1.0
    budget.acquire(60)  # exceeds 100 tokens in the same minute -> must wait
    assert sleeps and sleeps[0] > 0
    # The first entry aged out of the rolling minute, so only the new one remains.
    assert len(store.data["gemini_window"]) == 1


def test_heavy_calls_are_separated_by_70_seconds():
    clock = [1000.0]
    sleeps: list[float] = []
    def advance(delta):
        sleeps.append(delta)
        clock[0] += delta
    budget = GeminiBudget(_Store(), sleep=advance, now=lambda: clock[0],
                          max_tokens=200_000)
    budget.acquire(120_000, heavy=True)
    budget.acquire(120_000, heavy=True)
    assert sleeps == [70.0]


def _wire(monkeypatch):
    calls: list[str] = []

    def fake_dispatch(repo, inputs, token):
        calls.append(inputs["command"])
        return True, "HTTP 204"

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setattr("ghos_v6.watchdog._dispatch", fake_dispatch)
    monkeypatch.setattr("ghos_v6.watchdog.TelegramClient",
                        lambda token: type("T", (), {"send_text": lambda *a, **k: {}})())
    return calls


def test_watchdog_is_idempotent_and_alerts_on_missed_slots(monkeypatch, tmp_path):
    calls = _wire(monkeypatch)
    store = StateStore(tmp_path)
    now = datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc)
    first = watchdog_command(Config(), store, now=now)
    second = watchdog_command(Config(), store, now=now)
    # 05.11 is Thursday, so the guarded weekly poll-close is also recovered.
    assert first["missed"] == ["collect", "publish:europe_morning",
                               "publish:europe_midday", "publish:us_peak",
                               "facebook-handoff", "news", "metrics", "poll-close"]
    assert len(calls) == 8       # article needs a project; news-article waits for a brief
    assert second["dispatched"] == [] and second["failed"] == []
    assert calls == ["collect", "publish", "publish", "publish",
                     "facebook-handoff", "news", "metrics", "poll-close"]


def test_http_204_is_not_verified_without_state_confirmation(monkeypatch, tmp_path):
    calls = _wire(monkeypatch)
    store = StateStore(tmp_path)
    store.data["published"] = {
        "owner/repo": {"published_at": "2026-11-05T10:00:00Z"}
    }
    now = datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)
    result = watchdog_command(Config(), store, now=now)

    assert "article" in result["missed"]
    assert "article" in calls
    mark = store.data["last_runs"]["watchdog_dispatches"]["2026-11-05:article"]
    assert mark["dispatched"] is True
    assert mark["verified"] is False, "HTTP 204 alone must not retire the slot"
    assert mark["attempts"] == 1
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(e.get("type") == "watchdog_recovered" and e.get("slot") == "article"
               and e.get("verified") is False for e in events)


def test_unverified_dispatch_retries_only_after_cooldown(monkeypatch, tmp_path):
    calls = _wire(monkeypatch)
    store = StateStore(tmp_path)
    store.data["published"] = {
        "owner/repo": {"published_at": "2026-11-05T10:00:00Z"}
    }
    first_now = datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)
    watchdog_command(Config(), store, now=first_now)

    too_soon = datetime(2026, 11, 5, 14, 40, tzinfo=timezone.utc)
    watchdog_command(Config(), store, now=too_soon)
    assert calls.count("article") == 1, "no blind retry inside the 30-minute cooldown"

    later = datetime(2026, 11, 5, 15, 1, tzinfo=timezone.utc)
    watchdog_command(Config(), store, now=later)
    assert calls.count("article") == 2
    mark = store.data["last_runs"]["watchdog_dispatches"]["2026-11-05:article"]
    assert mark["attempts"] == 2 and mark["verified"] is False


def test_state_confirmation_marks_the_slot_verified(monkeypatch, tmp_path):
    _wire(monkeypatch)
    store = StateStore(tmp_path)
    store.data["published"] = {
        "owner/repo": {"published_at": "2026-11-05T10:00:00Z"}
    }
    watchdog_command(Config(), store, now=datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc))

    # The pipeline really published the article and the other slots finished.
    store.data["published"]["owner/repo"]["articles"] = {
        "devto": {"published_at": "2026-11-05T14:45:00Z"}
    }
    for key in ("collect", "publish:europe_morning", "publish:europe_midday"):
        store.data["last_runs"][key] = {"finished_at": "2026-11-05T14:40:00Z"}

    second = watchdog_command(Config(), store, now=datetime(2026, 11, 5, 15, 1, tzinfo=timezone.utc))
    mark = store.data["last_runs"]["watchdog_dispatches"]["2026-11-05:article"]
    assert mark["verified"] is True and mark.get("verified_at")
    assert "article" in second["verified"]
    assert second["dispatched"] == []
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(e.get("type") == "watchdog_verified" and e.get("slot") == "article" for e in events)


def test_article_is_not_dispatched_without_a_project_today(monkeypatch, tmp_path):
    calls = _wire(monkeypatch)
    store = StateStore(tmp_path)
    result = watchdog_command(Config(), store, now=datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc))
    assert "article" not in result["missed"]
    assert "article" not in calls, "article_of_day() would only say no_projects_today"


def test_article_recovery_window_does_not_backfill_a_stale_day(monkeypatch, tmp_path):
    calls = _wire(monkeypatch)
    store = StateStore(tmp_path)
    store.data["published"] = {
        "owner/repo": {"published_at": "2026-11-05T10:00:00Z"}
    }
    result = watchdog_command(Config(), store, now=datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc))
    assert "article" not in result["missed"]
    assert "article" not in calls


def test_stale_markers_are_cleaned_and_persisted(monkeypatch, tmp_path):
    _wire(monkeypatch)
    store = StateStore(tmp_path)
    store.data["last_runs"] = {"watchdog_dispatches": {
        "2026-11-05:publish:europe_midday": {
            "at": "2026-11-05T23:44:21Z", "ok": True,
        }
    }}
    store.save()
    watchdog_command(Config(), store, now=datetime(2026, 11, 6, 2, 37, tzinfo=timezone.utc))
    reread = StateStore(tmp_path).data["last_runs"]["watchdog_dispatches"]
    assert "2026-11-05:publish:europe_midday" not in reread


def test_concurrency_keeps_pending_runs_instead_of_evicting_them():
    for name in ("pipeline.yml", "watchdog.yml"):
        text = (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert "group: state-writer" in text
        assert "cancel-in-progress: false" in text
        assert "queue: max" in text, f"{name} must not evict a pending dispatch"


def test_capacity_503_falls_back_to_the_spare_model(monkeypatch):
    import ghos_v6.Gemini as G

    calls: list[str] = []

    def fake_request(method, url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return 503, {"error": {"message":
                "This model is currently experiencing high demand. Please try again later."}}, None
        return 200, {"candidates": [{"content": {"parts": [
            {"text": '{"status": "ok"}'}]}}]}, None

    monkeypatch.setattr(G, "request_json", fake_request)
    client = GeminiClient("key", "gemini-3.5-flash-lite",
                          fallback_model="gemini-3.1-flash-lite")
    assert client.generate_json("hi") == {"status": "ok"}
    assert len(calls) == 2
    assert "gemini-3.1-flash-lite" in calls[1]
