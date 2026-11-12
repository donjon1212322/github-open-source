"""Owner 12.11: retime the three project posts for the US audience.

Both coasts (evening Eastern + morning Pacific), fixed UTC minutes so DST
shifts both zones together, three posts and the 03:00 Minsk day boundary kept.
The live Cloudflare Worker was redeployed 12.11 with the same due minutes;
cli.main() still refuses retired slot names as a safety net against stale,
late or accidental dispatches.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ghos_v6 import cli, pipeline, watchdog

REPO = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def test_only_the_three_us_slots_are_accepted():
    assert cli.PUBLISH_SLOTS == frozenset({"us_evening", "us_morning", "us_midday"})


def test_new_crons_route_to_the_new_slot_names():
    src = (REPO / "src" / "ghos_v6" / "cli.py").read_text(encoding="utf-8")
    routes = dict(re.findall(
        r'"([^"]+)"\s*:\s*lambda:\s*publish_slot\(config, store, "([^"]+)"\)', src))
    assert routes == {
        "13 1 * * *": "us_evening",
        "23 16 * * *": "us_morning",
        "13 18 * * *": "us_midday",
    }


def test_stale_undeployed_worker_dispatches_never_publish(monkeypatch):
    called: list[str] = []
    monkeypatch.setattr(cli, "StateStore", lambda *a, **k: None)
    monkeypatch.setattr(
        cli, "publish_slot",
        lambda config, store, slot: (
            called.append(slot),
            {"ok": True, "published": False, "reason": "no_candidate"},
        )[1],
    )
    assert cli.main(["publish", "--slot", "europe_morning"]) == 0
    assert cli.main(["publish", "--slot", "europe_midday"]) == 0
    assert cli.main(["publish", "--slot", "us_peak"]) == 0
    assert called == [], "retired 07:13/12:43/17:18Z dispatches must be no-ops"
    assert cli.main(["publish", "--slot", "us_evening"]) == 0
    assert cli.main(["publish", "--slot", "manual"]) == 0
    assert called == ["us_evening", "manual"]


def test_watchdog_deadlines_follow_the_us_schedule():
    slots = {key: (deadline, inputs) for key, deadline, inputs in watchdog.SLOTS}
    assert slots["collect"] == ((0, 30), {"command": "collect"})
    assert slots["publish:us_evening"] == (
        (1, 30), {"command": "publish", "slot": "us_evening"})
    assert slots["publish:us_morning"] == (
        (16, 30), {"command": "publish", "slot": "us_morning"})
    assert slots["publish:us_midday"] == (
        (18, 30), {"command": "publish", "slot": "us_midday"})
    # The 11.11 recovery slots stay where they were.
    assert slots["facebook-handoff"][0] == (18, 30)
    assert slots["metrics"][0] == (20, 30)


def test_worker_source_has_the_new_due_minutes_and_no_old_ones():
    src = (REPO / "tools" / "external_tick" / "worker.js").read_text(encoding="utf-8")
    assert '["00:23", { command: "collect" }]' in src
    assert '["01:13", { command: "publish", slot: "us_evening" }]' in src
    assert '["16:23", { command: "publish", slot: "us_morning" }]' in src
    assert '["18:13", { command: "publish", slot: "us_midday" }]' in src
    for stale in ('"07:13"', '"12:43"', '"17:18"', "europe_morning", "us_peak"):
        assert stale not in src, stale


def _local(utc_hour: int, utc_minute: int, offset_hours: int) -> tuple[int, int]:
    moment = datetime(2026, 11, 12, utc_hour, utc_minute, tzinfo=UTC)
    local = moment - timedelta(hours=offset_hours)
    return local.hour, local.minute


def test_fixed_utc_minutes_stay_inside_both_us_windows_in_winter_and_summer():
    # Winter: EST = UTC-5, PST = UTC-8. Summer: EDT = UTC-4, PDT = UTC-7.
    for et_offset, pt_offset in ((5, 8), (4, 7)):
        evening_et = _local(1, 13, et_offset)[0]
        morning_pt = _local(16, 23, pt_offset)[0]
        assert 19 <= evening_et <= 22, evening_et
        assert 8 <= morning_pt <= 11, morning_pt


def test_day_boundary_and_post_limit_were_not_changed():
    assert pipeline.MAX_PROJECT_POSTS_PER_DAY == 3
    owner = (REPO / "docs" / "OWNER_RULES.md").read_text(encoding="utf-8")
    assert "03:00 Минск" in owner
    assert owner.count("3 поста в день") == 1
