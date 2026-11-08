"""Shared Gemini token budget.

The free tier is bounded by *input tokens per minute* (TPM), not only by
requests. GitHub runs each cron in a separate process, so an in-memory
limiter is not enough: the sliding window is persisted in ``state.json``
under ``gemini_window`` and committed with the rest of the state.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from zoneinfo import ZoneInfo

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


# ---------------------------------------------------------------------------
# Daily free-tier guard. Gemini's free allowance is 500 RPD per project for
# gemini-3.5-flash-lite, resetting at midnight Pacific. RPM/TPM alone cannot
# protect it: a slot that keeps retrying across the day can spend the whole
# daily quota one minute at a time. The counter lives in state.json, so every
# separate Actions run sees the same total.
# ---------------------------------------------------------------------------
FREE_RPD = 500
MAX_DAILY_UNITS = 450              # 90% of 500, with a 50-request safety margin
UNITS_PER_CALL = 3                 # one generate_json may make up to 3 HTTP POSTs


class GeminiQuotaGuard(RuntimeError):
    """Raised *before* an HTTP call when the daily free-tier budget is spent."""


def _pacific_day(moment: datetime | None = None) -> str:
    """RPD resets at midnight Pacific; fall back to UTC-8 if tzdata is absent."""
    now = moment or datetime.now(timezone.utc)
    try:
        return now.astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()
    except Exception:  # noqa: BLE001 - tzdata may be missing on a runner
        return now.astimezone(timezone(timedelta(hours=-8))).date().isoformat()


class GeminiDailyCap:
    """Count one logical Gemini generation as several HTTP requests.

    Deliberately independent of :class:`GeminiBudget`: the AI Brief keeps its
    owner-approved plain client (no TPM window, no 70 s heavy-call gap).
    """

    def __init__(self, store: Any, *, now: Callable[[], datetime] | None = None,
                 max_units: int = MAX_DAILY_UNITS, units_per_call: int = UNITS_PER_CALL):
        self.store = store
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.max_units = int(max_units)
        self.units_per_call = int(units_per_call)

    @property
    def usage(self) -> dict[str, Any]:
        day = _pacific_day(self.now())
        raw = self.store.data.get("gemini_daily")
        if not isinstance(raw, dict) or str(raw.get("day") or "") != day:
            return {"day": day, "units": 0}
        try:
            used = int(raw.get("units") or 0)
        except (TypeError, ValueError):
            used = 0
        if used < 0 or used > self.max_units * 10:  # corrupt state: reset, never lock out
            used = 0
        return {"day": day, "units": used}

    def consume(self, units: int | None = None) -> dict[str, Any]:
        usage = self.usage
        cost = self.units_per_call if units is None else max(1, int(units))
        if usage["units"] + cost > self.max_units:
            self.store.data["gemini_daily"] = usage
            raise GeminiQuotaGuard(
                f"Gemini free-tier daily guard: {usage['units']}/{self.max_units} "
                f"units used on {usage['day']}; refusing call {cost}"
            )
        usage["units"] += cost
        self.store.data["gemini_daily"] = usage
        return usage
