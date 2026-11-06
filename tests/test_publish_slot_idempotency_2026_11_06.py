"""Regression: late crons and the three-a-day quota, 06.11 (evening).

Two owner-reported inconsistencies:

1. GitHub delivered the 10:13 Minsk ``europe_morning`` cron at 18:04, hours
   after the watchdog had already recovered that slot. The late cron published
   a second project and consumed the quota reserved for ``us_peak``.
2. ``daily_limit_reached`` was an honest refusal but never wrote
   ``last_runs.publish:<slot>``, so the watchdog kept treating
   ``publish:us_peak`` as missed and would have escalated to warnings every
   30 minutes until 03:00 Minsk.

Owner decision: slots are idempotent, and a quota-closed slot is a finished
slot. Manual dispatches stay exempt from the idempotency guard.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ghos_v6 import pipeline, watchdog
from ghos_v6.config import Config
from ghos_v6.state import StateStore

DAY = "2026-11-06"


def _config():
    return Config(telegram_bot_token="dummy-token",
                  telegram_test_channel_id=-1009999999999)


def _post(repo: str):
    return {"full_name": repo, "published_at": f"{DAY}T10:00:00Z", "score": 90}


def _store(tmp_path, count: int = 1) -> StateStore:
    store = StateStore(tmp_path)
    store.data["published"] = {f"owner/repo{i}": _post(f"owner/repo{i}")
                               for i in range(count)}
    return store


def _forbid_network(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("a completed slot must never reach a network client")

    for name in ("collect_command", "TelegramClient", "GeminiClient", "GitHubApi"):
        if hasattr(pipeline, name):
            monkeypatch.setattr(pipeline, name, forbidden)


def test_late_cron_cannot_repeat_a_recovered_slot(monkeypatch, tmp_path):
    _forbid_network(monkeypatch)
    store = _store(tmp_path, count=1)
    store.data["last_runs"]["publish:europe_morning"] = {
        "finished_at": f"{DAY}T11:14:35Z", "repo": "owner/repo0",
    }

    result = pipeline.publish_slot(_config(), store, "europe_morning")

    assert result["reason"] == "slot_already_done"
    assert result["published"] is False
    assert len(store.data["published"]) == 1, "no second post from the late cron"
    events = [__import__("json").loads(line) for line in
              (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert any(e.get("type") == "publish_skipped" and
               e.get("reason") == "slot_already_done" for e in events)


def test_yesterdays_marker_does_not_block_todays_slot(monkeypatch, tmp_path):
    _forbid_network(monkeypatch)
    store = _store(tmp_path, count=3)
    store.data["last_runs"]["publish:europe_morning"] = {
        "finished_at": "2026-11-05T07:13:00Z", "repo": "owner/old",
    }

    result = pipeline.publish_slot(_config(), store, "europe_morning")

    assert result["reason"] == "daily_limit_reached", "a fresh UTC day is not blocked"


def test_daily_limit_records_a_finished_slot(monkeypatch, tmp_path):
    _forbid_network(monkeypatch)
    store = _store(tmp_path, count=3)

    result = pipeline.publish_slot(_config(), store, "us_peak")

    assert result["reason"] == "daily_limit_reached"
    assert result["published_today"] == 3
    slot = store.data["last_runs"]["publish:us_peak"]
    assert slot["finished_at"].startswith(DAY)
    assert slot["reason"] == "daily_limit_reached"
    assert slot["published"] is False
    assert slot["published_today"] == 3


def test_manual_dispatch_is_exempt_from_the_slot_guard(monkeypatch, tmp_path):
    _forbid_network(monkeypatch)
    store = _store(tmp_path, count=3)
    store.data["last_runs"]["publish:europe_morning"] = {
        "finished_at": f"{DAY}T11:14:35Z", "repo": "owner/repo0",
    }

    result = pipeline.publish_slot(_config(), store, "manual")

    assert result["reason"] == "daily_limit_reached", "manual is not blocked by a slot marker"


def _wire(monkeypatch):
    calls: list[str] = []
    alerts: list[str] = []
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setattr(
        watchdog, "_dispatch",
        lambda repo, inputs, token: (calls.append(inputs["command"]), (True, "HTTP 204"))[1],
    )
    monkeypatch.setattr(watchdog, "_notify",
                        lambda config, text: alerts.append(text) or True)
    return calls, alerts


def test_watchdog_does_not_retry_a_quota_closed_slot(monkeypatch, tmp_path):
    calls, alerts = _wire(monkeypatch)
    store = _store(tmp_path, count=3)
    store.data["news_topics"] = [{"published_at": f"{DAY}T00:19:57Z"}]
    store.data["last_runs"].update({
        "collect": {"finished_at": f"{DAY}T05:30:00Z"},
        "publish:europe_morning": {"finished_at": f"{DAY}T11:14:35Z"},
        "publish:europe_midday": {"finished_at": f"{DAY}T13:31:27Z"},
        "article": {"finished_at": f"{DAY}T16:34:34Z"},
        "publish:us_peak": {"finished_at": f"{DAY}T15:04:45Z",
                            "reason": "daily_limit_reached", "published": False},
    })

    # 21:30 Minsk = 18:30 UTC: us_peak is past its deadline.
    result = watchdog.watchdog_command(
        Config(), store, now=datetime(2026, 11, 6, 18, 30, tzinfo=timezone.utc))

    assert "publish:us_peak" not in result["missed"]
    assert result["dispatched"] == []
    assert calls == [], "no blind retries after an honest quota refusal"
    assert not any("us_peak" in a for a in alerts)


def test_watchdog_still_detects_a_genuinely_missed_us_peak(monkeypatch, tmp_path):
    calls, _ = _wire(monkeypatch)
    store = _store(tmp_path, count=3)
    store.data["news_topics"] = [{"published_at": f"{DAY}T00:19:57Z"}]
    store.data["last_runs"].update({
        "collect": {"finished_at": f"{DAY}T05:30:00Z"},
        "publish:europe_morning": {"finished_at": f"{DAY}T11:14:35Z"},
        "publish:europe_midday": {"finished_at": f"{DAY}T13:31:27Z"},
        "article": {"finished_at": f"{DAY}T16:34:34Z"},
    })

    result = watchdog.watchdog_command(
        Config(), store, now=datetime(2026, 11, 6, 18, 30, tzinfo=timezone.utc))

    assert "publish:us_peak" in result["missed"]
    assert "publish" in calls
