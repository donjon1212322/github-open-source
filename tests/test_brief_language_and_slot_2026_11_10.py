"""Owner 10.11: post 3581 went out at 02:11 UTC (05:11 Minsk) instead of the
planned 19:18 UTC slot, and printed a raw Russian channel line next to its
English twin. These regressions lock in the language filter and the schedule
guard. The cron string, cadence and UTC-day boundary are deliberately NOT
changed: an early run simply refuses to publish.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone

from ghos_v6 import cli, pipeline
from ghos_v6.config import Config
from ghos_v6.rumours import dedupe_claims, is_rumour_relevant
from ghos_v6.state import StateStore

RUSSIAN_CLAIM = (
    "Anthropic прямо сейчас раздают всем юзерам Ultra от 1 до 3 ресетов "
    "В зависимости от длительности подписки: <6 месяцев: 1 6–12 месяцев: 2"
)
ENGLISH_TWIN = "Anthropic is distributing 1 to 3 rate-limit resets based on subscription duration"
LIVE_3581_HOOK = (
    "Three model releases showed up in four channels this week. The thing I "
    "can't stop thinking about is an AI company selling a $200 transparent "
    "Game Boy handheld."
)


def test_cyrillic_claim_is_rejected_and_english_twin_is_kept():
    assert is_rumour_relevant(RUSSIAN_CLAIM) is False
    assert is_rumour_relevant(ENGLISH_TWIN) is True
    kept = dedupe_claims([c for c in (ENGLISH_TWIN, RUSSIAN_CLAIM)
                          if is_rumour_relevant(c)])
    assert kept == [ENGLISH_TWIN]


def test_other_language_scripts_are_rejected_too():
    assert is_rumour_relevant("Claude 5 传闻将于周五发布。") is False
    assert is_rumour_relevant("Claude 5 arrives Friday.") is True


def test_news_slot_opens_only_at_the_planned_evening_hour():
    assert pipeline._news_slot_open(datetime(2026, 11, 10, 2, 11, tzinfo=timezone.utc)) is False
    assert pipeline._news_slot_open(datetime(2026, 11, 10, 18, 59, tzinfo=timezone.utc)) is False
    assert pipeline._news_slot_open(datetime(2026, 11, 10, 19, 0, tzinfo=timezone.utc)) is True
    assert pipeline._news_slot_open(datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)) is True


def test_early_schedule_refuses_to_publish_and_leaves_no_watchdog_marker(monkeypatch, tmp_path):
    # The guard lives in the scheduled route, NOT in news_command: manual
    # runs, refreshes and watchdog recovery keep working unchanged.
    monkeypatch.setattr(cli, "_news_slot_open", lambda: False)
    store = StateStore(tmp_path)
    result = cli._run_scheduled(Config(), store, "18 19 * * *")
    assert result == {"ok": True, "published": False,
                      "reason": "news_before_scheduled_slot"}
    # No marker: the 19:18 cron / 20:00 watchdog must still be able to run.
    assert "news" not in (store.data.get("last_runs") or {})


def test_scheduled_route_still_publishes_in_the_evening_slot(monkeypatch, tmp_path):
    seen: list[str] = []
    monkeypatch.setattr(cli, "_news_slot_open", lambda: True)
    monkeypatch.setattr(cli, "news_command", lambda config, store: (
        seen.append("called"), {"ok": True, "published": True}
    )[1])
    result = cli._run_scheduled(Config(), StateStore(tmp_path), "18 19 * * *")
    assert result["published"] is True and seen == ["called"]


def test_publish_and_already_published_write_the_watchdog_marker():
    source = (pipeline.__file__)
    with open(source, encoding="utf-8") as handle:
        code = handle.read()
    assert code.count('"last_runs", {})["news"]') == 2


def test_brief_repair_removes_the_russian_line_from_3581(monkeypatch, tmp_path):
    calls: list[tuple] = []
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLISH_TARGET", "main")
    monkeypatch.setenv("TELEGRAM_MAIN_CHANNEL_ID", "@GitHub_Open_Source")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(
        pipeline, "TelegramClient",
        lambda token: type("T", (), {
            "edit_text": lambda self, chat, mid, text, preview_url="": (
                calls.append((chat, mid, text, preview_url)), {"message_id": mid})[1]
        })(),
    )
    post_text = (
        "🛰️ <b>AI Brief</b> · <i>no ads, no hype</i>\n\n"
        f"<b>{html.escape(LIVE_3581_HOOK, quote=False)}</b>\n\n"
        "🧬 <b>In this brief:</b> names\n\n"
        f"{pipeline._RUMOUR_HEADER}\n\n"
        f"◻️ <i>{html.escape(ENGLISH_TWIN, quote=False)}</i>\n"
        f"◻️ <i>{html.escape(RUSSIAN_CLAIM, quote=False)}</i>\n"
        "Which release will you test first?\n📣 @GitHub_Open_Source"
    )
    store = StateStore(tmp_path)
    store.data["news_topics"] = [{
        "telegram_message_id": 3581,
        "image_url": "https://i.ibb.co/example.png",
        "narrative_hook": LIVE_3581_HOOK,
        "post_text": post_text,
        "brief": {"unconfirmed_claims": [ENGLISH_TWIN, RUSSIAN_CLAIM],
                  "models": [], "stories": []},
    }]
    result = pipeline.brief_dedup_fix_command(Config.from_env(), store)
    assert result["ok"] is True and result["edited"] is True
    assert result["before"] == 2 and result["after"] == 1
    assert len(calls) == 1 and calls[0][1] == 3581
    sent = calls[0][2]
    assert ENGLISH_TWIN in sent
    assert "прямо сейчас" not in sent and "юзерам" not in sent
    assert LIVE_3581_HOOK in sent, "the complete hook must survive"
    assert "Which release will you test first?" in sent
    assert store.data["news_topics"][0]["brief"]["unconfirmed_claims"] == [ENGLISH_TWIN]
    second = pipeline.brief_dedup_fix_command(Config.from_env(), store)
    assert second["reason"] == "already_clean"
    assert len(calls) == 1
