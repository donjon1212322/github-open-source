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


#: Week-level limits, measured on the live channel on 23.11: 17 of the last
#: 22 posts were DevTools, 15 of 22 were about AI agents and Rust was 46 % of
#: the languages, because the day-scoped bonus below only ever compared a
#: candidate with *today's* posts - the first slot of every day had no
#: diversity pressure at all. These constants turn the same idea into a
#: seven-day view. They are penalties, not hard quotas: a thin queue must
#: still be able to publish, so nothing here can empty the queue or block a
#: slot. The largest one (-12) is smaller than the usual distance between a
#: great candidate and a mediocre one, so a genuinely better project still
#: wins - it just stops the third post about the same theme in a row.
WEEK_LANGUAGE_LIMIT = 3          # same language this many times -> we look further
WEEK_LANGUAGE_PENALTY = 5.0
WEEK_TOPIC_OVERLAP_PENALTY = 6.0
WEEK_OWNER_PENALTY = 12.0        # one post per owner per week is plenty
WEEK_FAMILY_LIMIT = 5            # five posts of one theme already flooded the week
WEEK_FAMILY_PENALTY = 8.0

#: Published records carry a Gemini ``category`` (DevTools / AI / Security),
#: but a *queued* candidate has not been generated yet - so a category-based
#: quota would never fire at selection time. These families are derived from
#: the fields both shapes always have (topics, name, description) and are
#: ordered: an "AI agent for iOS" is an AI-agent post first.
FAMILY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ai-agents", ("agent", "agentic", "claude-code", "codex", "mcp", "llm",
                   "openai", "anthropic", "gemini", "prompt", "chatbot")),
    ("privacy", ("privacy", "security", "pentest", "audit", "permission",
                 "malware", "vulnerability", "scanner", "exploit")),
    ("media", ("video", "clip", "film", "stream", "media", "music", "audio",
               "podcast", "image", "photo", "tv")),
    ("games", ("game", "gaming", "emulator", "switch", "diablo", "retro",
               "minecraft", "steam")),
    ("apple", ("ios", "iphone", "ipad", "macos", "apple", "swift", "xcode")),
    ("devtools", ("ide", "editor", "cli", "devtools", "developer-tools",
                  "terminal", "debug", "compiler", "build", "linter", "docker",
                  "kubernetes", "git")),
)


def theme_family(record: dict[str, Any]) -> str:
    """Coarse theme of a candidate or a published record, or ``""``."""
    parts = [
        str(record.get("name") or ""),
        str(record.get("description") or ""),
        " ".join(str(t) for t in (record.get("topics") or [])),
    ]
    if record.get("category"):
        parts.append(str(record.get("category")))
    haystack = " ".join(parts).lower()
    if not haystack.strip():
        return ""
    for family, keywords in FAMILY_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return family
    return ""


def _owner(record: dict[str, Any]) -> str:
    full_name = str(record.get("full_name") or "")
    if "/" in full_name:
        return full_name.split("/")[0].lower()
    return ""


def week_diversity_bonus(candidate: dict[str, Any], published_week: list[dict[str, Any]]) -> float:
    """Seven-day view of the same idea: no theme, language or owner loop."""
    if not published_week:
        return 0.0
    languages = Counter(str(p.get("language") or "") for p in published_week)
    families = Counter(family for family in (theme_family(p) for p in published_week) if family)
    topics = Counter(t for p in published_week for t in (p.get("topics") or []))
    owners = Counter(owner for owner in (_owner(p) for p in published_week) if owner)

    bonus = 0.0
    language = str(candidate.get("language") or "")
    if languages.get(language, 0) >= WEEK_LANGUAGE_LIMIT:
        bonus -= WEEK_LANGUAGE_PENALTY
    family = theme_family(candidate)
    if family and families.get(family, 0) >= WEEK_FAMILY_LIMIT:
        bonus -= WEEK_FAMILY_PENALTY
    overlap = sum(1 for t in candidate.get("topics") or [] if topics.get(t, 0) > 0)
    if overlap >= 2:
        bonus -= min(WEEK_TOPIC_OVERLAP_PENALTY, (overlap - 1) * 2.0)
    owner = _owner(candidate)
    if owner and owners.get(owner, 0) >= 1:
        bonus -= WEEK_OWNER_PENALTY
    return bonus


def diversity_bonus(
    candidate: dict[str, Any],
    published_today: list[dict[str, Any]],
    published_week: list[dict[str, Any]] | None = None,
) -> float:
    """Selection-time diversity adjustment in the reserved score band.

    ``published_today`` keeps the original day-scoped behaviour (that is what
    the daily limits were tuned on). ``published_week`` adds the seven-day
    view; when it is omitted the function behaves exactly as before.
    """
    if not published_today:
        return week_diversity_bonus(candidate, published_week or [])
    languages = Counter(str(p.get("language") or "") for p in published_today)
    topics = Counter(t for p in published_today for t in (p.get("topics") or []))
    bonus = 0.0
    if languages.get(str(candidate.get("language") or ""), 0) == 0:
        bonus += 4.0
    elif languages.get(str(candidate.get("language") or ""), 0) >= 2:
        bonus -= 8.0
    overlap = sum(1 for t in candidate.get("topics") or [] if topics.get(t, 0) > 0)
    bonus -= min(5.0, overlap * 1.2)
    return bonus + week_diversity_bonus(candidate, published_week or [])


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
    published_week: list[dict[str, Any]] | None = None,
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
        key=lambda c: (
            float(c.get("score") or 0)
            + diversity_bonus(c, published_today, published_week)
        )
        * value_multiplier(c, model),
    )
