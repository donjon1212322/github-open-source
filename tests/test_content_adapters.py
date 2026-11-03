from ghos_v6.content_adapters import (
    build_bluesky_text,
    build_devto_body,
    build_devto_title,
    build_Facebook_text,
    devto_tags,
    hashtags,
    image_alt_text,
)
from ghos_v6.platforms import build_link_facets


def _record_and_generated():
    record = {
        "name": "ExampleTool",
        "full_name": "example/tool",
        "url": "https://github.com/example/tool",
        "description": "A useful developer tool",
        "language": "Python",
        "stars": 1500,
        "forks": 120,
        "topics": ["cli", "developer-tools"],
        "license": {"spdx_id": "MIT"},
        "telegram_link": "https://t.me/c/123456/42",
        "telegraph_url": "https://telegra.ph/example-tool",
        "visual_kind": "card",
    }
    generated = {
        "hook": "Turns messy terminal output into clean, shareable reports",
        "summary": (
            "ExampleTool captures command output and turns it into structured "
            "reports that are easier to review in pull requests. It is built "
            "for small teams that live in the terminal."
        ),
        "key_features": [
            "Captures stdout and stderr with timestamps",
            "Exports Markdown and JSON reports",
            "Runs offline with no hosted service",
        ],
        "who_for": "CLI-heavy developers and maintainers",
        "why_now": "Recent releases added JSON export and the project is actively maintained.",
        "verdict": "Worth evaluating if your team shares terminal output often.",
        "quick_start": "pip install example-tool\nexample-tool run pytest",
        "discussion_question": "Would structured terminal reports help your code reviews?",
        "category": "developer-tools",
        "social_text": "Structured terminal reports for cleaner pull-request reviews.",
        "article_title": "ExampleTool: what it does and why developers should care",
        "article_markdown": "## What it is\n\nExample body.\n\n## Quick start\n\n```bash\npip install example-tool\n```",
    }
    return record, generated


def test_bluesky_text_fits_and_uses_repo_not_private_telegram_link():
    record, generated = _record_and_generated()
    text = build_bluesky_text(record, generated)
    assert len(text) <= 300
    assert "https://github.com/example/tool" in text
    assert "#OpenSource" in text
    assert text.startswith("🐍 ")
    assert "Daily picks: https://t.me/GitHub_Open_Source" in text
    assert "t.me/c/" not in text



def test_facebook_text_has_context_question_and_budget():
    record, generated = _record_and_generated()
    text = build_Facebook_text(record, generated)
    assert len(text) <= 1800
    assert "📌 WHY IT MATTERS" in text
    assert "━━━━━━━━━━━━" in text
    assert "Would structured terminal reports help your code reviews?" in text
    assert "Daily open-source picks on Telegram: https://t.me/GitHub_Open_Source" in text
    assert "GitHub: https://github.com/example/tool" in text
    assert "Give the repo" not in text


def test_devto_wrapper_and_alt_text():
    record, generated = _record_and_generated()
    assert devto_tags(record, generated)[0] == "opensource"
    assert len(devto_tags(record, generated)) <= 4
    assert build_devto_title(record, generated).startswith("ExampleTool:")
    body = build_devto_body(record, generated)
    assert "**TL;DR:**" in body
    assert "## Discuss" in body
    assert "No hands-on test is claimed" in body
    alt = image_alt_text(record, generated)
    assert "example/tool" in alt
    assert len(alt) > len("example/tool")


def test_bluesky_facets_include_clickable_hashtags_with_utf8_offsets():
    text = "🚀 Read https://example.com/post #OpenSource"
    facets = build_link_facets(text)
    encoded = text.encode("utf-8")
    rendered = {
        encoded[item["index"]["byteStart"] : item["index"]["byteEnd"]].decode(): item["features"][0]["$type"]
        for item in facets
    }
    assert rendered["https://example.com/post"] == "app.bsky.richtext.facet#link"
    assert rendered["#OpenSource"] == "app.bsky.richtext.facet#tag"


def test_bluesky_truncation_never_cuts_a_word_and_adds_a_period():
    record, generated = _record_and_generated()
    generated["summary"] = (
        "ExampleTool connects local commands to a structured analysis "
        "workflow for inspecting runtime behavior across many projects "
        "without a hosted service or external account requirement today"
    )
    text = build_bluesky_text(record, generated)
    assert len(text) <= 300
    assert " be." not in text
    assert "Captures stdout and stderr with timestamps" in text or "…" in text


def test_tag_cleanup_and_public_telegram_discussion_link():
    record, generated = _record_and_generated()
    record["language"] = "TypeScript"
    record["topics"] = ["antidetect", "agent-skills"]
    record["telegram_link"] = "https://t.me/GitHub_Open_Source/123"
    generated["category"] = "Security and Analysis"

    tags = hashtags(record, generated)
    assert "#TypeScript" in tags
    assert "#Security" in tags
    assert "SecurityAndAnalysis" not in tags
    assert "Antidetect" not in tags
    assert devto_tags(record, generated) == ["opensource", "typescript", "security", "ai"]

    assert "Discuss this pick on Telegram" in build_devto_body(record, generated)
