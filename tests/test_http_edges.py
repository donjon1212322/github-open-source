"""HTTP helper boundary branches, exercised without any real network."""
from __future__ import annotations

import pytest

from ghos_v6 import http


class _Response:
    def __init__(self, status: int, payload: object = None, *, text: str = "{}",
                 content: bytes = b"{}", headers: dict | None = None,
                 bad_json: bool = False):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text
        self.content = content
        self.headers = headers or {}
        self._bad_json = bad_json

    def json(self) -> object:
        if self._bad_json:
            raise ValueError("not json")
        return self._payload


def _addr(ip: str):
    return [(None, None, None, None, (ip, 443))]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/x",
        "ftp://example.com/x",
        "https://",
        "https://localhost/x",
        "https://metadata.google.internal/x",
        "https://printer.local/x",
    ],
)
def test_non_public_urls_are_rejected(url):
    with pytest.raises(http.UnsafeUrl):
        http.validate_public_url(url)


def test_private_dns_answer_is_rejected(monkeypatch):
    monkeypatch.setattr(
        http.socket, "getaddrinfo", lambda *a, **k: _addr("127.0.0.1")
    )
    with pytest.raises(http.UnsafeUrl, match="non-public"):
        http.validate_public_url("https://example.com/x")


def test_dns_failure_is_rejected(monkeypatch):
    def fail(*args, **kwargs):
        raise http.socket.gaierror("no such host")

    monkeypatch.setattr(http.socket, "getaddrinfo", fail)
    with pytest.raises(http.UnsafeUrl, match="cannot resolve"):
        http.validate_public_url("https://example.com/x")


def test_public_dns_answer_is_accepted(monkeypatch):
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **k: _addr("8.8.8.8"))
    assert http.validate_public_url("https://example.com/x") is None


def test_non_ip_dns_answers_are_skipped(monkeypatch):
    monkeypatch.setattr(http.socket, "getaddrinfo", lambda *a, **k: _addr("not-an-ip"))
    assert http.validate_public_url("https://example.com/x") is None


def test_retry_after_header_is_honoured_for_get(monkeypatch):
    calls: list[int] = []

    def fake_request(method: str, url: str, **kwargs: object) -> _Response:
        calls.append(1)
        if len(calls) == 1:
            return _Response(429, headers={"Retry-After": "3"})
        return _Response(200, {"ok": True})

    monkeypatch.setattr(http.requests, "request", fake_request)
    sleeps: list[int] = []
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    status, data, _ = http.request_json("GET", "https://example.com/x", retries=2)
    assert (status, data, sleeps, len(calls)) == (200, {"ok": True}, [3], 2)


def test_post_is_retried_only_when_explicitly_allowed(monkeypatch):
    calls: list[int] = []

    def fake_request(method: str, url: str, **kwargs: object) -> _Response:
        calls.append(1)
        return _Response(503 if len(calls) == 1 else 200, {"ok": True})

    monkeypatch.setattr(http.requests, "request", fake_request)
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)
    status, _, _ = http.request_json(
        "POST", "https://example.com/x", retries=2, retry_post=True
    )
    assert status == 200 and len(calls) == 2


def test_empty_body_and_invalid_json_are_handled(monkeypatch):
    monkeypatch.setattr(
        http.requests, "request",
        lambda *a, **k: _Response(200, content=b""),
    )
    assert http.request_json("GET", "https://example.com/x")[1] == {}

    monkeypatch.setattr(
        http.requests, "request",
        lambda *a, **k: _Response(200, text="plain error text", bad_json=True),
    )
    assert http.request_json("GET", "https://example.com/x")[1] == "plain error text"


def test_post_network_failure_without_retry_post_raises_immediately(monkeypatch):
    calls: list[int] = []

    def failing_request(method: str, url: str, **kwargs: object):
        calls.append(1)
        raise http.requests.RequestException("down")

    monkeypatch.setattr(http.requests, "request", failing_request)
    monkeypatch.setattr(http.time, "sleep", lambda _s: None)
    with pytest.raises(http.HttpError):
        http.request_json("POST", "https://example.com/x", retries=3)
    assert len(calls) == 1


def test_service_account_json_and_pem_are_redacted():
    pem = "-----BEGIN " + "PRIVATE KEY-----abc" + "-----END " + "PRIVATE KEY-----"
    source = '{"private_key": "' + pem + '"}'
    cleaned = http.sanitize(source)
    assert "abc" not in cleaned
    assert "[REDACTED]" in cleaned
