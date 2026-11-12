"""Owner 11.11: posts/articles were late by HOURS, not minutes.

Root cause found in the run logs: the minute-accurate Cloudflare Worker
dispatched every publishing command on time (start lag 0.0 min), but GitHub
delivered the SAME ``schedule`` events a second time 6-20 hours later. Those
late duplicates published into the next UTC day and consumed its 3-post quota
and one-brief marker. These regressions lock in the fix and the visibility
recommendations the owner approved in one go.
"""
from __future__ import annotations

import inspect
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ghos_v6 import cli, content_adapters as ca, news, pipeline, watchdog
from ghos_v6.config import Config
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _crons() -> set[str]:
    text = (REPO / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    return set(re.findall(r'cron:\s*["\']([^"\']+)["\']', text))


def test_every_workflow_cron_has_a_parseable_expected_run():
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    for schedule in _crons():
        assert cli._latest_expected_run(schedule, now) is not None, schedule


def test_cron_field_parser_handles_star_step_and_list():
    assert cli._cron_values("*", 0, 23)[:3] == [0, 1, 2]
    assert cli._cron_values("*/3", 0, 23) == [0, 3, 6, 9, 12, 15, 18, 21]
    assert cli._cron_values("0,18,19,20,21,22,23", 0, 23) == [0, 18, 19, 20, 21, 22, 23]
    assert cli._cron_values("garbage", 0, 23) == []


def test_late_minutes_match_the_real_incident():
    # 19:18 UTC cron arrived at 02:11 UTC the next day: 6 h 53 m late.
    late = cli._late_schedule_minutes(
        "18 19 * * *", datetime(2026, 11, 10, 2, 11, tzinfo=UTC))
    assert late is not None and 410 < late < 416
    # A one-minute jitter is still on time.
    assert cli._late_schedule_minutes(
        "18 19 * * *", datetime(2026, 11, 10, 19, 19, tzinfo=UTC)) == 1.0
    # 18:13Z midday slot, late-cron shape: still parseable.
    assert cli._late_schedule_minutes(
        "13 18 * * *", datetime(2026, 11, 10, 0, 44, tzinfo=UTC)) is not None
    # Moderation every three hours uses "*/3".
    assert cli._late_schedule_minutes(
        "47 */3 * * *", datetime(2026, 11, 11, 3, 48, tzinfo=UTC)) == 1.0
    # Weekly Sunday digest uses day-of-week 0.
    assert cli._latest_expected_run(
        "13 16 * * 0", datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    ) == datetime(2026, 11, 8, 16, 13, tzinfo=UTC)


def test_late_duplicate_cron_is_ignored_without_publishing(monkeypatch, tmp_path):
    calls: list[str] = []
    alerts: list[str] = []
    monkeypatch.setattr(cli, "news_command", lambda *a, **k: calls.append("news"))
    monkeypatch.setattr(cli, "alert_owner", lambda config, title, details="": alerts.append(title))
    store = StateStore(tmp_path)
    moment = datetime(2026, 11, 10, 2, 11, tzinfo=UTC)
    result = cli._run_scheduled(Config(), store, "18 19 * * *",
                                now=moment, enforce_window=True)
    assert result["reason"] == "late_schedule_ignored"
    assert result["minutes_late"] > 400
    assert calls == [], "the late duplicate must never reach the publisher"
    assert len(alerts) == 1
    health = store.data["schedule_health"]["2026-11-10"]
    assert health["late_ignored"] == 1
    assert health["last_schedule"] == "18 19 * * *"
    events = store.events_path.read_text(encoding="utf-8")
    assert "late_schedule_ignored" in events
    # Only ONE owner message per UTC day, even with a cascade of late crons.
    cli._run_scheduled(Config(), store, "23 16 * * *",
                       now=moment + timedelta(minutes=3), enforce_window=True)
    assert len(alerts) == 1
    assert store.data["schedule_health"]["2026-11-10"]["late_ignored"] == 2


def test_on_time_cron_still_runs_its_command(monkeypatch, tmp_path):
    seen: list[str] = []
    monkeypatch.setattr(cli, "_news_slot_open", lambda: True)
    monkeypatch.setattr(cli, "news_command", lambda config, store: (
        seen.append("news"), {"ok": True, "published": True})[1])
    result = cli._run_scheduled(Config(), StateStore(tmp_path), "18 19 * * *",
                                now=datetime(2026, 11, 10, 19, 19, tzinfo=UTC),
                                enforce_window=True)
    assert result["published"] is True and seen == ["news"]


def test_guard_is_enabled_by_the_workflow_event_name(monkeypatch, tmp_path):
    monkeypatch.setenv("EVENT_NAME", "schedule")
    monkeypatch.setattr(cli, "alert_owner", lambda *a, **k: None)
    result = cli._run_scheduled(Config(), StateStore(tmp_path), "23 16 * * *",
                                now=datetime(2026, 11, 10, 19, 59, tzinfo=UTC))
    assert result["reason"] == "late_schedule_ignored"


def test_late_facebook_cron_is_ignored_like_every_other_slot(monkeypatch, tmp_path):
    """Owner 12.11: Facebook stays enabled but is no longer guard-exempt."""
    seen: list[str] = []
    monkeypatch.setattr(cli, "facebook_handoff_command", lambda config, store: (
        seen.append("fb"), {"ok": True, "queued": 1})[1])
    monkeypatch.setattr(cli, "alert_owner", lambda *a, **k: None)
    store = StateStore(tmp_path)
    # 11.11 18:23Z cron actually arrived 12.11 00:53Z (+6.5 h).
    result = cli._run_scheduled(Config(), store, "23 18 * * *",
                                now=datetime(2026, 11, 12, 0, 53, tzinfo=UTC),
                                enforce_window=True)
    assert result["reason"] == "late_schedule_ignored"
    assert result["minutes_late"] > 300 and seen == []
    assert store.data["schedule_health"]["2026-11-12"]["late_ignored"] == 1
    assert cli.LATE_SCHEDULE_EXEMPT == frozenset({"53 18 * * 4"})


def test_thursday_close_learn_remains_exempt(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(cli, "close_poll_command",
                        lambda config, store: (calls.append("close"),
                                              {"ok": True, "closed": True})[1])
    monkeypatch.setattr(cli, "learn_command",
                        lambda config, store: (calls.append("learn"),
                                              {"ok": True, "updated": 1})[1])
    result = cli._run_scheduled(Config(), StateStore(tmp_path), "53 18 * * 4",
                                now=datetime(2026, 11, 12, 2, 0, tzinfo=UTC),
                                enforce_window=True)
    assert result["ok"] is True and calls == ["close", "learn"]


def test_skipped_brief_is_terminal_visible_and_not_retried(monkeypatch, tmp_path):
    alerts: list[str] = []
    monkeypatch.setattr(cli, "alert_owner",
                        lambda config, title, details="": alerts.append((title, details)))
    store = StateStore(tmp_path)
    moment = datetime(2026, 11, 11, 19, 19, tzinfo=UTC)
    result = {"ok": True, "published": False, "reason": "duplicate_digest",
              "removed": 5, "duplicate_ratio": 1.0, "stories": 0}
    assert cli._record_news_outcome(Config(), store, result, now=moment) is result
    marker = store.data["last_runs"]["news"]
    assert marker["reason"] == "duplicate_digest" and marker["published"] is False
    assert len(alerts) == 1 and "не вышел" in alerts[0][1]
    assert store.data["schedule_health"]["2026-11-11"]["skipped_brief"] == "duplicate_digest"
    assert store.data["brief_dedup_stats"][-1]["removed"] == 5
    # A second identical outcome must not alert again or retry blindly.
    cli._record_news_outcome(Config(), store, result, now=moment)
    assert len(alerts) == 1
    assert watchdog._has_run(store, "news", moment.date()) is True


def test_source_outage_stays_retryable(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "alert_owner", lambda *a, **k: None)
    store = StateStore(tmp_path)
    moment = datetime(2026, 11, 11, 19, 19, tzinfo=UTC)
    cli._record_news_outcome(
        Config(), store,
        {"ok": True, "published": False, "reason": "source_unavailable"}, now=moment)
    assert watchdog._has_run(store, "news", moment.date()) is False


def test_published_brief_result_is_left_alone(tmp_path):
    store = StateStore(tmp_path)
    result = {"ok": True, "published": True, "stories": 5}
    assert cli._record_news_outcome(Config(), store, result) is result
    assert "news" not in (store.data.get("last_runs") or {})


def test_published_brief_now_records_dedup_stats(tmp_path):
    """12.11: the weekly report must see a PUBLISHED brief's filter result."""
    store = StateStore(tmp_path)
    result = {"ok": True, "published": True, "stories": 3,
              "stories_removed": 1, "duplicate_ratio": 0.25}
    moment = datetime(2026, 11, 11, 19, 19, tzinfo=UTC)
    assert cli._record_news_outcome(Config(), store, result, now=moment) is result
    stats = store.data["brief_dedup_stats"]
    assert len(stats) == 1
    assert stats[-1]["removed"] == 1 and stats[-1]["kept"] == 3
    assert stats[-1]["published"] is True
    assert "news" not in (store.data.get("last_runs") or {})
    cli._record_news_outcome(Config(), store, result, now=moment)
    assert len(store.data["brief_dedup_stats"]) == 1, "exact repeat must not double count"


def test_watchdog_terminal_reasons_are_explicit():
    assert "duplicate_digest" in watchdog.NEWS_TERMINAL_REASONS
    assert "source_unavailable" not in watchdog.NEWS_TERMINAL_REASONS
    assert "published" in watchdog.NEWS_TERMINAL_REASONS


def test_normalizer_no_longer_flattens_h3_into_h2():
    body = ("## The model watch\n\n### GPT-6.2 Astra\n\nA concrete paragraph.\n\n"
            "## Rumours, clearly labelled\n\nNot confirmed.")
    cleaned = ca.normalize_article_markdown(body)
    assert "### GPT-6.2 Astra" in cleaned
    assert cleaned.count("### ") >= 1


def test_h3_structural_fallback_demotes_per_story_h2():
    body = ("> **TL;DR:** Three model drops.\n\n## The model watch\n\nDetails.\n\n"
            "## claude-fable-5-2\n\nMechanics.\n\n"
            "## Ripple-65B and Arete-27B\n\nBenchmarks.\n\n"
            "## Rumours, clearly labelled\n\nNot confirmed.")
    fixed = ca.ensure_h3_subheadings(body)
    assert len(re.findall(r"(?m)^###\s", fixed)) >= 2
    assert "### claude-fable-5-2" in fixed
    assert "## The model watch" in fixed
    # Idempotent: an article that already has H3 is not touched.
    assert ca.ensure_h3_subheadings(fixed) == fixed


def test_article_prompts_demand_h3_and_zero_corporate_items():
    assert "at least TWO ### H3" in news.NEWS_ARTICLE_STYLE
    assert "NO section at all: ZERO" in news.NEWS_ARTICLE_STYLE
    brief_prompt = inspect.getsource(news.synthesize_brief_telegram)
    assert "ZERO such items" in brief_prompt


def test_corporate_filing_stories_are_dropped_from_the_brief():
    brief = {"stories": [
        {"title": "Claude Fable 5.2", "status": "official_release"},
        {"title": "Anthropic amends S-1 filing", "status": "official_document"},
        {"title": "Trading via MCP", "status": "official_release"},
    ]}
    assert pipeline._drop_corporate_stories(brief) == 1
    assert [s["title"] for s in brief["stories"]] == ["Claude Fable 5.2", "Trading via MCP"]


def test_weekly_report_states_schedule_health_and_poll_signal(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    store.data["schedule_health"] = {"2026-11-11": {"late_ignored": 6,
                                                    "skipped_brief": "duplicate_digest"}}
    store.data["brief_dedup_stats"] = [{"date": "2026-11-11", "removed": 5,
                                        "ratio": 1.0, "kept": 0}]
    store.data["poll_history"] = [{"options": [{"votes": 2}, {"votes": 3}]}]
    result = pipeline.report_command(Config(), store)
    text = result["text"]
    assert "Late GitHub crons ignored: <b>6</b>" in text
    assert "duplicate_digest" in text
    assert "AI Brief de-dup: 1 run(s), 5 old stories removed" in text
    assert "insufficient signal" in text and "5/8 votes" in text


def test_article_slot_guarantees_h3_before_publishing(monkeypatch, tmp_path):
    captured: dict[str, str] = {}

    def fake_publish(self, title, body, *, tags=(), cover_url=""):
        captured["body"] = body
        return {"id": 5009999, "url": "https://dev.to/githubopensource/h3-1"}

    monkeypatch.setattr(pipeline, "_ensure_devto_cover", lambda *a: "https://i.ibb.co/cover.png")
    monkeypatch.setattr("ghos_v6.platforms.DevToClient.publish_markdown", fake_publish)
    monkeypatch.setenv("FEATURE_DEVTO", "true")
    monkeypatch.setenv("DEV_API_KEY", "test-dev-key")
    day = datetime.now(UTC).date().isoformat()
    store = StateStore(tmp_path)
    store.data["news_topics"] = [{
        "published_at": f"{day}T19:19:00Z",
        "headline": "Model watch",
        "article_title": "A real title",
        "article_markdown": ("> **TL;DR:** Three drops.\n\n## The model watch\n\nDetails.\n\n"
                             "## claude-fable-5-2\n\nMechanics.\n\n"
                             "## Ripple-65B\n\nBenchmarks.\n\n"
                             "## Rumours, clearly labelled\n\nNot confirmed."),
        "brief": {},
    }]
    result = pipeline.news_article_command(Config.from_env(), store)
    assert result["published"] is True
    assert len(re.findall(r"(?m)^###\s", captured["body"])) >= 2


def test_rollback_flag_is_explicit_in_repo_and_workflow():
    workflow = (REPO / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    example = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "FEATURE_TELEGRAM_DISCOVERY: ${{ vars.FEATURE_TELEGRAM_DISCOVERY }}" in workflow
    assert "FEATURE_TELEGRAM_DISCOVERY=true" in example
    # An unset GitHub variable arrives as an empty string: config must keep
    # the default True instead of silently flipping the feature off.
    import os
    old = os.environ.pop("FEATURE_TELEGRAM_DISCOVERY", None)
    try:
        assert Config.from_env().feature_telegram_discovery is True
    finally:
        if old is not None:
            os.environ["FEATURE_TELEGRAM_DISCOVERY"] = old
