"""Shared Gemini token budget.

The free tier is bounded by *input tokens per minute* (TPM), not only by
requests. GitHub runs each cron in a separate process, so an in-memory
limiter is not enough: the sliding window is persisted in ``state.json``
under ``gemini_window`` and committed with the rest of the state.
"""
from __future__ import annotations

import time
from typing import Any, Callable

MAX_INPUT_TOKENS_PER_MINUTE = 150_000  # 60% of the 250K free-tier limit
MAX_REQUESTS_PER_MINUTE = 8            # 53% of the 15 RPM limit
WINDOW_SECONDS = 60.0
HEAVY_GAP_SECONDS = 70.0              # separates video/multimodal calls
VIDEO_TOKEN_ESTIMATE = 120_000        # conservative: YouTube file_data


def estimate_tokens(prompt: str, youtube_url: str = "") -> int:
    """Cheap upper-bound estimate; exact count needs countTokens."""
    return len(prompt or "") // 4 + 1 + (VIDEO_TOKEN_ESTIMATE if youtube_url else 0)


class GeminiBudget:
    """Block until the next call fits in the rolling 60-second window."""

    def __init__(
        self,
        store: Any,
        *,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        max_tokens: int = MAX_INPUT_TOKENS_PER_MINUTE,
        max_requests: int = MAX_REQUESTS_PER_MINUTE,
    ) -> None:
        self.store = store
        self.sleep = sleep
        self.now = now
        self.max_tokens = max_tokens
        self.max_requests = max_requests

    def _entries(self) -> list[list[float]]:
        raw = self.store.data.get("gemini_window") or []
        out = []
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                try:
                    out.append([float(item[0]), int(item[1])])
                except (TypeError, ValueError):
                    continue
        return out

    def acquire(self, tokens: int, *, heavy: bool = False) -> None:
        t = self.now()
        entries = self._entries()
        if heavy and entries:
            wait = HEAVY_GAP_SECONDS - (t - entries[-1][0])
            if wait > 0:
                self.sleep(wait)
                t = self.now()
        tokens = max(1, min(int(tokens), self.max_tokens))
        while True:
            entries = [e for e in entries if t - e[0] < WINDOW_SECONDS]
            used = sum(e[1] for e in entries)
            if len(entries) < self.max_requests and used + tokens <= self.max_tokens:
                break
            wait = (WINDOW_SECONDS - (t - entries[0][0]) + 0.5) if entries else 1.0
            self.sleep(max(wait, 0.5))
            t = self.now()
        entries.append([t, tokens])
        self.store.data["gemini_window"] = entries[-self.max_requests:]
