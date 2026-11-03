"""Visual cascade: README media → branded card → GitHub OpenGraph."""
from __future__ import annotations

import io
import logging
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cards import render_project_card
from .http import UnsafeUrl, download_bytes, validate_public_url
from .images import content_type_for_format, open_safe_image

logger = logging.getLogger(__name__)


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
