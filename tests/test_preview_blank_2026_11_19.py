"""Owner 19.11: post 3618 shipped a completely white preview card.

microsoft/TokenEconomizer's README opens with an animated GIF whose first
frames are a blank white intro card. Pillow reads frame 0, the size/format
checks passed, and the blank frame became the live post's link preview.

The evening upgrade (same day): a README visual that is visually flat -
a blank intro, a white title banner - is replaced by OUR OWN branded
card, never by another plain picture. These tests pin both the flatness
gate and the preview-fix command that repairs a live post in place.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image

from ghos_v6 import media, pipeline
from ghos_v6.config import Config
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]


def _png(size: tuple[int, int], color, draw_text: bool = False) -> bytes:
    image = Image.new("RGB", size, color)
    if draw_text:
        # a dark band covering a third of the image: real content
        for y in range(size[1] // 3, 2 * size[1] // 3):
            for x in range(size[0] // 4, 3 * size[0] // 4):
                image.putpixel((x, y), (40, 40, 40))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _title_card_png(size: tuple[int, int] = (1200, 300)) -> bytes:
    """A white banner with a small dark caption, like the live title card.

    ~95% of the pixels stay white: real text, but visually a plain card -
    exactly what the owner wants replaced by our own branded card.
    """
    image = Image.new("RGB", size, (255, 255, 255))
    for y in range(size[1] // 2 - 10, size[1] // 2 + 10):
        for x in range(size[0] // 2 - 90, size[0] // 2 + 90):
            image.putpixel((x, y), (20, 20, 20))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _animated_gif(
    size: tuple[int, int],
    blank_frames: int,
    with_content: bool = True,
    title_card_only: bool = False,
) -> bytes:
    # consecutive frames alternate slightly so Pillow's GIF writer cannot
    # merge identical blank frames into one (it does that silently)
    frames = []
    for index in range(blank_frames):
        color = (255, 255, 255) if index % 2 == 0 else (250, 250, 250)
        frames.append(Image.new("RGB", size, color))
    if title_card_only:
        # the live TokenEconomizer case: every frame is a white title card
        # (flat, but not uniform) with a small dark caption
        for index in range(4):
            frames.append(Image.open(io.BytesIO(_title_card_png((size[0], size[1] // 2)))))
    elif with_content:
        # large blocks, not a fine grid: a fine pattern averages into a flat
        # grey after downscaling and would look "blank" to the checker too
        content = Image.new("RGB", size, (255, 255, 255))
        for y in range(size[1] // 3, 2 * size[1] // 3):
            for x in range(size[0] // 4, 3 * size[0] // 4):
                content.putpixel((x, y), (10, 10, 10))
        frames.append(content)
    buffer = io.BytesIO()
    frames[0].save(buffer, "GIF", save_all=True, append_images=frames[1:], duration=100, loop=0)
    return buffer.getvalue()


# --- the flatness guard ----------------------------------------------------

def test_a_white_title_card_counts_as_flat():
    assert media._looks_flat(_title_card_png()) is True
    assert media._looks_flat(_png((1200, 300), (255, 255, 255))) is True


def test_rich_images_are_not_flat():
    assert media._looks_flat(_png((1200, 300), (255, 255, 255), draw_text=True)) is False


def test_a_uniform_image_is_replaced_by_our_card(monkeypatch):
    monkeypatch.setattr(
        media, "download_bytes",
        lambda url, max_bytes=0: _png((1200, 300), (255, 255, 255)),
    )
    visual = media.choose_visual(
        {"full_name": "owner/demo", "media_urls": ["https://example.com/hero.png"]},
        {},
    )
    assert visual.kind == "card", "a flat hero must never become the preview"


def test_a_flat_banner_is_replaced_by_our_card_not_the_next_media(monkeypatch):
    served = {
        "https://example.com/flat-banner.png": _title_card_png(),
        "https://example.com/rich.png": _png((1200, 300), (255, 255, 255), draw_text=True),
    }
    monkeypatch.setattr(media, "download_bytes", lambda url, max_bytes=0: served[url])
    visual = media.choose_visual(
        {"full_name": "owner/demo", "media_urls": list(served)},
        {},
    )
    assert visual.kind == "card", "the owner wants OUR card for flat art, not a rescue image"


def test_a_technically_invalid_hero_still_tries_the_next_media(monkeypatch):
    served = {
        "https://example.com/tiny.png": _png((300, 100), (255, 255, 255), draw_text=True),
        "https://example.com/rich.png": _png((1200, 300), (255, 255, 255), draw_text=True),
    }
    monkeypatch.setattr(media, "download_bytes", lambda url, max_bytes=0: served[url])
    visual = media.choose_visual(
        {"full_name": "owner/demo", "media_urls": list(served)},
        {},
    )
    assert visual.kind == "readme"
    assert visual.url == "https://example.com/rich.png"


def test_an_image_with_content_still_passes():
    ok, content_type = media._validate_image(_png((1200, 300), (255, 255, 255), draw_text=True))
    assert ok is True
    assert content_type == "image/png"


# --- animated GIFs ---------------------------------------------------------

def test_gif_to_still_skips_flat_intro_frames():
    still = media._gif_to_still(_animated_gif((900, 300), blank_frames=3))
    assert still is not None
    assert still.startswith(b"\x89PNG")
    assert media._looks_flat(still) is False, "the chosen frame must have real content"


def test_a_fully_blank_gif_is_rejected():
    assert media._gif_to_still(_animated_gif((900, 300), blank_frames=70, with_content=False)) is None


def test_a_gif_of_only_white_title_cards_is_rejected():
    # The live TokenEconomizer case: every scanned frame is a white title
    # card (flat, but not uniform). No frame is good enough for a preview.
    gif = _animated_gif((900, 300), blank_frames=3, title_card_only=True)
    assert media._gif_to_still(gif) is None


def test_choose_visual_uses_the_gif_content_frame(monkeypatch):
    gif = _animated_gif((900, 300), blank_frames=3)
    monkeypatch.setattr(media, "download_bytes", lambda url, max_bytes=0: gif)
    visual = media.choose_visual(
        {"full_name": "owner/demo", "media_urls": ["https://example.com/teaser.gif"]},
        {},
    )
    assert visual.kind == "readme"
    assert visual.data.startswith(b"\x89PNG"), "the blank GIF frame must not reach the post"
    assert media._looks_flat(visual.data) is False


def test_choose_visual_falls_back_when_gif_content_is_beyond_the_scan(monkeypatch):
    # 70 blank frames, then content: the bounded scan must give up and let
    # the normal fallback chain (branded card) take over, not hang or guess.
    gif = _animated_gif((900, 300), blank_frames=70)
    monkeypatch.setattr(media, "download_bytes", lambda url, max_bytes=0: gif)
    visual = media.choose_visual(
        {"full_name": "owner/demo", "media_urls": ["https://example.com/slow-intro.gif"]},
        {},
    )
    assert visual.kind == "card"


# --- preview-fix: repair the live post in place -----------------------------

def _record() -> dict:
    return {
        "name": "TokenEconomizer",
        "full_name": "microsoft/TokenEconomizer",
        "description": "Cut coding-agent costs with experience-driven control",
        "topics": ["agents"],
        "license": {"spdx_id": "MIT"},
        "stars": 3000,
        "forks": 120,
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2026-11-01T00:00:00Z",
        "contributors_count": 9,
        "telegram_message_id": 3618,
        "telegram_chat_id": "@GitHub_Open_Source",
        "telegraph_url": "https://telegra.ph/TokenEconomizer-demo",
        "image_url": "https://i.ibb.co/old/blank.jpg",
        "media_urls": ["https://example.com/teaser.gif"],
        "generated": {"summary": "Cuts agent costs.", "hook": "Pay less per task."},
    }


def _events(tmp_path: Path) -> list[dict]:
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def test_preview_fix_edits_the_live_post_in_place(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    store.data = {"published": {"microsoft/tokeneconomizer": _record()}}
    good_png = _png((1200, 300), (255, 255, 255), draw_text=True)

    from ghos_v6.media import Visual

    monkeypatch.setattr(
        pipeline, "choose_visual",
        lambda candidate, generated, **kwargs: Visual(
            kind="readme", url="https://example.com/teaser.gif",
            data=good_png, content_type="image/png"),
    )
    monkeypatch.setattr(
        pipeline, "_host_local_image",
        lambda config, data, **kwargs: ("https://i.ibb.co/new/preview.jpg", "imgbb"),
    )

    edited = []

    class FakeTelegram:
        def __init__(self, token):
            pass

        def edit_text(self, chat_id, message_id, text_html, *, preview_url=""):
            edited.append((chat_id, message_id, text_html, preview_url))
            return {"message_id": message_id}

    monkeypatch.setattr(pipeline, "TelegramClient", FakeTelegram)
    config = Config(telegram_bot_token="x")

    result = pipeline.preview_fix_command(config, store, repo="Microsoft/TokenEconomizer")

    assert result["ok"] is True
    assert result["new_url"] == "https://i.ibb.co/new/preview.jpg"
    assert result["old_url"] == "https://i.ibb.co/old/blank.jpg"
    assert len(edited) == 1, "exactly one edit, never a duplicate post"
    chat_id, message_id, text_html, preview_url = edited[0]
    assert chat_id == "@GitHub_Open_Source"
    assert message_id == 3618
    assert preview_url == "https://i.ibb.co/new/preview.jpg"
    assert "https://telegra.ph/TokenEconomizer-demo" in text_html
    record = store.data["published"]["microsoft/tokeneconomizer"]
    assert record["image_url"] == "https://i.ibb.co/new/preview.jpg"
    assert record["visual_host"] == "imgbb"
    assert any(e.get("type") == "preview_fixed" for e in _events(tmp_path))


def test_preview_fix_rejects_unknown_repo(tmp_path):
    store = StateStore(tmp_path)
    store.data = {"published": {}}
    result = pipeline.preview_fix_command(
        Config(telegram_bot_token="x"), store, repo="owner/notpublished")
    assert result["ok"] is False
    assert result["reason"] == "not_published"


def test_preview_fix_rejects_a_malformed_repo_name(tmp_path):
    store = StateStore(tmp_path)
    store.data = {"published": {}}
    result = pipeline.preview_fix_command(
        Config(telegram_bot_token="x"), store, repo="not a repo name")
    assert result["ok"] is False
    assert result["reason"] == "bad_repo"


def test_preview_fix_without_an_image_candidate_is_honest(monkeypatch, tmp_path):
    store = StateStore(tmp_path)
    store.data = {"published": {"microsoft/tokeneconomizer": _record()}}
    from ghos_v6.media import Visual

    monkeypatch.setattr(
        pipeline, "choose_visual",
        lambda candidate, generated, **kwargs: Visual(
            kind="opengraph", url="https://opengraph.githubassets.com/1/x"),
    )
    result = pipeline.preview_fix_command(
        Config(telegram_bot_token="x"), store, repo="microsoft/TokenEconomizer")
    assert result["ok"] is False
    assert result["reason"] == "no_image_candidate"


# --- workflow wiring --------------------------------------------------------

def test_workflow_can_dispatch_preview_fix():
    text = (REPO / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    assert "preview-fix" in text
    assert "PREVIEW_FIX_REPO: ${{ inputs.preview_fix_repo }}" in text
    assert 'ghos-v6 preview-fix --repo "$PREVIEW_FIX_REPO"' in text
    assert "preview-fix|poll-preview" in text
