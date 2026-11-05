"""Regression tests for the 05.11 TPM incident and the schedule watchdog."""
from __future__ import annotations

from datetime import datetime, timezone

from ghos_v6.config import Config
from ghos_v6.Gemini import GeminiClient, HttpError, classify_error
from ghos_v6.rate_limit import GeminiBudget, estimate_tokens
from ghos_v6.state import StateStore
from ghos_v6.watchdog import watchdog_command


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


def test_watchdog_is_idempotent_and_alerts_on_missed_slots(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setenv("GH_TOKEN", "")
    calls: list[str] = []

    def fake_dispatch(repo, inputs, token):
        calls.append(inputs["command"])
        return False, "no repo/token"

    monkeypatch.setattr("ghos_v6.watchdog._dispatch", fake_dispatch)
    monkeypatch.setattr("ghos_v6.watchdog.TelegramClient",
                        lambda token: type("T", (), {"send_text": lambda *a, **k: {}})())
    store = StateStore(tmp_path)
    now = datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc)
    first = watchdog_command(Config(), store, now=now)
    second = watchdog_command(Config(), store, now=now)
    assert first["missed"] == ["collect", "publish:europe_morning",
                               "publish:europe_midday", "publish:us_peak", "news"]
    assert len(calls) == 5       # one dispatch attempt per missed slot
    assert second["recovered"] == [] and second["failed"] == []
    assert calls == ["collect", "publish", "publish", "publish", "news"]


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
