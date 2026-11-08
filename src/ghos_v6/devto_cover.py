"""dev.to cover art.

dev.to re-encodes ``main_image`` through a fixed
``width=1000,height=420,fit=cover,gravity=auto`` transform. A 1200x630 card
is therefore centre-cropped: the masthead and the stats row are cut off.
It also builds a 1200x627 social card from the same source, which crops the
sides instead. This module produces one 1000x420 cover that survives both:

* the whole 1200x630 card is scaled down, nothing is cropped;
* it sits inside a 760x399 safe area in the middle of the canvas, so the
  side margins are what a social-card crop removes;
* the margins use the card's own background colour, so the result reads as
  one continuous sheet rather than a letterboxed photo.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageDraw

COVER_W, COVER_H = 1000, 420
CARD_W, CARD_H = 1200, 630
# 760/1200 == 399/630; the card stays proportional and fully visible.
SAFE_W, SAFE_H = 760, 399
# Card D is a dark grid (owner 08.11). Matching margins make the 1000x420
# cover read as one continuous sheet instead of a light letterbox.
PAPER = (8, 12, 22)
INK = (34, 54, 88)
RULE = (34, 54, 88)
RED = (77, 216, 255)


def build_devto_cover(card_bytes: bytes) -> bytes:
    """Return a 1000x420 PNG containing the entire card, uncropped."""
    with Image.open(BytesIO(card_bytes)) as source:
        card = source.convert("RGB")
    # Preserve the aspect ratio of whatever the renderer produced, while
    # fitting it inside the safe box. A future 1200x630 -> 1400x700 change
    # must not silently crop the design either.
    scale = min(SAFE_W / card.width, SAFE_H / card.height)
    size = (max(1, round(card.width * scale)), max(1, round(card.height * scale)))
    card = card.resize(size, Image.Resampling.LANCZOS)

    canvas = Image.new("RGB", (COVER_W, COVER_H), PAPER)
    left = (COVER_W - size[0]) // 2
    top = (COVER_H - size[1]) // 2
    canvas.paste(card, (left, top))

    draw = ImageDraw.Draw(canvas)
    # Hairlines make the wide margins deliberate; the outer frame keeps the
    # cover bounded even on dev.to's dark theme. Nothing is cropped.
    draw.line((left - 1, 0, left - 1, COVER_H), fill=RULE, width=1)
    draw.line((left + size[0], 0, left + size[0], COVER_H), fill=RULE, width=1)
    draw.rectangle((0, 0, COVER_W - 1, COVER_H - 1), outline=INK, width=3)
    draw.line((0, 0, COVER_W, 0), fill=RED, width=2)

    out = BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()
