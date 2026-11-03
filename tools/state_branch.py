#!/usr/bin/env python3
"""Keep the live state out of ``main`` (phase B/C).

``main`` holds code and an empty bootstrap projection. The real
``state/state.json`` / ``state/events.jsonl`` live on an orphan branch named
``state``. Workflows read that branch through the GitHub API and write it
with ONE atomic, compare-and-swap commit per run:

    GET  git/ref/heads/state      -> head commit sha (CAS token)
    GET  git/commits/{head}       -> base tree
    POST git/trees (base_tree)    -> tree with the two state files
    POST git/commits              -> commit with parent == head
    PATCH git/refs/heads/state    -> force=False, so a concurrent
                                     writer makes this fail and we retry

No ``fetch-depth: 0``, no ``git reset --hard``, no ``pull --rebase``, no
state commits in ``main``. A monthly ``squash`` replaces the branch with a
single parentless commit, so its history can never grow without bound.

Stdlib only: the ``commit-state`` job installs no project dependencies and
receives no publishing secret. Authentication is ``GH_TOKEN`` (the workflow
``github.token``) or ``GITHUB_TOKEN`` / ``GH_API_TOKEN``.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_BRANCH = "state"
FILES = ("state/state.json", "state/events.jsonl")
MAX_STATE_BYTES = 90 * 1024 * 1024  # GitHub refuses files >= 100 MiB
WARN_STATE_BYTES = 8 * 1024 * 1024
STALE_DAYS = 2
BOOTSTRAP = {
    "version": 1, "published": {}, "queue": [], "seen_repos": {},
    "dead_letters": [], "news_topics": [], "last_runs": {},
    "publish_failures": {}, "suppressed": {},
}


class ApiError(RuntimeError):
    """A non-retryable GitHub API response."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"GitHub API HTTP {code}: {message}")
        self.code = code


def _ctx() -> tuple[str, str, str]:
    token = (
        os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_API_TOKEN")
        or ""
    )
    repo = os.environ.get("GITHUB_REPOSITORY") or ""
    api = os.environ.get("GITHUB_API_URL") or "https://api.github.com"
    if not token:
        raise SystemExit("GH_TOKEN is not set")
    if "/" not in repo:
        raise SystemExit("GITHUB_REPOSITORY is not set")
    return token, repo, api.rstrip("/")


def _api(method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    token, repo, api = _ctx()
    url = f"{api}/repos/{repo}/{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "ghos-v6-state-branch",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                raw = response.read()
                return dict(json.loads(raw)) if raw else {}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:300]
            if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(min(2**attempt, 8))
                continue
            raise ApiError(exc.code, body) from None
        except urllib.error.URLError:
            if attempt < 3:
                time.sleep(min(2**attempt, 8))
                continue
            raise SystemExit("GitHub API is unreachable") from None
    raise AssertionError("unreachable")


def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


def _read_remote(path: str, ref: str = STATE_BRANCH) -> tuple[str, bytes] | None:
    """Return (blob sha, bytes) of a file on a branch, or None on 404."""
    try:
        meta = _api("GET", f"contents/{path}?ref={ref}")
    except ApiError as exc:
        if exc.code == 404:
            return None
        raise
    sha = str(meta.get("sha") or "")
    encoded = meta.get("content") or ""
    if encoded and meta.get("encoding") == "base64":
        return sha, base64.b64decode(encoded)
    # Contents API omits content for files >= 1 MiB; the blob API does not.
    blob = _api("GET", f"git/blobs/{sha}")
    return sha, base64.b64decode(blob.get("content") or "")


