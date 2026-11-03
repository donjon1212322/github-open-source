from ghos_v6.formatter import utf16_len, visible_text
from ghos_v6.news import format_ai_brief


def test_ai_brief_has_primary_source_and_reaction_cta():
    brief = {
        "headline": "Example model adds a documented offline mode",
        "what_happened": ["The maintainers published release notes with an offline mode."],
        "confirmed": ["Offline mode is described in the official changelog."],
        "video_only_claims": ["The video claims a broader rollout next week."],
        "why_devs": "Builders can evaluate the feature from primary documentation.",
        "discussion_question": "Would offline mode change how you use this tool?",
        "official_url": "https://example.com/changelog",
        "official_name": "Official changelog",
    }
    rendered = format_ai_brief(brief)
    assert 'href="https://example.com/changelog"' in rendered
    assert "Official changelog" in rendered
    assert "<b>React</b>" in rendered
    assert "@GitHub_Open_Source" in rendered
    assert "#AINews #OpenSource" in rendered
    assert utf16_len(visible_text(rendered)) <= 2200
