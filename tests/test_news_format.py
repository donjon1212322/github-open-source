from ghos_v6.formatter import utf16_len, visible_text
from ghos_v6.news import format_ai_brief


def _brief():
    return {
        "headline": "Claude Fable 5.5 and GPT-6 Astra lead a packed model week",
        "summary": "A stealth checkpoint and a 6X inference-time result landed together.",
        "models": ["Claude Fable 5.5", "GPT-6 Astra", "Qwen 4 27B"],
        "stories": [
            {
                "title": "Claude Fable 5.5 surfaces as a stealth checkpoint",
                "models": ["Claude Fable 5.5"],
                "what_happened": ["A new checkpoint appeared without an announcement."],
                "why_it_matters": "Re-run evals pinned to the previous checkpoint.",
            },
            {
                "title": "GPT-6 Astra posts a 6X gain without training",
                "models": ["GPT-6 Astra"],
                "what_happened": ["The gain came from inference-time guidance."],
                "why_it_matters": "Separate model quality from scaffolding gains.",
            },
        ],
        "why_devs": "Two separate model stories landed at once.",
        "confirmed": ["Both names appear in the round-up."],
        "unconfirmed_claims": ["A release date remains unconfirmed."],
        "discussion_question": "Which checkpoint do you test first?",
        "official_url": "https://docs.anthropic.com/changelog",
        "official_name": "Anthropic changelog",
        "video_url": "https://www.youtube.com/watch?v=i-wYjUriSzo",
    }


def test_ai_brief_has_no_source_attribution_and_keeps_the_cta():
    rendered = format_ai_brief(_brief())
    # Owner decision 04.11.2026, second pass: do not name the source.
    for marker in (
        "Sources", "WorldofAI", "youtube.com/watch", "Video-only",
        "Spotted via", "OFFICIAL SOURCE", "official changelog",
        "docs.anthropic.com", "Primary source", "coderabbit", "Sponsored",
    ):
        assert marker.lower() not in rendered.lower(), marker
    assert "<b>React</b>" in rendered
    assert "@GitHub_Open_Source" in rendered
    assert "#AINews #OpenSource" in rendered
    assert "\n\n" in rendered, "blank lines are required between blocks"
    assert "──────────────" in rendered
    assert "1️⃣" in rendered and "2️⃣" in rendered
    assert "In this brief" in rendered
    assert utf16_len(rendered) <= 3920
    assert utf16_len(visible_text(rendered)) <= 3350


def test_every_exact_technical_name_survives_headline_cleanup():
    brief = _brief()
    brief["headline"] = "HUGE! Claude Fable 5.5 and GPT-6 Astra beat Qwen 4 27B"
    rendered = format_ai_brief(brief)
    for name in ("Claude Fable 5.5", "GPT-6 Astra", "Qwen 4 27B"):
        assert name in rendered, f"{name} must stay verbatim"
    assert "HUGE" not in rendered
