"""23.11: a Google capacity spike must not cost a project or wake the owner.

Production evidence (run 45215964174, 16:23 UTC): `gemini-3.5-flash-lite`
answered 503 "high demand", the fallback `gemini-3.1-flash-lite` did too, and
the slot reported `gemini_unavailable`. The watchdog retried at 16:30, hit the
same spike, and the candidate `valentderah/totk-pc-recomp` was **deleted from
the queue** and marked seen as `gemini_failed_twice` - as if the project were
unwritable. Two things were wrong:

* the per-candidate failure counter counted a provider-wide outage;
* the workflow run went red and emailed the owner about Google's load.

The split is now explicit: `capacity_503` / `quota_429` are a transient skip -
queue untouched, candidate never marked, the watchdog retries the slot, and the
run ends green with a warning annotation. Every other model error still counts
towards the per-candidate budget, because that one is about the project.

`requeue` repairs what the old behaviour already did.
"""
from __future__ import annotations

from ghos_v6 import cli, pipeline
from ghos_v6.state import StateStore


def _candidate(name: str = "totk-pc-recomp") -> dict:
    return {
        "repo_id": 123456 if name == "totk-pc-recomp" else 654321,
        "name": name,
        "full_name": f"valentderah/{name}",
        "url": f"https://github.com/valentderah/{name}",
        "description": "A Nintendo Switch recompilation project",
        "language": "C++",
        "stars": 900,
        "forks": 40,
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2026-11-22T00:00:00Z",
        "topics": ["recompilation"],
        "license": {"spdx_id": "MIT", "name": "MIT License"},
        "default_branch": "main",
        "sources": ["search"],
        "has_readme": True,
        "readme": "A project. " * 60,
        "contributors_count": 4,
    }


def _store(tmp_path) -> StateStore:
    store = StateStore(tmp_path)
    store.data["queue"] = [_candidate(), _candidate("other-project")]
    store.save()
    return store


def _config() -> pipeline.Config:
    return pipeline.Config(telegram_bot_token="x", telegram_test_channel_id=123)


def _stub(monkeypatch, error_class: str) -> None:
    monkeypatch.setattr(pipeline, "TelegramClient", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "GeminiClient", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "GitHubApi", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "enrich_candidate", lambda api, candidate, force=False: candidate)
    monkeypatch.setattr(
        pipeline,
        "generate_project_content",
        lambda client, candidate: {"llm_used": False, "error_class": error_class},
    )


