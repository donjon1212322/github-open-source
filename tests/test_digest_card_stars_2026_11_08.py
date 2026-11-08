"""Owner 08.11: the Sunday card must show GitHub stars, not internal traffic.

The live card for post 3571 showed "77 views / 82 views / ..." and
"Ranked by measured 24-hour views". Those numbers are an internal metric
and, at this scale, meaningless to a reader. Stars are already in state
and are what the channel audience can actually compare.
"""
from __future__ import annotations

from pathlib import Path

from ghos_v6 import pipeline
from ghos_v6.cards import render_digest_card
from ghos_v6.config import Config
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]
RECORDS = [
    {"name": "Concat", "full_name": "jub0t/Concat", "stars": 14124,
     "views": 122, "views_at_24h": 77, "score": 95.0,
     "language": "Rust", "category": "DevTools", "generated": {"hook": "Edit videos locally."}},
    {"name": "Filee", "full_name": "KnifeLemon/Filee", "stars": 21937,
     "views": 113, "views_at_24h": 72, "score": 94.0,
     "language": "C#", "category": "DevTools", "generated": {"hook": "Convert 263 formats offline."}},
]


def test_card_source_has_no_views_and_ranks_by_nothing_internal():
    source = (REPO / "src" / "ghos_v6" / "cards.py").read_text(encoding="utf-8")
    fn = source[source.index("def render_digest_card("):source.index("def render_project_card(")]
    assert "views" not in fn.lower()
    assert "editorial score" not in fn.lower()
    assert "stars" in fn
    assert "This week's five picks" in fn


def test_rendered_card_is_still_1200x630(tmp_path: Path):
    path = render_digest_card(RECORDS, tmp_path / "digest.png")
    from PIL import Image
    with Image.open(path) as image:
        assert image.size == (1200, 630)


def test_digest_text_shows_stars_and_never_views():
    text = pipeline.digest_text(RECORDS)
    assert "⭐ 14 124" in text
    assert "views" not in text.lower()
    assert "Top 5 of the Week" in text


def _wired(monkeypatch, tmp_path: Path, *, with_marker: bool = True):
    calls: list[dict] = []

    class FakeTelegram:
        def __init__(self, *a, **k):
            pass

        def edit_text(self, chat_id, message_id, text, preview_url=""):
            calls.append({"op": "edit", "message_id": message_id,
                          "text": text, "preview_url": preview_url})
            return {"message_id": message_id}

        def send_text(self, *a, **k):
            calls.append({"op": "send"})
            return {"message_id": 9999}

        def api(self, method, payload=None):
            calls.append({"op": method})
            return {"message_id": 9999}

    def fake_card(records, path):
        path.write_bytes(b"png")
        return path

    monkeypatch.setattr(pipeline, "TelegramClient", FakeTelegram)
    monkeypatch.setattr(pipeline, "render_digest_card", fake_card)
    monkeypatch.setattr(pipeline, "_recent_records", lambda store, days: RECORDS)
    monkeypatch.setattr(pipeline, "_host_local_image",
                        lambda *a, **k: ("https://i.ibb.co/NEW/sunday-digest-stars.png", "imgbb"))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-tg")
    monkeypatch.setenv("TELEGRAM_TEST_CHANNEL_ID", "-1001234567890")
    store = StateStore(tmp_path / "state.json")
    if with_marker:
        store.data["last_runs"]["digest"] = {
            "week": "2026-W45", "telegram_message_id": 3571,
            "poll_message_id": 3572,
        }
    return store, calls


def test_card_fix_edits_the_existing_post_and_creates_nothing(monkeypatch, tmp_path: Path):
    store, calls = _wired(monkeypatch, tmp_path)
    result = pipeline.digest_card_fix_command(Config.from_env(), store)
    assert result["ok"] is True and result["message_id"] == 3571
    edits = [c for c in calls if c.get("op") == "edit"]
    assert len(edits) == 1
    assert edits[0]["preview_url"] == "https://i.ibb.co/NEW/sunday-digest-stars.png"
    assert "⭐ 14 124" in edits[0]["text"]
    assert "views" not in edits[0]["text"].lower()
    assert not [c for c in calls if c.get("op") in ("send", "sendPoll")], "no duplicate post/poll"
    assert store.data["last_runs"]["digest"]["card_metric"] == "stars"
    assert store.data["last_runs"]["digest"]["poll_message_id"] == 3572, "poll untouched"


def test_card_fix_without_a_digest_post_is_a_noop(monkeypatch, tmp_path: Path):
    store, calls = _wired(monkeypatch, tmp_path, with_marker=False)
    result = pipeline.digest_card_fix_command(Config.from_env(), store)
    assert result == {"ok": True, "reason": "no_digest_post"}
    assert calls == []
