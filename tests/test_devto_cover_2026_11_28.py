"""Owner 28.11: "посмотри статью на dev.to там карточка обрезана".

The llm.asm article (28.11 13:34) shows a cropped card. Two causes, both
measured on the live objects:

* its preview JPEG on ImgBB is 57 344 bytes with no end-of-image marker
  (the victory-fork one is 72 881 bytes and complete), so Pillow raised
  "image file is truncated";
* ``build_devto_cover`` therefore returned nothing, the article fell back to
  the raw 1200x630 card, and dev.to's fixed ``width=1000,height=420,
  fit=cover`` cut the masthead and the stats row off.

The cover renderer now accepts a card the CDN served truncated, and an upload
is measured instead of believed.
"""
from __future__ import annotations

from io import BytesIO

from PIL import Image

from ghos_v6.devto_cover import COVER_H, COVER_W, build_devto_cover
from ghos_v6.media_host import ImgBBMediaHost


def _card(colour=(240, 236, 226)) -> bytes:
    image = Image.new("RGB", (1200, 630), colour)
    out = BytesIO()
    image.save(out, format="JPEG", quality=92)
    return out.getvalue()


def _truncated(data: bytes, keep: float = 0.8) -> bytes:
    return data[: int(len(data) * keep)]


def test_a_card_the_cdn_served_truncated_still_becomes_a_cover():
    card = _card()
    assert not _truncated(card).endswith(b"\xff\xd9")
    cover = build_devto_cover(_truncated(card))
    with Image.open(BytesIO(cover)) as image:
        assert image.size == (COVER_W, COVER_H)
        assert image.format == "PNG"


def test_a_complete_card_still_produces_the_same_cover():
    cover = build_devto_cover(_card())
    with Image.open(BytesIO(cover)) as image:
        assert image.size == (COVER_W, COVER_H)


# --- the upload is measured, not believed -----------------------------------

class _Response:
    def __init__(self, status_code, body=None, headers=None):
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}

    def json(self):
        return self._body


def _ok(url="https://i.ibb.co/abc/cover.jpg"):
    return _Response(200, {"data": {"image": {"url": url}, "url": url}, "success": True})


def test_a_half_stored_upload_is_retried_and_the_full_one_wins():
    """ImgBB answered success while storing 57 344 of 72 881 bytes."""
    payload = b"x" * 72_881
    posts: list[int] = []
    heads: list[int] = []

    def fake_post(url, data=None, timeout=None):
        posts.append(len(data.get("image") or ""))
        return _ok(f"https://i.ibb.co/abc/attempt-{len(posts)}.jpg")

    def fake_head(url, timeout=None, allow_redirects=True):
        heads.append(1)
        if len(heads) == 1:
            return _Response(200, headers={"content-length": "57344"})
        return _Response(200, headers={"content-length": str(len(payload))})

    host = ImgBBMediaHost("k", post_fn=fake_post, head_fn=fake_head, sleep_fn=lambda _s: None)
    url = host.upload_image(payload, filename="cover.jpg")
    assert url.endswith("attempt-2.jpg")
    assert len(posts) == 2


def test_an_unmeasurable_object_is_accepted_instead_of_retried_forever():
    def fake_post(url, data=None, timeout=None):
        return _ok()

    def fake_head(url, timeout=None, allow_redirects=True):
        raise RuntimeError("no network")

    host = ImgBBMediaHost("k", post_fn=fake_post, head_fn=fake_head, sleep_fn=lambda _s: None)
    assert host.upload_image(b"png") == "https://i.ibb.co/abc/cover.jpg"
