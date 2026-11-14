"""Owner 14.11: keep the linked discussion group, but have NO pinned posts.

Telegram automatically forwards AND pins every channel post inside a linked
discussion supergroup; the Bot API has no switch to disable that behaviour.
The approved fix is a best-effort ``unpinAllChatMessages`` on the linked group
after every NEW channel post, plus one explicit Doctor run that verifies bot
rights and clears the pins that are already there.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ghos_v6 import doctor, pipeline
from ghos_v6.config import Config
from ghos_v6.telegram import TelegramError

REPO = Path(__file__).resolve().parents[1]
CHANNEL = "@GitHub_Open_Source"
LINKED = -1003849113813
TITLE = "GitHub Open Source — Chat"


class _FakeTelegram:
    def __init__(self, *, linked: int | str | None = LINKED,
                 pinned_before: int | None = 3596, fail: bool = False):
        self.linked = linked
        self.pinned = pinned_before
        self.fail = fail
        self.calls: list[tuple[str, object]] = []

    def get_me(self) -> dict[str, object]:
        return {"id": 123, "username": "open_source_GP_bot", "first_name": "GP"}

    def discover_ids(self) -> dict[str, object]:
        return {}

    def get_chat(self, chat_id: object) -> dict[str, object]:
        self.calls.append(("get_chat", chat_id))
        if chat_id == CHANNEL:
            return {"username": "GitHub_Open_Source", "linked_chat_id": self.linked}
        return {
            "id": self.linked, "title": TITLE, "type": "supergroup",
            "pinned_message": {"message_id": self.pinned} if self.pinned else None,
        }

    def api(self, method: str, payload: dict[str, object] | None = None) -> object:
        self.calls.append((method, payload))
        if self.fail:
            raise TelegramError("not enough rights to unpin messages")
        if method == "getChatMember":
            return {"status": "administrator", "can_pin_messages": True}
        if method == "unpinAllChatMessages":
            self.pinned = None
            return True
        return {}


# --- shared helper ---------------------------------------------------------

def test_unpin_uses_the_linked_group_not_the_channel(monkeypatch):
    fake = _FakeTelegram()
    monkeypatch.setattr(pipeline, "TelegramClient", lambda token: fake)
    config = SimpleNamespace(telegram_bot_token="dummy", telegram_chat_id=CHANNEL)

    result = pipeline.unpin_linked_discussion(config)

    assert result == {"ok": True, "unpinned": True, "linked_chat_id": LINKED}
    assert ("unpinAllChatMessages", {"chat_id": LINKED}) in fake.calls
    assert all(payload != {"chat_id": CHANNEL} for method, payload in fake.calls
               if method == "unpinAllChatMessages")


def test_no_linked_group_is_an_honest_no_op():
    fake = _FakeTelegram(linked=None)
    config = SimpleNamespace(telegram_bot_token="dummy", telegram_chat_id=CHANNEL)

    result = pipeline.unpin_linked_discussion(config, fake)

    assert result == {"ok": True, "unpinned": False, "reason": "no_linked_group"}
    assert not any(method == "unpinAllChatMessages" for method, _ in fake.calls)


def test_a_failed_unpin_never_breaks_the_publication():
    fake = _FakeTelegram(fail=True)
    config = SimpleNamespace(telegram_bot_token="dummy", telegram_chat_id=CHANNEL)

    result = pipeline.unpin_linked_discussion(config, fake)

    assert result["ok"] is False
    assert result["reason"] == "unpin_failed"
    assert result["error"] == "TelegramError"


def test_every_new_channel_post_triggers_the_auto_unpin():
    """Project post, AI Brief, digest post, digest poll, replacement poll."""
    source = (REPO / "src" / "ghos_v6" / "pipeline.py").read_text(encoding="utf-8")
    call = "unpin_linked_discussion(config, telegram, channel_chat_id=chat_id)"
    assert source.count(call) >= 5, "a channel post lost its auto-unpin"
    assert 'client.api("unpinAllChatMessages"' in source


# --- Doctor flag + live diagnostic ----------------------------------------

def test_doctor_unpin_flag_is_wired_through_cli_and_workflow():
    cli = (REPO / "src" / "ghos_v6" / "cli.py").read_text(encoding="utf-8")
    doc = (REPO / "src" / "ghos_v6" / "doctor.py").read_text(encoding="utf-8")
    wf = (REPO / ".github" / "workflows" / "doctor.yml").read_text(encoding="utf-8")
    assert 'doctor.add_argument("--unpin-discussion", action="store_true"' in cli
    assert 'unpin_discussion=args.unpin_discussion' in cli
    assert "if unpin_discussion:" in doc
    assert 'unpinAllChatMessages' in doc
    assert "      unpin_discussion:" in wf
    assert "UNPIN_DISCUSSION" in wf
    assert "args+=(--unpin-discussion)" in wf


def test_run_doctor_unpin_discussion_clears_pins(monkeypatch, tmp_path: Path):
    fake = _FakeTelegram()
    monkeypatch.setattr(doctor, "TelegramClient", lambda token: fake)

    class FakeGemini:
        def __init__(self, *args, **kwargs):
            pass

        def list_models(self):
            return 200, [{"name": "models/gemini-3.5-flash-lite"}]

        def generate_json(self, *args, **kwargs):
            return {"status": "ok"}

    class FakeGitHubApi:
        def __init__(self, *args, **kwargs):
            pass

        def search(self, *args, **kwargs):
            return [{"full_name": f"owner/repo{i}"} for i in range(3)]

    def fake_card(candidate, generated, path, **kwargs):
        path.write_bytes(b"x" * 2000)
        return path, "v4"

    monkeypatch.setattr(doctor, "GeminiClient", FakeGemini)
    monkeypatch.setattr(doctor, "GitHubApi", FakeGitHubApi)
    monkeypatch.setattr(doctor, "fetch_worldofai_entries",
                        lambda: [{"video_id": "tg-test", "title": "Test",
                                  "published_at": "2026-11-14", "url": ""}])
    monkeypatch.setattr(doctor, "render_project_card", fake_card)

    config = Config(
        app_env="production", publish_target="main",
        telegram_main_channel_id=CHANNEL, telegram_bot_token="dummy",
        GitHub_token="dummy", Gemini_api_key="dummy",
        Gemini_model="gemini-3.5-flash-lite",
        telegraph_access_token="", dev_api_key="", bluesky_app_password="",
        telegram_owner_chat_id=None, telegram_test_channel_id=None,
        state_dir=tmp_path,
    )

    report = doctor.run_doctor(config, unpin_discussion=True)

    assert report["ok"] is True
    assert report["checks"]["telegram_discussion"]["ok"] is True
    unpin = report["checks"]["discussion_unpin"]
    assert unpin["ok"] is True
    assert unpin["linked_chat_id"] == LINKED
    assert unpin["group_title"] == TITLE
    assert unpin["group_type"] == "supergroup"
    assert unpin["bot_status"] == "administrator"
    assert unpin["can_pin_messages"] is True
    assert unpin["pinned_before"] == 3596
    assert unpin["pinned_after"] is None
    assert unpin["unpinned"] is True
    assert sum(1 for method, _ in fake.calls
               if method == "unpinAllChatMessages") == 1
