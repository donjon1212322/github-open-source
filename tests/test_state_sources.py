from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

from ghos_v6.cards import render_digest_card, render_project_card
from ghos_v6.config import Config
from ghos_v6.news import parse_rss, prefilter_entry
from ghos_v6.platforms import build_link_facets
from ghos_v6.sources import extract_media_urls, full_name_from_url
from ghos_v6.state import StateStore


def test_test_target_never_falls_back_to_main(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("PUBLISH_TARGET", "test")
    monkeypatch.setenv("TELEGRAM_MAIN_CHANNEL_ID", "@GitHub_Open_Source")
    monkeypatch.delenv("TELEGRAM_TEST_CHANNEL_ID", raising=False)
    config = Config.from_env()
    with pytest.raises(RuntimeError, match="TELEGRAM_TEST_CHANNEL_ID"):
        _ = config.telegram_chat_id


def test_state_deduplicates_by_full_name(tmp_path):
    store = StateStore(tmp_path)
    candidate = {"full_name": "Example/Tool", "repo_id": 42, "name": "Tool"}
    store.record_published({**candidate, "telegram_message_id": 7})
    store.save()
    assert store.is_published({"full_name": "example/tool"})
    assert store.events_path.read_text(encoding="utf-8").count("project_published") == 1


def test_full_name_and_media_extraction():
    assert full_name_from_url("https://github.com/example/tool/issues/1") == "example/tool"
    readme = """
![build](https://img.shields.io/github/actions/workflow/status/example/tool/ci.yml)
![demo](assets/screenshot.png)
<img src="https://example.com/preview.jpg">
"""
    urls = extract_media_urls(readme, "example/tool", "main")
    assert urls[0].endswith("assets/screenshot.png")
    assert not any("shields.io" in url for url in urls)


RSS = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/">
  <entry>
    <yt:videoId>abc123</yt:videoId>
    <title>New open source coding model released</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abc123"/>
    <published>{published}</published>
    <media:group>
      <media:description>Official docs: https://huggingface.co/example/model</media:description>
      <media:thumbnail url="https://i.ytimg.com/vi/abc123/hqdefault.jpg"/>
    </media:group>
  </entry>
</feed>
"""


def test_worldofai_rss_and_prefilter():
    now = datetime.now(timezone.utc)
    entries = parse_rss(RSS.format(published=(now - timedelta(hours=3)).isoformat()))
    assert entries[0]["video_id"] == "abc123"
    ok, reason = prefilter_entry(entries[0], now=now)
    assert ok, reason

    sponsored = dict(entries[0], title="Sponsored: new AI model")
    ok, reason = prefilter_entry(sponsored, now=now)
    assert not ok and reason == "sponsored"


def test_bluesky_facets_use_utf8_offsets():
    text = "🚀 Read https://example.com/post now"
    facets = build_link_facets(text)
    assert facets
    index = facets[0]["index"]
    encoded = text.encode("utf-8")
    assert encoded[index["byteStart"]:index["byteEnd"]].decode() == "https://example.com/post"


def test_cards_render(tmp_path):
    candidate = {
        "name": "Pawnix",
        "description": "Terminal chess",
        "language": "Python",
        "stars": 1240,
        "forks": 86,
        "contributors_count": 9,
        "license": {"spdx_id": "MIT"},
        "topics": ["cli"],
    }
    generated = {"hook": "A full chess client for your terminal.", "summary": "Keyboard-first chess.", "category": "cli"}
    path, style = render_project_card(candidate, generated, tmp_path / "card.png", issue_number=1)
    assert style == "neon_terminal"
    with Image.open(path) as image:
        assert image.size == (1200, 630)

    digest = render_digest_card([candidate], tmp_path / "digest.png")
    with Image.open(digest) as image:
        assert image.size == (1200, 630)
