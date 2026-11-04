"""Owner decision (2026-11-03): dev.to articles are standalone posts.

The screenshot showed "Open Source Finds (2 Part Series)" above the article.
dev.to groups every article that carries the same `series` name; a single
hardcoded string in `publish_article` therefore built a growing series out of
daily posts. The owner wants plain posts, so:

* POST never sends `series`;
* PUT sends `series: null`, which Forem documents as removal from a series;
* a maintenance command detaches already published legacy articles.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ghos_v6 import pipeline, platforms
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]
FLOW = REPO / ".github" / "workflows"
RECORD = {
    "full_name": "horizon-rust/horizon",
    "name": "horizon",
    "description": "Minecraft reimplemented in Rust",
    "url": "https://github.com/horizon-rust/horizon",
    "stars": 6107, "forks": 822, "language": "Rust",
    "license": {"spdx_id": "MIT"}, "topics": ["rust"],
    "key_features": ["Native loop", "Vanilla saves"],
    "telegraph_url": "https://telegra.ph/horizon",
    "image_url": "https://i.ibb.co/abc/card.png",
    "discussion_question": "Would you run it?",
}
GENERATED = {
    "article_markdown": (
        " ## What it is A clean-room engine. ## How it works It uses harnesses. "
        "## Key features - Native loop ## Getting started Clone it. "
        "## When not to use it Casual players."
    ),
    "key_features": ["Native loop"],
    "surfaces": {},
}


class _Resp:
    status_code = 201

    def json(self) -> dict:
        return {"id": 1, "url": "https://dev.to/x/y"}


def _capture(monkeypatch, client: platforms.DevToClient) -> list[tuple]:
    calls: list[tuple] = []

    def fake(method, url, **kwargs):
        calls.append((method, url, kwargs.get("json")))
        return _Resp()

    monkeypatch.setattr(client.session, "request", fake)
    return calls


def test_new_article_is_a_standalone_post_not_a_series(monkeypatch):
    client = platforms.DevToClient("dummy")
    calls = _capture(monkeypatch, client)
    client.publish_article(RECORD, GENERATED)
    payload = calls[0][2]["article"]
    assert "series" not in payload, "any series name groups all daily posts together"
    assert calls[0][0] == "POST"


def test_update_detaches_an_existing_article_from_any_series(monkeypatch):
    client = platforms.DevToClient("dummy")
    calls = _capture(monkeypatch, client)
    client.update_article(4968877, RECORD, GENERATED)
    payload = calls[0][2]["article"]
    assert payload["series"] is None, "null is Forem's documented series removal"
    assert calls[0][0] == "PUT"
    assert calls[0][1].endswith("/api/articles/4968877")


def test_clear_series_sends_only_a_minimal_null_put(monkeypatch):
    client = platforms.DevToClient("dummy")
    calls = _capture(monkeypatch, client)
    client.clear_series(4968458)
    method, url, body = calls[0]
    assert method == "PUT" and url.endswith("/api/articles/4968458")
    assert body == {"article": {"published": True, "series": None}}
    assert "body_markdown" not in body["article"], "never re-render a legacy article"


def test_unseries_command_collects_explicit_and_state_ids(monkeypatch, tmp_path):
    calls: list[str] = []

    class FakeClient:
        def __init__(self, key):
            self.key = key

        def clear_series(self, article_id):
            calls.append(str(article_id))
            return {"id": article_id}

    monkeypatch.setattr(pipeline, "DevToClient", FakeClient)
    store = StateStore(tmp_path)
    store.data["published"] = {
        "kajisho5/ffmpeg-skill": {"articles": {"devto": {"id": 4968877}}}
    }
    config = SimpleNamespace(require=lambda *a: None, dev_api_key="dummy")
    result = pipeline.unseries_articles(config, store, ids="4968458, 4968458")
    assert result["ok"] is True
    assert result["cleared"] == 2
    assert calls == ["4968458", "4968877"], "explicit + state ids, deduplicated once"


def test_unseries_command_rejects_anything_but_numeric_ids(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "DevToClient", lambda key: None)
    store = StateStore(tmp_path)
    config = SimpleNamespace(require=lambda *a: None, dev_api_key="dummy")
    with pytest.raises(ValueError, match="invalid dev.to article id"):
        pipeline.unseries_articles(config, store, ids="1; curl evil.example")


def test_unseries_command_tolerates_a_deleted_legacy_article(monkeypatch, tmp_path):
    class FakeClient:
        def __init__(self, key):
            pass

        def clear_series(self, article_id):
            if str(article_id) == "4968458":
                raise platforms.PlatformError("dev.to series cleanup HTTP 404: not found")
            return {"id": article_id}

    monkeypatch.setattr(pipeline, "DevToClient", FakeClient)
    store = StateStore(tmp_path)
    config = SimpleNamespace(require=lambda *a: None, dev_api_key="dummy")
    result = pipeline.unseries_articles(config, store, ids="4968458,4968877")
    assert result["missing"] == ["4968458"]
    assert result["cleared"] == 1


# ---------------------------------------------------------------------------
# Wiring and repository hygiene


def test_workflow_exposes_the_unseries_command_and_validates_its_input():
    text = (FLOW / "pipeline.yml").read_text(encoding="utf-8")
    assert "article-unseries" in text
    assert "article|article-update|article-unseries|devto-remove|news" in text, \
        "all dev.to maintenance commands must stay in the dispatch whitelist"
    assert "ARTICLE_IDS: ${{ inputs.article_ids }}" in text
    assert '^[0-9,[:space:]]{0,200}$' in text
    assert "ghos-v6 article-unseries --ids" in text
    assert "ghos-v6 devto-remove --ids" in text
    assert "^[0-9,[:space:]]{1,200}$" in text


def test_no_series_name_is_left_in_the_publishing_code():
    for path in (REPO / "src" / "ghos_v6").rglob("*.py"):
        assert "Open Source Finds" not in path.read_text(encoding="utf-8"), path


def test_dependabot_version_updates_are_gone():
    """Pinned action SHAs + hashed locks make these PRs unmergeable noise."""
    assert not (REPO / ".github" / "dependabot.yml").exists()
    for path in FLOW.glob("*.yml"):
        assert "dependabot" not in path.read_text(encoding="utf-8").lower(), path
    ops = (REPO / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
    assert "Dependabot, CodeQL и gitleaks включены" not in ops
