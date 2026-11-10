"""Cross-day memory of AI Brief STORIES (owner 10.11: "no more duplicates").

Before this module the Telegram brief had no inter-day story memory at all:
``video_id`` is rebuilt every day (``tg-<date>-<fingerprint>``), ``topic_key``
is a single coarse slug, and ``select_latest_news_entry`` deduplication only
ran on the retired YouTube path. A 48-hour discovery window therefore fed
yesterday's clusters straight back into today's brief (3579 -> 3581).

Two independent barriers:

* ``recent_cluster_keys`` is passed INTO discovery, so yesterday's channel
  clusters never occupy today's corpus slots;
* ``duplicate_story_ratio`` checks the GENERATED brief, so a reworded story
  that Gemini produced from a differently-keyed cluster cannot be published
  twice either.

Pure stdlib, no network. Bounded: keys older than MEMORY_DAYS are pruned and
the store never grows past MAX_KEYS.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

MEMORY_DAYS = 4
MAX_KEYS = 300
SIMILARITY_THRESHOLD = 0.65
MIN_SHARED_TOKENS = 2

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_WORDS = frozenset({
    "the", "and", "for", "with", "from", "that", "this", "into", "its",
    "are", "was", "were", "has", "have", "had", "not", "but", "all", "any",
    "new", "now", "out", "up", "more", "than", "then", "also", "via",
    "released", "release", "launches", "launched", "arrives", "arrive",
    "ships", "shipped", "opens", "opened", "introduces", "introduced",
    "announced", "announces", "updated", "update", "priced", "pricing",
    "lower", "higher", "faster", "slower", "leads", "lead", "based",
    "using", "used", "allows", "allow", "includes", "include", "brings",
    "delivers", "gets", "get", "makes", "make", "tools", "tool", "model",
    "models", "collection", "agent", "agents", "edition", "preview",
})


def story_tokens(title: Any, bullets: Iterable[Any] = ()) -> frozenset[str]:
    """Entity tokens of a story TITLE; rewording verbs must not change it.

    Bullets are intentionally ignored: two reports of the same launch quote
    different numbers, so including them destroys the overlap signal.
    """
    words = _TOKEN_RE.findall(str(title or "").lower())
    return frozenset(w for w in words if len(w) > 2 and w not in _STOP_WORDS)


def story_key(title: Any, bullets: Iterable[Any] = ()) -> str:
    return " ".join(sorted(story_tokens(title, bullets)))


def _similar(left: frozenset[str], right: frozenset[str]) -> bool:
    if not left or not right:
        return False
    shared = len(left & right)
    if shared < MIN_SHARED_TOKENS:
        return left == right
    return shared / min(len(left), len(right)) >= SIMILARITY_THRESHOLD


def _bounds(now: datetime | None, days: int) -> tuple[str, str]:
    moment = now or datetime.now(timezone.utc)
    today = moment.date()
    return today.isoformat(), (today - timedelta(days=days)).isoformat()


def _memory(store: Any) -> dict[str, dict[str, str]]:
    raw = store.data.get("brief_memory")
    if not isinstance(raw, dict):
        return {"clusters": {}, "stories": {}}
    return {
        "clusters": dict(raw.get("clusters") or {}),
        "stories": dict(raw.get("stories") or {}),
    }


def _fresh(mapping: dict[str, str], cutoff: str) -> dict[str, str]:
    return {k: v for k, v in mapping.items()
            if isinstance(k, str) and isinstance(v, str) and v >= cutoff}


def recent_cluster_keys(store: Any, *, now: datetime | None = None,
                        days: int = MEMORY_DAYS) -> set[str]:
    """Cluster keys that reached the channel in the last ``days`` days."""
    _today, cutoff = _bounds(now, days)
    memory = _memory(store)
    clusters = _fresh(memory["clusters"], cutoff)
    store.data["brief_memory"] = {
        "clusters": clusters, "stories": _fresh(memory["stories"], cutoff),
    }
    return set(clusters)


def duplicate_story_ratio(store: Any, stories: Iterable[Any], *,
                          now: datetime | None = None,
                          days: int = MEMORY_DAYS) -> float:
    """Fraction of generated stories already printed on a previous day."""
    today, cutoff = _bounds(now, days)
    memory = _memory(store)
    # Same-day keys are kept for recall but do not count as duplicates: news
    # --refresh edits today's post in place and must not be blocked.
    prior = [frozenset(k.split()) for k, day in _fresh(memory["stories"], cutoff).items()
             if day < today]
    items = [s for s in stories or [] if isinstance(s, dict)]
    if not items or not prior:
        return 0.0
    duplicates = 0
    for story in items:
        tokens = story_tokens(
            story.get("title"),
            story.get("what_happened") or story.get("bullets") or (),
        )
        if any(_similar(tokens, old) for old in prior):
            duplicates += 1
    return duplicates / len(items)


def remember_brief(store: Any, cluster_keys: Iterable[Any],
                   stories: Iterable[Any], *, now: datetime | None = None,
                   days: int = MEMORY_DAYS) -> int:
    """Record what actually reached the channel; prune old/bounded keys."""
    today, cutoff = _bounds(now, days)
    memory = _memory(store)
    clusters = _fresh(memory["clusters"], cutoff)
    printed = _fresh(memory["stories"], cutoff)
    for key in cluster_keys or []:
        key = str(key or "")
        if key:
            clusters[key] = today
    for story in stories or []:
        if not isinstance(story, dict):
            continue
        key = story_key(story.get("title"),
                        story.get("what_happened") or story.get("bullets") or ())
        if key:
            printed[key] = today
    for mapping in (clusters, printed):
        if len(mapping) > MAX_KEYS:
            for key in sorted(mapping, key=lambda k: (mapping[k], k))[:len(mapping) - MAX_KEYS]:
                del mapping[key]
    store.data["brief_memory"] = {"clusters": clusters, "stories": printed}
    return len(clusters) + len(printed)
