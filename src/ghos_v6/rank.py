"""Deterministic candidate ranking."""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .audience import appeal_signals
from .gate import parse_dt


def _num(candidate: dict[str, Any], *keys: str) -> float:
    for key in keys:
        try:
            if candidate.get(key) is not None:
                return float(candidate[key])
        except (TypeError, ValueError):
            continue
    return 0.0


def score_candidate(candidate: dict[str, Any], *, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    stars = _num(candidate, "stars", "stargazers_count")
    forks = _num(candidate, "forks", "forks_count")
    contributors = _num(candidate, "contributors_count")
    hn_points = _num(candidate, "hn_points")

    quality = min(30.0, 6.2 * math.log10(stars + 1) + min(5.0, math.log10(forks + 1) * 1.6))

    pushed = parse_dt(candidate.get("pushed_at") or candidate.get("updated_at"))
    pushed_days = max(0, (now - pushed).days) if pushed else 180
    freshness = max(0.0, 20.0 - pushed_days * 0.14)

    created = parse_dt(candidate.get("created_at"))
    age_days = max(1, (now - created).days) if created and created <= now else 365
    velocity = stars / age_days
    sources = candidate.get("sources") or []
    momentum = min(12.0, math.log1p(velocity) * 3.2) + min(5.0, max(0, len(sources) - 1) * 2.5)
    momentum += min(3.0, hn_points / 45.0)
    momentum = min(20.0, momentum)

    readme_len = int(
        candidate.get("readme_len")
        or len(str(candidate.get("readme") or candidate.get("readme_excerpt") or ""))
    )
    content = min(8.0, readme_len / 1800.0)
    if candidate.get("homepage"):
        content += 1.5
    if candidate.get("topics"):
        content += min(2.0, len(candidate.get("topics") or []) * 0.25)
    if candidate.get("media_urls"):
        content += 2.0
    if contributors >= 2:
        content += 1.5
    content = min(15.0, content)

    # Repo farms: an owner with many young, similarly named repos is
    # usually farming the trending page rather than shipping software.
    same_owner = int(candidate.get("owner_repo_count") or 0)
    if same_owner >= 5:
        momentum -= min(10.0, (same_owner - 4) * 2.0)

    # "Interesting project" bonus: something a reader can open and use
    # outranks another library with the same numbers. Capped so it nudges
    # ordering inside the queue instead of overriding the gate.
    appeal = min(10.0, len(appeal_signals(candidate)) * 3.5)

    base = quality + freshness + momentum + content + appeal
    # Reserve up to 15 points for diversity at selection time. Give a small
    # neutral baseline so single-candidate scores remain on a 0..100 scale.
    return round(min(100.0, base + 10.0), 3)


def rank_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for candidate in candidates:
        candidate["score"] = score_candidate(candidate)
    return sorted(
        candidates,
        key=lambda c: (float(c.get("score") or 0), int(c.get("stars") or c.get("stargazers_count") or 0)),
        reverse=True,
    )


def diversity_bonus(candidate: dict[str, Any], published_today: list[dict[str, Any]]) -> float:
    """Selection-time diversity adjustment in the reserved score band."""
    if not published_today:
        return 0.0
    languages = Counter(str(p.get("language") or "") for p in published_today)
    topics = Counter(t for p in published_today for t in (p.get("topics") or []))
    bonus = 0.0
    if languages.get(str(candidate.get("language") or ""), 0) == 0:
        bonus += 4.0
    elif languages.get(str(candidate.get("language") or ""), 0) >= 2:
        bonus -= 8.0
    overlap = sum(1 for t in candidate.get("topics") or [] if topics.get(t, 0) > 0)
    bonus -= min(5.0, overlap * 1.2)
    return bonus


def value_multiplier(candidate: dict[str, Any], model: dict[str, Any] | None) -> float:
    """Deterministic nudge from what the audience actually watched.

    The model is written by ``learn`` and is clamped to 0.85..1.15, so it can
    reorder near-equal candidates but never overrule the gate or the score.
    """
    if not model:
        return 1.0
    weight = 1.0
    language = str(candidate.get("language") or "").lower()
    languages = model.get("language_multipliers") or {}
    if language in languages:
        weight *= float(languages[language])
    topics = model.get("topic_multipliers") or {}
    hits = [float(topics[t]) for t in (str(t).lower() for t in candidate.get("topics") or []) if t in topics]
    if hits:
        weight *= sum(hits) / len(hits)
    return max(0.8, min(1.25, weight))


def select_candidate(
    queue: list[dict[str, Any]],
    published_today: list[dict[str, Any]],
    model: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not queue:
        return None
    dual_today = sum(1 for p in published_today if p.get("is_dual_use"))
    gems_today = sum(1 for p in published_today if p.get("is_hidden_gem"))
    eligible=[]
    for candidate in queue:
        if candidate.get("is_dual_use") and dual_today >= 1:
            continue
        if candidate.get("is_hidden_gem") and gems_today >= 1:
            continue
        eligible.append(candidate)
    if not eligible:
        # The day's quota for dual-use / hidden gems is used up. Return None
        # instead of falling back to the whole queue, which would break the
        # owner's "one per day" rule.
        return None
    return max(
        eligible,
        key=lambda c: (float(c.get("score") or 0) + diversity_bonus(c, published_today))
        * value_multiplier(c, model),
    )
