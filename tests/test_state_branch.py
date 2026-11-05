"""Phase B/C contracts: live state lives on an orphan ``state`` branch.

The owner asked for a real production deployment, not a roadmap note. These
tests pin the invariants that keep ``main`` small: one atomic CAS commit per
run, no full-history checkout, no loss of published records, monthly squash
and a heartbeat that notices a stale state branch.
"""
from __future__ import annotations

import base64
import importlib.util
import json
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("state_branch", REPO / "tools" / "state_branch.py")
assert _spec and _spec.loader
sb = importlib.util.module_from_spec(_spec)
sys.modules["state_branch"] = sb
_spec.loader.exec_module(sb)

HEAD = "a" * 40
TREE = "b" * 40


def _state(published: int = 1, queue: int = 2) -> bytes:
    return json.dumps({
        "version": 1,
        "published": {f"owner/repo-{i}": {"name": f"repo-{i}"} for i in range(published)},
        "queue": [{"name": f"q-{i}"} for i in range(queue)],
        "seen_repos": {}, "dead_letters": [], "news_topics": [],
        "last_runs": {}, "publish_failures": {}, "suppressed": {},
    }).encode("utf-8")


_USE_DEFAULT = object()


class FakeAPI:
    def __init__(self, *, state_bytes: bytes | None | object = _USE_DEFAULT,
                 events_bytes: bytes | None = b"event\n",
                 main_state: bytes | None | object = _USE_DEFAULT,
                 conflict_once: bool = False,
                 date: str | None = None) -> None:
        self.state_bytes = _state() if state_bytes is _USE_DEFAULT else state_bytes
        self.events_bytes = events_bytes
        self.main_state = (
            json.dumps(sb.BOOTSTRAP).encode() if main_state is _USE_DEFAULT else main_state
        )
        self.conflict_once = conflict_once
        self.date = date or (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat().replace("+00:00", "Z")
        self.calls: list[tuple[str, str, dict | None]] = []
        self._ref_conflicts = 0

    def __call__(self, method: str, path: str, payload: dict | None = None) -> dict:
        self.calls.append((method, path, payload))
        if method == "GET" and path.startswith("contents/"):
            ref = "state" if "ref=state" in path else "main"
            if "events.jsonl" in path:
                content = self.events_bytes
            else:
                content = self.state_bytes if ref == "state" else self.main_state
            if content is None:
                raise sb.ApiError(404, "not found")
            return {
                "sha": f"blob-{ref}", "encoding": "base64",
                "content": base64.b64encode(content).decode(), "size": len(content),
            }
        if method == "GET" and path.startswith("git/blobs/"):
            return {"content": ""}
        if method == "GET" and path.startswith("git/ref/heads/"):
            return {"object": {"sha": HEAD}}
        if method == "GET" and path.startswith("git/commits/"):
            return {"tree": {"sha": TREE}, "committer": {"date": self.date}}
        if method == "POST" and path == "git/blobs":
            return {"sha": "blob-new"}
        if method == "POST" and path == "git/trees":
            return {"sha": "tree-new"}
        if method == "POST" and path == "git/commits":
            return {"sha": "commit-new"}
        if method == "POST" and path == "git/refs":
            return {"ref": "refs/heads/state"}
        if method == "PATCH" and path.startswith("git/refs/heads/"):
            if self.conflict_once and self._ref_conflicts == 0:
                self._ref_conflicts += 1
                raise sb.ApiError(422, "update is not a fast forward")
            return {"object": {"sha": "commit-new"}}
        raise AssertionError(f"unexpected API call: {method} {path}")


def _install(monkeypatch, fake: FakeAPI) -> None:
    monkeypatch.setattr(sb, "_api", fake)
    monkeypatch.setattr(sb.time, "sleep", lambda *_: None)


def test_read_loads_live_state_and_events_from_the_state_branch(monkeypatch, tmp_path):
    fake = FakeAPI()
    _install(monkeypatch, fake)
    out = tmp_path / "state"
    assert sb.main(["read", "--out", str(out)]) == 0
    assert json.loads((out / "state.json").read_text())["published"]
    assert (out / "events.jsonl").read_bytes() == b"event\n"
    contents = [path for method, path, _ in fake.calls if method == "GET" and path.startswith("contents/")]
    assert len(contents) == 2
    assert all("ref=state" in path for path in contents), "state must never be read from main"


def test_read_keeps_bootstrap_when_the_branch_is_missing(monkeypatch, tmp_path):
    _install(monkeypatch, FakeAPI(state_bytes=None))
    out = tmp_path / "state"
    out.mkdir()
    (out / "state.json").write_text("keep")
    assert sb.main(["read", "--out", str(out)]) == 0
    assert (out / "state.json").read_text() == "keep"


def test_read_tolerates_an_empty_event_log(monkeypatch, tmp_path):
    _install(monkeypatch, FakeAPI(events_bytes=None))
    out = tmp_path / "state"
    assert sb.main(["read", "--out", str(out)]) == 0
    assert json.loads((out / "state.json").read_text())["published"]
    assert (out / "events.jsonl").read_bytes() == b""


def test_write_is_one_atomic_cas_commit(monkeypatch, tmp_path):
    fake = FakeAPI()
    _install(monkeypatch, fake)
    src = tmp_path / "state-new"
    src.mkdir()
    (src / "state.json").write_bytes(_state(published=2))
    (src / "events.jsonl").write_bytes(b"event\n")
    assert sb.main(["write", "--from", str(src)]) == 0
    commits = [p for m, path, p in fake.calls if m == "POST" and path == "git/commits"]
    assert len(commits) == 1, "one commit per run, not one per file"
    assert commits[0]["parents"] == [HEAD]
    assert commits[0]["tree"] == "tree-new"
    patches = [p for m, path, p in fake.calls if m == "PATCH" and path.startswith("git/refs/heads/")]
    assert len(patches) == 1
    assert patches[0] == {"sha": "commit-new", "force": False}
    ref_paths = [path for m, path, _ in fake.calls if m == "PATCH"]
    assert all("heads/state" in path for path in ref_paths)


def test_write_retries_when_another_run_moved_the_ref(monkeypatch, tmp_path):
    fake = FakeAPI(conflict_once=True)
    _install(monkeypatch, fake)
    src = tmp_path / "state-new"
    src.mkdir()
    (src / "state.json").write_bytes(_state())
    assert sb.main(["write", "--from", str(src)]) == 0
    commits = [1 for m, path, _ in fake.calls if m == "POST" and path == "git/commits"]
    assert len(commits) == 2, "a lost CAS race must be retried, not lost"


def test_write_refuses_to_lose_published_records(monkeypatch, tmp_path):
    _install(monkeypatch, FakeAPI(state_bytes=_state(published=4)))
    src = tmp_path / "state-new"
    src.mkdir()
    (src / "state.json").write_bytes(_state(published=1))
    with pytest.raises(SystemExit, match="published count decreased"):
        sb.main(["write", "--from", str(src)])


def test_write_refuses_a_state_file_github_would_reject(monkeypatch, tmp_path):
    _install(monkeypatch, FakeAPI())
    monkeypatch.setattr(sb, "MAX_STATE_BYTES", 200)
    src = tmp_path / "state-new"
    src.mkdir()
    (src / "state.json").write_bytes(_state(published=3))
    with pytest.raises(SystemExit, match="GitHub refuses"):
        sb.main(["write", "--from", str(src)])


def test_write_with_no_artifact_is_a_no_op(tmp_path):
    assert sb.main(["write", "--from", str(tmp_path / "missing")]) == 0


def test_squash_makes_a_parentless_commit_and_force_updates_the_ref(monkeypatch):
    fake = FakeAPI()
    _install(monkeypatch, fake)
    assert sb.main(["squash"]) == 0
    commits = [p for m, path, p in fake.calls if m == "POST" and path == "git/commits"]
    assert commits and commits[-1]["parents"] == []
    patches = [p for m, path, p in fake.calls if m == "PATCH" and path.startswith("git/refs/heads/")]
    assert patches[-1]["force"] is True


def test_init_refuses_to_overwrite_an_existing_state_branch(monkeypatch, tmp_path):
    _install(monkeypatch, FakeAPI())
    src = tmp_path / "state"
    src.mkdir()
    (src / "state.json").write_bytes(_state())
    with pytest.raises(SystemExit, match="already exists"):
        sb.main(["init", "--from", str(src)])


def test_status_flags_a_stale_branch_and_state_left_in_main(monkeypatch, capsys):
    main = dict(sb.BOOTSTRAP)
    main["published"] = {"owner/repo": {}}
    _install(monkeypatch, FakeAPI(date="2026-10-01T00:00:00Z",
                                  main_state=json.dumps(main).encode()))
    assert sb.main(["status"]) == 1
    report = json.loads(capsys.readouterr().out.strip())
    assert report["healthy"] is False
    assert any("stale" in p for p in report["problems"])
    assert any("main still holds" in p for p in report["problems"])


def test_status_is_healthy_on_a_fresh_bootstrap(monkeypatch, capsys):
    _install(monkeypatch, FakeAPI())
    assert sb.main(["status"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["healthy"] is True


def test_digest_changes_when_state_changes(tmp_path):
    src = tmp_path / "state"
    src.mkdir()
    (src / "state.json").write_bytes(_state(published=1))
    (src / "events.jsonl").write_bytes(b"a\n")
    first = sb.cmd_digest(type("Args", (), {"source": str(src)}))
    (src / "events.jsonl").write_bytes(b"b\n")
    second = sb.cmd_digest(type("Args", (), {"source": str(src)}))
    assert first == second == 0


def test_check_bootstrap_rejects_live_state_in_main(tmp_path):
    src = tmp_path / "state"
    src.mkdir()
    (src / "state.json").write_bytes(_state())
    (src / "events.jsonl").write_bytes(b"")
    with pytest.raises(SystemExit, match="state belongs"):
        sb.main(["check-bootstrap", "--from", str(src)])


def test_check_bootstrap_accepts_the_committed_bootstrap():
    assert sb.main(["check-bootstrap", "--from", str(REPO / "state")]) == 0


# ---------------------------------------------------------------------------
# Workflow contracts: the promises above must be wired into Actions.

FLOW = REPO / ".github" / "workflows"


def test_pipeline_uses_the_state_branch_and_no_full_history_clone():
    text = (FLOW / "pipeline.yml").read_text(encoding="utf-8")
    assert "tools/state_branch.py read" in text
    assert "tools/state_branch.py write" in text
    assert "fetch-depth: 0" not in text
    assert "git reset --hard FETCH_HEAD" not in text
    assert "contents: write" in text
    assert "state-writer" in text


def test_lifecycle_squashes_and_heartbeats_the_state_branch():
    text = (FLOW / "lifecycle.yml").read_text(encoding="utf-8")
    assert "tools/state_branch.py squash" in text
    assert "tools/state_branch.py status" in text
    assert "group: state-writer" in text


def test_ci_guards_against_state_returning_to_main():
    text = (FLOW / "ci.yml").read_text(encoding="utf-8")
    assert "tools/state_branch.py check-bootstrap" in text


def test_doctor_checks_state_branch_health():
    text = (FLOW / "doctor.yml").read_text(encoding="utf-8")
    assert "tools/state_branch.py status" in text
