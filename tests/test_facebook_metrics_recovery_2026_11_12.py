"""Owner 12.11: "да сделай нужные правки включая фейсбук".

The 11.11 health check found three holes: the Facebook hand-off waited 6.5 h
for a late native GitHub cron; one daily views snapshot vanished because its
dispatch never got a runner; and the de-dup counter ignored PUBLISHED briefs.
These regressions lock in the idempotency markers, watchdog recovery slots and
honest statistics while Facebook stays enabled and Make/Sheet stay untouched.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ghos_v6 import cli, pipeline, watchdog
from ghos_v6 import content_adapters as ca, sheets
from ghos_v6.config import Config
from ghos_v6.state import StateStore

UTC = timezone.utc
MOMENT = datetime(2026, 11, 12, 18, 30, tzinfo=UTC)


def _fb_config() -> Config:
    return Config(
        app_env="production",
        publish_target="main",
        telegram_main_channel_id="@GitHub_Open_Source",
        feature_sheets_handoff=True,
        sheets_spreadsheet_id="sheet",
        sheets_credentials_json="{}",
    )


def _wire_fb(monkeypatch) -> list[int]:
    appends: list[int] = []
    monkeypatch.setattr(
        sheets, "append_rows",
        lambda *a, **k: (appends.append(1),
                         {"ok": True, "appended": 1, "range": "facebook!A1"})[1],
    )
    monkeypatch.setattr(sheets, "row_for", lambda *a, **k: ["row"])
    monkeypatch.setattr(ca, "build_Facebook_text", lambda record, generated: "text")
    monkeypatch.setattr(ca, "facebook_media_url", lambda record: "")
    return appends


def _today_record(day: str) -> dict[str, dict[str, object]]:
    return {"owner/repo": {
        "published_at": f"{day}T17:19:00Z",
        "score": 90,
        "full_name": "owner/repo",
        "generated": {},
    }}


def test_facebook_handoff_is_idempotent_per_utc_day(monkeypatch, tmp_path):
    appends = _wire_fb(monkeypatch)
    store = StateStore(tmp_path)
    store.data["published"] = _today_record(MOMENT.date().isoformat())

    first = pipeline.facebook_handoff_command(_fb_config(), store, now=MOMENT)
    assert first["queued"] == 1
    assert len(appends) == 1
    marker = store.data["last_runs"]["facebook-handoff"]
    assert marker["reason"] == "queued" and marker["queued"] == 1
    assert watchdog._has_run(store, "facebook-handoff", MOMENT.date()) is True

    # A late native cron, the watchdog dispatch or a manual retry in the same
    # UTC day must never append a second Sheet row.
    second = pipeline.facebook_handoff_command(_fb_config(), store, now=MOMENT)
    assert second == {"ok": True, "queued": 0, "reason": "already_done"}
    assert len(appends) == 1


def test_facebook_nothing_pending_stays_retryable_later_the_same_day(monkeypatch, tmp_path):
    appends = _wire_fb(monkeypatch)
    store = StateStore(tmp_path)
    first = pipeline.facebook_handoff_command(_fb_config(), store, now=MOMENT)
    assert first["reason"] == "nothing_pending"
    assert store.data["last_runs"]["facebook-handoff"]["reason"] == "nothing_pending"

    # A project published after the empty slot must still reach the Sheet.
    store.data["published"] = _today_record(MOMENT.date().isoformat())
    second = pipeline.facebook_handoff_command(_fb_config(), store, now=MOMENT)
    assert second["queued"] == 1 and len(appends) == 1
    assert store.data["last_runs"]["facebook-handoff"]["reason"] == "queued"


def test_facebook_feature_flag_is_still_a_no_op(monkeypatch, tmp_path):
    _wire_fb(monkeypatch)
    store = StateStore(tmp_path)
    result = pipeline.facebook_handoff_command(Config(), store, now=MOMENT)
    assert result == {"ok": True, "queued": 0, "reason": "feature_disabled"}
    assert "facebook-handoff" not in (store.data.get("last_runs") or {})


def _wire_metrics(monkeypatch, ok: bool) -> None:
    monkeypatch.setattr(pipeline, "_channel_handle",
                        lambda config: "GitHub_Open_Source")
    monkeypatch.setattr(
        pipeline.insights, "fetch_post_stats",
        lambda channel, message_id, **k: (
            {"ok": True, "views": 101, "reactions": 0,
             "fetched_at": "2026-11-12T20:23:00Z"} if ok else {"ok": False}
        ),
    )
    monkeypatch.setattr(pipeline.insights, "age_hours", lambda ts: 24.0)
    monkeypatch.setattr(pipeline.insights, "views_at_reference",
                        lambda views, age: float(views))
    monkeypatch.setattr(pipeline.insights, "median_views", lambda records: 100.0)
    monkeypatch.setattr(pipeline.insights, "value_score",
                        lambda record, median_views: 1.0)


def _metrics_store(tmp_path) -> StateStore:
    store = StateStore(tmp_path)
    store.data["published"] = {"owner/repo": {
        "telegram_message_id": 3584,
        "published_at": "2026-11-12T07:14:00Z",
    }}
    return store


def test_metrics_marker_is_written_only_after_a_real_measurement(monkeypatch, tmp_path):
    _wire_metrics(monkeypatch, ok=True)
    store = _metrics_store(tmp_path)
    moment = datetime(2026, 11, 12, 20, 30, tzinfo=UTC)
    result = pipeline.metrics_command(Config(), store, now=moment)
    assert result["updated"] == 1
    assert store.data["last_runs"]["metrics"]["reason"] == "measured"
    assert watchdog._has_run(store, "metrics", moment.date()) is True


def test_metrics_failed_fetch_leaves_the_slot_recoverable(monkeypatch, tmp_path):
    _wire_metrics(monkeypatch, ok=False)
    store = _metrics_store(tmp_path)
    moment = datetime(2026, 11, 12, 20, 30, tzinfo=UTC)
    result = pipeline.metrics_command(Config(), store, now=moment)
    assert result["updated"] == 0
    assert "metrics" not in (store.data.get("last_runs") or {})
    assert watchdog._has_run(store, "metrics", moment.date()) is False


def test_watchdog_recovers_facebook_and_metrics_without_a_worker_redeploy():
    slots = {key: (deadline, inputs) for key, deadline, inputs in watchdog.SLOTS}
    assert slots["facebook-handoff"] == ((18, 30), {"command": "facebook-handoff"})
    assert slots["metrics"] == ((20, 30), {"command": "metrics"})
    assert "23 18 * * *" not in cli.LATE_SCHEDULE_EXEMPT
    assert cli.LATE_SCHEDULE_EXEMPT == frozenset({"53 18 * * 4"})


def test_weekly_report_sees_a_published_brief_dedup_result(tmp_path):
    store = StateStore(tmp_path)
    moment = datetime(2026, 11, 11, 19, 19, tzinfo=UTC)
    cli._record_news_outcome(
        Config(), store,
        {"ok": True, "published": True, "stories": 3,
         "stories_removed": 1, "duplicate_ratio": 0.25},
        now=moment,
    )
    text = pipeline.report_command(Config(), store)["text"]
    assert "AI Brief de-dup: 1 run(s), 1 old stories removed" in text
