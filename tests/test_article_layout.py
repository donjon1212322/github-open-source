"""Layout, return-loop and platform-surface contracts (2026-11-03).

The owner rejected the flat dev.to article, asked for a visible separator
between the channel signature and the hashtags, and removed Mastodon from
the project after the account was banned for spam.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from ghos_v6 import Gemini, content_adapters as ca, formatter, news, pipeline, platforms
from ghos_v6.config import Config

REPO = Path(__file__).resolve().parents[1]
REAL = "https://t.me/GitHub_Open_Source"

FLAT_DEVTO = (
    " ## What it is Horizon is a clean-room Rust engine for Minecraft. "
    "## How it works It uses differential harnesses against vanilla. "
    "## Key features - Native game loop - Vanilla save support - MCP control "
    "## Getting started Clone the repo and build with Cargo. "
    "## When not to use it Casual players will still miss features."
)


def _record() -> dict:
    return {
        "full_name": "horizon-rust/horizon",
        "name": "horizon",
        "description": "Minecraft Java Edition reimplemented in Rust",
        "url": "https://github.com/horizon-rust/horizon",
        "stars": 6107,
        "forks": 822,
        "language": "Rust",
        "license": {"spdx_id": "MIT"},
        "topics": ["rust", "minecraft"],
        "key_features": ["Native game loop", "Vanilla saves", "MCP control"],
        "telegraph_url": "https://telegra.ph/horizon-11-03",
        "image_url": "https://i.ibb.co/abc/card.png",
        "discussion_question": "Would you run it in production?",
    }


def _generated() -> dict:
    return {
        "article_markdown": FLAT_DEVTO,
        "key_features": ["Native game loop", "Vanilla saves", "MCP control"],
        "surfaces": {"devto_body": FLAT_DEVTO},
    }


def test_devto_article_has_real_sections_not_a_wall_of_text():
    body = ca.build_devto_body(_record(), _generated())
    headings = re.findall(r"(?m)^## .+$", body)
    assert len(headings) >= 4, headings
    assert not re.search(r"\w ## ", body), "inline heading markers render as prose"
    assert re.search(r"(?m)^- ", body), "feature lists must be real Markdown bullets"
    assert "\n\n" in body
    assert "> **TL;DR:**" in body
    assert REAL in body
    assert "## Discuss" in body


def test_devto_summary_is_visually_separated_from_the_body():
    body = ca.build_devto_body(_record(), _generated())
    assert f"**Daily picks:** [Follow GitHub Open Source on Telegram]({REAL})\n\n---\n\n## " in body


def test_markdown_surfaces_keep_their_newlines():
    assert "devto_body" in Gemini.MARKDOWN_SURFACE_KEYS
    multi = "## What it is\n\nA paragraph.\n\n## Key features\n\n- One\n- Two"
    assert "\n\n" in Gemini._clean_markdown(multi, 9_000)
    assert Gemini._clean_text(multi, 9_000).count("\n") == 0
    assert "\n\n" in ca.surface_markdown(_generated(), "devto_body") or FLAT_DEVTO.startswith(" ##")


def test_channel_signature_is_separated_from_hashtags():
    post = formatter.format_project_post(_record())
    assert formatter.CHANNEL_TAG_DIVIDER in post
    signature = "daily open-source picks"
    tags = "#opensource"
    assert signature in post and tags in post
    assert post.index(signature) < post.index(formatter.CHANNEL_TAG_DIVIDER) < post.index(tags)


def test_ai_brief_separates_the_channel_from_the_tags():
    brief = news.format_ai_brief(
        {
            "headline": "A new model shipped",
            "what_happened": ["The model was released"],
            "why_devs": "It changes local inference",
            "confirmed": ["Release notes published"],
        }
    )
    assert formatter.CHANNEL_TAG_DIVIDER in brief
    assert brief.index("daily open-source picks") < brief.index(
        formatter.CHANNEL_TAG_DIVIDER
    ) < brief.index("#AINews #OpenSource")


def test_mastodon_is_gone_from_the_project():
    assert not hasattr(platforms, "MastodonClient")
    assert not hasattr(Config(), "feature_mastodon")
    assert not hasattr(Config(), "mastodon_access_token")
    assert "mastodon" not in inspect.getsource(pipeline.process_crossposts).lower()
    assert not any("mastodon" in key.lower() for key in Gemini.SURFACE_KEYS)
    for path in (
        REPO / ".env.example",
        REPO / ".env.keys.template",
        *sorted((REPO / ".github" / "workflows").glob("*.yml")),
    ):
        assert "mastodon" not in path.read_text(encoding="utf-8").lower(), path.name


def test_article_update_command_is_wired_end_to_end():
    workflow = (REPO / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    assert "article-update" in workflow
    # It must be in BOTH the input choice and the shell whitelist: a command
    # that is only in the case statement is rejected by the API with 422.
    choices = workflow.split("options:", 1)[1].split("slot:", 1)[0]
    assert "- article-update" in choices
    whitelist = workflow.split('case "$COMMAND" in', 1)[1].split("*)", 1)[0]
    assert "article-update" in whitelist
    assert hasattr(pipeline, "update_recent_article")
    assert hasattr(platforms.DevToClient, "update_article")
    cli_src = (REPO / "src" / "ghos_v6" / "cli.py").read_text(encoding="utf-8")
    assert '"article-update"' in cli_src


def test_article_update_uses_put_not_a_duplicate_post(monkeypatch):
    from ghos_v6 import platforms as pl

    client = pl.DevToClient("dummy-key")
    calls: dict[str, str] = {}

    class _Resp:
        status_code = 200

        def json(self) -> dict:
            return {"url": "https://dev.to/example"}

    def fake_request(method, url, **kwargs):
        calls["url"] = url
        calls["method"] = method.lower()
        return _Resp()

    monkeypatch.setattr(client.session, "request", fake_request)
    result = client.update_article(4968180, _record(), _generated())
    assert calls["method"] == "put"
    assert calls["url"] == "https://dev.to/api/articles/4968180"
    assert result["url"].startswith("https://dev.to/")


def test_devto_throttling_is_retried_before_giving_up(monkeypatch):
    from ghos_v6 import platforms as pl

    client = pl.DevToClient("dummy-key")
    seen: list[int] = []

    class _Resp:
        status_code = 429
        headers: dict[str, str] = {}

        def json(self) -> dict:
            return {"error": "Retry later"}

    def fake_request(method, url, **kwargs):
        seen.append(1)
        return _Resp()

    monkeypatch.setattr(client.session, "request", fake_request)
    monkeypatch.setattr(pl.time, "sleep", lambda _s: None)
    response = client._request("GET", "https://dev.to/api/articles/me/all")
    assert len(seen) == 4, "four bounded attempts"
    assert response.status_code == 429


def test_article_id_is_resolved_from_the_editable_article_list():
    from ghos_v6.platforms import DevToClient

    url = "https://dev.to/githubopensource/horizon-3pc1"
    items = [
        {"id": 111, "url": "https://dev.to/githubopensource/other-1"},
        {"id": 222, "url": url, "path": "/githubopensource/horizon-3pc1"},
    ]
    assert DevToClient.resolve_article_id(items, url, 999) == (222, "url_match")
    assert DevToClient.resolve_article_id([], url, 999) == (999, "state_id")


def test_article_update_recreates_a_deleted_article(monkeypatch, tmp_path):
    """A 404 from PUT must not strand the fix or duplicate a live article."""
    from ghos_v6 import pipeline as pl
    from ghos_v6.config import Config
    from ghos_v6.state import StateStore

    store = StateStore(tmp_path)
    store.data = {
        "published": {
            "owner/demo": {
                "full_name": "owner/demo",
                "name": "demo",
                "url": "https://github.com/owner/demo",
                "published_at": "2026-11-03T09:00:00Z",
                "generated": _generated(),
                "articles": {"devto": {"id": 1, "url": "https://dev.to/x/old"}},
            }
        },
        "events": [],
    }

    class FakeClient:
        def __init__(self, key):
            self.key = key

        def my_articles(self):
            return []

        @staticmethod
        def resolve_article_id(items, url, fallback):
            return fallback, "state_id"

        def update_article(self, article_id, record, generated):
            raise pl.PlatformError("dev.to update HTTP 404: not found")

        def publish_article(self, record, generated):
            return {"id": 999, "url": "https://dev.to/x/new"}

    monkeypatch.setattr(pl, "DevToClient", FakeClient)
    result = pl.update_recent_article(Config(dev_api_key="x"), store)
    assert result["action"] == "created"
    assert result["created"] == 1 and result["updated"] == 0
    assert store.data["published"]["owner/demo"]["articles"]["devto"]["url"].endswith("/new")


def test_env_key_template_asks_only_for_the_missing_values():
    text = (REPO / ".env.keys.template").read_text(encoding="utf-8")
    import re

    for obsolete in (
        "FACEBOOK_PAGE_ID",
        "FACEBOOK_PAGE_ACCESS_TOKEN",
        "FEATURE_FACEBOOK",
        "MASTODON_ACCESS_TOKEN",
        "MASTODON_INSTANCE_URL",
        "FEATURE_MASTODON",
    ):
        assert not re.search(rf"(?m)^{obsolete}=", text), obsolete
    for missing in (
        "GOOGLE_SERVICE_ACCOUNT_JSON",
        "FACEBOOK_SHEETS_ID",
        "FACEBOOK_SHEETS_RANGE",
    ):
        assert missing in text
    assert "@GitHub_Open_Source" in text
