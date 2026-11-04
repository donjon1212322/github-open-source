"""Owner corrections, 04.11.2026 (third pass): card legibility, factual
accuracy and an actually interesting digest.

The published post said "Claude Fable 5.5 Released". It was not released:
only a mysterious/leaked checkpoint plus release-date speculation. The
card also had a truncated headline, overlapping stat columns and 15 px
type. These tests encode all three fixes.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from ghos_v6 import pipeline
from ghos_v6.cards import (
    AI_BRIEF_BAND_TOP,
    AI_BRIEF_MIN_FONT,
    _font_size,
    _fit,
    ai_brief_card_columns,
    ai_brief_card_geometry,
    render_ai_brief_card,
)
from ghos_v6.news import (
    NEWS_SCHEMA,
    card_hook_text,
    STATUS_LABELS,
    _clean_unconfirmed,
    enforce_claim_status,
    format_ai_brief,
    synthesize_brief,
)
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]
LONG_HEADLINE = "Anthropic Fable 5.5, DeepSeek Harness, LongCat-2.5, and OpenAI GPT Updates"
HOOK = "A checkpoint nobody announced, a 6x math jump with no training, and a 1.6T model whose weights are still coming."


def _draw():
    return ImageDraw.Draw(Image.new("RGB", (1200, 630)))


# ---------------------------------------------------------------------------
# Card: no truncation, no overlap, readable type

def test_card_never_truncates_a_long_headline_or_overlaps_the_stat_band():
    draw = _draw()
    for headline in (LONG_HEADLINE, "A checkpoint nobody announced just beat the flagship on 3D code generation"):
        geo = ai_brief_card_geometry(draw, headline, HOOK)
        assert " ".join(geo["head_lines"]) == " ".join(headline.split()), "words were cut"
        assert geo["head_bottom"] <= geo["stand_top"]
        assert geo["stand_bottom"] <= AI_BRIEF_BAND_TOP - 8, "hook runs into the stat band"
        assert geo["head_top"] >= 120
        assert _font_size(geo["head_font"]) >= 36
        assert _font_size(geo["stand_font"]) >= 22


def test_card_stat_columns_do_not_collide_and_type_is_readable():
    draw = _draw()
    cols = ai_brief_card_columns("Claude Fable 5.5 · GPT-6 Astra · DeepSeek Harness", 5)
    assert len(cols) == 2, "three tiny columns were the original bug"
    left_end = cols[0][0] + cols[0][1]
    assert left_end < cols[1][0], "stat columns overlap"
    assert cols[1][0] + cols[1][1] <= 1110
    assert AI_BRIEF_MIN_FONT >= 21, "15-17 px type turned to mush in the 940 px preview"
    for _x, width, _label, value in cols:
        font = _fit(draw, value, "sans", 27, width, True, AI_BRIEF_MIN_FONT + 1)
        assert _font_size(font) >= 22
    assert "OFFICIAL SOURCE" not in " ".join(c[2] for c in cols)
    assert "WorldofAI" not in " ".join(c[3] for c in cols)


def test_rendered_card_has_the_expected_size_and_its_own_band(tmp_path):
    out = tmp_path / "card.png"
    render_ai_brief_card(LONG_HEADLINE, HOOK, "Claude Fable 5.5 · GPT-6 Astra", out, stories=4)
    assert Image.open(out).size == (1200, 630)
    source = (REPO / "src" / "ghos_v6" / "cards.py").read_text(encoding="utf-8")
    fn = source[source.index("def render_ai_brief_card("):source.index("def render_digest_card(")]
    assert "ai_brief_card_geometry" in fn, "the fixed non-overlapping bands must be used"
    assert "_wrap(draw, headline" not in fn, "a truncating wrap must not return"


# ---------------------------------------------------------------------------
# Facts: a leaked checkpoint is not a release

def _brief(status="leaked_checkpoint", title="Claude Fable 5.5 Released with 3D generation"):
    return {
        "headline": "Claude Fable 5.5 and GPT-6 Astra lead a model round-up",
        "hook": "A checkpoint nobody announced is beating the flagship on 3D code.",
        "models": ["Claude Fable 5.5", "GPT-6 Astra"],
        "stories": [
            {
                "title": title,
                "models": ["Claude Fable 5.5"],
                "what_happened": ["Anthropic released Claude Fable 5.5 as a new model."],
                "why_it_matters": "Re-run any eval pinned to Fable 5.1.",
                "confirmed": True,
                "status": status,
                "basis": "leaker screenshots and partner-access claims, unverified",
            },
            {
                "title": "GPT-6 Astra demonstrates weight-space programming",
                "models": ["GPT-6 Astra"],
                "what_happened": ["A 7.9% to 48.1% MATH-500 jump was reported."],
                "why_it_matters": "Try a targeted weight edit instead of retraining.",
                "confirmed": False,
                "status": "research_report",
                "basis": "researchers' own write-up, no independent reproduction",
            },
        ],
        "confirmed": ["Claude Fable 5.5 released"],
        "unconfirmed_claims": ["Unconfirmed rumours suggest a Friday launch (unconfirmed)"],
        "why_devs": "Only one of these is an official launch.",
        "discussion_question": "What would you test first?",
        "eligible": True,
        "topic_key": "claude-fable-5-5",
        "rejection_reason": "",
    }


def test_schema_requires_hook_status_and_basis():
    assert "hook" in NEWS_SCHEMA["required"]
    story = NEWS_SCHEMA["properties"]["stories"]["items"]
    assert {"status", "basis"} <= set(story["required"])


def test_prompt_demands_fact_verification_and_a_real_hook():
    captured = {}

    class _Gemini:
        def generate_json(self, prompt, **kwargs):
            captured["prompt"] = prompt
            return _brief()

    synthesize_brief({"title": "t", "description": "[Time Stamps]", "url": "u"}, {}, _Gemini(), use_video=False)
    for marker in (
        "VERIFY BEFORE YOU CLAIM", "is NOT a release", "FORBIDDEN outside status=official_release",
        "leaked_checkpoint", "basis is ONE short phrase", "MAKE IT INTERESTING",
        'NEVER "This digest covers', "Claude Fable 5.5 Released",
    ):
        assert marker in captured["prompt"], marker


def test_leaked_checkpoint_is_downgraded_and_clearly_labelled():
    cleaned = enforce_claim_status(_brief())
    story = cleaned["stories"][0]
    assert "Released" not in story["title"]
    assert "released" not in story["what_happened"][0]
    assert story["confirmed"] is False
    rendered = format_ai_brief(cleaned)
    assert STATUS_LABELS["leaked_checkpoint"] in rendered
    assert "leaker screenshots" in rendered
    assert "Released" not in rendered
    assert "✅ Claude Fable 5.5" not in rendered, "an unverified claim must not be a receipt"
    assert "Only ✅ is an officially confirmed launch." in rendered


def test_official_release_keeps_its_factual_verbs():
    brief = _brief(status="official_release", title="DeepSeek Harness Added Creator Mode")
    brief["stories"][0]["basis"] = "official model card and release notes"
    brief["stories"][0]["what_happened"] = ["Creator Mode builds plugins from a single prompt."]
    cleaned = enforce_claim_status(brief)
    assert "Added" in cleaned["stories"][0]["title"]
    assert cleaned["stories"][0]["confirmed"] is True
    assert STATUS_LABELS["official_release"] in format_ai_brief(cleaned)


def test_announced_but_not_shipped_is_not_called_released():
    cleaned = enforce_claim_status(_brief(status="announced_not_shipped"))
    assert "Announced" in cleaned["stories"][0]["title"]
    assert "Released" not in cleaned["stories"][0]["title"]


def test_hook_leads_the_post_and_receipts_show_evidence_levels():
    rendered = format_ai_brief(enforce_claim_status(_brief()))
    assert "exact names" in rendered and "<blockquote>" in rendered
    assert "This digest covers" not in rendered
    assert "A checkpoint nobody announced" not in rendered
    assert "🔬 1 research / demo" in rendered
    assert "◻️ 1 unverified" in rendered
    assert "<b>So what:</b>" in rendered


def test_doubled_unconfirmed_wording_is_cleaned():
    assert _clean_unconfirmed("Unconfirmed rumours suggest a Friday launch (unconfirmed)") == (
        "Rumours suggest a Friday launch"
    )
    rendered = format_ai_brief(enforce_claim_status(_brief()))
    assert "Unconfirmed: Unconfirmed" not in rendered
    assert "(unconfirmed)" not in rendered.lower()


def test_no_source_attribution_in_the_corrected_brief():
    rendered = format_ai_brief(enforce_claim_status(_brief())).lower()
    for marker in ("sources", "worldofai", "youtube", "video-only", "spotted via", "official source", "sponsored"):
        assert marker not in rendered, marker


# ---------------------------------------------------------------------------
# Pipeline: the card gets the hook, not a dry summary

def _config():
    return SimpleNamespace(
        feature_ai_brief=True, require=lambda *a: None,
        telegram_bot_token="x", Gemini_api_key="x", Gemini_model="x",
        telegram_chat_id="@GitHub_Open_Source", telegram_owner_chat_id=None,
        imgbb_api_key="",
    )


def test_news_command_hands_the_hook_and_story_count_to_the_card(monkeypatch, tmp_path):
    from datetime import datetime, timedelta, timezone

    published = datetime.now(timezone.utc) - timedelta(hours=5)
    entry = {
        "video_id": "i-wYjUriSzo", "title": "AI news",
        "url": "https://www.youtube.com/watch?v=i-wYjUriSzo",
        "published_at": published.isoformat(), "published": published,
        "description": "[Time Stamps]",
    }
    captured = {}
    card = tmp_path / "card.png"
    card.write_bytes(b"png")

    class _Gemini:
        def __init__(self, *a, **k):
            pass

        def generate_json(self, prompt, **kwargs):
            return dict(_brief())

    class _Telegram:
        def __init__(self, *a, **k):
            pass

        def send_text(self, chat_id, text, **kwargs):
            captured["text"] = text
            return {"message_id": 42}

    def _card(headline, standfirst, models_line, path, *, stories=0):
        captured.update(headline=headline, standfirst=standfirst, stories=stories)
        return card

    monkeypatch.setattr(pipeline, "GeminiClient", _Gemini)
    monkeypatch.setattr(pipeline, "TelegramClient", _Telegram)
    monkeypatch.setattr(pipeline, "fetch_worldofai_entries", lambda: [entry])
    monkeypatch.setattr(pipeline, "extract_primary_urls", lambda e: [])
    monkeypatch.setattr(pipeline, "render_ai_brief_card", _card)
    monkeypatch.setattr(pipeline, "_host_local_image", lambda *a, **k: ("", ""))

    result = pipeline.news_command(_config(), StateStore(tmp_path))
    assert result["published"] is True
    assert "exact names" in captured["standfirst"]
    assert "This digest covers" not in captured["standfirst"]
    assert captured["stories"] == 2
    assert "Leaked checkpoint" in captured["text"]
    assert "Released" not in captured["text"]


def test_card_never_cuts_the_hook_mid_phrase():
    long_hook = (
        "A checkpoint nobody announced is beating the flagship on 3D code, "
        "a 0.016% weight edit moved MATH-500 from 7.9% to 48.1%, and a 1.6T "
        "model's weights are still coming soon."
    )
    short = card_hook_text({"hook": long_hook})
    assert len(short) <= 115
    assert short.endswith("…"), "a shortened hook must mark the cut explicitly"
    assert not short[:-1].endswith(("a", "the", "and", "of", "on")), "no dangling article"
    geo = ai_brief_card_geometry(_draw(), LONG_HEADLINE, short)
    assert " ".join(geo["stand_lines"]) == short, "the card must not chop the hook"
    assert geo["stand_bottom"] <= AI_BRIEF_BAND_TOP - 8


def test_long_card_hook_still_fits_the_fixed_band():
    draw = _draw()
    for hook in (
        HOOK,
        "Six names, one leaked checkpoint, one unverified 6x result, and an official post whose weights are still coming soon.",
        "One leaked checkpoint, one 0.016% weight edit, one 6x MATH-500 claim, and a 1.6T model whose weights are still coming soon: four stories, zero official launches.",
    ):
        geo = ai_brief_card_geometry(draw, LONG_HEADLINE, hook)
        assert " ".join(geo["head_lines"]) == " ".join(LONG_HEADLINE.split())
        assert " ".join(geo["stand_lines"]) == hook, "the card chopped the hook"
        assert geo["stand_bottom"] <= AI_BRIEF_BAND_TOP - 8
        assert _font_size(geo["stand_font"]) >= 22


def test_card_hook_prefers_the_dedicated_short_field():
    assert card_hook_text({"card_hook": "One checkpoint, one claim, no launch.", "hook": HOOK}) == (
        "One checkpoint, one claim, no launch."
    )
    assert card_hook_text({"hook": "A checkpoint nobody announced."}) == (
        "A checkpoint nobody announced."
    )
    assert card_hook_text({}) == ""


# ---------------------------------------------------------------------------
# Fourth pass within the same correction: the live refresh still labelled an
# S-1 filing and a demo as "✅ Official release", and opened on a dry recap.

def _story(status, title, bullets, basis):
    return {
        "title": title, "models": ["Claude Fable 5.5"],
        "what_happened": bullets, "why_it_matters": "Re-run it yourself.",
        "confirmed": True, "status": status, "basis": basis,
    }


def _brief_with(*stories, hook=None, headline=None):
    return {
        "headline": headline or "Anthropic S-1 filings reveal RSI milestone while Claude Fable 5.5 and LongCat-2.5 surface",
        "hook": hook or "Anthropic's amended S-1 disclosures suggest models may imminently cross RSI thresholds for accelerated AI progress.",
        "summary": "A round-up of model news.",
        "models": ["Claude Fable 5.5", "GPT-6 Astra", "LongCat-2.5"],
        "stories": list(stories),
        "why_devs": "Check the evidence level before you re-run anything.",
        "discussion_question": "What would you test first?",
        "eligible": True, "topic_key": "t", "rejection_reason": "",
    }


def test_policy_filing_is_not_an_official_release():
    brief = _brief_with(
        _story("official_release", "Anthropic amends S-1 filing to disclose RSI policy thresholds",
               ["Anthropic updated its S-1 filing with the SEC."], "official model card"),
        _story("leaked_checkpoint", "Claude Fable 5.5 checkpoint surfaces",
               ["An unannounced checkpoint appeared."], "leaker screenshots, unverified"),
    )
    cleaned = enforce_claim_status(brief)
    assert cleaned["stories"][0]["status"] == "official_document"
    assert "filing" in cleaned["stories"][0]["basis"]
    rendered = format_ai_brief(cleaned)
    assert "📄 Official filing · not a product launch" in rendered
    assert "✅ Official release" not in rendered
    assert "✅ 1 official" not in rendered
    assert "📄 1 filing / announced" in rendered


def test_demo_and_leak_evidence_downgrade_a_false_release_label():
    cases = (
        ("A vendor demo of Creator Mode needed several prompt rounds",
         ["Creator Mode was shown in an open beta demo."], "demo_or_benchmark"),
        ("An unannounced checkpoint leaked in partner-access screenshots",
         ["A checkpoint appeared in an unverified leak."], "leaked_checkpoint"),
        ("The weights are coming soon on GitHub",
         ["GitHub and HuggingFace are marked Coming soon."], "announced_not_shipped"),
        ("MIT researchers report a weight-space result",
         ["Researchers published their own write-up, not replicated."], "research_report"),
    )
    for title, bullets, expected in cases:
        cleaned = enforce_claim_status(
            _brief_with(_story("official_release", title, bullets, "official model card"))
        )
        assert cleaned["stories"][0]["status"] == expected, title


def test_dry_corporate_hook_is_replaced_by_a_factual_teaser():
    cleaned = enforce_claim_status(_brief_with(
        _story("leaked_checkpoint", "Claude Fable 5.5 checkpoint surfaces",
               ["An unannounced checkpoint appeared."], "leaker screenshots, unverified"),
    ))
    hook = cleaned["hook"]
    assert not hook.lower().startswith("anthropic")
    assert "S-1" not in hook and "RSI" not in hook
    assert any(ch.isdigit() for ch in hook), "a concrete number must be present"
    assert "exact names" in hook and "?" in hook
    assert "Claude Fable 5.5" in hook
    assert "6x" not in hook and "0.016%" not in hook
    assert len(card_hook_text(cleaned)) <= 115
    rendered = format_ai_brief(cleaned)
    assert "Anthropic's amended S-1" not in rendered
    assert "<blockquote>" in rendered


def test_headline_leads_with_model_news_not_the_filing():
    cleaned = enforce_claim_status(_brief_with(
        _story("leaked_checkpoint", "Claude Fable 5.5 checkpoint surfaces",
               ["An unannounced checkpoint appeared."], "leaker screenshots, unverified"),
    ))
    assert cleaned["headline"].startswith("Claude Fable 5.5")
    assert "S-1" not in cleaned["headline"]
    assert "lead the model watch" in cleaned["headline"]
    assert "Anthropic S-1 filings" not in format_ai_brief(cleaned)


def test_a_fluent_but_wrong_hook_never_reaches_the_post():
    """The live 05:01 refresh attributed GPT-6 Astra's result to Fable 5.5."""
    wrong = "Claude Fable 5.5 achieves a 6x math improvement by directly editing 0.016% of neural network weights."
    brief = _brief_with(
        _story("leaked_checkpoint", "Claude Fable 5.5 checkpoint surfaces",
               ["An unannounced checkpoint appeared."], "leaker screenshots, unverified"),
        _story("research_report", "GPT-6 Astra demonstrates weight-space programming",
               ["MATH-500 moved from 7.9% to 48.1% on Qwen3.5-0.8B."], "researchers' own write-up"),
        hook=wrong,
    )
    cleaned = enforce_claim_status(brief)
    rendered = format_ai_brief(cleaned)
    assert "6x math improvement" not in rendered
    assert "0.016% of neural network weights" not in rendered
    assert wrong not in rendered
    assert "exact names" in rendered
    assert "what would you actually re-run first?" in rendered
    assert len(card_hook_text(cleaned)) <= 115
