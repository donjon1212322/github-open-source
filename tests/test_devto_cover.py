"""dev.to re-encodes main_image into 1000x420 fit=cover.

The owner's screenshot showed the 1200x630 card with its masthead and stats
row cut off. These tests pin a dedicated cover that survives both the article
header crop and the 1200x627 social-card crop.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image

from ghos_v6 import platforms
from ghos_v6.devto_cover import COVER_H, COVER_W, PAPER, build_devto_cover

FLAT = (
    " ## What it is A clean-room engine. ## How it works It uses harnesses. "
    "## Key features - Native loop - Vanilla saves ## Getting started Clone it. "
    "## When not to use it Casual players."
)

MARKERS = {
    "tl": (220, 30, 30),
    "tr": (30, 90, 220),
    "bl": (30, 170, 60),
    "br": (240, 190, 20),
}


def _card_bytes() -> bytes:
    image = Image.new("RGB", (1200, 630), (247, 239, 222))
    for x, y, colour in ((0, 0, MARKERS["tl"]), (1140, 0, MARKERS["tr"]),
                         (0, 570, MARKERS["bl"]), (1140, 570, MARKERS["br"])):
        for dx in range(60):
            for dy in range(60):
                image.putpixel((x + dx, y + dy), colour)
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def _near(pixel: tuple[int, int, int], colour: tuple[int, int, int], tol: int = 35) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(pixel, colour))


def _record() -> dict:
    return {
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


def _generated() -> dict:
    return {"article_markdown": FLAT, "key_features": ["Native loop"],
            "surfaces": {"devto_body": FLAT}}


def test_cover_matches_devto_ratio_and_keeps_all_four_corners():
    with Image.open(BytesIO(build_devto_cover(_card_bytes()))) as cover:
        assert cover.size == (COVER_W, COVER_H) == (1000, 420)
        rgb = cover.convert("RGB")
        # The whole 1200x630 card is scaled into a 760x399 box centred at
        # (120, 10). All four corner markers must still be present.
        points = {
            "tl": (139, 29), "tr": (861, 29),
            "bl": (139, 390), "br": (861, 390),
        }
        for name, point in points.items():
            assert _near(rgb.getpixel(point), MARKERS[name]), name


def test_cover_has_safe_side_margins_for_social_cards():
    with Image.open(BytesIO(build_devto_cover(_card_bytes()))) as cover:
        rgb = cover.convert("RGB")
        # A 1200x627 social card crops ~98 px from each side; the margins
        # must be plain paper so nothing meaningful is lost.
        assert _near(rgb.getpixel((6, 210)), PAPER)
        assert _near(rgb.getpixel((994, 210)), PAPER)


def test_main_image_prefers_the_cover_but_falls_back():
    assert platforms.DevToClient._main_image({
        "image_url": "https://i.ibb.co/card.png",
        "devto_cover_url": "https://i.ibb.co/cover.png",
    }) == "https://i.ibb.co/cover.png"
    assert platforms.DevToClient._main_image({
        "image_url": "https://i.ibb.co/card.png",
    }) == "https://i.ibb.co/card.png"
    assert platforms.DevToClient._main_image({}) == ""


def test_publish_and_update_both_send_the_uncropped_cover(monkeypatch):
    client = platforms.DevToClient("dummy-key")
    calls: list[dict] = []

    class _Resp:
        status_code = 201

        def json(self) -> dict:
            return {"id": 1, "url": "https://dev.to/x/y"}

    def fake_request(method, url, **kwargs):
        calls.append(kwargs.get("json") or {})
        return _Resp()

    monkeypatch.setattr(client.session, "request", fake_request)
    record = _record()
    record["devto_cover_url"] = "https://i.ibb.co/cover.png"
    generated = _generated()

    client.publish_article(record, generated)
    client.update_article(1, record, generated)

    assert calls[0]["article"]["main_image"] == "https://i.ibb.co/cover.png"
    assert calls[1]["article"]["main_image"] == "https://i.ibb.co/cover.png"
