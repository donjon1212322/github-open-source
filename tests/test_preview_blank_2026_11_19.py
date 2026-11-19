"""Owner 19.11: post 3618 shipped a completely white preview card.

microsoft/TokenEconomizer's README opens with an animated GIF whose first
frames are a blank white intro card. Pillow reads frame 0, the size/format
checks passed, and the blank frame became the live post's link preview.

These tests pin the fix: a uniform image is never a preview, an animated
GIF contributes its first meaningful frame, and the new preview-fix
command re-picks the image and edits the live post in place.
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
        # a light-grey band on white: real content, but far from uniform
        for y in range(size[1] // 3, 2 * size[1] // 3):
            for x in range(size[0] // 4, 3 * size[0] // 4):
                image.putpixel((x, y), (40, 40, 40))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _animated_gif(size: tuple[int, int], blank_frames: int, with_content: bool = True) -> bytes:
    # consecutive frames alternate slightly so Pillow's GIF writer cannot
    # merge identical blank frames into one (it does that silently)
    frames = []
    for index in range(blank_frames):
        color = (255, 255, 255) if index % 2 == 0 else (250, 250, 250)
        frames.append(Image.new("RGB", size, color))
    if with_content:
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


def _is_blank(data: bytes) -> bool:
    return media._frame_is_blank(Image.open(io.BytesIO(data)))


# --- the blank-image guard -------------------------------------------------

def test_a_uniform_image_is_never_a_preview():
    ok, content_type = media._validate_image(_png((1200, 300), (255, 255, 255)))
    assert ok is False
    assert content_type == ""


def test_an_image_with_content_still_passes():
    ok, content_type = media._validate_image(_png((1200, 300), (255, 255, 255), draw_text=True))
    assert ok is True
    assert content_type == "image/png"


# --- animated GIFs ---------------------------------------------------------

def test_gif_to_still_skips_blank_intro_frames():
    still = media._gif_to_still(_animated_gif((900, 300), blank_frames=3))
    assert still is not None
    assert still.startswith(b"\x89PNG")
    assert not _is_blank(still), "the chosen frame must be the title card, not the intro"


def test_a_fully_blank_gif_is_rejected():
    assert media._gif_to_still(_animated_gif((900, 300), blank_frames=70, with_content=False)) is None


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


def test_choose_visual_uses_the_gif_content_frame(monkeypatch):
    gif = _animated_gif((900, 300), blank_frames=3)
    monkeypatch.setattr(media, "download_bytes", lambda url, max_bytes=0: gif)
    visual = media.choose_visual(
        {"full_name": "owner/demo", "media_urls": ["https://example.com/teaser.gif"]},
        {},
    )
    assert visual.kind == "readme"
    assert visual.data.startswith(b"\x89PNG"), "the blank GIF frame must not reach the post"
    assert not _is_blank(visual.data)


def test_choose_visual_skips_a_blank_banner_and_takes_the_next_media(monkeypatch):
    blank = _png((1200, 300), (255, 255, 255))
    good = _png((1200, 300), (255, 255, 255), draw_text=True)
    served = {
        "https://example.com/blank.png": blank,
        "https://example.com/good.png": good,
    }
    monkeypatch.setattr(media, "download_bytes", lambda url, max_bytes=0: served[url])
    visual = media.choose_visual(
        {"full_name": "owner/demo", "media_urls": list(served)},
        {},
    )
    assert visual.kind == "readme"
    assert visual.url == "https://example.com/good.png"


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
