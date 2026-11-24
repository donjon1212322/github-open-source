"""Owner 24.11: even spacing, and Sunday belongs to the digest.

Measured complaint, not a hunch: the Telegram posts came in a three-hour block
(16:23, 18:13, 19:18) and on Sunday the digest+ poll landed **twelve minutes**
before a project post, which is why the poll collected two votes from 51 views.

The schedule now:

    01:13Z  project (20:13 ET / 17:13 PT)  - the strongest slot, untouched
    15:13Z  project (10:13 ET / 07:13 PT)  - was 16:23
    18:13Z  midday  - publish six days a week, the digest+ poll on Sunday
    21:48Z  AI Brief                        - was 19:18
    22:23Z  second dev.to article           - was 19:53

Gaps between Telegram-visible posts are 3 h 00 and 3 h 05 instead of 1 h 50
and 1 h 05. Sunday loses the midday project post instead of gaining a fifth
post, so the poll gets three quiet hours around it.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from ghos_v6 import cli, pipeline, watchdog
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]
UTC = timezone.utc

TELEGRAM_MINUTES = ("01:13", "15:13", "18:13", "21:48")


def _worker_minutes() -> dict[str, str]:
    src = (REPO / "tools" / "external_tick" / "worker.js").read_text(encoding="utf-8")
    found = {}
    for minute, body in re.findall(r'\["(\d\d:\d\d)", (\{[^}]*\})', src):
        command = re.search(r'command: "([a-z-]+)"', body)
        if command:
            found[minute] = command.group(1)
    return found


def _workflow_crons() -> set[str]:
    src = (REPO / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    return set(re.findall(r'cron:\s*"([^"]+)"', src))


CRON_RE = re.compile(r"^[\d*/,\- ]+$")


def _cli_routes() -> set[str]:
    """Every cron string cli.py can route, in either lookup table."""
    src = (REPO / "src" / "ghos_v6" / "cli.py").read_text(encoding="utf-8")
    keys = set(re.findall(r'"([0-9][^"]{4,})"\s*:', src))
    return {key for key in keys if CRON_RE.match(key) and " " in key}


# --- the three places must agree -------------------------------------------

def test_every_cron_has_a_route_and_every_route_has_a_cron():
    assert _workflow_crons() == _cli_routes()


def test_the_worker_fires_the_same_minutes_as_the_github_crons():
    worker = _worker_minutes()
    crons = _workflow_crons()
    expected = {
        "00:23": "23 0 * * *",
        "01:13": "13 1 * * *",
        "13:33": "33 13 * * *",
        "15:13": "13 15 * * *",
        "18:13": "13 18 * * *",
        "21:48": "48 21 * * *",
        "22:23": "23 22 * * *",
    }
    for minute, cron in expected.items():
        assert minute in worker, minute
        assert cron in crons, cron
    # A redeploy of the Worker is required for these; a stale one must not
    # resurrect the retired minutes.
    for stale in ("16:23", "19:18", "19:53", "16:13"):
        assert stale not in worker, stale


def test_telegram_posts_are_at_least_three_hours_apart():
    def minutes(value: str) -> int:
        hour, minute = value.split(":")
        return int(hour) * 60 + int(minute)

    points = sorted(minutes(m) for m in TELEGRAM_MINUTES)
    gaps = [b - a for a, b in zip(points, points[1:])]
    # 01:13 -> 15:13 -> 18:13 -> 21:48. The first gap is the overnight
    # sleep window; every gap inside the waking day is >= three hours, where
    # the old schedule had 1 h 50 (16:23 -> 18:13) and 1 h 05 (18:13 -> 19:18).
    assert gaps == [840, 180, 215], gaps
    assert min(gaps[1:]) >= 180


# --- Sunday belongs to the digest ------------------------------------------

def _store(tmp_path) -> StateStore:
    store = StateStore(tmp_path)
    store.data["queue"] = [
        {
            "repo_id": 1, "full_name": "owner/demo", "name": "Demo",
            "description": "A useful demo project", "license": {"spdx_id": "MIT"},
            "stars": 500, "forks": 20, "created_at": "2025-01-01T00:00:00Z",
            "pushed_at": "2026-10-01T00:00:00Z", "contributors_count": 3,
            "has_readme": True, "readme": "A useful demo project. " * 30,
            "topics": ["python"],
        }
    ]
    return store


def _sunday(monkeypatch, tmp_path):
    """Freeze the clock onto a Sunday without touching the tick under test."""
    import ghos_v6.pipeline as pipeline_module

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 22, 18, 13, tzinfo=tz or UTC)

    monkeypatch.setattr(pipeline_module, "datetime", _Frozen)
    return _Frozen


def test_the_midday_project_slot_is_retired_on_sunday(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _sunday(monkeypatch, tmp_path)
    config = pipeline.Config(telegram_bot_token="x", telegram_test_channel_id=123)
    result = pipeline.publish_slot(config, store, "us_midday")
    assert result == {"ok": True, "published": False, "reason": "sunday_digest_slot",
                      "slot": "us_midday"}
    assert store.data["queue"], "the queue must not be touched"
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "sunday_digest_slot" in events
    # The slot is recorded as finished, so the watchdog does not chase it.
    assert "publish:us_midday" in store.data["last_runs"]


def test_the_midday_chair_runs_the_digest_on_sunday(monkeypatch, tmp_path):
    """One cron, decided by the weekday: digest on Sunday, project otherwise."""
    calls: list[str] = []
    monkeypatch.setattr(cli, "digest_command",
                        lambda config, store: (calls.append("digest"), {"ok": True})[1])
    monkeypatch.setattr(cli, "publish_slot",
                        lambda config, store, slot: (calls.append(slot), {"ok": True})[1])
    cli._run_scheduled(cli.Config(), StateStore(tmp_path / "sun"), "13 18 * * *",
                       now=datetime(2026, 11, 22, 18, 13, tzinfo=UTC), enforce_window=False)
    cli._run_scheduled(cli.Config(), StateStore(tmp_path / "mon"), "13 18 * * *",
                       now=datetime(2026, 11, 23, 18, 13, tzinfo=UTC), enforce_window=False)
    assert calls == ["digest", "us_midday"]


def test_the_watchdog_does_not_chase_the_sunday_midday_slot(monkeypatch, tmp_path):
    calls: list[dict] = []
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(watchdog, "_dispatch",
                        lambda repo, inputs, token: (calls.append(dict(inputs)), (True, "HTTP 204"))[1])
    monkeypatch.setattr(watchdog, "_notify", lambda config, text: True)
    store = StateStore(tmp_path)
    moment = datetime(2026, 11, 22, 19, 0, tzinfo=UTC)  # Sunday, past every deadline
    for key, _deadline, _inputs in watchdog.SLOTS:
        store.data.setdefault("last_runs", {})[key] = {"finished_at": "2026-11-22T18:14:00Z"}
    store.data["last_runs"]["publish:us_midday"] = {"finished_at": "2026-11-21T18:14:00Z"}
    store.data["last_runs"]["digest"] = {"finished_at": "2026-11-22T18:14:00Z"}
    watchdog.watchdog_command(watchdog.Config(telegram_bot_token="x"), store, now=moment)
    assert not [c for c in calls if c.get("slot") == "us_midday"]


# --- fair view measurement --------------------------------------------------

def test_views_are_snapshotted_once_at_a_fixed_age(tmp_path, monkeypatch):
    """Slot comparisons need the same post measured at the same age."""
    from ghos_v6 import insights

    store = _store(tmp_path)
    old = dict(store.data["queue"][0])
    old.update({"full_name": "owner/old", "telegram_message_id": 123,
                "published_at": "2026-11-22T18:13:00Z"})
    young = dict(store.data["queue"][0])
    young.update({"full_name": "owner/young", "telegram_message_id": 124,
                  "published_at": "2026-11-24T17:23:00Z"})
    store.data["published"] = {"owner/old": old, "owner/young": young}
    store.data["queue"] = []
    monkeypatch.setattr(insights, "fetch_post_stats",
                        lambda channel, message_id: {"ok": True, "views": 70, "reactions": 2,
                                                     "fetched_at": "2026-11-24T20:23:00Z"})
    # 26 h is inside the 24-36 h window; 3 h is far too young to compare.
    monkeypatch.setattr(insights, "age_hours",
                        lambda published_at: 26.0 if published_at == "2026-11-22T18:13:00Z" else 3.0)
    monkeypatch.setattr(insights, "views_at_reference", lambda views, age: 68)
    monkeypatch.setattr(insights, "median_views", lambda records: 70)
    monkeypatch.setattr(insights, "value_score", lambda record, median_views: 1.0)
    monkeypatch.setattr(pipeline, "_channel_handle", lambda config: "GitHub_Open_Source")
    config = pipeline.Config(telegram_bot_token="x", telegram_test_channel_id=123)

    first = pipeline.metrics_command(config, store)
    assert first["updated"] == 2
    measured = store.data["published"]["owner/old"]
    assert measured["views_measured_24h"] == 70
    assert measured["views_measured_24h_age"] == 26.0
    # A three-hour-old post is a projection, not a measurement: no snapshot.
    assert "views_measured_24h" not in store.data["published"]["owner/young"]

    # A later fetch must not overwrite the fixed-age snapshot ...
    monkeypatch.setattr(insights, "fetch_post_stats",
                        lambda channel, message_id: {"ok": True, "views": 200, "reactions": 9,
                                                     "fetched_at": "2026-11-26T20:23:00Z"})
    pipeline.metrics_command(config, store)
    assert store.data["published"]["owner/old"]["views_measured_24h"] == 70
    assert store.data["published"]["owner/old"]["views"] == 200
    # ... and a post that is only *now* old enough is stamped then.
    monkeypatch.setattr(insights, "age_hours", lambda published_at: 30.0)
    pipeline.metrics_command(config, store)
    assert store.data["published"]["owner/young"]["views_measured_24h"] == 200


# --- every post gets a real measurement, not a projection -------------------

METRICS_PASSES = ("20:23", "02:23")  # UTC minutes of the two views snapshots
POST_MINUTES = ("01:13", "15:13", "18:13", "21:48")


def _minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def test_each_slot_is_read_once_between_24_and_36_hours():
    """A single 20:23 reading left the evening posts outside the window.

    The 01:13 post was 43 h old at its first reading and the 21:48/22:23 posts
    were 22 h: one was projected from a 19-hour sample, the others never
    measured at all. The night pass closes that hole; this test keeps it shut.
    """
    for post in POST_MINUTES:
        ages = []
        for offset in (0, 1440, 2880):  # the pass on the same day and the two after
            for pass_minute in METRICS_PASSES:
                age = _minutes(pass_minute) + offset - _minutes(post)
                if age > 0:
                    ages.append(age / 60)
        inside = [age for age in ages if 24.0 <= age <= 36.0]
        assert inside, f"{post} is never measured inside 24-36 h: {ages}"


def test_the_night_pass_has_its_own_marker_so_the_watchdog_still_owns_20_23(
    tmp_path, monkeypatch):
    from ghos_v6 import insights

    store = _store(tmp_path)
    record = dict(store.data["queue"][0])
    record.update({"telegram_message_id": 123, "published_at": "2026-11-23T21:48:00Z"})
    store.data["published"] = {"owner/demo": record}
    store.data["queue"] = []
    monkeypatch.setattr(insights, "fetch_post_stats",
                        lambda channel, message_id: {"ok": True, "views": 61, "reactions": 1,
                                                     "fetched_at": "2026-11-24T02:23:00Z"})
    monkeypatch.setattr(insights, "age_hours", lambda published_at: 28.6)
    monkeypatch.setattr(insights, "views_at_reference", lambda views, age: 62)
    monkeypatch.setattr(insights, "median_views", lambda records: 61)
    monkeypatch.setattr(insights, "value_score", lambda record, median_views: 1.0)
    monkeypatch.setattr(pipeline, "_channel_handle", lambda config: "GitHub_Open_Source")
    config = pipeline.Config(telegram_bot_token="x", telegram_test_channel_id=123)

    pipeline.metrics_command(config, store, marker="metrics-night")
    assert store.data["published"]["owner/demo"]["views_measured_24h"] == 61
    assert "metrics-night" in store.data["last_runs"]
    # The evening chair stays the watchdog's business: a night pass must not
    # look like proof that the 20:23 snapshot already happened.
    assert "metrics" not in store.data["last_runs"]


def test_the_0223_cron_is_the_night_pass_not_the_evening_one(monkeypatch, tmp_path):
    """The route, not the command, decides which marker a pass writes."""
    seen: list[str] = []
    monkeypatch.setattr(cli, "metrics_command",
                        lambda config, store, **kwargs: (seen.append(kwargs.get("marker", "metrics")),
                                                         {"ok": True})[1])
    store = StateStore(tmp_path)
    cli._run_scheduled(cli.Config(), store, "23 2 * * *",
                       now=datetime(2026, 11, 25, 2, 23, tzinfo=UTC), enforce_window=False)
    cli._run_scheduled(cli.Config(), store, "23 20 * * *",
                       now=datetime(2026, 11, 25, 20, 23, tzinfo=UTC), enforce_window=False)
    assert seen == ["metrics-night", "metrics"]


# --- a blocked chair is retired, not paid back into the next hour -----------

def test_a_late_chair_is_retired_instead_of_crowding_the_next_one(monkeypatch, tmp_path):
    """24.11: the 15:13 chair published at 17:06, 67 minutes before 18:13.

    Three 503s delayed it; the watchdog kept recovering it because the
    transient budget is four hours, which is longer than the new three-hour
    spacing. Inside the window recovery must still work, outside it the chair
    is retired and the next chair keeps its time.
    """
    calls: list[dict] = []
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setattr(watchdog, "_dispatch",
                        lambda repo, inputs, token: (calls.append(dict(inputs)), (True, "HTTP 204"))[1])
    monkeypatch.setattr(watchdog, "_notify", lambda config, text: True)

    inside = StateStore(tmp_path / "inside")
    watchdog.watchdog_command(watchdog.Config(telegram_bot_token="x"), inside,
                              now=datetime(2026, 11, 24, 16, 0, tzinfo=UTC))
    assert {"command": "publish", "slot": "us_morning"} in calls

    calls.clear()
    outside = StateStore(tmp_path / "outside")
    watchdog.watchdog_command(watchdog.Config(telegram_bot_token="x"), outside,
                              now=datetime(2026, 11, 24, 16, 30, tzinfo=UTC))
    assert not [c for c in calls if c.get("slot") == "us_morning"]
    assert "publish:us_morning" not in outside.data.get("last_runs", {}), \
        "a retired chair is not written into state as a run"


def test_the_late_limit_keeps_two_hours_between_any_two_posts():
    chairs = sorted(_minutes(f"{h:02d}:{m:02d}") for h, m in watchdog.PROJECT_CHAIR_MINUTES.values())
    gaps = [b - a for a, b in zip(chairs, chairs[1:])]
    assert gaps == [840, 180], gaps  # overnight, then 15:13 -> 18:13
    # Worst case: both neighbours run as late as the limit allows, so the
    # shortest gap must still leave two hours between two posts.
    assert min(gaps) - watchdog.LATE_POST_LIMIT_MINUTES >= 120


def test_the_watchdog_chair_table_matches_the_crons():
    crons = _workflow_crons()
    for slot, (hour, minute) in watchdog.PROJECT_CHAIR_MINUTES.items():
        assert f"{minute:02d} {hour} * * *" in crons, slot
