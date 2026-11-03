from datetime import datetime, timezone

import pytest

from ghos_v6.media_host import GitHubReleaseMediaHost, ImgBBMediaHost, MediaHostError
from ghos_v6.telegraph import TelegraphClient, TelegraphError


def _fixed_now():
    return datetime(2026, 11, 1, tzinfo=timezone.utc)


def test_GitHub_release_host_creates_monthly_prerelease_and_uploads_asset():
    calls = []

    def fake(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if method == "GET" and url.endswith("/releases/tags/media-2026-11"):
            return 404, {"message": "Not Found"}, None
        if method == "POST" and url.endswith("/releases"):
            body = kwargs["json_body"]
            assert body["tag_name"] == "media-2026-11"
            assert body["prerelease"] is True
            return (
                201,
                {
                    "id": 7,
                    "upload_url": "https://uploads.github.com/repos/acme/repo/releases/7/assets{?name,label}",
                },
                None,
            )
        if method == "POST" and url.startswith("https://uploads.github.com/"):
            assert kwargs["data"] == b"png-bytes"
            assert kwargs["params"]["name"].startswith("card-")
            assert kwargs["params"]["name"].endswith(".png")
            return (
                201,
                {
                    "browser_download_url": (
                        "https://github.com/acme/repo/releases/download/"
                        "media-2026-11/card.png"
                    )
                },
                None,
            )
        raise AssertionError((method, url))

    host = GitHubReleaseMediaHost("token", "acme/repo", request_fn=fake, now_fn=_fixed_now)
    url = host.upload_image(b"png-bytes", filename="card.png")
    assert url.startswith("https://github.com/acme/repo/releases/download/media-2026-11/")
    assert [call[0] for call in calls] == ["GET", "POST", "POST"]


def test_GitHub_release_host_reuses_deterministic_asset_on_already_exists():
    def fake(method, url, **kwargs):
        if method == "GET":
            return (
                200,
                {
                    "id": 7,
                    "upload_url": "https://uploads.github.com/repos/acme/repo/releases/7/assets{?name,label}",
                },
                None,
            )
        return 422, {"errors": [{"code": "already_exists", "field": "name"}]}, None

    host = GitHubReleaseMediaHost("token", "acme/repo", request_fn=fake, now_fn=_fixed_now)
    url = host.upload_image(b"same-bytes", filename="cover.png")
    assert "/releases/download/media-2026-11/cover-" in url
    assert url.endswith(".png")


def test_GitHub_release_host_requires_token_and_repository():
    host = GitHubReleaseMediaHost("", "", request_fn=lambda *a, **k: (500, {}, None))
    with pytest.raises(MediaHostError):
        host.upload_image(b"png")


def test_telegraph_upload_surfaces_unknown_error_string(monkeypatch):
    class Response:
        status_code = 400

        @staticmethod
        def json():
            return "Unknown error"

    monkeypatch.setattr(
        "ghos_v6.telegraph.requests.post", lambda *args, **kwargs: Response()
    )
    with pytest.raises(TelegraphError, match="Unknown error"):
        TelegraphClient("token").upload_image(b"png-bytes")


class _Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


def test_imgbb_upload_returns_direct_cdn_image_url():
    seen = {}

    def fake_post(url, data=None, timeout=None):
        seen["url"] = url
        seen["data"] = dict(data or {})
        return _Response(
            200,
            {
                "data": {
                    "url": "https://i.ibb.co/abc/cover.png",
                    "display_url": "https://i.ibb.co/xyz/cover.png",
                    "image": {"url": "https://i.ibb.co/abc/cover.png"},
                },
                "success": True,
                "status": 200,
            },
        )

    host = ImgBBMediaHost("secret-key", post_fn=fake_post)
    url = host.upload_image(b"\x89PNG-bytes", filename="cover.png")
    assert url == "https://i.ibb.co/abc/cover.png"
    assert seen["url"].endswith("/1/upload")
    assert seen["data"]["key"] == "secret-key"
    assert seen["data"]["name"].startswith("cover-")
    assert "expiration" not in seen["data"]  # permanent by default


def test_imgbb_reports_vendor_error_without_leaking_key():
    def fake_post(url, data=None, timeout=None):
        return _Response(400, {"error": {"message": "Invalid API v1 key."}, "success": False})

    host = ImgBBMediaHost("secret-key", post_fn=fake_post)
    with pytest.raises(MediaHostError) as exc:
        host.upload_image(b"png")
    assert "Invalid API v1 key." in str(exc.value)
    assert "secret-key" not in str(exc.value)


def test_imgbb_expiration_is_passed_when_configured():
    captured = {}

    def fake_post(url, data=None, timeout=None):
        captured.update(data or {})
        return _Response(200, {"data": {"image": {"url": "https://i.ibb.co/x/y.png"}}, "success": True})

    ImgBBMediaHost("k", expiration=604800, post_fn=fake_post).upload_image(b"png")
    assert captured["expiration"] == "604800"


def test_editorial_card_never_ends_with_ellipsis_and_keeps_all_words():

    from PIL import Image

    from ghos_v6.cards import _fit_block, _short_copy
    from PIL import ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    long_copy = (
        "An open-source, self-hosted, fully local alternative to the entire "
        "cloud productivity stack"
    )
    trimmed = _short_copy(long_copy, 74)
    _font_obj, lines = _fit_block(draw, trimmed, "serif", 40, 720, 2, minimum=26)
    assert "…" not in " ".join(lines)
    # every word that was kept must be rendered, nothing silently dropped
    assert " ".join(lines).split() == trimmed.split()


def test_short_copy_prefers_clause_boundary():
    from ghos_v6.cards import _short_copy

    value = _short_copy("Run documents and slides on your own hardware, offline and free", 45)
    assert "…" not in value
    assert len(value) <= 45
    assert value.endswith(("hardware", "own hardware"))
