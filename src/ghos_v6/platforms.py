"""External article/social platform adapters.

Each adapter is independently feature-flagged by the pipeline; one
platform failure must never roll back the Telegram publication.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

from .content_adapters import build_devto_body, build_devto_title, devto_tags
from .http import USER_AGENT, sanitize

URL_RE = re.compile(r"https://[^\s<>\"')]+")


class PlatformError(RuntimeError):
    pass


def _error(platform: str, response: requests.Response) -> PlatformError:
    try:
        body = response.json()
    except ValueError:
        body = response.text[:300]
    return PlatformError(sanitize(f"{platform} HTTP {response.status_code}: {body}")[:500])


class DevToClient:
    def __init__(self, api_key: str):
        self.api_key = api_key.strip()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.forem.api-v1+json",
                "api-key": self.api_key,
            }
        )

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Bounded retry for dev.to throttling (429) and transient 5xx."""
        timeout = kwargs.pop("timeout", 60)
        delays = (5, 15, 30)
        for attempt in range(4):
            response = self.session.request(method, url, timeout=timeout, **kwargs)
            if response.status_code not in {429, 500, 502, 503, 504} or attempt == 3:
                return response
            raw = str(response.headers.get("Retry-After") or "")
            delay = int(raw) if raw.isdigit() else delays[min(attempt, 2)]
            time.sleep(min(max(delay, 1), 45))
        return response

    def me(self) -> dict[str, Any]:
        response = self.session.get("https://dev.to/api/users/me", timeout=30)
        if response.status_code != 200:
            raise _error("dev.to", response)
        return dict(response.json() or {})

    @staticmethod
    def _tags(record: dict[str, Any], generated: dict[str, Any] | None = None) -> list[str]:
        return devto_tags(record, generated)

    @staticmethod
    def _main_image(record: dict[str, Any]) -> str:
        """dev.to crops main_image to 1000x420; prefer the letterboxed cover."""
        for key in ("devto_cover_url", "image_url"):
            url = str(record.get(key) or "")
            if url.startswith("https://"):
                return url
        return ""

    def publish_article(self, record: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
        if not self.configured:
            raise PlatformError("DEV_API_KEY is not set")
        markdown = build_devto_body(record, generated).strip()
        if not str(generated.get("article_markdown") or "").strip():
            raise PlatformError("dev.to article markdown is empty")
        article: dict[str, Any] = {
            "title": build_devto_title(record, generated),
            "body_markdown": markdown,
            "published": True,
            "tags": self._tags(record, generated),
        }
        # Owner decision (2026-11-03): every article is a standalone post.
        # A `series` name would silently group every daily article into one
        # growing "Part N Series" block on dev.to, which the owner rejected.
        # No canonical_url to Telegraph: the dev.to version is adapted
        # with its own TL;DR/discussion wrapper and should be indexable
        # as the public article surface.
        cover = self._main_image(record)
        if cover:
            article["main_image"] = cover
        response = self._request(
            "POST", "https://dev.to/api/articles", json={"article": article}
        )
        if response.status_code not in {200, 201}:
            raise _error("dev.to", response)
        return dict(response.json() or {})

    def publish_markdown(
        self,
        title: str,
        body_markdown: str,
        *,
        tags: tuple[str, ...] = (),
        cover_url: str = "",
    ) -> dict[str, Any]:
        """Publish an already-rendered standalone article (owner 2026-11-07).

        Used ONLY by the second daily dev.to article built from the AI Brief.
        ``article_of_day()`` and its project markdown pipeline are untouched.
        """
        if not self.configured:
            raise PlatformError("DEV_API_KEY is not set")
        if not body_markdown.strip():
            raise PlatformError("dev.to article markdown is empty")
        article: dict[str, Any] = {
            "title": title[:120],
            "body_markdown": body_markdown,
            "published": True,
            "tags": list(tags)[:4],
        }
        if cover_url.startswith("https://"):
            article["main_image"] = cover_url
        response = self._request(
            "POST", "https://dev.to/api/articles", json={"article": article}
        )
        if response.status_code not in (200, 201):
            raise _error("dev.to", response)
        return dict(response.json() or {})

    def update_markdown(
        self,
        article_id: int | str,
        title: str,
        body_markdown: str,
        *,
        tags: tuple[str, ...] = (),
        cover_url: str = "",
    ) -> dict[str, Any]:
        """PUT an already-rendered standalone article back in place.

        Unlike ``update_article`` this does NOT rebuild a project review: it
        preserves the Gemini-written companion article verbatim. No duplicate
        post is created (owner 08.11: fix the live article in place).
        """
        if not self.configured:
            raise PlatformError("DEV_API_KEY is not set")
        if not body_markdown.strip():
            raise PlatformError("dev.to article markdown is empty")
        article: dict[str, Any] = {
            "title": title[:120],
            "body_markdown": body_markdown,
            "published": True,
            "tags": list(tags)[:4],
            "series": None,
        }
        if cover_url.startswith("https://"):
            article["main_image"] = cover_url
        response = self._request(
            "PUT", f"https://dev.to/api/articles/{article_id}",
            json={"article": article},
        )
        if response.status_code not in {200, 201}:
            raise _error("dev.to update", response)
        return dict(response.json() or {})


    def my_articles(self, per_page: int = 100) -> list[dict[str, Any]]:
        """Articles owned by this API key, including drafts."""
        response = self._request(
            "GET", "https://dev.to/api/articles/me/all",
            params={"per_page": per_page}, timeout=30,
        )
        if response.status_code != 200:
            raise _error("dev.to article list", response)
        return list(response.json() or [])

    def remove_article(self, article_id: int | str) -> str:
        """Remove an article from the public listing (owner decision 04.11.2026).

        Forem has no guaranteed DELETE endpoint: some deployments answer
        404/405, others accept it. Try DELETE first and fall back to the
        documented ``PUT /api/articles/{id}/unpublish``. Returns the verb
        that worked so the caller can report it honestly.
        """
        if not self.configured:
            raise PlatformError("DEV_API_KEY is not set")
        base = f"https://dev.to/api/articles/{article_id}"
        response = self._request("DELETE", base)
        if response.status_code in {200, 202, 204}:
            return "deleted"
        # /unpublish is privilege-gated (super_moderator); an ordinary
        # api-key gets 401. Forem's documented removal for a normal user is
        # PUT /articles/{id} with published:false - it becomes a private
        # draft and disappears from the public feed and account listing.
        response = self._request("PUT", f"{base}/unpublish")
        if response.status_code in {200, 202, 204}:
            return "unpublished"
        response = self._request("PUT", base, json={"article": {"published": False}})
        if response.status_code in {200, 201, 202, 204}:
            return "drafted"
        raise _error("dev.to remove article", response)

    @staticmethod
    def resolve_article_id(
        items: list[dict[str, Any]], url: str, fallback: int | str
    ) -> tuple[Any, str]:
        """Prefer an id the account can actually edit over a stale state id."""
        for item in items:
            item_url = str(item.get("url") or "")
            path = str(item.get("path") or "")
            if url and (item_url == url or (path and url.endswith(path))):
                return item.get("id") or fallback, "url_match"
        return fallback, "state_id"

    def update_article(
        self, article_id: int | str, record: dict[str, Any], generated: dict[str, Any]
    ) -> dict[str, Any]:
        """Re-render an existing article after a formatting fix.

        dev.to exposes PUT /api/articles/{id}; no duplicate post is created.
        """
        if not self.configured:
            raise PlatformError("DEV_API_KEY is not set")
        article: dict[str, Any] = {
            "title": build_devto_title(record, generated),
            "body_markdown": build_devto_body(record, generated).strip(),
            "published": True,
            "tags": self._tags(record, generated),
            "series": None,  # detach any legacy series; standalone post only
        }
        cover = self._main_image(record)
        if cover:
            article["main_image"] = cover
        response = self._request(
            "PUT", f"https://dev.to/api/articles/{article_id}",
            json={"article": article},
        )
        if response.status_code not in {200, 201}:
            raise _error("dev.to update", response)
        return dict(response.json() or {})


    def clear_series(self, article_id: int | str) -> dict[str, Any]:
        """Detach one already published article from a dev.to series.

        Forem documents ``series: null`` in PUT /api/articles/{id} as the way
        to remove series membership. The body and cover are left untouched.
        """
        if not self.configured:
            raise PlatformError("DEV_API_KEY is not set")
        response = self._request(
            "PUT", f"https://dev.to/api/articles/{article_id}",
            json={"article": {"published": True, "series": None}},
        )
        if response.status_code not in {200, 201}:
            raise _error("dev.to series cleanup", response)
        return dict(response.json() or {})


def build_link_facets(text: str) -> list[dict[str, Any]]:
    """Build AT Protocol rich-text facets for links and hashtags.

    Byte offsets are UTF-8 based, as required by Bluesky.  Hashtag
    facets make our CamelCase tags clickable instead of plain text.
    """
    facets: list[dict[str, Any]] = []
    encoded = text.encode("utf-8")
    url_spans: list[tuple[int, int]] = []

    def byte_range(start: int, value: str) -> tuple[int, int]:
        byte_start = len(text[:start].encode("utf-8"))
        return byte_start, byte_start + len(value.encode("utf-8"))

    for match in URL_RE.finditer(text):
        value = match.group(0).rstrip(".,;:!?")
        if not value:
            continue
        byte_start, byte_end = byte_range(match.start(), value)
        if byte_end > len(encoded):
            continue
        url_spans.append((match.start(), match.start() + len(value)))
        facets.append(
            {
                "index": {"byteStart": byte_start, "byteEnd": byte_end},
                "features": [
                    {"$type": "app.bsky.richtext.facet#link", "uri": value}
                ],
            }
        )

    for match in re.finditer(r"(?<![A-Za-z0-9_#])#([A-Za-z0-9_]+)", text):
        if any(start <= match.start() < end for start, end in url_spans):
            continue
        value = match.group(0)
        byte_start, byte_end = byte_range(match.start(), value)
        if byte_end > len(encoded):
            continue
        facets.append(
            {
                "index": {"byteStart": byte_start, "byteEnd": byte_end},
                "features": [
                    {"$type": "app.bsky.richtext.facet#tag", "tag": match.group(1)}
                ],
            }
        )

    return sorted(facets, key=lambda item: item["index"]["byteStart"])



class BlueskyClient:
    def __init__(self, handle: str, app_password: str):
        handle = handle.strip().lstrip("@")
        if handle and "." not in handle:
            handle += ".bsky.social"
        self.handle = handle
        self.app_password = app_password.strip()
        self._session: dict[str, Any] | None = None

    @property
    def configured(self) -> bool:
        return bool(self.handle and self.app_password)

    def create_session(self) -> dict[str, Any]:
        if self._session:
            return self._session
        response = requests.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": self.handle, "password": self.app_password},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if response.status_code != 200:
            raise _error("Bluesky", response)
        self._session = dict(response.json() or {})
        return self._session

    def verify(self) -> dict[str, Any]:
        session = self.create_session()
        return {"handle": session.get("handle"), "did": session.get("did")}

    def _upload_blob(self, data: bytes, content_type: str) -> dict[str, Any] | None:
        if not data:
            return None
        session = self.create_session()
        response = requests.post(
            "https://bsky.social/xrpc/com.atproto.repo.uploadBlob",
            data=data[:990_000],
            headers={
                "Authorization": f"Bearer {session.get('accessJwt')}",
                "Content-Type": content_type or "image/png",
                "User-Agent": USER_AGENT,
            },
            timeout=60,
        )
        if response.status_code != 200:
            raise _error("Bluesky upload", response)
        return (response.json() or {}).get("blob")

    def post(
        self,
        text: str,
        *,
        url: str = "",
        image_data: bytes = b"",
        content_type: str = "image/png",
        image_alt: str = "",
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        if not self.configured:
            raise PlatformError("Bluesky credentials are not set")
        session = self.create_session()
        body_text = str(text or "").strip()
        if url and url not in body_text:
            suffix = f"\n\n{url}"
            body_text = (body_text[: max(0, 300 - len(suffix))].rstrip() + suffix)[:300]
        else:
            body_text = body_text[:300]
        record: dict[str, Any] = {
            "$type": "app.bsky.feed.post",
            "text": body_text,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "langs": ["en"],
            "facets": build_link_facets(body_text),
        }
        blob = self._upload_blob(image_data, content_type) if image_data else None
        if blob:
            record["embed"] = {
                "$type": "app.bsky.embed.images",
                "images": [{"alt": image_alt[:300] or "Project cover", "image": blob}],
            }
        response = requests.post(
            "https://bsky.social/xrpc/com.atproto.repo.createRecord",
            json={
                "repo": session.get("did"),
                "collection": "app.bsky.feed.post",
                "record": record,
            },
            headers={"Authorization": f"Bearer {session.get('accessJwt')}", "User-Agent": USER_AGENT},
            timeout=45,
        )
        if response.status_code != 200:
            raise _error("Bluesky post", response)
        result = dict(response.json() or {})
        at_uri = str(result.get("uri") or "")
        rkey = at_uri.rsplit("/", 1)[-1] if at_uri else ""
        if rkey:
            result["url"] = f"https://bsky.app/profile/{session.get('handle')}/post/{rkey}"
        return result



class FacebookClient:
    """Legacy direct Page adapter. NOT wired into v6's publishing path.

    The owner requires Facebook posts to go through make.com reading a Google
    Sheet row (see sheets.py). Nothing in config.py, doctor.py or pipeline.py
    constructs this client; it is kept only as a reference implementation.
    """
    API = "https://graph.facebook.com/v26.0"

    def __init__(self, page_id: str, page_access_token: str):
        self.page_id = page_id.strip()
        self.token = page_access_token.strip()

    @property
    def configured(self) -> bool:
        return bool(self.page_id and self.token)

    def verify(self) -> dict[str, Any]:
        response = requests.get(
            f"{self.API}/{self.page_id}",
            params={"fields": "id,name", "access_token": self.token},
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        if response.status_code != 200:
            raise _error("Facebook", response)
        return dict(response.json() or {})

    def post(self, text: str, *, url: str = "", idempotency_key: str = "") -> dict[str, Any]:
        if not self.configured:
            raise PlatformError("Facebook Page token is not set")
        payload: dict[str, Any] = {"message": str(text)[:5000], "access_token": self.token}
        if url.startswith("https://"):
            payload["link"] = url
        response = requests.post(
            f"{self.API}/{self.page_id}/feed",
            data=payload,
            headers={"User-Agent": USER_AGENT},
            timeout=45,
        )
        if response.status_code not in {200, 201}:
            raise _error("Facebook post", response)
        return dict(response.json() or {})
