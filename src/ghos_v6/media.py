"""Visual cascade: README media → branded card → GitHub OpenGraph."""
from __future__ import annotations

import io
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from .cards import render_project_card
from .http import UnsafeUrl, download_bytes, validate_public_url
from .images import content_type_for_format, open_safe_image

logger = logging.getLogger(__name__)

# Owner 19.11: post 3618 (microsoft/TokenEconomizer) shipped a completely
# white preview. The README opens with an animated GIF whose first frames
# are a blank intro card, and Pillow reads frame 0. In the evening the owner
# upgraded the rule: a README visual that is visually flat - a blank intro,
# a white title banner, one dominant colour covering ~everything - is
# replaced by OUR OWN branded card, never by another plain picture.
#: Share of pixels inside the dominant 16-level grey band that makes an
#: image "flat". Measured on the live channel: white title cards land at
#: 0.94-0.98, real screenshots and diagrams at <= 0.89.
FLAT_DOMINANT_FRACTION = 0.92
#: How deep to look into an animation for a frame with real content.
GIF_FRAME_SCAN_LIMIT = 60
#: Grey levels per band; absorbs GIF-palette and JPEG noise around a colour.
_FLAT_WINDOW = 16


@dataclass
class Visual:
    kind: str  # readme | card | opengraph
    url: str = ""
    local_path: Path | None = None
    data: bytes = b""
    content_type: str = "image/png"
    card_style: str = ""


def _is_safe_public_url(url: str) -> bool:
    try:
        validate_public_url(url)
    except UnsafeUrl:
        return False
    return True


def _flat_fraction(image: Image.Image) -> float:
    """Share of pixels that sit in the dominant ``_FLAT_WINDOW`` grey band.

    A white title card lands at ~0.95+, a real screenshot or diagram at
    ~0.9 or below. A sliding window (not one exact level) keeps the metric
    stable under GIF palettes and JPEG noise.
    """
    grey = image.convert("L")
    if max(grey.size) > 640:
        grey.thumbnail((640, 640))
    hist = grey.histogram()
    total = sum(hist) or 1
    running = sum(hist[:_FLAT_WINDOW])
    best = running
    for level in range(_FLAT_WINDOW, 256):
        running += hist[level] - hist[level - _FLAT_WINDOW]
        if running > best:
            best = running
    return best / total


def _is_visually_flat(image: Image.Image) -> bool:
    """True when one colour visually dominates the whole image."""
    return _flat_fraction(image) >= FLAT_DOMINANT_FRACTION


def _looks_flat(data: bytes) -> bool:
    try:
        with open_safe_image(io.BytesIO(data)) as image:
            return _is_visually_flat(image)
    except Exception:  # noqa: BLE001 - undecodable is never a good preview
        return True


def _gif_to_still(data: bytes) -> bytes | None:
    """First frame of an animated GIF that has real content, as PNG bytes.

    Animations often open on a blank intro or a plain white title card.
    Returns ``None`` for non-animated files and for animations whose
    scanned frames are all flat, so the caller keeps its normal validation
    and fallback chain.
    """
    try:
        with open_safe_image(io.BytesIO(data)) as gif:
            frames = getattr(gif, "n_frames", 1) or 1
            if frames <= 1:
                return None
            for index in range(min(frames, GIF_FRAME_SCAN_LIMIT)):
                gif.seek(index)
                frame = gif.convert("RGB")
                if not _is_visually_flat(frame):
                    buffer = io.BytesIO()
                    frame.save(buffer, format="PNG")
                    return buffer.getvalue()
    except Exception:  # noqa: BLE001 - a broken animation is not a preview
        return None
    return None


def _validate_image(data: bytes) -> tuple[bool, str]:
    try:
        # formats= is the security boundary: Pillow must not even try the
        # PSD/TIFF/FITS decoders on a file a third party controls.
        with open_safe_image(io.BytesIO(data)) as image:
            image.seek(0)
            width, height = image.size
            fmt = (image.format or "").upper()
    except Exception:
        return False, ""
    if width < 800 or height < 250:
        return False, ""
    content_type = content_type_for_format(fmt)
    return bool(content_type), content_type


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "project"


def choose_visual(
    candidate: dict[str, Any],
    generated: dict[str, Any],
    *,
    issue_number: int | None = None,
    work_dir: Path | None = None,
) -> Visual:
    for url in candidate.get("media_urls") or []:
        if not _is_safe_public_url(str(url)):
            continue
        try:
            data = download_bytes(str(url), max_bytes=8_000_000)
        except Exception as exc:  # noqa: BLE001 - try next visual candidate
            logger.info("README media rejected (%s): %s", type(exc).__name__, url)
            continue
        # An animated GIF must contribute a real frame, not its blank intro.
        still = _gif_to_still(data)
        if still is not None:
            data = still
        ok, content_type = _validate_image(data)
        if not ok:
            continue
        if _looks_flat(data):
            # Owner decision (19.11, evening): a flat README visual (blank
            # intro, white title banner) is replaced by OUR OWN branded
            # card - not by the next plain picture from the same README.
            logger.info("README media is visually flat; using our card: %s", url)
            break
        return Visual(kind="readme", url=str(url), data=data, content_type=content_type)
    try:
        directory = work_dir or Path(tempfile.gettempdir()) / "ghos-v6-media"
        filename = _safe_filename(str(candidate.get("full_name") or candidate.get("name") or "project")) + ".png"
        path, style = render_project_card(
            candidate,
            generated,
            directory / filename,
            issue_number=issue_number,
        )
        return Visual(
            kind="card",
            local_path=path,
            data=path.read_bytes(),
            content_type="image/png",
            card_style=style,
        )
    except Exception as exc:  # noqa: BLE001 - OpenGraph is final visual fallback
        logger.warning("Card render failed; using GitHub OpenGraph: %s", type(exc).__name__)
    full_name = str(candidate.get("full_name") or "")
    og_url = f"https://opengraph.githubassets.com/1/{full_name}" if full_name else ""
    return Visual(kind="opengraph", url=og_url, content_type="image/png")
