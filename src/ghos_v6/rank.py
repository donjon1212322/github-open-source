"""Candidate scoring and selection.

The owner's rule (23.11): **a star count is not interest**. A repository
created three days ago that already has 600 stars is a stronger signal than a
two-year-old project sitting at 12 000, because the first one is what people
are looking at *right now*. An additive, capped formula ("30 points for
stars") cannot express that and, measured on the live queue, it collapsed:
27 candidates spanned 80.28..87.40 - every project looked the same.

So scoring here is a small, auditable model with three properties:

1. **Ten named signals**, each mapped to 0..1. Growth (stars per day, taken
   from real snapshots when we have them) carries the most weight; absolute
   popularity keeps only 8 of 100.
2. **Pool normalisation.** ``rank_candidates`` converts every signal into a
   percentile inside the current pool, so the score uses the whole 0..100
   range and "who is best today" is answered by comparison, not by caps.
   ``score_candidate`` alone (used by tests and single lookups) falls back to
   the raw 0..1 values.
3. **Nothing invented.** Every signal comes from the repository's own API
   data, our own star snapshots, or Hacker News points. No hidden weights,
   no model, and ``score_parts`` is stored with each candidate so the reason
   for a pick can be read back.

Diversity and audience nudges stay outside this score: they reorder
near-equal candidates at selection time (see the bottom of the file).
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from .audience import appeal_signals
from .gate import parse_dt

#: Weights in points out of 100. Growth first because that is the owner's
#: rule; popularity last-but-one because a famous repository is not news.
#:
#: 23.11 (second pass): three momentum signals instead of one -
#: ``growth`` (rate over the whole observed window), ``acceleration`` (is the
#: rate rising or flattening) and ``forks_rate`` (a fork means "I installed
#: it", which is a stronger act than a star). Together they are 42 of 100;
#: the absolute star count is 5.
WEIGHTS: dict[str, float] = {
    "growth": 24.0,        # stars per day, from real snapshots when available
    "acceleration": 12.0,  # last window vs the one before: take-off or plateau
    "forks_rate": 8.0,     # forks per day: people actually taking the project
    "burst": 6.0,          # a brand-new project taking off right now
    "freshness": 8.0,      # pushed_at: is anybody still working on it
    "content": 8.0,        # README, topics, homepage, media: can we cover it
    "community": 5.0,      # forks/stars reuse, contributors, open issues
    "external": 7.0,       # Hacker News points: outside validation
    "appeal": 7.0,         # audience appeal signals (product, not a building block)
    "popularity": 5.0,     # log10 stars - deliberately small
    "reliability": 5.0,    # licence, not archived, owner is not a farm
    "corroboration": 5.0,  # how many independent sources found it
}

#: An acceleration signal needs a window to compare against. With fewer than
#: this many daily snapshots (or a shorter span) we do not guess: the signal
#: is reported as unknown and scores the neutral 0.5 percentile.
#: Signals that may be unmeasurable for a given candidate. When the flag is 0
#: the percentile is a neutral 0.5 instead of a punishing 0.
UNKNOWN_FLAGS: dict[str, str] = {
    "acceleration": "acceleration_known",
    "forks_rate": "forks_measured",
}

ACCELERATION_MIN_POINTS = 3
ACCELERATION_MIN_SPAN_DAYS = 4.0

#: How many days of star history we keep per repository. Two snapshots are
#: enough for a rate; more would only bloat the committed state file.
STAR_HISTORY_POINTS = 8


def _num(candidate: dict[str, Any], *keys: str) -> float:
    for key in keys:
        try:
            if candidate.get(key) is not None:
                return float(candidate[key])
        except (TypeError, ValueError):
            continue
    return 0.0


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _age_days(candidate: dict[str, Any], now: datetime) -> float | None:
    created = parse_dt(candidate.get("created_at"))
    if created is None or created > now:
        return None
    return max(0.0, (now - created).total_seconds() / 86400.0)


def _snapshots(candidate: dict[str, Any]) -> list[tuple[datetime, float, float]]:
    """Our own daily observations: ``[(day, stars, forks), ...]`` sorted.

    ``star_history`` is written by ``StateStore.mark_seen``. Entries written
    before 23.11 carry only ``[day, stars]``; forks then fall back to a
    lifetime average, which is exactly the honest thing to do.
    """
    points: list[tuple[datetime, float, float]] = []
    for item in (candidate.get("star_history") or [])[-STAR_HISTORY_POINTS:]:
        try:
            stamp = str(item[0])
            stars = float(item[1])
            forks = float(item[2]) if len(item) > 2 else -1.0
        except (TypeError, ValueError, IndexError):
            continue
        parsed = parse_dt(f"{stamp}T00:00:00Z") if len(stamp) == 10 else parse_dt(stamp)
        if parsed is not None:
            points.append((parsed, stars, forks))
    points.sort(key=lambda pair: pair[0])
    return points


def observed_growth(candidate: dict[str, Any], stars: float) -> tuple[float, float]:
    """Stars per day from our own snapshots: ``(rate, days_covered)``.

    Without at least two points on different days we return ``(0.0, 0.0)``
    and the caller falls back to the age-based velocity - honest about what
    we do not know.
    """
    points = _snapshots(candidate)
    if len(points) < 2:
        return 0.0, 0.0
    (first_day, first_stars, _), (last_day, last_stars, _) = points[0], points[-1]
    days = (last_day - first_day).total_seconds() / 86400.0
    if days < 1.0:
        return 0.0, 0.0
    # The freshest number is the live API value, not the last snapshot.
    delta = max(stars, last_stars) - first_stars
    return max(0.0, delta / days), days


def observed_acceleration(candidate: dict[str, Any], stars: float) -> tuple[float, int]:
    """``(ratio, known)``: is the star rate rising, or flattening?

    The window is split in half: the daily rate of the newer half divided by
    the daily rate of the older half. 1.0 is "same speed", 3.0 is a take-off,
    0.2 is "the launch is over". Returns ``(0.0, 0)`` when there is not
    enough history - three snapshots over four days at least.
    """
    points = _snapshots(candidate)
    if len(points) < ACCELERATION_MIN_POINTS:
        return 0.0, 0
    first_day, last_day = points[0][0], points[-1][0]
    span = (last_day - first_day).total_seconds() / 86400.0
    if span < ACCELERATION_MIN_SPAN_DAYS:
        return 0.0, 0
    middle = points[0][0] + (points[-1][0] - points[0][0]) / 2
    older = [p for p in points if p[0] <= middle]
    newer = [p for p in points if p[0] >= middle]
    if len(older) < 2 or len(newer) < 2:
        return 0.0, 0
    old_days = max(1.0, (older[-1][0] - older[0][0]).total_seconds() / 86400.0)
    new_days = max(1.0, (last_day - newer[0][0]).total_seconds() / 86400.0)
    old_rate = max(0.0, older[-1][1] - older[0][1]) / old_days
    new_rate = max(0.0, max(stars, points[-1][1]) - newer[0][1]) / new_days
    if old_rate <= 0:
        # No history of growth at all: if it is growing now, that is a start,
        # not an acceleration. One is the neutral answer.
        return (2.0 if new_rate > 0 else 1.0), 1
    return round(new_rate / old_rate, 3), 1


def observed_forks_rate(candidate: dict[str, Any], age_days: float | None) -> tuple[float, int]:
    """``(forks per day, known)`` - measured when snapshots carry forks."""
    points = _snapshots(candidate)
    forks_now = _num(candidate, "forks", "forks_count")
    usable = [p for p in points if p[2] >= 0]
    if len(usable) >= 2:
        (first_day, _, first_forks), (last_day, _, last_forks) = usable[0], usable[-1]
        days = (last_day - first_day).total_seconds() / 86400.0
        if days >= 1.0:
            return max(0.0, max(forks_now, last_forks) - first_forks) / days, 1
    if age_days is not None and age_days > 0 and forks_now > 0:
        return forks_now / max(1.0, age_days), 1
    return 0.0, 0


def star_signals(
    candidate: dict[str, Any], *, now: datetime | None = None
) -> dict[str, float]:
    """The momentum signals, before any normalisation."""
    now = now or datetime.now(timezone.utc)
    stars = _num(candidate, "stars", "stargazers_count")
    age = _age_days(candidate, now)

    measured, covered = observed_growth(candidate, stars)
    if measured > 0:
        growth = measured
        source = "snapshots"
    elif age is not None:
        # Lifetime average. For a two-day-old repository this *is* the launch
        # rate; for an old one it is history, which is why the pool
        # normalisation below matters more than the number itself.
        growth = stars / max(1.0, age)
        source = "velocity"
    else:
        growth = 0.0
        source = "unknown"

    acceleration, acceleration_known = observed_acceleration(candidate, stars)
    forks_rate, forks_known = observed_forks_rate(candidate, age)

    # A young repository that is already popular is the strongest signal we
    # can see: it means hundreds of people decided within days, not years.
    burst = 0.0
    if age is not None and age <= 30:
        burst = _clamp(stars / max(1.0, age + 2.0) / 50.0)

    return {
        "stars": stars,
        "age_days": age if age is not None else -1.0,
        "growth": growth,
        "growth_covered_days": covered,
        "growth_source": 1.0 if source == "snapshots" else 0.0,
        "acceleration": acceleration,
        "acceleration_known": float(acceleration_known),
        "forks_rate": forks_rate,
        "forks_known": float(forks_known),
        "burst": burst,
    }


def score_components(
    candidate: dict[str, Any], *, now: datetime | None = None
) -> dict[str, float]:
    """Every signal as a raw 0..1 value, plus the stars behind them."""
    now = now or datetime.now(timezone.utc)
    star = star_signals(candidate, now=now)
    stars = star["stars"]
    forks = _num(candidate, "forks", "forks_count")
    contributors = _num(candidate, "contributors_count")
    hn_points = _num(candidate, "hn_points")
    sources = [str(s) for s in (candidate.get("sources") or [])]

    # Growth: log scale so 3 stars/day and 3000 stars/day are different but
    # neither one saturates the whole scale on its own.
    growth = _clamp(math.log1p(star["growth"]) / math.log1p(200.0))

    pushed = parse_dt(candidate.get("pushed_at") or candidate.get("updated_at"))
    pushed_days = max(0.0, (now - pushed).total_seconds() / 86400.0) if pushed else 180.0
    freshness = _clamp(1.0 - pushed_days / 90.0)

    readme_len = int(
        candidate.get("readme_len")
        or len(str(candidate.get("readme") or candidate.get("readme_excerpt") or ""))
    )
    content = (
        _clamp(readme_len / 6000.0)
        + _clamp(len(candidate.get("topics") or []) / 8.0)
        + (1.0 if candidate.get("homepage") else 0.4)
        + (1.0 if candidate.get("media_urls") else 0.5)
    ) / 4.0

    reuse = _clamp((forks / stars) * 10.0) if stars > 0 else 0.0
    community = (
        reuse
        + _clamp(contributors / 25.0)
        + (0.5 if _num(candidate, "open_issues", "open_issues_count") > 0 else 0.0)
    ) / 2.5

    external = _clamp(hn_points / 300.0)
    appeal = _clamp(len(appeal_signals(candidate)) / 3.0)
    popularity = _clamp(math.log10(stars + 1.0) / 5.0)

    reliability = 1.0
    if stars <= 0:
        reliability = 0.0
    # Repo farms: an owner with many young, similarly named repos is usually
    # farming the trending page rather than shipping software.
    same_owner = _num(candidate, "owner_repo_count")
    if same_owner >= 5:
        reliability -= min(0.7, (same_owner - 4) * 0.15)
    licence = candidate.get("license")
    has_licence = bool(str((licence or {}).get("spdx_id") or licence or "").strip()) if isinstance(
        licence, dict
    ) else bool(licence)
    if not has_licence:
        reliability -= 0.3
    reliability = _clamp(reliability)

    corroboration = _clamp(max(0, len(sources) - 1) / 2.0)

    # Acceleration: 1.0 is "same speed", 3.0 is a take-off. Log so that a
    # flattening project is not punished twice as hard as a take-off is
    # rewarded; unknown history scores a neutral 0.5 percentile below.
    acceleration = 0.0
    if star["acceleration_known"]:
        acceleration = _clamp(math.log(max(star["acceleration"], 0.2), 3) / 2.0 + 0.5)

    # Forks per day, log-scaled like growth: 0.05/day and 50/day are years
    # apart in reality but should not be on the same point of the scale.
    forks_rate = _clamp(math.log1p(star["forks_rate"]) / math.log1p(60.0))

    return {
        "growth": growth,
        "acceleration": acceleration,
        "forks_rate": forks_rate,
        "burst": star["burst"],
        "freshness": freshness,
        "content": content,
        "community": community,
        "external": external,
        "appeal": appeal,
        "popularity": popularity,
        "reliability": reliability,
        "corroboration": corroboration,
        "stars": stars,
        "forks": forks,
        "stars_per_day": star["growth"],
        "acceleration_ratio": star["acceleration"],
        "forks_per_day": star["forks_rate"],
        "age_days": star["age_days"],
        "growth_measured": star["growth_source"],
        "acceleration_known": star["acceleration_known"],
        "forks_measured": star["forks_known"],
    }


def _percentiles(candidates: list[dict[str, Any]], parts: list[dict[str, float]]) -> list[dict[str, float]]:
    """Turn each raw signal into a 0..1 percentile inside the pool.

    Ties share the middle of their range, so four clones with identical
    numbers cannot outrank each other by accident. A single-candidate pool
    maps every signal to 0.5: no comparison, no claim.
    """
    total = len(candidates)
    normalised: list[dict[str, float]] = [dict(part) for part in parts]
    if total <= 1:
        for row in normalised:
            for name in WEIGHTS:
                row[name] = 0.5
        return normalised
    for name in WEIGHTS:
        flag = UNKNOWN_FLAGS.get(name)
        if flag:
            # A signal we could not measure is not a zero: it is silence.
            # Percentile 0.5 keeps it out of the comparison instead of
            # punishing a repository for our own missing history.
            values = sorted(part[name] for part in parts if part.get(flag))
            population = max(1, len(values))
            for row, part in zip(normalised, parts):
                if not part.get(flag):
                    row[name] = 0.5
                    continue
                value = part[name]
                below = sum(1 for other in values if other < value)
                equal = sum(1 for other in values if other == value)
                row[name] = (below + (equal - 1) / 2.0) / max(1, population - 1)
            continue
        values = sorted(part[name] for part in parts)
        for row, part in zip(normalised, parts):
            value = part[name]
            below = sum(1 for other in values if other < value)
            equal = sum(1 for other in values if other == value)
            row[name] = (below + (equal - 1) / 2.0) / (total - 1)
    return normalised


def _weighted(parts: dict[str, float]) -> float:
    total = sum(WEIGHTS[name] * float(parts[name]) for name in WEIGHTS)
    return round(min(100.0, max(0.0, total)), 2)


def score_candidate(candidate: dict[str, Any], *, now: datetime | None = None) -> float:
    """Single-candidate score 0..100 without pool normalisation.

    Used where there is nothing to compare against (tests, one-off lookups,
    the fallback in ``rank_candidates``). Production ranking goes through
    ``rank_candidates`` so percentiles - and therefore the full 0..100
    range - are used.
    """
    parts = score_components(candidate, now=now)
    return _weighted({name: parts[name] for name in WEIGHTS})


def rank_candidates(
    candidates: list[dict[str, Any]],
    *,
    seen: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Score a pool, then sort. Star snapshots from ``state`` are attached first."""
    moment = now or datetime.now(timezone.utc)
    for candidate in candidates:
        if seen is not None:
            key = str(candidate.get("full_name") or "").strip().lower()
            record = seen.get(key) if key else None
            if isinstance(record, dict) and record.get("star_history"):
                candidate.setdefault("star_history", record["star_history"])
    parts = [score_components(candidate, now=moment) for candidate in candidates]
    normalised = _percentiles(candidates, parts)
    for candidate, raw, norm in zip(candidates, parts, normalised):
        candidate["score"] = _weighted({name: norm[name] for name in WEIGHTS})
        candidate["score_parts"] = {
            name: round(float(norm[name]), 3) for name in WEIGHTS
        }
        candidate["score_parts"]["stars_per_day"] = round(float(raw["stars_per_day"]), 2)
        candidate["score_parts"]["age_days"] = round(float(raw["age_days"]), 1)
        candidate["score_parts"]["growth_measured"] = int(raw["growth_measured"])
        # The raw value behind every soft signal, plus a flag saying whether it
        # was measured at all: after the fact the owner must be able to see why
        # a repository scored 72 and not 45.
        candidate["score_parts"]["acceleration_ratio"] = round(float(raw["acceleration_ratio"]), 2)
        candidate["score_parts"]["forks_per_day"] = round(float(raw["forks_per_day"]), 2)
        candidate["score_parts"]["acceleration_known"] = int(raw["acceleration_known"])
        candidate["score_parts"]["forks_measured"] = int(raw["forks_measured"])
    return sorted(
        candidates,
        key=lambda c: (float(c.get("score") or 0),
                       int(c.get("stars") or c.get("stargazers_count") or 0)),
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


#: A poll promise ("we will cover the winning job next week") is a
#: commitment, not a nudge: a matching candidate is taken even when it is not
#: the day's best-scoring one, as long as it clears this floor. Set low on
#: purpose - everything in the queue already passed the gate; the floor only
#: stops an obviously dead repository from being posted to keep a promise.
OBLIGATION_MIN_SCORE = 20.0
OBLIGATION_BOOST = 25.0


def obligation_terms(obligation: dict[str, Any] | None) -> list[str]:
    if not obligation:
        return []
    terms = [str(t).lower().strip() for t in (obligation.get("topics") or []) if str(t).strip()]
    terms += [str(t).lower().strip() for t in (obligation.get("keywords") or []) if str(t).strip()]
    seen: list[str] = []
    for term in terms:
        if term and term not in seen:
            seen.append(term)
    return seen


def obligation_matches(candidate: dict[str, Any], obligation: dict[str, Any] | None) -> bool:
    """Does this candidate keep the promise made by the closed poll?"""
    terms = obligation_terms(obligation)
    if not terms:
        return False
    topics = {str(t).lower() for t in (candidate.get("topics") or [])}
    words = set(
        str(candidate.get("name") or "").lower().replace("-", " ").replace("_", " ").split()
        + str(candidate.get("description") or "").lower().replace("-", " ").split()
        + str(candidate.get("full_name") or "").lower().replace("/", " ").replace("-", " ").split()
    )
    # Terms are curated by ``polls_design.label_keywords``: the generic words
    # ("app", "tool", "fast") are dropped there, so a single full match is a
    # real match - "editor" or "notes" is specific enough on its own.
    for term in terms:
        if term in topics:
            return True
        bits = term.replace("-", " ").split()
        if bits and all(bit in words for bit in bits):
            return True
    return False


def select_candidate(
    queue: list[dict[str, Any]],
    published_today: list[dict[str, Any]],
    model: dict[str, Any] | None = None,
    published_week: list[dict[str, Any]] | None = None,
    obligation: dict[str, Any] | None = None,
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
    def weight_of(candidate: dict[str, Any]) -> float:
        return (
            float(candidate.get("score") or 0)
            + diversity_bonus(candidate, published_today, published_week)
        ) * value_multiplier(candidate, model)

    if obligation and obligation.get("status") == "open":
        # The poll promised this topic. Prefer a matching candidate; the
        # diversity penalties below still decide *which* match, so the
        # promise cannot flood the week with one theme either.
        matching = [
            candidate for candidate in eligible
            if obligation_matches(candidate, obligation)
            and (
                obligation.get("kind") == "cover_theme"
                or float(candidate.get("score") or 0) >= OBLIGATION_MIN_SCORE
            )
        ]
        if matching:
            return max(matching, key=weight_of)
    return max(eligible, key=weight_of)
