"""Owner decisions, 2026-11-04.

A. Channel posts must not carry an inline keyboard, so Telegram can show
   its native "Leave a comment" footer.
B. Remove the older duplicate dev.to article, but protect any article that
   the live state still links to.
C. The AI Brief uses ONLY the newest WorldofAI video, only if it is today's
   or yesterday's, never the same video twice, with Gemini instructed to
   cut all advertising, and with a readable sectioned layout.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from ghos_v6 import news, pipeline, platforms
from ghos_v6.news import format_ai_brief, select_latest_news_entry
from ghos_v6.state import StateStore
from ghos_v6.telegram import TelegramClient

REPO = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# A. Option A - no inline keyboard


def test_project_post_has_no_reply_markup_by_default():
    captured = []
    client = TelegramClient("token")
    client.send_message_payload = lambda payload: (captured.append(payload), {"message_id": 1})[1]
    client.send_project(
        "@GitHub_Open_Source",
        "A <b>long</b> project post with links in the text",
        preview_url="https://i.ibb.co/a/card.png",
        github_url="https://github.com/a/b",
        telegraph_url="https://telegra.ph/a",
    )
    assert "reply_markup" not in captured[0], "native comment button must survive"
    assert captured[0]["text"].startswith("A ")


def test_explicit_opt_in_still_can_build_buttons():
    captured = []
    client = TelegramClient("token")
    client.send_message_payload = lambda payload: (captured.append(payload), {"message_id": 1})[1]
    client.send_project(
        "@GitHub_Open_Source", "text", preview_url="", github_url="https://github.com/a/b",
        include_buttons=True,
    )
    assert "reply_markup" in captured[0]


def test_news_command_source_has_no_inline_keyboard():
    source = (REPO / "src" / "ghos_v6" / "pipeline.py").read_text(encoding="utf-8")
    start = source.index("def news_command(")
    body = source[start:source.index("\ndef digest_command(", start)]
    assert "buttons=" not in body
    assert "build_buttons" not in body


# ---------------------------------------------------------------------------
# B. dev.to duplicate removal


class _Resp:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def json(self):
        return self._body


def test_remove_article_uses_delete_then_unpublish_fallback():
    replies = iter([_Resp(405), _Resp(204)])
    calls = []
    client = platforms.DevToClient("k")
    client._request = lambda method, url, **kw: (calls.append((method, url)), next(replies))[1]
    assert client.remove_article(4968458) == "unpublished"
    assert calls[0][0] == "DELETE"
    assert calls[1][0] == "PUT" and calls[1][1].endswith("/unpublish")


def test_remove_article_drafts_when_unpublish_is_privilege_gated():
    # Real dev.to answer on 04.11.2026: DELETE 404, /unpublish 401.
    replies = iter([_Resp(404), _Resp(401), _Resp(200)])
    calls = []
    client = platforms.DevToClient("k")
    client._request = lambda method, url, **kw: (calls.append((method, url, kw)), next(replies))[1]
    assert client.remove_article(4968458) == "drafted"
    assert calls[2][2] == {"json": {"article": {"published": False}}}


def test_remove_article_reports_a_real_delete():
    calls = []
    client = platforms.DevToClient("k")
    client._request = lambda method, url, **kw: (calls.append(method), _Resp(204))[1]
    assert client.remove_article(4968458) == "deleted"
    assert calls == ["DELETE"]


def test_state_referenced_article_cannot_be_removed(monkeypatch, tmp_path):
    removed = []

    class _Fake:
        def __init__(self, key):
            pass

        def remove_article(self, article_id):
            removed.append(article_id)
            return "deleted"

    monkeypatch.setattr(pipeline, "DevToClient", _Fake)
    store = StateStore(tmp_path)
    store.data["published"] = {
        "horizon-rust/horizon": {
            "full_name": "horizon-rust/horizon",
            "articles": {"devto": {"id": 4971446, "url": "https://dev.to/x/y-3djc"}},
        }
    }
    result = pipeline.remove_devto_articles(
        SimpleNamespace(require=lambda *a: None, dev_api_key="k"),
        store,
        ids="4971446,4968458",
    )
    assert result["blocked"] == ["4971446"]
    assert removed == ["4968458"]
    assert [item["id"] for item in result["removed"]] == ["4968458"]


# ---------------------------------------------------------------------------
# C. one newest video, fresh, deduplicated, ad-free, readable


def _entry(video_id, hours_ago, *, now=None):
    now = now or datetime.now(timezone.utc)
    published = now - timedelta(hours=hours_ago)
    return {
        "video_id": video_id,
        "title": "Anthropic ships a new model checkpoint",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "published_at": published.isoformat(),
        "published": published,
        "description": "Sponsored by CodeRabbit. Try it free at https://coderabbit.link/x",
    }


def test_selector_takes_only_the_newest_video():
    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    entries = [_entry("AAA", 30, now=now), _entry("BBB", 5, now=now), _entry("CCC", 20, now=now)]
    chosen, reason = select_latest_news_entry(entries, set(), now=now)
    assert reason == ""
    assert chosen["video_id"] == "BBB"


def test_selector_rejects_anything_older_than_yesterday():
    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    chosen, reason = select_latest_news_entry([_entry("OLD", 72, now=now)], set(), now=now)
    assert chosen is None and reason == "video_too_old"


def test_selector_rejects_a_video_already_published():
    now = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
    chosen, reason = select_latest_news_entry([_entry("AAA", 5, now=now)], {"AAA"}, now=now)
    assert chosen is None and reason == "news_already_published"


def test_gemini_prompt_demands_ad_removal():
    captured = {}

    class _Gemini:
        def __init__(self, *a, **k):
            pass

        def generate_json(self, prompt, **kwargs):
            captured["prompt"] = prompt
            return {
                "eligible": True, "headline": "Vendor ships Product 2",
                "summary": "A factual summary.", "what_happened": ["Product 2 shipped"],
                "why_devs": "Developers can test it.", "confirmed": [],
                "video_only_claims": [], "official_name": "Vendor",
                "topic_key": "product-2", "discussion_question": "Would you use it?",
                "rejection_reason": "",
            }

    result = news.synthesize_brief(_entry("AAA", 5), {}, _Gemini(), use_video=True)
    assert "ADVERTISING MUST BE REMOVED" in captured["prompt"]
    assert "sponsor" in captured["prompt"].lower()
    # Second pass: the round-up must stay a round-up, with verbatim names
    # and no source attribution.
    assert "FOUR TO SEVEN" in captured["prompt"]
    assert "EXACT TECHNICAL NAMES ARE SACRED" in captured["prompt"]
    assert "NO SOURCE ATTRIBUTION" in captured["prompt"]
    assert result["eligible"] is True


def test_brief_is_sectioned_and_not_a_wall_of_text():
    rendered = format_ai_brief({
        "headline": "Claude Fable 5.5 and GPT-6 Astra lead a packed model week",
        "summary": "A stealth checkpoint and a 6X result landed together.",
        "models": ["Claude Fable 5.5", "GPT-6 Astra", "Qwen 4 27B"],
        "stories": [
            {
                "title": "Claude Fable 5.5 surfaces as a stealth checkpoint",
                "models": ["Claude Fable 5.5"],
                "what_happened": ["A checkpoint surfaced without an announcement."],
                "why_it_matters": "Re-run evals pinned to the previous checkpoint.",
            },
            {
                "title": "GPT-6 Astra posts a 6X gain without training",
                "models": ["GPT-6 Astra"],
                "what_happened": ["The gain came from inference-time guidance."],
                "why_it_matters": "Separate model quality from scaffolding.",
            },
        ],
        "why_devs": "Two model stories landed at once.",
        "confirmed": ["Both names are named in the chapter list."],
        "unconfirmed_claims": [],
        "official_url": "https://docs.anthropic.com/changelog",
        "official_name": "Anthropic changelog",
        "video_url": "https://www.youtube.com/watch?v=AAA",
        "discussion_question": "Which checkpoint do you test first?",
    })
    for section in ("In this brief", "Why developers should care", "Receipts", "Your take?"):
        assert section in rendered
    assert "\n\n" in rendered, "blank lines are required between blocks"
    assert "──────────────" in rendered
    assert "1️⃣" in rendered and "2️⃣" in rendered
    assert "Claude Fable 5.5" in rendered and "GPT-6 Astra" in rendered
    # The owner asked for no source attribution.
    assert "Sources" not in rendered
    assert "youtube.com" not in rendered and "WorldofAI" not in rendered
    assert "docs.anthropic.com" not in rendered
    assert "<b>React</b>" in rendered
    assert "Sponsored" not in rendered and "coderabbit" not in rendered.lower()


def _config():
    return SimpleNamespace(
        feature_ai_brief=True, require=lambda *a: None,
        telegram_bot_token="x", Gemini_api_key="x", Gemini_model="x",
        telegram_chat_id="@GitHub_Open_Source", telegram_owner_chat_id=None,
        imgbb_api_key="",
    )


def _wire_news(monkeypatch, tmp_path, *, brief=None):
    sent = []
    prompts = []

    class _Gemini:
        def __init__(self, *a, **k):
            pass

        def generate_json(self, prompt, **kwargs):
            prompts.append(prompt)
            return brief or {
                "eligible": True,
                "headline": "Claude Fable 5.5 and GPT-6 Astra lead a model round-up",
                "summary": "Two model stories landed together.",
                "models": ["Claude Fable 5.5", "GPT-6 Astra"],
                "stories": [
                    {"title": "Claude Fable 5.5 appears as a checkpoint",
                     "models": ["Claude Fable 5.5"],
                     "what_happened": ["A checkpoint surfaced."],
                     "why_it_matters": "Re-run pinned evals."},
                    {"title": "GPT-6 Astra posts a 6X gain",
                     "models": ["GPT-6 Astra"],
                     "what_happened": ["The gain came from inference-time guidance."],
                     "why_it_matters": "Separate model quality from scaffolding."},
                ],
                "why_devs": "Developers can test it.", "confirmed": [],
                "unconfirmed_claims": [], "official_name": "Vendor",
                "topic_key": "product-2", "discussion_question": "Would you use it?",
                "rejection_reason": "",
            }

    class _Telegram:
        def __init__(self, *a, **k):
            pass

        def send_text(self, chat_id, text, **kwargs):
            sent.append({"text": text, "kwargs": kwargs})
            return {"message_id": 777}

    card = tmp_path / "card.png"
    card.write_bytes(b"png")
    monkeypatch.setattr(pipeline, "GeminiClient", _Gemini)
    monkeypatch.setattr(pipeline, "TelegramClient", _Telegram)
    monkeypatch.setattr(pipeline, "extract_primary_urls", lambda e: [])
    monkeypatch.setattr(pipeline, "render_ai_brief_card", lambda *a, **k: card)
    monkeypatch.setattr(pipeline, "_host_local_image", lambda *a, **k: ("", ""))
    return sent, prompts


def test_news_publishes_one_brief_without_buttons_then_dedupes(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    entries = [_entry("OLD", 30, now=now), _entry("NEW", 5, now=now)]
    monkeypatch.setattr(pipeline, "fetch_worldofai_entries", lambda: entries)
    sent, prompts = _wire_news(monkeypatch, tmp_path)
    store = StateStore(tmp_path)

    first = pipeline.news_command(_config(), store)
    assert first["published"] is True
    assert first["video_id"] == "NEW"
    assert len(sent) == 1
    assert "buttons" not in sent[0]["kwargs"] and "reply_markup" not in sent[0]["kwargs"]
    assert "Why developers should care" in sent[0]["text"]
    assert "Claude Fable 5.5" in sent[0]["text"]
    assert "ADVERTISING MUST BE REMOVED" in prompts[0]

    second = pipeline.news_command(_config(), store)
    assert second["published"] is False
    assert second["reason"] == "news_already_published"
    assert len(sent) == 1, "the same video must never be published twice"
