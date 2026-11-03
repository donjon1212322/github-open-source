"""Post-cards at Telegram-friendly weight.

Telegram recompresses every photo it stores: it caps the long side at
2560 px *and* re-encodes as JPEG at a quality it chooses. A 1-px hairline
rule and 15 px sans text are exactly what dies first in that pass — the
screenshot from the live channel showed rules turning grey and labels
smearing into each other.

So the card we upload must already look like the card we want to survive:
slightly heavier hairlines, and a JPEG we control at quality 92 instead of
a large PNG that Telegram squashes on its own terms.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from .images import open_safe_image

# Telegram's own limits for sendPhoto: 10 MB, and it will downscale the long
# side to 2560 px. We stay well under both so it never touches our pixels.
MAX_UPLOAD_BYTES = 4_500_000
JPEG_QUALITY = 92


def to_telegram_jpeg(image: Image.Image, *, quality: int = JPEG_QUALITY) -> bytes:
    """Encode exactly once, at a quality we choose, in the colour space Telegram expects."""
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, "JPEG", quality=quality, optimize=True, subsampling=0)
    data = buffer.getvalue()
    if len(data) <= MAX_UPLOAD_BYTES:
        return data
    # Only if a card still manages to be huge: step quality down, never resize.
    for lower in (88, 84, 80, 75):
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "JPEG", quality=lower, optimize=True, subsampling=0)
        data = buffer.getvalue()
        if len(data) <= MAX_UPLOAD_BYTES:
            return data
    return data


def prepare_for_telegram(path: Path) -> bytes:
    return to_telegram_jpeg(open_safe_image(str(path)))

def shrink_for_web_preview(image: bytes | str | Path, *, max_width: int = 1200, quality: int = 82) -> bytes:
    """Re-encode a card as a web-friendly JPEG under ~300 KB.

    Link-preview crawlers are far less patient than ``sendPhoto``: Telegram
    fetches the URL with a short timeout and drops the preview when the
    image is missing a real ``image/*`` content-type or is slow to load.
    A 1200x630 card at ``quality=82`` subsampling 4:2:0 lands around
    150-250 KB, which loads fast enough for the preview to survive.
    """
    from io import BytesIO

    from PIL import Image

    if isinstance(image, (bytes, bytearray)):
        source = open_safe_image(BytesIO(image))
    else:
        source = open_safe_image(str(image))
    source = source.convert("RGB")
    if source.width > max_width:
        height = round(source.height * max_width / source.width)
        source = source.resize((max_width, height), Image.Resampling.LANCZOS)
    out = BytesIO()
    source.save(out, format="JPEG", quality=quality, optimize=True, progressive=True, subsampling=2)
    data = out.getvalue()
    if len(data) > 300_000:  # still too heavy: drop quality once
        out = BytesIO()
        source.save(out, format="JPEG", quality=72, optimize=True, progressive=True, subsampling=2)
        data = out.getvalue()
    return data
