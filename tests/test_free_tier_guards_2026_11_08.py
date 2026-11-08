"""Long-term free-tier guards (08.11 full audit).

Two failure modes could outspend a free plan without ever tripping the
per-minute Gemini limiter or the watchdog's 30-minute cooldown:
a slot that never confirms in state, retried forever, and a whole day of
Gemini calls with no daily (RPD) accounting. Both are bounded here.
"""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ghos_v6.config import Config
from ghos_v6.Gemini import GeminiClient
from ghos_v6.rate_limit import (
    FREE_RPD,
    MAX_DAILY_UNITS,
    UNITS_PER_CALL,
    GeminiDailyCap,
    GeminiQuotaGuard,
)
from ghos_v6.state import StateStore
from ghos_v6.watchdog import (
    MAX_ATTEMPTS_PER_SLOT,
    MAX_DAILY_DISPATCHES,
    watchdog_command,
)

REPO = Path(__file__).resolve().parents[1]


class _Store:
    def __init__(self):
        self.data = {}


def test_daily_allowance_is_conservative():
    assert FREE_RPD == 500
    assert MAX_DAILY_UNITS < FREE_RPD
    assert UNITS_PER_CALL >= 2, "one generation can make several HTTP POSTs"


def test_daily_cap_is_shared_across_separate_runs():
    store = _Store()
    moment = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    for _ in range(10):
        GeminiDailyCap(store, now=lambda: moment).consume()
    assert store.data["gemini_daily"]["units"] == 10 * UNITS_PER_CALL
    # A brand-new process (new cap object) must see the same total.
    GeminiDailyCap(store, now=lambda: moment).consume()
    assert store.data["gemini_daily"]["units"] == 11 * UNITS_PER_CALL


def test_daily_cap_resets_on_the_pacific_day():
    store = _Store()
    before = datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc)
    after = datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)
    GeminiDailyCap(store, now=lambda: before).consume()
    assert store.data["gemini_daily"]["units"] == UNITS_PER_CALL
    GeminiDailyCap(store, now=lambda: after).consume()
    assert store.data["gemini_daily"]["day"] != "2026-11-08"
    assert store.data["gemini_daily"]["units"] == UNITS_PER_CALL


def test_corrupt_counter_never_locks_the_bot_out():
    store = _Store()
    moment = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    store.data["gemini_daily"] = {"day": "2026-11-08", "units": 10**9}
    assert GeminiDailyCap(store, now=lambda: moment).usage["units"] == 0


def test_gemini_client_stops_before_spending_the_rpd(monkeypatch):
    store = _Store()
    moment = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)
    store.data["gemini_daily"] = {
        "day": GeminiDailyCap(store, now=lambda: moment).usage["day"],
        "units": MAX_DAILY_UNITS - 1,
    }
    monkeypatch.setattr(
        "ghos_v6.Gemini.request_json",
        lambda *a, **k: pytest.fail("no HTTP call may leave the process after the cap"),
    )
    client = GeminiClient("not-a-real-key", daily_cap=GeminiDailyCap(store, now=lambda: moment))
    with pytest.raises(GeminiQuotaGuard):
        client.generate_json("hello")


def test_every_scheduled_gemini_client_has_a_daily_cap():
    source = (REPO / "src/ghos_v6/pipeline.py").read_text(encoding="utf-8")
    assert source.count("GeminiClient(") == source.count("daily_cap=GeminiDailyCap(store)")
    # Owner 05.11: the AI Brief keeps a plain client, without the TPM window
    # or the 70-second heavy-call gap. The daily cap must not smuggle those in.
    news_block = source.split("client = GeminiClient(config.Gemini_api_key", 1)[1][:200]
    assert "budget=" not in news_block
    assert "fallback_model=" not in news_block


def _wire(monkeypatch):
    calls: list[str] = []
    sent: list[str] = []

    def fake_dispatch(repo, inputs, token):
        calls.append(inputs["command"])
        return True, "HTTP 204"

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setattr("ghos_v6.watchdog._dispatch", fake_dispatch)
    monkeypatch.setattr("ghos_v6.watchdog._notify",
                        lambda config, text: (sent.append(text), True)[1])
    return calls, sent


def test_watchdog_never_retries_a_slot_indefinitely(monkeypatch, tmp_path):
    calls, _sent = _wire(monkeypatch)
    store = StateStore(tmp_path)
    now = datetime(2026, 11, 8, 21, 0, tzinfo=timezone.utc)
    marks = store.data.setdefault("last_runs", {}).setdefault("watchdog_dispatches", {})
    marks["2026-11-08:news"] = {
        "at": (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
        "dispatched": True, "verified": False, "attempts": MAX_ATTEMPTS_PER_SLOT,
    }
    result = watchdog_command(Config(), store, now=now)
    assert "news" in result["missed"]
    assert "news" not in calls, "three unverified attempts is the hard ceiling"
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(e.get("type") == "watchdog_dispatch_capped" and e.get("scope") == "slot"
               for e in events)


def test_watchdog_has_a_daily_dispatch_ceiling(monkeypatch, tmp_path):
    calls, sent = _wire(monkeypatch)
    monkeypatch.setattr("ghos_v6.watchdog.MAX_DAILY_DISPATCHES", 2)
    store = StateStore(tmp_path)
    now = datetime(2026, 11, 8, 21, 0, tzinfo=timezone.utc)
    result = watchdog_command(Config(), store, now=now)
    assert len(calls) <= 2
    assert any("Рековери остановлено" in text for text in sent)
    assert len([text for text in sent if "Рековери остановлено" in text]) == 1
    # A second tick in the same day must not alert again.
    watchdog_command(Config(), store, now=now + timedelta(minutes=40))
    assert len([text for text in sent if "Рековери остановлено" in text]) == 1
    assert result["ok"] is True


def test_cap_constants_are_sane():
    import ghos_v6.watchdog as module
    assert MAX_ATTEMPTS_PER_SLOT == 3
    assert MAX_DAILY_DISPATCHES >= 10, "legitimate recovery must still work"
    source = inspect.getsource(module)
    assert "_dispatch_allowed" in source
    assert "MAX_DAILY_DISPATCHES" in source
