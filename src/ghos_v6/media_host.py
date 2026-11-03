"""Durable public hosting for locally rendered card images.

`telegra.ph/upload` is an undocumented endpoint and intermittently
returns HTTP 400 ``"Unknown error"`` for external clients.  Rendered
cards therefore cannot depend on it.  In GitHub Actions the workflow
already has a ``github.token`` with ``contents: write``; this module
uploads card PNGs as assets on a monthly *prerelease* (for example
``media-2026-11``) in the same public repository.  The returned
``releases/download/...`` URL is public immediately, does not bloat
git history with binaries, and is stable enough for Telegram link
previews and Telegraph ``<img>`` tags.

The monthly releases are marked as prereleases so they never become
the repository's "Latest" software release.
"""
from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, cast
from urllib.parse import quote

import requests

from .http import request_json, sanitize

_API_BASE = "https://api.github.com"
_REPO_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


class MediaHostError(RuntimeError):
    pass


def _safe_asset_name(filename: str, data: bytes) -> str:
    raw = Path(str(filename or "card.png")).name
    suffix = Path(raw).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
        suffix = ".png"
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", Path(raw).stem).strip("-._") or "card"
    digest = hashlib.sha256(data).hexdigest()[:12]
    return f"{stem[:80]}-{digest}{suffix}"


class GitHubReleaseMediaHost:
    """Upload images as assets on a monthly GitHub prerelease."""

    def __init__(
        self,
        token: str,
        repository: str,
        *,
        request_fn: Callable[..., tuple[int, Any, Any]] = request_json,
        now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.token = (token or "").strip()
        self.repository = (repository or "").strip().strip("/")
        self._request = request_fn
        self._now = now_fn

    @property
    def configured(self) -> bool:
        return bool(self.token and _REPO_RE.match(self.repository))

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.GitHub+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if extra:
            headers.update(extra)
        return headers

    def _call(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        data: Any = None,
        params: dict[str, Any] | None = None,
        content_type: str = "",
    ) -> tuple[int, Any]:
        extra = {"Content-Type": content_type} if content_type else None
        status, body, _response = self._request(
            method,
            url,
            headers=self._headers(extra),
            json_body=json_body,
            data=data,
            params=params,
            timeout=60,
            retries=2,
            retry_post=False,
        )
        return status, body

    @staticmethod
    def _error(action: str, status: int, body: Any) -> MediaHostError:
        return MediaHostError(
            f"GitHub media host {action} failed HTTP {status}: {sanitize(str(body))[:240]}"
        )

    def _get_or_create_release(self, tag: str) -> dict[str, Any]:
        repo_url = f"{_API_BASE}/repos/{self.repository}"
        status, body = self._call("GET", f"{repo_url}/releases/tags/{quote(tag, safe='')}")
        if status == 200 and isinstance(body, dict):
            return body
        if status != 404:
            raise self._error("release lookup", status, body)

        status, body = self._call(
            "POST",
            f"{repo_url}/releases",
            json_body={
                "tag_name": tag,
                "name": f"Media assets {tag.removeprefix('media-')}",
                "body": (
                    "Generated public card images for GitHub Open Source posts. "
                    "This is a media-assets prerelease, not a software release."
                ),
                "draft": False,
                "prerelease": True,
            },
        )
        if status in (200, 201) and isinstance(body, dict):
            return body
        if status == 422:  # a concurrent run may have created it first
            status2, body2 = self._call(
                "GET", f"{repo_url}/releases/tags/{quote(tag, safe='')}"
            )
            if status2 == 200 and isinstance(body2, dict):
                return body2
        raise self._error("release create", status, body)

    def upload_image(
        self,
        data: bytes,
        *,
        filename: str = "card.png",
        content_type: str = "image/png",
    ) -> str:
        if not self.configured:
            raise MediaHostError("GitHub media host is not configured (token/repository)")
        if not data:
            raise MediaHostError("Cannot upload an empty image")

        now = self._now()
        tag = f"media-{now:%Y-%m}"
        release = self._get_or_create_release(tag)
        upload_url = str(release.get("upload_url") or "").split("{", 1)[0]
        if not upload_url.startswith("https://uploads.github.com/"):
            raise MediaHostError("GitHub release response has no usable upload_url")

        asset_name = _safe_asset_name(filename, data)
        status, body = self._call(
            "POST",
            upload_url,
            data=data,
            params={"name": asset_name},
            content_type=content_type or "application/octet-stream",
        )
        if status in (200, 201) and isinstance(body, dict):
            url = str(body.get("browser_download_url") or "")
            if url.startswith("https://"):
                return url
        if status == 422 and isinstance(body, dict):
            errors = body.get("errors") or []
            if any(
                isinstance(err, dict) and err.get("code") == "already_exists"
                for err in errors
            ):
                return (
                    f"https://github.com/{self.repository}/releases/download/"
                    f"{quote(tag, safe='')}/{quote(asset_name, safe='')}"
                )
        raise self._error("asset upload", status, body)


class ImgBBMediaHost:
    """Upload an image to ImgBB and return its direct CDN URL.

    ImgBB serves the file with a real image content-type, which is what
    Telegram link previews, Telegraph ``<img>`` tags and dev.to cover
    images expect.  The API accepts either a binary upload or base64 in
    the ``image`` field; base64 is used here so the call is a single
    form POST and stays under the 32 MB limit for our 1200x630 cards.
    """

    API = "https://api.imgbb.com/1/upload"

    def __init__(
        self,
        api_key: str,
        *,
        expiration: int | None = None,
        post_fn: Callable[..., Any] | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.expiration = expiration
        self._post = post_fn or requests.post

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def upload_image(
        self,
        data: bytes,
        *,
        filename: str = "card.png",
        content_type: str = "image/png",
    ) -> str:
        if not self.configured:
            raise MediaHostError("ImgBB is not configured (IMGBB_API_KEY is empty)")
        if not data:
            raise MediaHostError("Cannot upload an empty image")

        name = _safe_asset_name(filename, data).rsplit(".", 1)[0]
        payload: dict[str, Any] = {
            "key": self.api_key,
            "image": base64.b64encode(data).decode("ascii"),
            "name": name,
        }
        if self.expiration:
            payload["expiration"] = str(int(self.expiration))
        try:
            response = self._post(self.API, data=payload, timeout=120)
        except requests.RequestException as exc:
            raise MediaHostError(
                f"ImgBB upload request failed: {sanitize(str(exc))[:200]}"
            ) from exc
        try:
            body = response.json()
        except ValueError as exc:
            raise MediaHostError(
                f"ImgBB upload returned non-JSON HTTP {response.status_code}"
            ) from exc

        success = isinstance(body, dict) and bool(body.get("success"))
        if response.status_code >= 400 or not success:
            detail: Any = body
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                detail = body["error"].get("message") or body["error"]
            raise MediaHostError(
                f"ImgBB upload failed HTTP {response.status_code}: {sanitize(str(detail))[:200]}"
            )

        data_obj = body.get("data") if isinstance(body, dict) else None
        data_obj = data_obj if isinstance(data_obj, dict) else {}
        assert isinstance(data_obj, dict)
        data_obj = cast(dict[str, Any], data_obj)
        image_obj = data_obj.get("image") if isinstance(data_obj.get("image"), dict) else {}  # type: ignore[union-attr]
        url = str(
            image_obj.get("url")  # type: ignore[union-attr]
            or data_obj.get("display_url")
            or data_obj.get("url")
            or ""
        )
        if not url.startswith("https://"):
            raise MediaHostError("ImgBB response has no usable image URL")
        return url
