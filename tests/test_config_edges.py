"""Boundary branches of configuration, target lock and pure helpers.

These are cheap, deterministic branch tests: no network, no clock, no files.
They exist so the CI gate can measure branch coverage instead of only lines.
"""
from __future__ import annotations

import pytest

from ghos_v6 import dedup, telegram_payload
from ghos_v6.config import Config, telegram_message_link
from ghos_v6.state import StateStore


@pytest.mark.parametrize(
    ("raw", "default", "expected"),
    [
        (None, False, False),
        ("", True, True),
        ("1", False, True),
        ("TRUE", False, True),
        (" yes ", False, True),
        ("on", False, True),
        ("0", True, False),
        ("maybe", True, False),
    ],
)
def test_bool_env_parsing_covers_every_spelling(monkeypatch, raw, default, expected):
    from ghos_v6.config import _bool

    if raw is None:
        monkeypatch.delenv("PROBE_FLAG", raising=False)
    else:
        monkeypatch.setenv("PROBE_FLAG", raw)
    assert _bool("PROBE_FLAG", default) is expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, None), ("", None), ("not-a-number", None), (" 42 ", 42), ("-100123", -100123)],
)
def test_int_env_parsing_never_guesses(monkeypatch, raw, expected):
    from ghos_v6.config import _int_or_none

    if raw is None:
        monkeypatch.delenv("PROBE_INT", raising=False)
    else:
        monkeypatch.setenv("PROBE_INT", raw)
    assert _int_or_none("PROBE_INT") == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ()),
        ("", ()),
        ("@channel_a, channel_b ,, ", ("channel_a", "channel_b")),
    ],
)
def test_csv_env_parsing_strips_handles_and_blanks(monkeypatch, raw, expected):
    from ghos_v6.config import _csv

    if raw is None:
        monkeypatch.delenv("PROBE_CSV", raising=False)
    else:
        monkeypatch.setenv("PROBE_CSV", raw)
    assert _csv("PROBE_CSV") == expected


@pytest.mark.parametrize(
    ("chat_id", "main", "expected"),
    [
        ("@GitHub_Open_Source", False, "https://t.me/GitHub_Open_Source/17"),
        ("-1001234567890", False, "https://t.me/c/1234567890/17"),
        ("123", True, "https://t.me/GitHub_Open_Source/17"),
        ("123", False, ""),
        ("", False, ""),
    ],
)
def test_message_links_are_built_only_for_known_chat_shapes(chat_id, main, expected):
    assert telegram_message_link(chat_id, 17, main=main) == expected


@pytest.mark.parametrize(
    ("env", "target", "is_main"),
    [
        ("production", "main", True),
        ("production", "test", False),
        ("test", "main", False),
        ("staging", "main", False),
    ],
)
def test_target_lock_requires_both_production_and_main(monkeypatch, env, target, is_main):
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("PUBLISH_TARGET", target)
    monkeypatch.setenv("TELEGRAM_MAIN_CHANNEL_ID", "@GitHub_Open_Source")
    assert Config.from_env().is_main_target is is_main


def test_main_target_resolves_the_canonical_channel(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLISH_TARGET", "main")
    monkeypatch.setenv("TELEGRAM_MAIN_CHANNEL_ID", "@GitHub_Open_Source")
    assert Config.from_env().telegram_chat_id == "@GitHub_Open_Source"


def test_main_target_without_a_channel_raises(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLISH_TARGET", "main")
    monkeypatch.delenv("TELEGRAM_MAIN_CHANNEL_ID", raising=False)
    monkeypatch.delenv("MAIN_TELEGRAM_CHANNEL", raising=False)
    monkeypatch.setenv("TELEGRAM_TEST_CHANNEL_ID", "-1009999999999")
    with pytest.raises(RuntimeError, match="TELEGRAM_MAIN_CHANNEL_ID"):
        _ = Config.from_env().telegram_chat_id


def test_test_target_resolves_a_numeric_channel_and_raises_when_absent(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PUBLISH_TARGET", "test")
    monkeypatch.setenv("TELEGRAM_TEST_CHANNEL_ID", "-1009999999999")
    assert Config.from_env().telegram_chat_id == -1009999999999
    monkeypatch.delenv("TELEGRAM_TEST_CHANNEL_ID", raising=False)
    with pytest.raises(RuntimeError, match="TELEGRAM_TEST_CHANNEL_ID"):
        _ = Config.from_env().telegram_chat_id


def test_legacy_channel_alias_is_still_read(monkeypatch):
    monkeypatch.delenv("TELEGRAM_MAIN_CHANNEL_ID", raising=False)
    monkeypatch.setenv("MAIN_TELEGRAM_CHANNEL", "@Legacy_Channel")
    assert Config.from_env().telegram_main_channel_id == "@Legacy_Channel"


def test_require_reports_every_missing_secret_at_once():
    with pytest.raises(RuntimeError) as exc:
        Config().require("GEMINI_API_KEY", "IMGBB_API_KEY", "TELEGRAM_BOT_TOKEN")
    message = str(exc.value)
    assert "GEMINI_API_KEY" in message and "IMGBB_API_KEY" in message
    Config(telegram_bot_token="x").require("TELEGRAM_BOT_TOKEN")


@pytest.mark.parametrize(
    "value",
    ["", "not-a-date", "2026-13-45T99:99:99Z", None],
)
def test_invalid_timestamps_never_raise(value):
    assert dedup._parse(value) is None
    from ghos_v6.state import _parse_ts

    assert _parse_ts(value) is None


@pytest.mark.parametrize(
    "url",
    ["", "http://example.com", "https://", "https://exa mple.com/x", "not a url"],
)
def test_only_valid_https_urls_become_buttons(url):
    assert telegram_payload.build_buttons(github_url=url) == []


def test_send_message_payload_disables_preview_for_an_invalid_url():
    payload = telegram_payload.build_send_message_payload(1, "text", preview_url="javascript:x")
    assert payload["link_preview_options"] == {"is_disabled": True}
    assert "reply_markup" not in payload


def test_state_validation_rejects_a_broken_projection(tmp_path):
    from ghos_v6.state import StateCorruptError

    store = StateStore(tmp_path)
    store.data["published"] = []
    with pytest.raises(StateCorruptError, match="published must be an object"):
        store.save()

    store.data["published"] = {}
    store.data["queue"] = {}
    with pytest.raises(StateCorruptError, match="queue must be a list"):
        store.save()
