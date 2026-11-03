"""Telegraph client and a compact Markdown→DOM converter."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

from .http import USER_AGENT, sanitize

API_BASE = "https://api.telegra.ph"
AUTHOR_NAME = "GitHub Open Source"
AUTHOR_URL = "https://t.me/GitHub_Open_Source"

_INLINE_PATTERNS = [
    ("code", re.compile(r"`([^`]+)`")),
    ("bold", re.compile(r"\*\*([^*]+)\*\*")),
    ("italic", re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")),
    ("link", re.compile(r"\[([^\]]+)\]\((https://[^)\s]+)\)")),
]

#: Link allowlist for model-written articles. The channel may link to the
#: project on GitHub, its own Telegram channel, and Telegraph. Everything
#: else is rendered as plain text so a prompt-injected article cannot send
#: readers to an arbitrary domain.
_ALLOWED_LINK_HOSTS = {"github.com", "www.github.com", "telegra.ph", "opengraph.githubassets.com"}
_ALLOWED_TELEGRAM_PATH = "/GitHub_Open_Source"


def _allowed_link(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in _ALLOWED_LINK_HOSTS:
        return True
    if host == "t.me":
        path = parsed.path.rstrip("/")
        return path == _ALLOWED_TELEGRAM_PATH or path.startswith(_ALLOWED_TELEGRAM_PATH + "/")
    return False
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+)$")
_BULLET_RE = re.compile(r"^[*-]\s+(.+)$")
_NUMBER_RE = re.compile(r"^\d+[.)]\s+(.+)$")


def _inline(text: str) -> list[Any]:
    if not text:
        return []
    best_kind = None
    best_match = None
    for kind, pattern in _INLINE_PATTERNS:
        match = pattern.search(text)
        if match and (best_match is None or match.start() < best_match.start()):
            best_kind, best_match = kind, match
    if best_match is None or best_kind is None:
        return [text]
    nodes: list[Any] = []
    before = text[: best_match.start()]
    after = text[best_match.end() :]
    if before:
        nodes.append(before)
    if best_kind == "code":
        nodes.append({"tag": "code", "children": [best_match.group(1)]})
    elif best_kind == "bold":
        nodes.append({"tag": "strong", "children": _inline(best_match.group(1))})
    elif best_kind == "italic":
        nodes.append({"tag": "em", "children": _inline(best_match.group(1))})
    elif best_kind == "link":
        if _allowed_link(best_match.group(2)):
            nodes.append(
                {
                    "tag": "a",
                    "attrs": {"href": best_match.group(2)},
                    "children": _inline(best_match.group(1)),
                }
            )
        else:
            # Drop the URL but keep the anchor text: the article still reads.
            nodes.extend(_inline(best_match.group(1)))
    nodes.extend(_inline(after))
    return [node for node in nodes if not (isinstance(node, str) and node == "")]


def markdown_to_dom(markdown: str) -> list[Any]:
    lines = str(markdown or "").replace("\r\n", "\n").splitlines()
    out: list[Any] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_tag: str | None = None
    in_code = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        text = " ".join(line.strip() for line in paragraph if line.strip())
        paragraph = []
        if text:
            children = _inline(text)
            if children:
                out.append({"tag": "p", "children": children})

    def flush_list() -> None:
        nonlocal list_items, list_tag
        if list_items and list_tag:
            out.append(
                {
                    "tag": list_tag,
                    "children": [
                        {"tag": "li", "children": _inline(item)} for item in list_items
                    ],
                }
            )
        list_items = []
        list_tag = None

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                out.append({"tag": "pre", "children": [{"tag": "code", "children": ["\n".join(code_lines)]}]})
                code_lines = []
                in_code = False
            else:
                flush_paragraph()
                flush_list()
                in_code = True
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        heading = _HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            flush_list()
            tag = "h4" if len(heading.group(1)) >= 4 else "h3"
            out.append({"tag": tag, "children": _inline(heading.group(2).strip())})
            continue
        bullet = _BULLET_RE.match(stripped)
        number = _NUMBER_RE.match(stripped)
        if bullet or number:
            flush_paragraph()
            tag = "ul" if bullet else "ol"
            if list_tag and list_tag != tag:
                flush_list()
            list_tag = tag
            match = bullet or number
            assert match is not None
            list_items.append(match.group(1).strip())
            continue
        if stripped in {"---", "***", "___"}:
            flush_paragraph()
            flush_list()
            out.append({"tag": "hr"})
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            out.append({"tag": "blockquote", "children": _inline(stripped.lstrip("> ").strip())})
            continue
        flush_list()
        paragraph.append(line)

    if in_code and code_lines:
        out.append({"tag": "pre", "children": [{"tag": "code", "children": ["\n".join(code_lines)]}]})
    flush_paragraph()
    flush_list()
    return out or [{"tag": "p", "children": [""]}]


def figure_node(image_url: str, caption: str = "") -> dict[str, Any]:
    children: list[Any] = [{"tag": "img", "attrs": {"src": image_url}}]
    if caption:
        children.append({"tag": "figcaption", "children": [caption]})
    return {"tag": "figure", "children": children}


class TelegraphError(RuntimeError):
    pass


class TelegraphClient:
    def __init__(self, access_token: str = ""):
        self.access_token = access_token.strip()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    @property
    def configured(self) -> bool:
        return bool(self.access_token)

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(f"{API_BASE}/{method}", json=payload, timeout=45)
        try:
            body = response.json()
        except ValueError as exc:
            raise TelegraphError(f"Telegraph {method} returned non-JSON HTTP {response.status_code}") from exc
        if not body.get("ok"):
            raise TelegraphError(f"Telegraph {method} failed: {sanitize(str(body.get('error') or body))[:240]}")
        result = body.get("result")
        if not isinstance(result, dict):
            raise TelegraphError(f"Telegraph {method} response has no result object")
        return result

    def get_account_info(self) -> dict[str, Any]:
        if not self.configured:
            raise TelegraphError("TELEGRAPH_ACCESS_TOKEN is not set")
        return self._post(
            "getAccountInfo",
            {"access_token": self.access_token, "fields": ["short_name", "author_name", "page_count"]},
        )

    def create_page(
        self,
        *,
        title: str,
        markdown: str,
        image_url: str = "",
        image_caption: str = "",
    ) -> dict[str, Any]:
        if not self.configured:
            raise TelegraphError("TELEGRAPH_ACCESS_TOKEN is not set")
        content = markdown_to_dom(markdown)
        if image_url.startswith("https://"):
            content = [figure_node(image_url, image_caption), *content]
        return self._post(
            "createPage",
            {
                "access_token": self.access_token,
                "title": str(title)[:256],
                "author_name": AUTHOR_NAME,
                "author_url": AUTHOR_URL,
                "content": json.dumps(content, ensure_ascii=False),
                "return_content": False,
            },
        )

    def upload_image(
        self,
        data: bytes,
        *,
        filename: str = "cover.png",
        content_type: str = "image/png",
    ) -> str:
        if not data:
            raise TelegraphError("Cannot upload an empty image")
        response = requests.post(
            "https://telegra.ph/upload",
            files={"file": (filename, data, content_type)},
            headers={"User-Agent": USER_AGENT},
            timeout=60,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise TelegraphError(f"Telegraph upload returned non-JSON HTTP {response.status_code}") from exc
        if response.status_code >= 400:
            raise TelegraphError(
                f"Telegraph upload failed HTTP {response.status_code}: {sanitize(str(body))[:200]}"
            )
        if isinstance(body, str):
            raise TelegraphError(f"Telegraph upload failed: {sanitize(body)[:200]}")
        if isinstance(body, dict) and body.get("error"):
            raise TelegraphError(f"Telegraph upload failed: {sanitize(str(body['error']))[:200]}")
        if not isinstance(body, list) or not body or not isinstance(body[0], dict):
            raise TelegraphError("Telegraph upload response has no file entry")
        src = str(body[0].get("src") or "")
        if src.startswith("/"):
            return "https://telegra.ph" + src
        if src.startswith("https://"):
            return src
        raise TelegraphError("Telegraph upload response has no usable src")

    def upload_image_path(self, path: Path) -> str:
        suffix = path.suffix.lower()
        content_type = {
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }.get(suffix, "application/octet-stream")
        return self.upload_image(path.read_bytes(), filename=path.name, content_type=content_type)
