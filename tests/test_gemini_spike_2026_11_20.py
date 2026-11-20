"""Owner 20.11: a Gemini capacity spike outlived the 2-attempt backoff.

Run 603 (publish slot us_morning, 16:23Z) failed: gemini-3.5-flash-lite
answered 503 "high demand", the spare model (gemini-3.1-flash-lite) was
congested too, and both backoff attempts (~2.5 min apart) hit the same
spike. The watchdog recovered the slot minutes later - but a repeat failure
in that recovery would have burned strike two and dead-lettered an innocent
repo. The slot-critical project call now retries three times; the delays
stay linear (65 s, 130 s) so no two attempts share a rolling TPM minute.
"""
from __future__ import annotations

from ghos_v6 import Gemini as G
from ghos_v6.Gemini import HttpError, generate_with_backoff


def test_project_generation_survives_a_two_failure_capacity_spike(monkeypatch):
    recorded: dict[str, list[int]] = {}
    real = G.generate_with_backoff

    def spy(call, *, attempts=2, base_delay=65.0, sleep=None, jitter=None):
        recorded.setdefault("attempts", []).append(attempts)
        return real(call, attempts=attempts, base_delay=base_delay,
                    sleep=lambda _s: None, jitter=lambda lo, hi: 1.0)

    monkeypatch.setattr(G, "generate_with_backoff", spy)

    calls = {"n": 0}

    class SpikeClient:
        configured = True

        def generate_project(self, candidate):
            calls["n"] += 1
            if calls["n"] < 3:  # attempts one and two die inside the spike
                raise HttpError(503, "This model is currently experiencing high demand")
            return {"llm_used": True, "summary": "ok", "hook": "ok", "safety": "reject"}

        def generate_surfaces(self, candidate, base):
            return {}

    out = G.generate_project_content(
        SpikeClient(),
        {"full_name": "owner/demo", "name": "demo",
         "description": "A useful demo project"},
    )

    assert out["llm_used"] is True, "two transient failures must not kill the slot"
    assert "error_class" not in out, "no deterministic fallback may be used"
    assert calls["n"] == 3
    assert recorded["attempts"][0] == 3, "the project call must ask for three attempts"


def test_third_attempt_still_lands_in_its_own_tpm_minute():
    waits: list[float] = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise HttpError(503, "high demand")
        return "ok"

    assert generate_with_backoff(
        flaky, attempts=3, base_delay=65.0, sleep=waits.append,
        jitter=lambda lo, hi: 1.0,
    ) == "ok"
    assert waits == [65.0, 130.0], "linear spacing: no two attempts share a TPM minute"


def test_permanent_errors_still_fail_fast_with_three_attempts():
    calls = {"n": 0}

    def broken():
        calls["n"] += 1
        raise HttpError(400, "invalid schema")

    try:
        generate_with_backoff(broken, attempts=3, sleep=lambda _s: None)
    except HttpError:
        pass
    else:
        raise AssertionError("permanent errors must not be retried")
    assert calls["n"] == 1
