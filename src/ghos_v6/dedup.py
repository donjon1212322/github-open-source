"""Anti-duplication with deliberate re-visits.

Three layers, cheapest first:

1. ``repo_id``/``full_name`` — the same repository is never published twice
   inside the cooldown window.
2. Content fingerprints — a renamed fork or a copy-paste repost of the same
   README is caught even when the owner and name are new.
3. Milestone re-visits — after the cooldown, a project may come back *only*
   with a measurable event (stars grew by ≥50 %, a major release, a new
   owner). The card then says so; it is an update, not a repeat.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

COOLDOWN_DAYS = 90
CONTENT_SIMILARITY = 0.55
MILESTONE_STAR_GROWTH = 1.5  # 50 % more stars than at publication
_STOP = {
    "the", "and", "for", "with", "your", "from", "that", "this", "into",
    "you", "are", "its", "all", "any", "can", "out", "use", "using", "based",
}


def _norm_text(value: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (value or "").lower())

def description_fingerprint(description: str) -> str:
    """Shingle hash of the description: survives a rename, catches a copy."""
    words = [w for w in _norm_text(description).split() if w not in _STOP and len(w) > 2]
    if len(words) < 4:
        return ""
    tokens = sorted(_shingles(description))
    return hashlib.sha1("|".join(tokens).encode("utf-8")).hexdigest()[:16]


def _shingles(description: str) -> set[str]:
    """Word bigrams — one fixed size, otherwise two texts never intersect."""
    words = [w for w in _norm_text(description).split() if w not in _STOP and len(w) > 2]
    return set(" ".join(words[i : i + 2]) for i in range(max(0, len(words) - 1)))


def similarity(first: str, second: str) -> float:
    """Shingle containment (0..1): how much of the shorter text is reused.

    "Fast" → "blazing fast" must not hide a repost, so the check compares
    shingle sets and measures containment of the shorter description rather
    than exact equality. Jaccard is too harsh when one text adds a clause.
    """
    a, b = _shingles(first), _shingles(second)
    if len(a) < 3 or len(b) < 3:
        return 0.0
    return len(a & b) / min(len(a), len(b))


#: The queue stores only this much of a README, so the fingerprint must be
#: computed over the same prefix at save time and at check time. Hashing the
#: full README on save and the 1200-char excerpt on check produced two
#: different fingerprints and let renamed copies through.
README_FINGERPRINT_CHARS = 1200


def readme_for_fingerprint(candidate: dict[str, Any]) -> str:
    return str(candidate.get("readme_excerpt") or candidate.get("readme") or "")[
        :README_FINGERPRINT_CHARS
    ]


def readme_fingerprint(readme: str) -> str:
    """Hash of the README body without badges, images or headings' markup."""
    body = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", readme or "")
    body = re.sub(r"https?://\S+", " ", body)
    body = re.sub(r"[#>*_`|\-]{1,}", " ", body)
    words = [w for w in _norm_text(body).split() if len(w) > 3][:400]
    if len(words) < 40:
        return ""
    return hashlib.sha1(" ".join(words).encode("utf-8")).hexdigest()[:16]


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def milestone(
    record: dict[str, Any],
    candidate: dict[str, Any],
    *,
    now: datetime | None = None,
    cooldown_days: int = COOLDOWN_DAYS,
) -> str:
    """Name of the milestone that justifies a re-visit, or ``""``."""
    now = now or datetime.now(timezone.utc)
    published_at = _parse(record.get("published_at"))
    if published_at is None:
        return ""
    if now - published_at < timedelta(days=cooldown_days):
        return ""
    try:
        old = float(record.get("stars") or 0)
        new = float(candidate.get("stars") or candidate.get("stargazers_count") or 0)
    except (TypeError, ValueError):
        return ""
    if old > 0 and new >= old * MILESTONE_STAR_GROWTH:
        return f"stars +{int((new / old - 1) * 100)}%"
    latest = str(candidate.get("latest_release_tag") or "")
    if latest and latest != str(record.get("latest_release_tag") or "") and re.match(r"^v?\d+\.0(\.0)?$", latest):
        return f"release {latest}"
    return ""


def check(
    candidate: dict[str, Any],
    store_data: dict[str, Any],
    *,
    now: datetime | None = None,
    cooldown_days: int = COOLDOWN_DAYS,
) -> dict[str, Any]:
    """``{"verdict": "new"|"duplicate"|"revisit", "reason": str, "key": str}``."""
    from .state import StateStore

    key = StateStore.repo_key(candidate)
    published = store_data.get("published") or {}
    description = str(candidate.get("description") or "")
    desc_fp = description_fingerprint(description)
    readme_fp = readme_fingerprint(readme_for_fingerprint(candidate))

    candidate_id = candidate.get("repo_id") or candidate.get("id")
    for other_key, record in published.items():
        other_id = record.get("repo_id") or record.get("id")
        same_repo = other_key == key or (
            candidate_id is not None
            and other_id is not None
            and str(other_id) == str(candidate_id)
        )
        # Old records may not carry a fingerprint. Fall back to the stored
        # description so a renamed/transferred repo is still caught.
        record_desc_fp = record.get("description_fp") or description_fingerprint(
            str(record.get("description") or "")
        )
        same_content = bool(
            (desc_fp and desc_fp == record_desc_fp)
            or (readme_fp and readme_fp == record.get("readme_fp"))
            or similarity(description, str(record.get("description") or "")) >= CONTENT_SIMILARITY
        )
        if not (same_repo or same_content):
            continue
        why = milestone(record, candidate, now=now, cooldown_days=cooldown_days)
        if why and same_repo:
            return {"verdict": "revisit", "reason": why, "key": key}
        if same_content and not same_repo:
            return {"verdict": "duplicate", "reason": "same_description_or_readme", "key": key}
        return {"verdict": "duplicate", "reason": "already_published", "key": key}

    seen = store_data.get("seen_repos") or {}
    hit = seen.get(key) or {}
    last_decision = str(hit.get("last_decision") or "")
    if last_decision.startswith("rejected:"):
        return {"verdict": "new", "reason": f"previously_{last_decision}", "key": key}
    return {"verdict": "new", "reason": "first_time", "key": key}
