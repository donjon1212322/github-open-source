"""Small HTTP helper with bounded retries and secret-safe errors."""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

USER_AGENT = "GitHubOpenSourceBot/6.0 (+https://t.me/GitHub_Open_Source)"

_SENSITIVE_ENV = (
    "TELEGRAM_BOT_TOKEN",
    "GEMINI_API_KEY",
    "GH_API_TOKEN",
    "GITHUB_TOKEN",
    "DEV_API_KEY",
    "BLUESKY_APP_PASSWORD",
    "FACEBOOK_PAGE_ACCESS_TOKEN",
    "TELEGRAPH_ACCESS_TOKEN",
    "IMGBB_API_KEY",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
)


def sanitize(text: str) -> str:
    """Remove credential-shaped substrings before text can reach a log."""
    out = str(text)
    for name in _SENSITIVE_ENV:
        value = os.getenv(name)
        if value and len(value) >= 8:
            out = out.replace(value, "[REDACTED]")
    out = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "[REDACTED]", out)
    out = re.sub(r"github_pat_[A-Za-z0-9_]{10,}", "[REDACTED]", out)
    # Service-account JSON and PEM keys can appear inside an error message.
    out = re.sub(r'"private_key"\s*:\s*"[^"]*"', '"private_key": "[REDACTED]"', out)
    out = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                 "[REDACTED]", out, flags=re.S)
    return out


class UnsafeUrl(RuntimeError):
    """A URL points at a non-public address or uses a forbidden scheme."""


def validate_public_url(url: str) -> None:
    """Reject non-HTTPS URLs and hosts that resolve to a private address.

    Called before every request *and* on every redirect hop, so a public
    host cannot redirect the bot to 127.0.0.1 or the cloud metadata IP.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise UnsafeUrl("only https URLs are allowed")
    host = parsed.hostname.lower()
    if host in {"localhost", "metadata.google.internal"} or host.endswith(".local"):
        raise UnsafeUrl(f"host not allowed: {host}")
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrl(f"cannot resolve {host}") from exc
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not ip.is_global:
            raise UnsafeUrl(f"{host} resolves to a non-public address")


class HttpError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(sanitize(message))
        self.status = status


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    data: Any = None,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
    retries: int = 2,
    retry_post: bool = False,
) -> tuple[int, Any, requests.Response | None]:
    """Request JSON. Returns ``(status, parsed_json_or_text, response)``.

    Retries are limited to idempotent methods unless ``retry_post`` is set.
    HTTP error statuses are returned, not raised, so callers can make
    provider-specific fallbacks. Network failures raise :class:`HttpError`
    after retries.
    """
    merged_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        merged_headers.update(headers)
    attempts = retries + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = requests.request(
                method,
                url,
                headers=merged_headers,
                json=json_body,
                data=data,
                params=params,
                timeout=timeout,
            )
            if response.status_code == 429 and attempt < attempts - 1:
                if method.upper() in {"GET", "HEAD"} or retry_post:
                    retry_after = response.headers.get("Retry-After", "")
                    delay = min(int(retry_after), 30) if retry_after.isdigit() else min(2**attempt, 4)
                    time.sleep(delay)
                    continue
            if response.status_code in (500, 502, 503, 504) and attempt < attempts - 1:
                if method.upper() in {"GET", "HEAD"} or retry_post:
                    time.sleep(min(2**attempt, 4))
                    continue
            try:
                parsed = response.json() if response.content else {}
            except ValueError:
                parsed = sanitize(response.text[:1000])
            return response.status_code, parsed, response
        except requests.RequestException as exc:  # noqa: PERF203 - explicit retry loop
            last_error = exc
            if attempt < attempts - 1 and (method.upper() in {"GET", "HEAD"} or retry_post):
                time.sleep(min(2**attempt, 4))
                continue
            break
    raise HttpError(0, f"network error: {last_error}")


def download_bytes(
    url: str,
    *,
    max_bytes: int = 8_000_000,
    timeout: int = 30,
    max_redirects: int = 3,
) -> bytes:
    """Download a bounded response body, validating every redirect hop.

    Redirects are followed manually so a public URL cannot bounce the bot
    to a private address. The caller no longer has to remember to validate.
    """
    current = url
    for _ in range(max_redirects + 1):
        validate_public_url(current)
        with requests.get(
            current,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            stream=True,
            allow_redirects=False,
        ) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location", "")
                if not location:
                    raise HttpError(response.status_code, "redirect without Location")
                current = urljoin(current, location)
                continue
            response.raise_for_status()
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise HttpError(413, f"download exceeds {max_bytes} bytes")
                chunks.append(chunk)
            return b"".join(chunks)
    raise HttpError(310, "too many redirects")
