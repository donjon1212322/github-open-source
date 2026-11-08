"""Owner complaint 08.11.2026, pinned against the LIVE 07.11 artefacts.

Four defects were visible in production:
  1. dev.to cropped the 1200x630 Telegram card to 1000x420;
  2. no feedback channel (no comments CTA / discussion link);
  3. the cover card printed "10 exact names, 3 official launches, 0 only
     announced..." — meaningless telemetry clipped with an ellipsis;
  4. one rumour appeared twice inside post 3568, and the article copied the
     Telegram post's closing question verbatim.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image

from ghos_v6 import pipeline
from ghos_v6.cards import render_ai_brief_card_v4
from ghos_v6.config import Config
from ghos_v6.devto_cover import COVER_H, COVER_W, build_devto_cover
from ghos_v6.news import (
    append_discussion_cta,
    brief_number_facts,
    card_hook_text,
    dedupe_claims,
    ensure_distinct_question,
    format_ai_brief_digest,
)
from ghos_v6.state import StateStore

QUESTION = "How do you currently handle long-term session memory in your production AI agents?"
TELEMETRY = (
    "10 exact names, 3 official launches, 0 only announced, 2 unverified claims. "
    "Gemini Omni 1.2 Flash leads the watch — what would you actually re-run first?"
)


def _brief() -> dict[str, object]:
    """Shape actually stored in state.news_topics[-1].brief on 07.11."""
    return {
        "headline": "New model drops from Google and Thinking Machines reshape video and open weights",
        "hook": TELEMETRY,
        "summary": "Model releases, open weights and kernel security audits.",
        "why_devs": (
            "Keeping up with these model updates ensures your stack leverages "
            "the fastest and most cost-effective APIs available."
        ),
        "discussion_question": QUESTION,
        "models": ["Gemini Omni 1.2 Flash", "Inkling 2 Small", "GPT-6 Cyber"],
        "stories": [
            {
                "title": "Google Gemini Omni 1.2 Flash",
                "bullets": [
                    "Rolled out video generation up to 15 seconds.",
                    "Dropped 720p API pricing by 30 percent to about $0.07 per second.",
                ],
                "why": "Cheaper generation makes real-time video iteration practical.",
                "status": "official_release",
                "basis": "official google blog post and pricing docs",
            },
            {
                "title": "Thinking Machines Inkling 2 Small",
                "bullets": ["Reported a 594B parameter MoE model with 17B active parameters."],
                "why": "A strong open-weights baseline.",
                "status": "official_release",
                "basis": "official thinking machines announcement",
            },
        ],
        "unconfirmed_claims": [
            "Anthropic is working on a collaborative mode for Claude Code Projects.",
            "US AI market impacted by recent election results regarding regulations.",
            "Anthropic is working on a collaborative mode for Claude Code Projects. "
            "> Recent activity on the Projects page summarises the latest threads.",
        ],
    }


# ---------------------------------------------------------------------------
# 3. Meaningful card headline, never telemetry, never clipped.
# ---------------------------------------------------------------------------
def test_card_headline_carries_a_concrete_fact_not_service_counters():
    line = card_hook_text(_brief())
    assert line
    assert "exact names" not in line.lower()
    assert "official launches" not in line.lower()
    assert "unverified claims" not in line.lower()
    assert "…" not in line and "..." not in line
    assert any(ch.isdigit() for ch in line), "intrigue must carry a concrete number"
    assert "Gemini Omni 1.2 Flash" in line


def test_digest_has_no_telemetry_no_duplicate_rumour_and_no_generic_why():
    text = format_ai_brief_digest(_brief())
    assert "10 exact names" not in text
    assert "3 official launches" not in text
    assert text.count("collaborative mode for Claude Code Projects") == 1
    assert "Keeping up with these model updates" not in text
    assert QUESTION in text


def test_card_footer_facts_are_numbers_not_a_second_copy_of_the_names():
    facts = brief_number_facts(_brief())
    assert facts
    assert all(any(ch.isdigit() for ch in f) for f in facts)
    assert "Gemini Omni" not in " ".join(facts)


def test_dedupe_claims_removes_a_rewording_of_the_same_rumour():
    claims = dedupe_claims(_brief()["unconfirmed_claims"])
    assert len(claims) == 2
    assert sum("collaborative mode" in c for c in claims) == 1


# ---------------------------------------------------------------------------
# 1. dev.to gets its own 1000x420 cover; the Telegram card stays 1200x630.
# ---------------------------------------------------------------------------
def test_card_renders_uncropped_and_devto_cover_matches_its_aspect(tmp_path: Path):
    path = tmp_path / "card.png"
    render_ai_brief_card_v4(
        "New model drops from Google",
        card_hook_text(_brief()),
        "Gemini Omni 1.2 Flash · Inkling 2 Small",
        path,
        stories=5,
        subhead="Gemini Omni 1.2 Flash · Inkling 2 Small · GPT-6 Cyber",
        facts_line="15 seconds · 30 percent · 594B parameters",
        date_label="07 NOV 2026",
    )
    with Image.open(path) as card:
        assert card.size == (1200, 630)
    with Image.open(BytesIO(build_devto_cover(path.read_bytes()))) as cover:
        assert cover.size == (COVER_W, COVER_H) == (1000, 420)


def test_article_slot_hosts_a_dedicated_devto_cover(monkeypatch, tmp_path: Path):
    captured: dict[str, str] = {}

    def fake_cover(_config, _record):
        return "https://i.ibb.co/devto-cover.png"

    def fake_publish(self, title, body, *, tags=(), cover_url=""):
        captured["cover_url"] = cover_url
        return {"id": 4995411, "url": "https://dev.to/githubopensource/fixed-1"}

    monkeypatch.setattr(pipeline, "_ensure_devto_cover", fake_cover)
    monkeypatch.setattr("ghos_v6.platforms.DevToClient.publish_markdown", fake_publish)
    monkeypatch.setenv("FEATURE_DEVTO", "true")
    monkeypatch.setenv("DEV_API_KEY", "test-dev-key")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")

    day = datetime.now(timezone.utc).date().isoformat()
    store = StateStore(tmp_path / "state.json")
    store.data["news_topics"] = [{
        "published_at": f"{day}T19:19:00Z",
        "headline": "Model watch",
        "image_url": "https://i.ibb.co/telegram-card.png",
        "article_title": "A real title",
        "article_markdown": "> **TL;DR** A long read.\n\n## The model watch\n\nDetails.",
        "brief": _brief(),
    }]
    pipeline.news_article_command(Config.from_env(), store)
    assert captured["cover_url"] == "https://i.ibb.co/devto-cover.png"
    assert "telegram-card" not in captured["cover_url"]


# ---------------------------------------------------------------------------
# 2. Feedback: explicit comments CTA with a discussion link.
# ---------------------------------------------------------------------------
def test_article_ends_with_a_comments_cta_and_a_distinct_question():
    body = (
        "> **TL;DR** Three model drops.\n\n"
        "## The model watch\n\nA concrete detail.\n\n"
        "## Where this leaves us\n\n" + QUESTION + "\n"
    )
    fixed = ensure_distinct_question(body, QUESTION)
    assert QUESTION not in fixed
    assert "?" in fixed
    with_cta = append_discussion_cta(fixed)
    # Owner clarified: feedback = the Telegram channel link in the dev.to
    # article. No dev.to comments CTA, no inline buttons in the post.
    assert "https://t.me/GitHub_Open_Source" in with_cta
    assert "feedback and discussion are in the channel" in with_cta.lower()
    assert "comments below" not in with_cta.lower()
    assert append_discussion_cta(with_cta).count("t.me/GitHub_Open_Source") == 1


# ---------------------------------------------------------------------------
# 4. The live artefacts are EDITED, never duplicated.
# ---------------------------------------------------------------------------
def test_card_is_legible_after_telegram_link_preview_recompression(tmp_path: Path):
    """Owner 08.11: the card was 'замылена', small captions unreadable."""
    source = Path(__file__).resolve().parents[1] / "src/ghos_v6/cards.py"
    text = source.read_text()
    body = text[text.index("def render_ai_brief_card_v4("):]
    assert "for x in range(0, W, 80)" not in body, "80px grid causes moire after downscale"
    assert '_font("mono", 17' not in body
    assert '_font("mono", 19' not in body
    assert '_font("sans", 25' not in body
    path = tmp_path / "legible.png"
    render_ai_brief_card_v4(
        "New model drops", "Gemini Omni 1.2 Flash — Rolled out video generation up to 15 seconds",
        "Gemini Omni 1.2 Flash · Inkling 2 Small", path,
        stories=5, subhead="Gemini Omni 1.2 Flash · Inkling 2 Small · GPT-6 Cyber",
        facts_line="15 seconds  ·  30 percent  ·  594B", date_label="2026-11-07",
    )
    with Image.open(path) as im:
        assert im.size == (1200, 630)


def test_edit_payload_has_no_inline_buttons_native_comments_stay():
    """Owner 08.11: 'кнопки в постах не нужны, там коментарии'."""
    from ghos_v6.telegram_payload import build_edit_message_payload
    payload = build_edit_message_payload(
        "@GitHub_Open_Source", 3568, "<b>AI Brief</b>",
        preview_url="https://i.ibb.co/card.png",
    )
    assert "reply_markup" not in payload
    assert "inline_keyboard" not in payload
    assert payload["message_id"] == 3568
    assert payload["parse_mode"] == "HTML"


def test_update_markdown_puts_to_the_existing_article_never_posts(monkeypatch):
    from ghos_v6.platforms import DevToClient

    calls: list[tuple[str, str]] = []

    class _Response:
        status_code = 200

        def json(self):
            return {"id": 4995411}

    def fake_request(self, method, url, **kwargs):
        calls.append((method, url))
        return _Response()

    monkeypatch.setattr(DevToClient, "_request", fake_request)
    client = DevToClient("test-key")
    client.update_markdown(
        4995411, "A meaningful companion title", "body",
        cover_url="https://i.ibb.co/devto-cover.png",
    )
    assert calls == [("PUT", "https://dev.to/api/articles/4995411")]
    assert all(method != "POST" for method, _ in calls), "no duplicate article"


_LONG = (
    "> **TL;DR** A full companion long read.\n\n## What actually moved today\n\n"
    "A concrete technical fact.\n\n## The model watch\n\n### Gemini Omni 1.2 Flash\n\n"
    "Exact details.\n\n## Where this leaves us\n\n"
    "Which of these would you re-run in your own eval harness first, and what would you measure?\n"
) + "\n\n".join(
    f"Paragraph {i}: concrete mechanics, what changed and what is still unknown."
    for i in range(90)
)


def test_quality_fix_updates_the_live_post_and_article_in_place(monkeypatch, tmp_path: Path):
    edited: dict[str, object] = {}
    updated: dict[str, object] = {}

    class FakeGemini:
        def __init__(self, *a, **k):
            pass

        def generate_json(self, prompt, **kwargs):
            return {"article_title": "A meaningful companion title", "article_markdown": _LONG}

    class FakeTelegram:
        def __init__(self, *a, **k):
            pass

        def edit_text(self, chat_id, message_id, text, *, preview_url=""):
            edited.update(message_id=message_id, text=text, preview_url=preview_url)
            return {"message_id": message_id}

    class FakeDevTo:
        def __init__(self, *a, **k):
            pass

        def update_markdown(self, article_id, title, body, *, tags=(), cover_url=""):
            updated.update(id=article_id, body=body, cover_url=cover_url)
            return {"id": article_id}

        def publish_markdown(self, *a, **k):
            raise AssertionError("quality-fix must NEVER create a second article")

    monkeypatch.setattr(pipeline, "GeminiClient", FakeGemini)
    monkeypatch.setattr(pipeline, "TelegramClient", FakeTelegram)
    monkeypatch.setattr(pipeline, "DevToClient", FakeDevTo)
    monkeypatch.setattr(pipeline, "_host_local_image", lambda *a, **k: ("https://i.ibb.co/new-card.png", "imgbb"))
    monkeypatch.setattr(pipeline, "_ensure_devto_cover", lambda *a, **k: "https://i.ibb.co/devto-cover.png")
    for key, value in (
        ("TELEGRAM_BOT_TOKEN", "test-tg"),
        ("GEMINI_API_KEY", "test-gemini"),
        ("DEV_API_KEY", "test-dev"),
        ("TELEGRAM_MAIN_CHANNEL_ID", "@GitHub_Open_Source"),
        ("TELEGRAM_TEST_CHANNEL_ID", "-1001234567890"),
    ):
        monkeypatch.setenv(key, value)

    store = StateStore(tmp_path / "state.json")
    store.data["news_topics"] = [{
        "published_at": "2026-11-07T19:19:05Z",
        "telegram_message_id": 3568,
        "headline": "New model drops from Google",
        "image_url": "https://i.ibb.co/old-card.png",
        "brief": _brief(),
    }]
    store.data["last_runs"] = {
        "news-article": {"devto_id": 4995411, "finished_at": "2026-11-07T19:53:53Z"}
    }

    result = pipeline.quality_fix_command(Config.from_env(), store)
    assert result["ok"] is True
    assert result["action"] == "updated"
    assert edited["message_id"] == 3568
    assert "10 exact names" not in str(edited["text"])
    assert str(edited["text"]).count("collaborative mode for Claude Code Projects") == 1
    assert updated["id"] == 4995411
    assert "https://t.me/GitHub_Open_Source" in str(updated["body"])
    assert QUESTION not in str(updated["body"])
    assert updated["cover_url"] == "https://i.ibb.co/devto-cover.png"
    assert store.data["news_topics"][0]["image_url"] == "https://i.ibb.co/new-card.png"