def _validate_state(local: bytes, remote: bytes | None) -> dict[str, Any]:
    if len(local) > MAX_STATE_BYTES:
        raise SystemExit(
            f"state.json is {len(local) / 1024 / 1024:.1f} MiB; "
            "GitHub refuses files >= 100 MiB. Archive before committing."
        )
    if len(local) > WARN_STATE_BYTES:
        print(f"::warning::state.json is {len(local) / 1024 / 1024:.1f} MiB; archive soon", file=sys.stderr)
    parsed = json.loads(local.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise SystemExit("invalid state: state.json must contain a JSON object")
    if not isinstance(parsed.get("published"), dict):
        raise SystemExit("invalid state: published is not an object")
    if not isinstance(parsed.get("queue"), list):
        raise SystemExit("invalid state: queue is not a list")
    if remote:
        previous = json.loads(remote.decode("utf-8"))
        before = len((previous or {}).get("published") or {})
        after = len(parsed["published"])
        if after < before:
            raise SystemExit(
                f"refusing to commit: published count decreased {before} -> {after}"
            )
    return parsed


def _commit_tree(files: dict[str, bytes], message: str, parents: list[str],
                 base_tree: str | None = None) -> str:
    """Create blobs + a tree + a commit; return the new commit sha."""
    entries = []
    for path, content in files.items():
        blob = _api("POST", "git/blobs", {"content": _b64(content), "encoding": "base64"})
        entries.append(
            {"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]}
        )
    tree_payload: dict[str, Any] = {"tree": entries}
    if base_tree:
        tree_payload["base_tree"] = base_tree
    tree = _api("POST", "git/trees", tree_payload)
    commit = _api(
        "POST", "git/commits",
        {"message": message, "tree": tree["sha"], "parents": parents},
    )
    return str(commit["sha"])


def _head(ref: str = STATE_BRANCH) -> str:
    data = _api("GET", f"git/ref/heads/{ref}")
    return str((data.get("object") or {}).get("sha") or "")


# --------------------------------------------------------------------------
# commands


def cmd_init(args: argparse.Namespace) -> int:
    """Create the orphan state branch from a directory of state files."""
    source = Path(args.source)
    files = _collect(source)
    try:
        _head()
    except ApiError as exc:
        if exc.code != 404:
            raise
    else:
        raise SystemExit(
            "state branch already exists; use 'squash' to collapse it, "
            "never 'init' (it would discard live state)"
        )
    commit = _commit_tree(files, "chore(state): initial orphan state branch [skip ci]", [])
    _api("POST", "git/refs", {"ref": f"refs/heads/{STATE_BRANCH}", "sha": commit})
    print(f"created orphan branch {STATE_BRANCH} at {commit[:8]} with {len(files)} files")
    return 0


def _collect(source: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in FILES:
        candidate = source / Path(path).name
        if candidate.is_file():
            files[path] = candidate.read_bytes()
    if "state/state.json" not in files:
        raise SystemExit(f"state.json is missing from {source}")
    return files


def cmd_read(args: argparse.Namespace) -> int:
    """Download the state branch into a local directory (bootstrap fallback)."""
    loaded: dict[str, bytes] = {}
    for path in FILES:
        item = _read_remote(path)
        if item is None and path == "state/state.json":
            print(f"::warning::{path} missing on {STATE_BRANCH}; keeping local bootstrap", file=sys.stderr)
            return 0
        # An empty event log is valid; never let a missing events file hide
        # the live state.json, or the bot would republish everything.
        loaded[path] = item[1] if item else b""
    state = _validate_state(loaded["state/state.json"], None)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for path, content in loaded.items():
        (out / Path(path).name).write_bytes(content)
    print(
        f"state loaded from {STATE_BRANCH}: published={len(state['published'])} "
        f"queue={len(state['queue'])} bytes={len(loaded['state/state.json'])}"
    )
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    """One atomic CAS commit per run. Missing artifact means 'no changes'."""
    source = Path(args.source)
    if not source.is_dir():
        print("No new state was produced; nothing to commit.")
        return 0
    files = _collect(source)
    for attempt in range(1, 6):
        remote = _read_remote("state/state.json")
        parsed = _validate_state(files["state/state.json"], remote[1] if remote else None)
        head = _head()
        base = _api("GET", f"git/commits/{head}")
        try:
            commit = _commit_tree(
                files,
                "chore(state): update publishing state [skip ci]",
                [head],
                base_tree=str((base.get("tree") or {}).get("sha") or ""),
            )
            _api("PATCH", f"git/refs/heads/{STATE_BRANCH}",
                 {"sha": commit, "force": False})
        except ApiError as exc:
            if exc.code in (409, 422) and attempt < 5:
                print(f"state branch moved (HTTP {exc.code}); retry {attempt}/5")
                time.sleep(min(attempt, 4))
                continue
            raise
        print(
            f"state ok: published={len(parsed['published'])} "
            f"queue={len(parsed['queue'])} branch={STATE_BRANCH} commit={commit[:8]}"
        )
        return 0
    raise SystemExit("failed to commit state after 5 attempts")


def cmd_squash(args: argparse.Namespace) -> int:
    """Collapse the state branch history into one parentless commit."""
    files: dict[str, bytes] = {}
    for path in FILES:
        item = _read_remote(path)
        files[path] = item[1] if item else b""
    _validate_state(files["state/state.json"], None)
    commit = _commit_tree(files, "chore(state): monthly squash [skip ci]", [])
    _api("PATCH", f"git/refs/heads/{STATE_BRANCH}", {"sha": commit, "force": True})
    print(f"state branch squashed to parentless commit {commit[:8]}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Machine-readable health of the state branch (heartbeat + guards)."""
    report: dict[str, Any] = {"branch": STATE_BRANCH}
    try:
        head = _head()
    except ApiError as exc:
        if exc.code == 404:
            report.update({"exists": False, "healthy": False, "error": "state branch missing"})
            print(json.dumps(report))
            return 1
        raise
    report["exists"] = True
    report["head"] = head[:12]
    commit = _api("GET", f"git/commits/{head}")
    raw_date = str((commit.get("committer") or {}).get("date") or "")
    try:
        when = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        report["last_commit_at"] = raw_date
        report["age_days"] = round(
            (datetime.now(timezone.utc) - when).total_seconds() / 86400, 2
        )
    except ValueError:
        report["age_days"] = -1
    live = _read_remote("state/state.json")
    if live:
        state = _validate_state(live[1], None)
        report["state_bytes"] = len(live[1])
        report["published"] = len(state["published"])
        report["queue"] = len(state["queue"])
    else:
        report["state_bytes"] = 0
        report["published"] = 0
    bootstrap = _read_remote("state/state.json", ref="main")
    report["main_published"] = (
        len((json.loads(bootstrap[1].decode("utf-8")) or {}).get("published") or {})
        if bootstrap else -1
    )
    problems = []
    if report.get("state_bytes", 0) > WARN_STATE_BYTES:
        problems.append(f"state.json is {report['state_bytes'] / 1024 / 1024:.1f} MiB")
    if report.get("age_days", 0) > STALE_DAYS:
        problems.append(f"state branch stale for {report['age_days']} days")
    if report["main_published"] not in (0, -1):
        problems.append(f"main still holds {report['main_published']} published records")
    report["problems"] = problems
    report["healthy"] = not problems
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["healthy"] else 1


def cmd_digest(args: argparse.Namespace) -> int:
    """Hash the local state files so a workflow can skip an unchanged upload."""
    source = Path(args.source)
    digest = hashlib.sha256()
    for path in FILES:
        candidate = source / Path(path).name
        digest.update(path.encode())
        digest.update(candidate.read_bytes() if candidate.is_file() else b"")
    print(digest.hexdigest())
    return 0


def cmd_check_bootstrap(args: argparse.Namespace) -> int:
    """CI guard: main must never accumulate state again."""
    source = Path(args.source)
    state = json.loads((source / "state.json").read_text(encoding="utf-8"))
    events = (source / "events.jsonl").read_bytes() if (source / "events.jsonl").exists() else b""
    bad = [
        key for key in ("published", "queue", "seen_repos", "dead_letters")
        if state.get(key) not in ({}, [])
    ]
    if bad or events.strip():
        raise SystemExit(
            f"main holds live state ({bad or ['events.jsonl']}); "
            "state belongs on the 'state' branch"
        )
    print("bootstrap ok: main state is empty")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func, default in (
        ("init", cmd_init, "state"), ("read", cmd_read, "state"),
        ("write", cmd_write, "state-new"), ("squash", cmd_squash, "state"),
        ("status", cmd_status, "state"), ("digest", cmd_digest, "state"),
        ("check-bootstrap", cmd_check_bootstrap, "state"),
    ):
        child = sub.add_parser(name)
        if name in ("read", "digest"):
            child.add_argument("--out" if name == "read" else "--source", default=default)
        elif name in ("init", "write", "check-bootstrap"):
            child.add_argument("--source", "--from", dest="source", default=default)
        child.set_defaults(func=func)
    args = parser.parse_args(argv)
    if not hasattr(args, "source"):
        args.source = "state"
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