def test_a_capacity_spike_never_touches_the_queue(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _stub(monkeypatch, "capacity_503")
    for _ in range(3):  # the old code deleted the project on the second try
        result = pipeline.publish_slot(_config(), store, "manual")
        assert result["reason"] == "gemini_capacity"
        assert result["queue_unchanged"] is True
        assert result["retry"] is True
    assert len(store.data["queue"]) == 2
    assert store.data.get("publish_failures", {}) == {}
    assert "valentderah/totk-pc-recomp" not in (store.data.get("seen_repos") or {})
    assert store.data.get("dead_letters") == []


def test_a_read_timeout_is_the_same_kind_of_noise(tmp_path, monkeypatch):
    """23.11, second attempt: the 503 became a 90 s read timeout and the
    project lost a life for it. A timeout is the provider being unreachable."""
    store = _store(tmp_path)
    _stub(monkeypatch, "network")
    result = pipeline.publish_slot(_config(), store, "manual")
    assert result["reason"] == "gemini_capacity"
    assert store.data.get("publish_failures", {}) == {}
    assert len(store.data["queue"]) == 2


def test_quota_exhaustion_is_the_same_kind_of_noise(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _stub(monkeypatch, "quota_429")
    result = pipeline.publish_slot(_config(), store, "manual")
    assert result["reason"] == "gemini_capacity"
    assert len(store.data["queue"]) == 2


def test_a_project_the_model_cannot_write_still_leaves_the_queue(tmp_path, monkeypatch):
    """The fix must not turn a real defect into an endless retry loop."""
    store = _store(tmp_path)
    _stub(monkeypatch, "other")
    first = pipeline.publish_slot(_config(), store, "manual")
    assert first["reason"] == "gemini_unavailable"
    assert len(store.data["queue"]) == 2
    second = pipeline.publish_slot(_config(), store, "manual")
    assert second["reason"] == "gemini_failed_twice"
    assert len(store.data["queue"]) == 1  # only the broken project is gone


def test_cli_exit_code_tells_a_spike_from_a_fault(monkeypatch):
    """Green run for a spike, red run for everything else."""
    from ghos_v6 import cli as cli_module

    monkeypatch.setattr(
        cli_module.Config, "from_env", classmethod(lambda cls: cli_module.Config(telegram_bot_token="x"))
    )
    monkeypatch.setattr(cli_module, "StateStore", lambda *a, **k: None)
    for reason, expected in (("gemini_capacity", 0), ("gemini_unavailable", 1), ("gemini_failed_twice", 1)):
        monkeypatch.setattr(
            cli_module,
            "publish_slot",
            lambda config, store, slot, _reason=reason: {"ok": False, "reason": _reason},
        )
        assert cli_module.main(["publish", "--slot", "manual"]) == expected, reason


def test_the_error_classifier_does_not_call_a_timeout_a_project_defect():
    import requests

    from ghos_v6.Gemini import classify_error

    exc = requests.exceptions.ReadTimeout(
        "HTTPSConnectionPool(host='generativelanguage.googleapis.com', port=443): "
        "Read timed out. (read timeout=90)"
    )
    assert classify_error(exc) == "network"
    assert classify_error(ConnectionError("network unreachable")) == "network"
    assert classify_error(ValueError("bad json")) == "other"


def test_transient_skip_reasons_cover_capacity_only():
    assert pipeline.TRANSIENT_SKIP_REASONS == {"gemini_capacity"}
    assert pipeline.CAPACITY_ERROR_CLASSES == {"capacity_503", "quota_429", "network"}


class _FakeApi:
    def __init__(self, found: bool = True) -> None:
        self.found = found

    def repo(self, full_name: str):
        if not self.found:
            return None
        return {"full_name": full_name, "id": 123456, "html_url": f"https://github.com/{full_name}"}

    def contributors_count(self, full_name: str) -> int:
        return 4

    def readme(self, full_name: str) -> str:
        return "A project. " * 60


def test_requeue_rebuilds_the_candidate_from_github(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.data["queue"] = [_candidate("other-project")]
    store.data["seen_repos"] = {"valentderah/totk-pc-recomp": {"full_name": "valentderah/totk-pc-recomp"}}
    store.data["publish_failures"] = {"valentderah/totk-pc-recomp": 2}
    store.data["dead_letters"] = [
        {"repo": "valentderah/totk-pc-recomp", "platform": "gemini", "error": "gemini_unavailable twice"},
        {"repo": "someone/else", "platform": "telegram", "error": "bad"},
    ]

    monkeypatch.setattr(pipeline, "GitHubApi", lambda *a, **k: _FakeApi())
    monkeypatch.setattr(
        pipeline,
        "map_repo",
        lambda raw, source: _candidate() | {"sources": [source]},
    )
    monkeypatch.setattr(pipeline, "enrich_candidate", lambda api, candidate, force=False: candidate)

    for bad in ("valentderah", "valentderah/", "../../etc/passwd", "a b/c d", ""):
        result = pipeline.requeue_command(_config(), store, bad)
        assert result["ok"] is False and result["reason"] == "invalid_repo", bad

    result = pipeline.requeue_command(_config(), store, "valentderah/totk-pc-recomp")
    assert result["ok"] is True and result["requeued"] is True
    keys = [store.repo_key(item) for item in store.data["queue"]]
    assert keys[0] == "valentderah/totk-pc-recomp"  # back at the front
    assert keys.count("valentderah/totk-pc-recomp") == 1  # and not duplicated
    assert "valentderah/totk-pc-recomp" not in (store.data.get("seen_repos") or {})
    assert store.data.get("publish_failures") == {}
    assert [item["repo"] for item in store.data["dead_letters"]] == ["someone/else"]
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "candidate_requeued" in events


def test_requeue_refuses_a_missing_or_unknown_repository(tmp_path, monkeypatch):
    store = _store(tmp_path)
    monkeypatch.setattr(pipeline, "GitHubApi", lambda *a, **k: _FakeApi(found=False))
    result = pipeline.requeue_command(_config(), store, "valentderah/gone-forever")
    assert result == {"ok": False, "requeued": False, "reason": "repo_not_found",
                      "repo": "valentderah/gone-forever"}
    assert len(store.data["queue"]) == 2  # nothing was invented


def test_the_requeue_command_is_reachable_from_the_cli_and_the_workflow():
    from pathlib import Path

    cli_source = Path("src/ghos_v6/cli.py").read_text(encoding="utf-8")
    assert 'requeue.add_argument("--repo", required=True)' in cli_source
    assert 'args.command == "requeue"' in cli_source
    workflow = Path(".github/workflows/pipeline.yml").read_text(encoding="utf-8")
    assert "          - requeue" in workflow
    assert "ghos-v6 requeue --repo" in workflow
    assert cli is not None


# --- 24.11: the outage must not spend the watchdog's recovery budget --------

def test_a_transient_slot_keeps_its_recovery_budget(tmp_path, monkeypatch):
    """23.11: the morning slot was abandoned at 18:00 after four 503s.

    Gemini was answering normally at 18:14 and the slot was already retired by
    the three-attempt ceiling, so the day ended one post short for no reason
    the channel could act on. While state carries a fresh transient mark the
    watchdog keeps trying (up to a wider, still bounded ceiling).
    """
    from datetime import datetime, timedelta, timezone

    from ghos_v6 import watchdog
    from ghos_v6.config import Config as RealConfig

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        watchdog,
        "_dispatch",
        lambda repo, inputs, token: (
            calls.append((inputs.get("command", ""), inputs.get("slot", ""))),
            (True, "HTTP 204"),
        )[1],
    )
    monkeypatch.setattr(watchdog, "_notify", lambda config, text: True)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    monkeypatch.setenv("GH_TOKEN", "")
    monkeypatch.setattr(watchdog, "MAX_DAILY_DISPATCHES", 24)

    # 15:30Z: the morning chair (15:13) is missed but still inside its
    # late-post window (24.11: +60 min), so the recovery budget is what the
    # test is about. At 19:00 the chair would already be retired.
    now = datetime(2026, 11, 23, 15, 30, tzinfo=timezone.utc)
    store = StateStore(tmp_path)
    marks = store.data.setdefault("last_runs", {}).setdefault("watchdog_dispatches", {})
    marks["2026-11-23:publish:us_morning"] = {
        "at": (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "dispatched": True, "verified": False, "attempts": 3,
    }
    store.data["transient_skips"] = {
        "publish:us_morning": {"at": (now - timedelta(minutes=20)).isoformat().replace("+00:00", "Z"),
                               "reason": "gemini_capacity"}
    }
    watchdog.watchdog_command(RealConfig(telegram_bot_token="x"), store, now=now)
    assert ("publish", "us_morning") in calls, "a provider outage must not retire the slot"

    # Without the mark the ceiling still holds: a genuinely broken slot stops.
    calls.clear()
    store2 = StateStore(tmp_path / "no-mark")
    marks2 = store2.data.setdefault("last_runs", {}).setdefault("watchdog_dispatches", {})
    marks2["2026-11-23:publish:us_morning"] = dict(marks["2026-11-23:publish:us_morning"])
    watchdog.watchdog_command(RealConfig(telegram_bot_token="x"), store2, now=now)
    assert ("publish", "us_morning") not in calls


def test_a_stale_transient_mark_does_not_widen_the_budget(tmp_path):
    from datetime import datetime, timedelta, timezone

    from ghos_v6 import watchdog

    now = datetime(2026, 11, 23, 23, 0, tzinfo=timezone.utc)
    store = StateStore(tmp_path)
    store.data["transient_skips"] = {
        "publish:us_morning": {"at": (now - timedelta(hours=9)).isoformat().replace("+00:00", "Z"),
                       "reason": "gemini_capacity"}
    }
    assert watchdog._transient_slot(store, "publish:us_morning", now) is False
    store.data["transient_skips"]["publish:us_morning"]["at"] = (now - timedelta(minutes=30)).isoformat()
    assert watchdog._transient_slot(store, "publish:us_morning", now) is True


def test_the_transient_ceiling_is_wider_but_still_finite():
    from ghos_v6 import watchdog

    assert watchdog.MAX_TRANSIENT_ATTEMPTS_PER_SLOT > watchdog.MAX_ATTEMPTS_PER_SLOT
    assert watchdog.MAX_TRANSIENT_ATTEMPTS_PER_SLOT <= watchdog.MAX_DAILY_DISPATCHES
    assert watchdog.TRANSIENT_MARK_HOURS <= 6.0


def test_a_finished_slot_forgets_its_transient_mark(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _stub(monkeypatch, "capacity_503")
    pipeline.publish_slot(_config(), store, "us_morning")
    # the marker key must be the watchdog's slot key, not the bare slot name
    assert (store.data.get("transient_skips") or {}).get("publish:us_morning")
    pipeline._finish_publish_slot(store, "us_morning", {"ok": True, "published": True, "reason": "published"})
    assert not (store.data.get("transient_skips") or {}).get("publish:us_morning")


def test_the_news_article_skip_marks_its_slot(tmp_path):
    store = StateStore(tmp_path)
    result = pipeline._transient_gemini_skip(
        store,
        pipeline.is_transient_error and RuntimeError("503 high demand"),
        event="news_article_skipped",
        reason=pipeline.ARTICLE_GEMINI_SKIP,
        slot="news-article",
    )
    assert result is not None and result["ok"] is True
    assert (store.data["transient_skips"]["news-article"]["reason"]
            == pipeline.ARTICLE_GEMINI_SKIP)
