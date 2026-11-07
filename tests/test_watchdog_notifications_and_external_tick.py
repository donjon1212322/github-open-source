"""Regression tests for the 06.11 fix: no crying wolf, external clock entry.

Two false alarms reached the owner on 06.11: a dispatch that completed three
minutes later was announced as a scary "missed slot", and no closing message
ever arrived. GitHub also dropped entire batches of ``schedule`` events, so the
workflow now accepts a ``repository_dispatch`` tick from a free external clock
and has a second independent cron minute.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ghos_v6.config import Config
from ghos_v6.state import StateStore
from ghos_v6.watchdog import watchdog_command

REPO = Path(__file__).resolve().parents[1]
WD = REPO / ".github" / "workflows" / "watchdog.yml"


def _wire(monkeypatch, ok: bool = True):
    calls: list[str] = []
    sent: list[str] = []

    def fake_dispatch(repo, inputs, token):
        calls.append(inputs["command"])
        return ok, ("HTTP 204" if ok else "HTTP 500")

    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setattr("ghos_v6.watchdog._dispatch", fake_dispatch)
    monkeypatch.setattr("ghos_v6.watchdog._notify",
                        lambda config, text: (sent.append(text), True)[1])
    return calls, sent


def test_first_successful_dispatch_is_silent(monkeypatch, tmp_path):
    calls, sent = _wire(monkeypatch)
    store = StateStore(tmp_path)
    result = watchdog_command(Config(), store, now=datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc))
    assert len(calls) == 5                      # article needs a project today
    assert result["failed"] == []
    assert sent == [], "an accepted HTTP 204 is not yet a problem to report"
    assert result["alerts"] == []


def test_state_confirmation_sends_a_closing_message(monkeypatch, tmp_path):
    calls, sent = _wire(monkeypatch)
    store = StateStore(tmp_path)
    watchdog_command(Config(), store, now=datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc))
    assert sent == []

    store.data["last_runs"]["collect"] = {"finished_at": "2026-11-05T21:05:00Z"}
    second = watchdog_command(Config(), store, now=datetime(2026, 11, 5, 21, 11, tzinfo=timezone.utc))

    assert "collect" in second["verified"]
    assert len(sent) == 1 and "Слот восстановлен" in sent[0]
    assert "больше действий не требуется" in sent[0]


def test_failed_dispatch_alerts_immediately(monkeypatch, tmp_path):
    _calls, sent = _wire(monkeypatch, ok=False)
    store = StateStore(tmp_path)
    result = watchdog_command(Config(), store, now=datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc))
    assert len(result["failed"]) == 5
    assert len(sent) == 5
    assert all("не удался" in text and "Пропущен слот" in text for text in sent)


def test_second_unverified_attempt_alerts(monkeypatch, tmp_path):
    calls, sent = _wire(monkeypatch)
    store = StateStore(tmp_path)
    store.data["published"] = {"owner/repo": {"published_at": "2026-11-05T10:00:00Z"}}
    first = watchdog_command(Config(), store, now=datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc))
    assert "article" in first["dispatched"]
    assert sent == [], "the first attempt stays quiet until state can confirm"

    watchdog_command(Config(), store, now=datetime(2026, 11, 5, 15, 1, tzinfo=timezone.utc))
    assert any("Повторная попытка №2" in text for text in sent)
    assert calls.count("article") == 2


def test_no_alarm_is_sent_for_a_slot_that_already_ran(monkeypatch, tmp_path):
    _calls, sent = _wire(monkeypatch)
    store = StateStore(tmp_path)
    store.data["last_runs"]["collect"] = {"finished_at": "2026-11-05T06:00:00Z"}
    result = watchdog_command(Config(), store, now=datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc))
    assert "collect" not in result["missed"]
    assert not any("collect" in text for text in sent)


def test_watchdog_accepts_an_external_tick_and_has_a_redundant_cron():
    text = WD.read_text(encoding="utf-8")
    assert "repository_dispatch:" in text
    assert "watchdog_tick" in text
    assert 'cron: "27 */2 * * *"' in text
    assert 'cron: "57 */2 * * *"' in text, "second independent schedule minute"
    assert "queue: max" in text, "external ticks must not evict pending runs"
    assert "group: state-writer" in text


def test_external_tick_recipe_is_complete_and_secret_free():
    base = REPO / "tools" / "external_tick"
    names = ("README.md", "worker.js", "wrangler.toml.example")
    for name in names:
        assert (base / name).is_file(), name
    blob = "\n".join((base / n).read_text(encoding="utf-8") for n in names)
    assert "github_pat_" not in blob, "never commit a token"
    assert "watchdog_tick" in blob
    assert "/dispatches" in blob
    assert "GH_PAT" in blob and "GH_REPO" in blob
    assert "wrangler secret put" in blob
    assert "*/30" in blob, "the currently deployed 30-minute clock is documented"
    assert "* * * * *" in blob, "minute-accurate clock is prepared in the repo"
    assert "workflow_dispatch" in blob
    assert "SLOTS" in blob
    assert "NO retries" in blob


def test_operations_doc_uses_the_current_ai_brief_cron():
    text = (REPO / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    assert "18 19 * * *" in text or "22:18" in text
    assert "10 19 * * *" not in text, "stale AI Brief cron was documented for weeks"


def test_watchdog_result_reports_alerts_for_logs(monkeypatch, tmp_path):
    _calls, _sent = _wire(monkeypatch, ok=False)
    store = StateStore(tmp_path)
    result = watchdog_command(Config(), store, now=datetime(2026, 11, 5, 21, 0, tzinfo=timezone.utc))
    assert isinstance(result["alerts"], list) and result["alerts"]
    assert json.dumps(result, ensure_ascii=False)
