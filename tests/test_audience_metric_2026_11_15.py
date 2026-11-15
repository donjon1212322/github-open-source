"""Owner 15.11: fix ONE honest success metric and read real reactions.

From /home/user/VIEWS_AND_CADENCE_REASONING.md the owner selected two of the
three follow-ups: (1) define views@24h per post + daily sum as THE metric,
(2) stop recording zero reactions. Item 3 (public poll feedback) was left to
the agent and deliberately NOT implemented: with 5-7 votes and the owner's
"show nothing below 8 votes" answer it could never appear, and it would break
the 08.11 rule "poll results go only to the owner report".

Nothing here changes cadence, slots, MIN_SIGNAL_VOTES, public post text,
polls, Worker, secrets or variables.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path

from ghos_v6 import insights, pipeline
from ghos_v6.config import Config
from ghos_v6.polls_design import MIN_SIGNAL_VOTES
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]
UTC = timezone.utc
NOW = datetime(2026, 11, 14, 20, 23, tzinfo=UTC)


# --- 1. Real reaction chips are counted, not silently zeroed ---------------

EMBED_WITH_REACTIONS = '''<div class="tgme_widget_message_reactions js-message_reactions">
<span class="tgme_reaction"><i class="emoji" style="background-image:url('//telegram.org/img/emoji/40/E29DA4.png')"><b>❤</b></i>1</span>
<span class="tgme_reaction"><i class="emoji"><b>🔥</b></i>2</span></div>
<span class="tgme_widget_message_views">122</span>'''
EMBED_NO_REACTIONS = '<span class="tgme_widget_message_views">44</span>'
EMBED_LEGACY_CHIP = '<span class="tgme_reaction"> 3 </span><span class="tgme_widget_message_views">55</span>'


class _Response:
    def __init__(self, text: str):
        self.status_code = 200
        self.text = text


def _fake_get(text: str):
    return lambda *args, **kwargs: _Response(text)


def test_fetch_post_stats_counts_the_real_embed_reaction_chips(monkeypatch):
    monkeypatch.setattr(insights.requests, "get", _fake_get(EMBED_WITH_REACTIONS))
    stats = insights.fetch_post_stats("GitHub_Open_Source", 3592)
    assert stats["ok"] is True
    assert stats["views"] == 122
    assert stats["reactions"] == 3


def test_fetch_post_stats_handles_legacy_chip_and_no_reactions(monkeypatch):
    monkeypatch.setattr(insights.requests, "get", _fake_get(EMBED_LEGACY_CHIP))
    assert insights.fetch_post_stats("GitHub_Open_Source", 1)["reactions"] == 3
    monkeypatch.setattr(insights.requests, "get", _fake_get(EMBED_NO_REACTIONS))
    assert insights.fetch_post_stats("GitHub_Open_Source", 2)["reactions"] == 0


def test_metrics_command_persists_the_reaction_count(monkeypatch, tmp_path):
    monkeypatch.setattr(
        insights, "fetch_post_stats",
        lambda channel, message_id, **k: {
            "ok": True, "views": 101, "reactions": 2,
            "fetched_at": "2026-11-14T20:23:00Z",
        },
    )
    monkeypatch.setattr(insights, "age_hours", lambda ts: 24.0)
    monkeypatch.setattr(insights, "views_at_reference", lambda views, age: float(views))
    monkeypatch.setattr(insights, "median_views", lambda records: 100.0)
    monkeypatch.setattr(insights, "value_score", lambda record, median_views: 1.0)
    monkeypatch.setattr(pipeline, "_channel_handle", lambda config: "GitHub_Open_Source")
    store = StateStore(tmp_path)
    store.data["published"] = {"owner/repo": {
        "telegram_message_id": 3596,
        "published_at": "2026-11-14T07:14:00Z",
    }}
    result = pipeline.metrics_command(Config(), store, now=NOW)
    assert result["updated"] == 1
    record = store.data["published"]["owner/repo"]
    assert record["reactions"] == 2
    assert record["views_history"][-1]["reactions"] == 2


# --- 2. The one fixed, age-normalised success metric ----------------------

def _records() -> list[dict]:
    records = []
    for day, value in (("2026-11-05", 60), ("2026-11-06", 50),
                       ("2026-11-12", 70), ("2026-11-13", 80),
                       ("2026-11-14", 90)):
        for i in range(3):
            records.append({
                "published_at": f"{day}T0{i + 1}:00:00+00:00",
                "views_at_24h": value,
            })
    # An unmeasured post must be excluded, never counted as zero views.
    records.append({"published_at": "2026-11-14T09:00:00+00:00"})
    return records


def test_daily_view_metric_uses_age_normalised_values_and_two_windows():
    metric = insights.daily_view_metric(_records(), now=NOW)
    assert metric["posts"] == 9          # the unmeasured post is excluded
    assert metric["days"] == 3
    assert metric["per_post"] == 80.0
    assert metric["daily_sum"] == 240.0
    assert metric["previous_per_post"] == 55.0
    assert metric["previous_daily_sum"] == 165.0
    assert metric["verdict"] == "higher"
    assert metric["per_post_change"] > 5
    assert metric["daily_sum_change"] > 5


def test_daily_view_metric_refuses_to_call_noise_a_trend():
    records = []
    for day, value in (("2026-11-06", 70), ("2026-11-13", 71)):
        for i in range(3):
            records.append({
                "published_at": f"{day}T0{i + 1}:00:00+00:00",
                "views_at_24h": value,
            })
    metric = insights.daily_view_metric(records, now=NOW)
    assert metric["verdict"] == "about the same"
    assert abs(metric["per_post_change"]) <= 5


def test_daily_view_metric_is_honest_when_there_is_no_history():
    records = [{"published_at": "2026-11-14T01:00:00+00:00", "views_at_24h": 82}]
    metric = insights.daily_view_metric(records, now=NOW)
    assert metric["per_post"] == 82.0
    assert metric["verdict"] == "not enough history"
    empty = insights.daily_view_metric([], now=NOW)
    assert empty["per_post"] is None
    assert empty["daily_sum"] is None


def test_daily_view_metric_never_reads_the_raw_view_counter():
    source = inspect.getsource(insights.daily_view_metric)
    assert "views_at_24h" in source
    assert 'record.get("views")' not in source
    assert "median(" in source


# --- 3. Report shows exactly one headline metric, privately ---------------

FIXED_METRIC = {
    "posts": 21, "days": 7, "per_post": 82.0, "daily_sum": 242.0,
    "previous_posts": 21, "previous_days": 7,
    "previous_per_post": 68.0, "previous_daily_sum": 210.0,
    "per_post_change": 20.6, "daily_sum_change": 15.2,
    "verdict": "higher",
}


def test_weekly_report_shows_the_one_fixed_metric(monkeypatch, tmp_path):
    monkeypatch.setattr(insights, "daily_view_metric", lambda records: FIXED_METRIC)
    store = StateStore(tmp_path)
    text = pipeline.report_command(Config(), store)["text"]
    assert "<b>Audience metric (last 7 days)</b>" in text
    assert "Views@24h per post: <b>82</b>" in text
    assert "sum per day: <b>242</b>" in text
    assert "<b>higher</b>" in text
    assert "Previous 7 days: 68 / 210" in text
    # The verdict stays in the private owner report; no public post changed.
    assert "Audience metric" not in inspect.getsource(pipeline.digest_text)


def test_owner_rules_fix_the_metric_definition():
    text = (REPO / "docs" / "OWNER_RULES.md").read_text(encoding="utf-8").lower()
    assert "просмотры за сутки на пост" in text
    assert "сумма за день" in text
    assert "сырые просмотры" in text


# --- 4. Item 3 deliberately NOT implemented (owner 15.11) -----------------

def test_poll_feedback_stays_private_and_signal_threshold_unchanged():
    assert MIN_SIGNAL_VOTES == 8
    digest = inspect.getsource(pipeline.digest_text)
    assert "result_line" not in digest
    assert "last_result" not in digest
    assert "profile_result" not in digest
    close = inspect.getsource(pipeline.close_poll_command)
    assert "send_text" not in close       # no new public feedback post
    assert "send_owner" not in close      # and no duplicate owner alert
