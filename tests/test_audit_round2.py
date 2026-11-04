"""Regression tests for the second audit (2026-11-02, round 2)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from ghos_v6 import dedup, pipeline, sheets, telegram
from ghos_v6.config import Config
from ghos_v6.Gemini import deterministic_project_content
from ghos_v6.news import fetch_primary_text
from ghos_v6.state import StateStore


class _FakeSocial:
    def __init__(self, *args, **kwargs):
        pass

    def post(self, text, url="", image_data=b"", content_type="", image_alt="", idempotency_key=""):
        return {"url": "https://social.example/post/1"}


def _record(name, score, hours_ago):
    now = datetime.now(timezone.utc)
    # Anchor to the start of the UTC day: subtracting hours from "now" used
    # to move these records into yesterday whenever the suite ran before
    # 04:00 UTC, which made the best-of-day tests fail by wall-clock hour.
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "full_name": f"owner/{name}",
        "name": name,
        "score": score,
        "description": f"A project called {name}",
        "url": f"https://github.com/owner/{name}",
        "image_url": "",
        "published_at": (day_start + timedelta(minutes=hours_ago)).isoformat(),
        "crossposts": {
            "bluesky": {"status": "pending", "attempts": 0, "due_at": (now - timedelta(minutes=5)).isoformat()},
        },
    }


def _config():
    return Config(
        telegram_bot_token="x",
        feature_bluesky=True,
        bluesky_handle="o",
        bluesky_app_password="p",
    )


def test_only_the_best_post_of_the_day_is_crossposted(monkeypatch, tmp_path):
    """The winner must be chosen from all of today's posts, not only from
    the ones that still have a pending cross-post. Otherwise the first run
    crowns the first post, the next run crowns the second, and so on."""
    monkeypatch.setattr(pipeline, "BlueskyClient", _FakeSocial)
    store = StateStore(tmp_path)
    store.data = {"published": {}}
    for name, score, hours in (("midday", 71, 2), ("morning", 88, 3), ("evening", 60, 4)):
        store.data["published"][f"owner/{name}"] = _record(name, score, hours)
    config = _config()

    pipeline.process_crossposts(config, store)
    assert store.data["published"]["owner/morning"]["crossposts"]["bluesky"]["status"] == "published"
    assert store.data["published"]["owner/midday"]["crossposts"]["bluesky"]["status"] == "held"
    assert store.data["published"]["owner/evening"]["crossposts"]["bluesky"]["status"] == "held"

    # The winner already went out. The other two become pending again: a
    # later run must not crown one of them just because it is the only
    # pending post left.
    now = datetime.now(timezone.utc)
    for name in ("midday", "evening"):
        store.data["published"][f"owner/{name}"]["crossposts"]["bluesky"] = {
            "status": "pending",
            "attempts": 0,
            "due_at": (now - timedelta(minutes=5)).isoformat(),
        }
    pipeline.process_crossposts(config, store)
    assert store.data["published"]["owner/midday"]["crossposts"]["bluesky"]["status"] == "held"
    assert store.data["published"]["owner/evening"]["crossposts"]["bluesky"]["status"] == "held"


def test_deterministic_fallback_fails_closed():
    candidate = {
        "name": "Demo",
        "full_name": "owner/demo",
        "description": "A useful demo project",
        "readme": "A useful demo project. " * 60,
        "is_dual_use": False,
    }
    result = deterministic_project_content(candidate)
    assert result["safety"] == "reject"


def test_readme_fingerprint_uses_the_stored_prefix():
    full = "word " * 3000
    candidate = {"readme": full, "readme_excerpt": full[:1200], "description": "A project"}
    record = {
        "readme_fp": dedup.readme_fingerprint(dedup.readme_for_fingerprint(candidate)),
        "description_fp": dedup.description_fingerprint("A completely different description"),
    }
    copy = {
        "readme": full[:1200] + " a different tail",
        "readme_excerpt": full[:1200],
        "description": "Another project",
    }
    result = dedup.check(copy, {"published": {"owner/copy": record}})
    assert result["verdict"] == "duplicate"


def test_fetch_primary_text_rejects_private_address():
    assert fetch_primary_text("https://127.0.0.1/secret") is None


def test_facebook_handoff_takes_the_best_of_the_day(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    store.data = {"published": {}}
    for name, score, hours in (("midday", 71, 2), ("morning", 88, 3), ("evening", 60, 4)):
        store.data["published"][f"owner/{name}"] = _record(name, score, hours)
    monkeypatch.setattr(
        sheets,
        "append_rows",
        lambda *args, **kwargs: {"ok": True, "appended": 1, "range": "facebook!A1"},
    )
    config = Config(
        feature_sheets_handoff=True,
        sheets_spreadsheet_id="sheet",
        sheets_credentials_json="{}",
    )
    result = pipeline.facebook_handoff_command(config, store, limit=1)
    assert result["queued"] == 1
    assert result["projects"] == ["owner/morning"]


def test_collect_pages_argument_is_forwarded(monkeypatch):
    from ghos_v6 import cli

    captured = {}

    def fake_collect(config, store, *, extra_pages=None):
        captured["extra_pages"] = extra_pages
        return {"ok": True}

    monkeypatch.setattr(cli, "collect_command", fake_collect)
    monkeypatch.setattr(cli, "StateStore", lambda *args, **kwargs: None)
    cli.main(["collect", "--pages", "2", "--pages", "3"])
    assert captured["extra_pages"] == ["2", "3"]


def test_telegram_retries_after_429(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        if len(calls) == 1:
            response.status_code = 429
            response._content = b'{"ok":false,"parameters":{"retry_after":0}}'
        else:
            response.status_code = 200
            response._content = b'{"ok":true,"result":{"id":1}}'
        response.headers["Content-Type"] = "application/json"
        return response

    client = telegram.TelegramClient("token")
    monkeypatch.setattr(client.session, "post", fake_post)
    monkeypatch.setattr(telegram.time, "sleep", lambda seconds: None)
    assert client.api("getMe") == {"id": 1}
    assert len(calls) == 2


def test_committed_state_has_no_test_channel_id():
    state = json.loads(Path("state/state.json").read_text(encoding="utf-8"))
    text = json.dumps(state, ensure_ascii=False)
    assert "t.me/c/" not in text
    assert "t.me/c/" not in text
    assert len(state["queue"]) <= 28
