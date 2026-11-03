"""Append-only JSONL event log + deterministic JSON state.

The JSONL log is the audit trail. ``state.json`` is a deterministic,
human-reviewable projection committed by GitHub Actions. No binary
database is the only source of truth.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _parse_ts(value: Any) -> datetime | None:
    """Parse an ISO timestamp from state, tolerating the naive form."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return utcnow().isoformat().replace("+00:00", "Z")


DEFAULT_STATE: dict[str, Any] = {
    "version": 1,
    "published": {},
    "queue": [],
    "seen_repos": {},
    "dead_letters": [],
    "news_topics": [],
    "last_runs": {},
    "publish_failures": {},
    "suppressed": {},
}


class StateCorruptError(RuntimeError):
    """The committed projection is unreadable or has the wrong shape.

    Failing loudly is deliberate: silently resetting the state would make
    the bot republish projects it already published and would let the
    workflow commit the empty result.
    """


class StateStore:
    def __init__(self, state_dir: Path | str = Path("state")):
        self.state_dir = Path(state_dir)
        self.state_path = self.state_dir / "state.json"
        self.events_path = self.state_dir / "events.jsonl"
        self._rotate_events()
        self.data: dict[str, Any] = json.loads(json.dumps(DEFAULT_STATE))
        self.load()

    def validate(self, data: dict[str, Any] | None = None) -> None:
        """Raise StateCorruptError when the projection is not usable."""
        data = self.data if data is None else data
        if not isinstance(data, dict):
            raise StateCorruptError("state.json must contain a JSON object")
        for key in ("published", "seen_repos", "last_runs", "publish_failures", "suppressed"):
            if key in data and not isinstance(data.get(key), dict):
                raise StateCorruptError(f"state.{key} must be an object")
        for key in ("queue", "dead_letters", "news_topics"):
            if key in data and not isinstance(data.get(key), list):
                raise StateCorruptError(f"state.{key} must be a list")

    def load(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if self.state_path.exists():
            try:
                raw_text = self.state_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise StateCorruptError(f"cannot read {self.state_path}: {exc}") from exc
            try:
                raw = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise StateCorruptError(f"{self.state_path} is not valid JSON: {exc}") from exc
            if not isinstance(raw, dict):
                raise StateCorruptError(f"{self.state_path} must contain a JSON object")
            merged = json.loads(json.dumps(DEFAULT_STATE))
            merged.update(raw)
            self.validate(merged)
            self.data = merged
        if not self.events_path.exists():
            self.events_path.touch()

    def save(self) -> None:
        self.validate()
        self._compact_state()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, tmp_name = tempfile.mkstemp(dir=self.state_dir, prefix="state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp_name, self.state_path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)


    #: events.jsonl is committed to git by the workflow, so it must not grow
    #: without bound: a year of six crons at ~20 events each is millions of
    #: lines pulled down on every run. Keep the tail, roll the head once.
    EVENTS_MAX_LINES = 5000
    EVENTS_KEEP_LINES = 2000

    # Long-term retention. ``state.json`` is committed on every run, so an
    # ever-growing projection eventually hits GitHub's 100 MB per-file push
    # limit. Keep every anti-duplication key forever, but shed the heavy
    # generated payload and stale "seen" entries. v5 solved the same problem
    # with its monthly "Keep Last 10 Published Posts" workflow.
    SEEN_REPOS_TTL_DAYS = 180
    PUBLISHED_FULL_RETENTION_DAYS = 90
    HISTORY_MAX_ITEMS = 5
    LIST_MAX_ITEMS = 500

    #: Must survive compaction. ``dedup.check`` needs ids and fingerprints;
    #: metrics, ranking, digest and cleanup need the rest. The expensive
    #: ``generated`` / ``articles`` / ``media_urls`` blobs are deliberately
    #: absent - they are only read for fresh posts.
    _PUBLISHED_INDEX_FIELDS = (
        "repo_id", "id", "full_name", "name", "url", "description",
        "description_fp", "readme_fp", "published_at", "stars",
        "latest_release_tag", "score", "value_score", "views",
        "views_at_24h", "views_history", "language", "topics", "category",
        "slot", "telegram_message_id", "telegram_chat_id", "telegram_link",
        "telegraph_url", "image_url", "crossposts", "revisit_of",
        "is_dual_use", "is_hidden_gem",
    )

    def _compact_state(self) -> None:
        """Bound the projection without losing de-duplication keys.

        Idempotent: compacted records are marked, so a second save is a no-op.
        No ``published`` key is ever removed - the ``commit-state`` workflow
        refuses to commit a state whose published count decreased.
        """
        now = utcnow()
        changed = 0

        seen = self.data.get("seen_repos")
        if isinstance(seen, dict):
            cutoff = now - timedelta(days=self.SEEN_REPOS_TTL_DAYS)
            for key, record in list(seen.items()):
                stamp = _parse_ts(record.get("last_seen_at")) if isinstance(record, dict) else None
                if stamp is not None and stamp < cutoff:
                    del seen[key]
                    changed += 1

        published = self.data.get("published")
        if isinstance(published, dict):
            cutoff = now - timedelta(days=self.PUBLISHED_FULL_RETENTION_DAYS)
            for key, record in published.items():
                if not isinstance(record, dict):
                    continue
                # History is bounded for every record, not only for old ones:
                # a fresh project revisited several times would otherwise keep
                # an unbounded list until it crosses the retention threshold.
                history = list(record.get("history") or [])
                if len(history) > self.HISTORY_MAX_ITEMS:
                    record["history"] = history[-self.HISTORY_MAX_ITEMS:]
                    changed += 1
                if record.get("_compact"):
                    continue
                stamp = _parse_ts(record.get("published_at"))
                if stamp is None or stamp >= cutoff:
                    continue
                compact = {f: record[f] for f in self._PUBLISHED_INDEX_FIELDS if f in record}
                if record.get("history"):
                    compact["history"] = record["history"]
                compact["_compact"] = True
                published[key] = compact
                changed += 1

        for name in ("dead_letters", "news_topics", "poll_history"):
            items = self.data.get(name)
            if isinstance(items, list) and len(items) > self.LIST_MAX_ITEMS:
                del items[:-self.LIST_MAX_ITEMS]
                changed += 1

        if changed:
            self.event("state_compacted", changed=changed)

    def _rotate_events(self) -> None:
        try:
            if not self.events_path.exists():
                return
            with self.events_path.open("r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
            if len(lines) <= self.EVENTS_MAX_LINES:
                return
            tail = lines[-self.EVENTS_KEEP_LINES:]
            tmp = self.events_path.with_suffix(".jsonl.tmp")
            tmp.write_text("".join(tail), encoding="utf-8")
            tmp.replace(self.events_path)
        except OSError:
            logger.warning("events.jsonl rotation failed; keeping the file as is")

    def event(self, event_type: str, **payload: Any) -> None:
        record = {"ts": iso_now(), "type": event_type, **payload}
        with self.events_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    # -- published / dedup -------------------------------------------------
    @staticmethod
    def repo_key(candidate: dict[str, Any]) -> str:
        full_name = str(candidate.get("full_name") or "").strip().lower()
        if full_name:
            return full_name
        repo_id = candidate.get("repo_id") or candidate.get("id")
        return f"id:{repo_id}" if repo_id else str(candidate.get("url") or "").lower()

    def is_published(self, candidate: dict[str, Any]) -> bool:
        key = self.repo_key(candidate)
        published = self.data.get("published", {})
        if key in published:
            return True
        repo_id = candidate.get("repo_id") or candidate.get("id")
        if repo_id is None:
            return False
        # Published records are keyed by owner/name. A repo that was renamed
        # or transferred keeps its numeric id, so compare against the record.
        for record in published.values():
            other_id = record.get("repo_id") or record.get("id")
            if other_id is not None and str(other_id) == str(repo_id):
                return True
        return False

    def mark_seen(self, candidate: dict[str, Any], decision: str) -> None:
        key = self.repo_key(candidate)
        if not key:
            return
        seen = self.data.setdefault("seen_repos", {})
        prev = seen.get(key, {})
        seen[key] = {
            "full_name": candidate.get("full_name") or prev.get("full_name") or "",
            "repo_id": candidate.get("repo_id") or candidate.get("id") or prev.get("repo_id"),
            "first_seen_at": prev.get("first_seen_at") or iso_now(),
            "last_seen_at": iso_now(),
            "last_decision": decision,
            "sources": sorted(set(prev.get("sources") or []) | set(candidate.get("sources") or [])),
        }

    def set_queue(self, candidates: list[dict[str, Any]], *, merge: bool = True) -> None:
        """Replace or extend the pipeline.

        The queue is a pipeline, not a daily list: three posts a day decide
        much faster than a nightly collect refills it. Overwriting it every
        night threw away the part of yesterday's selection that had not been
        published yet. With ``merge`` the fresh results are prepended to
        whatever is still unpublished, and a candidate that appears again is
        refreshed rather than duplicated.
        """
        fresh = list(candidates or [])
        if not merge:
            self.data["queue"] = fresh
            return
        existing = list(self.data.get("queue") or [])
        fresh_keys = {self.repo_key(c).lower() for c in fresh}
        kept = [c for c in existing if self.repo_key(c).lower() not in fresh_keys]
        # fresh first (better data), then the survivors of previous runs
        merged = fresh + kept
        # keep the pipeline bounded: about a week and a half of slots
        self.data["queue"] = merged[:28]

    def pop_queue_candidate(self, key: str) -> None:
        self.data["queue"] = [c for c in self.data.get("queue", []) if self.repo_key(c) != key]

    def record_published(self, record: dict[str, Any]) -> str:
        key = self.repo_key(record)
        published = self.data.setdefault("published", {})
        previous = published.get(key)
        if previous:
            # A deliberate revisit must not erase the earlier publication.
            history = list(previous.get("history") or [])
            history.append(
                {
                    "published_at": previous.get("published_at"),
                    "telegram_message_id": previous.get("telegram_message_id"),
                    "telegraph_url": previous.get("telegraph_url"),
                    "score": previous.get("score"),
                }
            )
            record["history"] = history
        published[key] = record
        self.pop_queue_candidate(key)
        self.event(
            "project_published",
            repo=key,
            telegram_message_id=record.get("telegram_message_id"),
            telegraph_url=record.get("telegraph_url") or "",
            score=record.get("score"),
        )
        return key

    def published_records(self) -> list[dict[str, Any]]:
        return list(self.data.get("published", {}).values())

    def add_dead_letter(self, item: dict[str, Any]) -> None:
        self.data.setdefault("dead_letters", []).append(item)
        self.event("dead_letter", platform=item.get("platform"), repo=item.get("repo"))
