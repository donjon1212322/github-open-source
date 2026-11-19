"""Visual cascade: README media → branded card → GitHub OpenGraph."""
from __future__ import annotations

import io
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

from .cards import render_project_card
from .http import UnsafeUrl, download_bytes, validate_public_url
from .images import content_type_for_format, open_safe_image

logger = logging.getLogger(__name__)

# Owner 19.11: post 3618 (microsoft/TokenEconomizer) shipped a completely
# white preview. The README opens with an animated GIF whose first frames
# are a blank intro card, and Pillow reads frame 0. A single flat colour is
# never a meaningful preview, no matter which file it came from.
BLANK_STDEV = 3.0
#: How deep to look into an animation for the first frame with content.
GIF_FRAME_SCAN_LIMIT = 60


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


def _frame_is_blank(image: Image.Image) -> bool:
    """True when the frame is one flat colour (white/black intro card)."""
    thumb = image.convert("L")
    thumb.thumbnail((160, 160))
    return ImageStat.Stat(thumb).stddev[0] < BLANK_STDEV


def _gif_to_still(data: bytes) -> bytes | None:
    """First meaningful frame of an animated GIF, as PNG bytes.

    Animations often open on a blank intro/title card (fade-ins, white
    leads). Pillow's default frame 0 would turn that into the post's
    preview. Returns ``None`` for non-animated files and for animations
    whose scanned frames are all blank, so the caller keeps its normal
    validation and fallback chain.
    """
    try:
        with open_safe_image(io.BytesIO(data)) as gif:
            frames = getattr(gif, "n_frames", 1) or 1
            if frames <= 1:
                return None
            for index in range(min(frames, GIF_FRAME_SCAN_LIMIT)):
                gif.seek(index)
                frame = gif.convert("RGB")
                if not _frame_is_blank(frame):
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
            blank = _frame_is_blank(image)
    except Exception:
        return False, ""
    if width < 800 or height < 250:
        return False, ""
    if blank:
        # A uniform single-colour image is an intro card or a placeholder,
        # never a preview the channel wants to show.
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
        if ok:
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
