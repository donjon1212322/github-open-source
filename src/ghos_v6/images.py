"""Safe image decoding for untrusted README media.

Pillow sniffs the content, not the extension. Without an explicit
``formats=`` allowlist a file that claims to be a PNG can reach the PSD,
TIFF, FITS or XBM decoders — all of which have had memory-safety bugs and
decompression bombs. The allowlist plus a pixel cap keeps the decoder on
the four formats the channel actually publishes.
"""
from __future__ import annotations

import io
from typing import BinaryIO

from PIL import Image

#: The only formats we accept from untrusted sources.
SAFE_FORMATS: tuple[str, ...] = ("PNG", "JPEG", "GIF", "WEBP")

#: Pillow raises at 2x this value, so 20M gives the intended 40 MP cap.
#: 40 MP is far above any card or README screenshot we publish, and it
#: bounds the worst-case decompression memory to a few hundred MB.
MAX_IMAGE_PIXELS = 20_000_000

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

_CONTENT_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


def open_safe_image(
    source: bytes | bytearray | BinaryIO | str,
    *,
    formats: tuple[str, ...] = SAFE_FORMATS,
) -> Image.Image:
    """Open an image, refusing anything outside ``formats``.

    Raises ``PIL.UnidentifiedImageError`` (a subclass of ``Exception``) for
    unsupported formats, so callers can treat it as "not a usable image".
    """
    if isinstance(source, (bytes, bytearray)):
        return Image.open(io.BytesIO(source), formats=formats)
    return Image.open(source, formats=formats)


def content_type_for_format(fmt: str | None) -> str:
    """Map a Pillow format name to a content type, or "" when unsupported."""
    return _CONTENT_TYPES.get((fmt or "").upper(), "")
