"""Owner 08.11: weekly digest/poll must be recoverable WITHOUT duplicates.

Before this change the Sunday digest, Sunday owner report and Thursday
poll-close were the only slots with no safety net: worker.js excluded them
and watchdog.SLOTS did not contain them, because a second trigger would
create a second post/poll. Now each command writes an ISO-week marker, so
the exact-minute Worker dispatch, the native GitHub cron and the Watchdog
can all fire in the same week - the second and third are harmless no-ops.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ghos_v6 import pipeline, watchdog
from ghos_v6.config import Config
from ghos_v6.state import StateStore, iso_week_key

REPO = Path(__file__).resolve().parents[1]
SUNDAY = datetime(2026, 11, 8, 17, 0, tzinfo=timezone.utc)
THURSDAY = datetime(2026, 11, 12, 19, 10, tzinfo=timezone.utc)
FRIDAY = datetime(2026, 11, 6, 19, 10, tzinfo=timezone.utc)


def test_iso_week_key_is_stable_within_a_week_and_changes_on_monday():
    # Sunday 08.11 closes ISO week W45; Monday 09.11 opens W46.
    assert iso_week_key(SUNDAY) == iso_week_key(
        datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
    )
    assert iso_week_key(SUNDAY) != iso_week_key(
        datetime(2026, 11, 9, 9, 0, tzinfo=timezone.utc)
    )


def test_weekly_guard_blocks_the_same_week_and_allows_the_next(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    assert pipeline.weekly_guard(store, "digest", now=SUNDAY) is None
    pipeline.mark_weekly(store, "digest", now=SUNDAY, telegram_message_id=99)
    assert pipeline.weekly_guard(store, "digest", now=SUNDAY) == {
        "ok": True, "published": False, "reason": "digest_already_done"
    }
    next_week = datetime(2026, 11, 15, 16, 30, tzinfo=timezone.utc)
    assert pipeline.weekly_guard(store, "digest", now=next_week) is None


def _wired_digest(monkeypatch, tmp_path: Path):
    sent: list[dict] = []

    class FakeTelegram:
        def __init__(self, *a, **k):
            pass

        def send_text(self, chat_id, text, **kwargs):
            sent.append({"chat_id": chat_id, "text": text})
            return {"message_id": 4000 + len(sent)}

    def fake_card(records, path):
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr(pipeline, "TelegramClient", FakeTelegram)
    monkeypatch.setattr(pipeline, "render_digest_card", fake_card)
    monkeypatch.setattr(pipeline, "_host_local_image", lambda *a, **k: ("https://i.ibb.co/digest.png", "imgbb"))
    monkeypatch.setattr(pipeline, "_recent_records", lambda store, days: [
        {"name": f"repo-{i}", "full_name": f"org/repo-{i}", "score": 10 - i,
         "stars": 100, "url": "https://github.com/org/repo", "generated": {"hook": "A useful tool"}}
        for i in range(5)
    ])
    monkeypatch.setattr(pipeline.polls, "send_week_poll", lambda *a, **k: {"message_id": 5001})
    for key, value in (
        ("TELEGRAM_BOT_TOKEN", "test-tg"),
        ("TELEGRAM_TEST_CHANNEL_ID", "-1001234567890"),
    ):
        monkeypatch.setenv(key, value)
    store = StateStore(tmp_path / "state.json")
    return store, sent


def test_digest_and_poll_are_posted_once_even_if_triggered_three_times(monkeypatch, tmp_path: Path):
    store, sent = _wired_digest(monkeypatch, tmp_path)
    cfg = Config.from_env()
    first = pipeline.digest_command(cfg, store)
    second = pipeline.digest_command(cfg, store)
    third = pipeline.digest_command(cfg, store)
    assert first["published"] is True
    assert second == {"ok": True, "published": False, "reason": "digest_already_done"}
    assert third == second
    assert len(sent) == 1, "the Top-5 post must never be duplicated"
    marker = store.data["last_runs"]["digest"]
    assert marker["week"] == iso_week_key()
    assert marker["telegram_message_id"] == 4001
    assert marker["poll_message_id"] == 5001
    assert store.data["active_poll"]["message_id"] == 5001


def test_close_poll_with_no_active_poll_still_marks_the_week(monkeypatch, tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-tg")
    result = pipeline.close_poll_command(Config.from_env(), store)
    assert result["reason"] == "no_active_poll"
    assert store.data["last_runs"]["poll-close"]["week"] == iso_week_key()
    # The watchdog must not dispatch the same slot again 30 minutes later.
    again = pipeline.close_poll_command(Config.from_env(), store)
    assert again["reason"] == "poll-close_already_done"


def test_watchdog_recovers_the_sunday_digest_and_then_stays_quiet(monkeypatch, tmp_path: Path):
    calls: list[dict] = []

    def fake_dispatch(repo, inputs, token):
        calls.append(dict(inputs))
        return True, "HTTP 204"

    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GH_TOKEN", "test-token")
    monkeypatch.setattr(watchdog, "_dispatch", fake_dispatch)
    monkeypatch.setattr(watchdog, "_notify", lambda config, text: False)
    store = StateStore(tmp_path / "state.json")
    # Pretend every daily slot already finished, so only the weekly gap shows.
    for key, _deadline, _inputs in watchdog.SLOTS:
        store.data.setdefault("last_runs", {})[key] = {"finished_at": SUNDAY.isoformat()}

    first = watchdog.watchdog_command(Config(), store, now=SUNDAY)
    assert first["checked"] == len(watchdog.SLOTS) + 2
    assert "digest" in first["dispatched"]
    assert "report" in first["dispatched"]
    assert "poll-close" not in first["dispatched"]
    assert len([c for c in calls if c.get("command") == "digest"]) == 1

    # A late native GitHub cron arrives afterwards and writes the marker.
    # The marker must be written for the SAME ISO week the watchdog checks;
    # otherwise this test silently depends on the wall-clock date.
    pipeline.mark_weekly(store, "digest", now=SUNDAY, telegram_message_id=1234)
    pipeline.mark_weekly(store, "report", now=SUNDAY, sent=True)
    calls.clear()
    later = watchdog.watchdog_command(Config(), store, now=SUNDAY.replace(hour=17, minute=30))
    assert "digest" not in later["dispatched"]
    assert "report" not in later["dispatched"]
    assert all(c.get("command") not in ("digest", "report") for c in calls)


def test_watchdog_ignores_weekly_slots_on_the_wrong_weekday(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(watchdog, "_dispatch", lambda *a: (True, "HTTP 204"))
    monkeypatch.setattr(watchdog, "_notify", lambda config, text: False)
    store = StateStore(tmp_path / "state.json")
    result = watchdog.watchdog_command(Config(), store, now=FRIDAY)
    assert result["checked"] == len(watchdog.SLOTS)
    assert not any(k in result["missed"] for k in ("digest", "report", "poll-close"))


def test_thursday_poll_close_is_the_only_weekly_slot(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(watchdog, "_dispatch", lambda *a: (True, "HTTP 204"))
    monkeypatch.setattr(watchdog, "_notify", lambda config, text: False)
    store = StateStore(tmp_path / "state.json")
    result = watchdog.watchdog_command(Config(), store, now=THURSDAY)
    assert result["checked"] == len(watchdog.SLOTS) + 1
    assert "poll-close" in result["dispatched"]
    assert "digest" not in result["dispatched"]
