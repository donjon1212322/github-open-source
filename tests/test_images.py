"""Untrusted image handling: only PNG/JPEG/GIF/WEBP may reach Pillow."""
from __future__ import annotations

import io

import pytest
from PIL import Image, UnidentifiedImageError

from ghos_v6.images import MAX_IMAGE_PIXELS, open_safe_image
from ghos_v6.media import _validate_image


def _encode(fmt: str, size: tuple[int, int] = (900, 300)) -> bytes:
    # 19.11: a uniform single-colour image is a blank preview and is
    # rejected by _validate_image now. Carry a real band so these tests
    # keep checking the FORMAT allowlist, not the blankness guard.
    image = Image.new("RGB", size, (10, 20, 30))
    for y in range(size[1] // 3, 2 * size[1] // 3):
        for x in range(size[0] // 4, 3 * size[0] // 4):
            image.putpixel((x, y), (200, 210, 220))
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return buffer.getvalue()


def test_png_is_accepted():
    ok, content_type = _validate_image(_encode("PNG"))
    assert ok is True
    assert content_type == "image/png"


def test_tiff_is_rejected_before_it_reaches_the_decoder():
    # TIFF is a legitimate Pillow format but not one we publish. The
    # allowlist must reject it instead of handing it to the TIFF decoder.
    ok, content_type = _validate_image(_encode("TIFF"))
    assert ok is False
    assert content_type == ""


def test_open_safe_image_refuses_unsupported_format():
    with pytest.raises(UnidentifiedImageError):
        open_safe_image(_encode("TIFF"))


def test_pixel_cap_is_configured():
    assert Image.MAX_IMAGE_PIXELS == MAX_IMAGE_PIXELS
