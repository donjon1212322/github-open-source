"""Adaptive weekly polls: a different, data-driven question every Sunday.

Owner 08.11, three explicit corrections:
1. "Project of the Week" asked people to re-elect a project they had already
   seen - a vote with no consequence except a duplicate post.
2. Simply swapping in repo names from the queue does not help: readers have
   no idea what ``Concat`` or ``m3e-canvas`` does, and a Telegram poll option
   is capped at 100 characters, so a real description does not fit.
3. Polls must not repeat; they must be shaped by what the audience liked.

So an option is a JOB / outcome / friction, never a repo name. The chosen
job maps internally to an unpublished candidate in ``queue``; the result is a
NEW post, not a re-run. Type selection is adaptive (uncertainty, past vote
turnout, measured view shifts), the profile feeds ``rank.value_multiplier``,
and Gemini only rephrases - a deterministic fallback survives a 503.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .state import StateStore, iso_now, iso_week_key

logger = logging.getLogger(__name__)

MIN_SIGNAL_VOTES = 8          # below this a poll is archive only, not a signal
EMA_ALPHA = 0.35
MAX_OPTION = 100             # Telegram Bot API hard cap
MAX_QUESTION = 300
RECENT_TYPE_WINDOW = 3       # never reuse a type from the last 3 weeks

# Static types still vary by week through the adaptive selector and Gemini
# rephrasing. Their option keys are stable so votes accumulate on one axis.
STATIC_OPTIONS: dict[str, list[tuple[str, str]]] = {
    "format": [
        ("deep_dive", "One project, explained properly"),
        ("quick_picks", "Five projects, one line each"),
        ("comparison", "Two similar tools, side by side"),
        ("model_news", "Short model and API news brief"),
    ],
    "depth": [
        ("runnable", "A runnable example I can copy"),
        ("benchmarks", "Benchmarks against the alternatives"),
        ("selfhost", "License, self-hosting and privacy notes"),
        ("setup", "A five-minute setup walkthrough"),
    ],
    "friction": [
        ("no_docker", "There is no Docker image"),
        ("license", "The license is unclear"),
        ("paid_api", "It needs a paid API key"),
        ("deps", "Too many dependencies to install"),
    ],
}

QUESTIONS: dict[str, str] = {
    "job": ("Which of these would save you time this week? We will cover the "
            "winning job with a real open-source tool next week."),
    "direction": "Which kind of tool should we dig into next week?",
    "format": "Which of our post formats is easiest for you to read?",
    "depth": "What is missing from our write-ups?",
    "friction": "What most often stops you from trying a new tool?",
    "reality_check": "We noticed a shift in what got read last week. Coincidence?",
}

DECIDES: dict[str, str] = {
    "job": "which job we cover next week",
    "direction": "which kind of tools we look for",
    "format": "how we format the posts",
    "depth": "what we add to every write-up",
    "friction": "which tools we filter out",
    "reality_check": "whether we change the mix",
}

BANNED_WORDS = ("github", "stars", "repo", "fork", "trending", "open source daily")
GENERIC_CLUSTERS = {"open-source", "developer-tools", "llm", "ai", "mcp"}


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def cluster_key(candidate: dict[str, Any]) -> str:
    """A coarse bucket so four options are not four identical AI agents."""
    topics = [str(t).strip().lower() for t in (candidate.get("topics") or []) if t]
    for topic in topics:
        if topic not in GENERIC_CLUSTERS:
            return topic
    return str(candidate.get("category") or candidate.get("language") or "misc").strip().lower()


# Coarse domains. Distinct GitHub topics are NOT distinct subject matter:
# claude-code / codex / agent-skills / agentic-ai are all "AI tooling".
# Four options from four "different" topics like that still read as one poll.
_FAMILY_TOPICS: dict[str, set[str]] = {
    "ai_tools": {"claude-code", "ai-agents", "codex", "agent-skills", "agentic-ai",
                 "llm", "deepseek", "mcp", "claude-code-plugin", "prompt-engineering",
                 "anthropic", "coding-agents", "computer-use", "gemini-cli"},
    "platforms": {"macos", "ios", "android", "windows", "linux", "swift", "swiftui",
                  "tauri", "launcher", "apple-silicon"},
    "media": {"subtitles", "cad", "animation", "audio-player", "video", "3d",
              "vulkan", "graphics", "gamedev", "tv", "image"},
    "languages": {"rust", "python", "javascript", "typescript", "go", "shell", "c",
                  "cpp", "kotlin"},
    "self_hosted": {"self-hosted", "privacy", "home-automation", "docker", "local-first"},
    "cli_tools": {"cli", "gui", "developer-tools", "terminal", "tui"},
}
_FAMILY_KEYWORDS = (
    ("ai_tools", ("agent", "llm", "model", "prompt", "claude", "gpt", "gemini")),
    ("media", ("video", "audio", "3d", "cad", "animation", "subtitle", "render")),
    ("platforms", ("macos", "ios", "android", "windows", "linux", "tv ")),
)


def family_key(candidate: dict[str, Any]) -> str:
    """Broad subject family, so four options are four genuinely different jobs."""
    for topic in (str(t).strip().lower() for t in (candidate.get("topics") or [])):
        for family, members in _FAMILY_TOPICS.items():
            if topic in members:
                return family
    blob = (str(candidate.get("description") or "") + " "
            + str((candidate.get("generated") or {}).get("hook") or "")).lower()
    for family, words in _FAMILY_KEYWORDS:
        if any(word in blob for word in words):
            return family
    return str(candidate.get("language") or "misc").strip().lower()


def _fallback_label(candidate: dict[str, Any]) -> str:
    """Deterministic, name-free job statement from the queue description."""
    raw = _clean(candidate.get("description") or (candidate.get("generated") or {}).get("hook") or "")
    name = str(candidate.get("name") or "").strip()
    full = str(candidate.get("full_name") or "").strip()
    for token in (full, name):
        if token:
            raw = re.sub(re.escape(token), " ", raw, flags=re.IGNORECASE)
    raw = _clean(raw).replace("/", " ")
    first = raw.split(". ")[0].split("! ")[0].strip(" .-:;")
    if len(first) > MAX_OPTION:
        first = first[:MAX_OPTION].rsplit(" ", 1)[0].rstrip(" ,-")
    if len(first) < 12:
        language = str(candidate.get("language") or "open-source").strip()
        category = str(candidate.get("category") or "developer").strip()
        first = f"Try an open-source {language} tool for {category.lower()}"
    return first[:MAX_OPTION]


def sanitize_label(text: str, candidate: dict[str, Any]) -> str:
    """Reject anything that smuggles a repo name or service copy into an option."""
    label = _clean(text).replace("/", " ").strip('"').strip()
    low = label.lower()
    name = str(candidate.get("name") or "").lower().strip()
    full = str(candidate.get("full_name") or "").lower().strip()
    bad = (
        not label
        or len(label) < 12
        or (name and name in low)
        or (full and full in low)
        or any(word in low for word in BANNED_WORDS)
        or "http" in low
        or label.count(" ") < 2
    )
    if bad:
        return _fallback_label(candidate)
    if len(label) > MAX_OPTION:
        label = label[:MAX_OPTION].rsplit(" ", 1)[0].rstrip(" ,-")
    return label


def job_labels(candidates: list[dict[str, Any]], client: Any | None) -> tuple[list[str], bool]:
    """One Gemini call for the whole shortlist; fallback per item on failure."""
    fallback = [_fallback_label(c) for c in candidates]
    if client is None or not candidates:
        return fallback, True
    lines = [
        f"{i + 1}. {_clean(c.get('description') or (c.get('generated') or {}).get('hook') or '')[:220]}"
        for i, c in enumerate(candidates)
    ]
    prompt = (
        "For each tool description, write ONE short job statement in plain English: "
        "what a developer could DO with it. Rules: max 95 characters; start with a "
        "verb or a concrete outcome; no repository names, no company names, no "
        "slashes, no GitHub, no star counts, no marketing, no source labels, no "
        "numbers that are just metrics; at least three words. Keep the same order.\n\n"
        + "\n".join(lines)
        + '\n\nReturn JSON: {"labels": ["...", "..."]} with exactly '
        + str(len(candidates)) + " strings."
    )
    try:
        data = client.generate_json(prompt, temperature=0.4, max_output_tokens=512)
        raw = (data or {}).get("labels") or []
        if not isinstance(raw, list) or len(raw) != len(candidates):
            return fallback, True
        return [
            sanitize_label(str(raw[i]), c) for i, c in enumerate(candidates)
        ], False
    except Exception as exc:  # noqa: BLE001 - a 503 must never lose the poll
        logger.warning("poll job labels fallback: %s", type(exc).__name__)
        return fallback, True


def _distinct_candidates(queue: list[dict[str, Any]], limit: int = 4) -> list[dict[str, Any]]:
    """One candidate per broad family AND per cluster, highest score first."""
    ranked = sorted(queue, key=lambda c: float(c.get("score") or 0), reverse=True)
    picked: list[dict[str, Any]] = []
    families: set[str] = set()
    clusters: set[str] = set()
    for strict in (True, False):
        for candidate in ranked:
            if candidate in picked:
                continue
            family = family_key(candidate)
            cluster = cluster_key(candidate)
            if strict and (family in families or cluster in clusters):
                continue
            if not strict and cluster in clusters:
                continue
            families.add(family)
            clusters.add(cluster)
            picked.append(candidate)
            if len(picked) == limit:
                return picked
    for candidate in ranked:  # homogeneous queue: fill without the guard
        if candidate not in picked:
            picked.append(candidate)
        if len(picked) == limit:
            break
    return picked


def _view_shift(store: StateStore) -> str | None:
    """Name a language whose recent posts clearly beat the earlier ones."""
    records = [r for r in store.published_records() if r.get("views_at_24h")]
    if len(records) < 8:
        return None
    recent = sorted(records, key=lambda r: str(r.get("published_at") or ""), reverse=True)[:6]
    older = [r for r in records if r not in recent][:6]
    if not older:
        return None
    for field in ("language", "category"):
        recent_vals: dict[str, list[float]] = {}
        older_vals: dict[str, list[float]] = {}
        for group, rows in (("recent", recent), ("older", older)):
            target = recent_vals if group == "recent" else older_vals
            for r in rows:
                key = str(r.get(field) or "").lower()
                if key:
                    target.setdefault(key, []).append(float(r.get("views_at_24h") or 0))
        for key, values in recent_vals.items():
            base = older_vals.get(key) or []
            if len(values) < 2 or len(base) < 2:
                continue
            if sum(values) / len(values) > 1.35 * (sum(base) / len(base)):
                return key
    return None


def _profile(store: StateStore) -> dict[str, Any]:
    return store.data.setdefault("audience_profile", {
        "version": 1, "updated_at": "", "axes": {}, "confidence": {},
        "weeks": [], "last_result": "", "type_history": [],
    })


def choose_type(store: StateStore, *, now: Any = None) -> tuple[str, bool]:
    """Adaptive, not a calendar carousel. Returns (type, simplify)."""
    profile = _profile(store)
    weeks = profile.get("weeks") or []
    history = [str(w.get("type")) for w in weeks][-RECENT_TYPE_WINDOW:]
    last = weeks[-1] if weeks else {}
    last_type = str(last.get("type") or "")
    low = bool(last.get("low_confidence"))
    shift = _view_shift(store)
    queue = store.data.get("queue") or []

    if not weeks or not profile.get("axes"):
        return "job", False
    if shift and "reality_check" not in history:
        return "reality_check", low
    if int(last.get("votes") or 0) >= MIN_SIGNAL_VOTES:
        follow = {"job": "direction", "direction": "depth", "format": "depth",
                  "depth": "friction", "friction": "format",
                  "reality_check": "job"}.get(last_type, "job")
        if follow not in history and follow != last_type:
            return follow, False
    # Prefer the least-known axis among types unused in the recent window.
    confidence = profile.get("confidence") or {}
    candidates = [t for t in QUESTIONS if t not in history and t != last_type
                  and t != "reality_check"]
    if not candidates:
        candidates = [t for t in QUESTIONS if t != last_type and t != "reality_check"]
    if not candidates:
        candidates = ["job"]
    candidates.sort(key=lambda t: (float(confidence.get(t, 0.0)), t))
    chosen = candidates[0]
    if low and len(queue) > 12:
        chosen = "job"          # short question, concrete jobs, easiest to answer
    return chosen, low


def build_week_poll(store: StateStore, *, client: Any = None, now: Any = None) -> dict[str, Any]:
    """Produce a complete poll spec WITHOUT publishing anything."""
    poll_type, simplify = choose_type(store, now=now)
    question = QUESTIONS[poll_type]
    options: list[str] = []
    keys: list[str] = []
    option_map: dict[str, str] = {}
    topics_map: dict[str, list[str]] = {}
    clusters: list[str] = []
    families: list[str] = []
    used_fallback = True

    if poll_type in ("job", "direction"):
        queue = [c for c in (store.data.get("queue") or [])
                 if not c.get("disabled") and not c.get("archived")]
        picked = _distinct_candidates(queue, limit=3 if simplify else 4)
        labels, used_fallback = job_labels(picked, client)
        for i, (candidate, label) in enumerate(zip(picked, labels), 1):
            key = f"{poll_type}_{cluster_key(candidate)}_{i}"
            keys.append(key)
            options.append(label[:MAX_OPTION])
            option_map[key] = str(candidate.get("full_name") or "")
            topics_map[key] = [str(t).lower() for t in (candidate.get("topics") or [])][:6]
            clusters.append(cluster_key(candidate))
            families.append(family_key(candidate))
    elif poll_type == "reality_check":
        shift = _view_shift(store) or "open-source"
        label = shift.title()
        question = f"{label} posts got more attention last week. Coincidence?"
        options = [f"Show more {label} tools", "Keep the current mix",
                   "It was the topic, not the language"]
        keys = ["more_of_it", "keep_mix", "topic_not_language"]
        for k in keys:
            topics_map[k] = [shift]
    else:
        pairs = STATIC_OPTIONS[poll_type]
        if simplify:
            pairs = pairs[:3]
        keys = [k for k, _ in pairs]
        options = [v[:MAX_OPTION] for _, v in pairs]

    if len(options) < 2:  # never ask a one-option poll
        options = ["More deep dives", "More quick picks", "More comparisons"]
        keys = ["deep_dive", "quick_picks", "comparison"]
        poll_type, used_fallback = "format", True

    spec: dict[str, Any] = {
        "type": poll_type,
        "week": iso_week_key(now),
        "question": question[:MAX_QUESTION],
        "options": options[:10],
        "option_keys": keys[:10],
        "option_map": option_map,
        "option_topics": topics_map,
        "clusters": clusters,
        "families": families,
        "axis": poll_type,
        "simplified": simplify,
        "gemini_fallback": used_fallback,
        "decides": DECIDES.get(poll_type, "what we publish next"),
    }
    return spec


def _update_profile(store: StateStore, tally: dict[str, Any], poll: dict[str, Any],
                    spec_meta: dict[str, Any], *, now: Any = None) -> str:
    """EMA with a turnout discount. Small samples are archive, not signal."""
    profile = _profile(store)
    votes = int(tally.get("total_voters") or 0)
    axis = str(spec_meta.get("axis") or "")
    keys = list(spec_meta.get("option_keys") or [])
    topics_map = spec_meta.get("option_topics") or {}
    low = votes < MIN_SIGNAL_VOTES or not keys or not axis
    entries = tally.get("options") or []
    axes: dict[str, Any] = profile.setdefault("axes", {})
    confidence: dict[str, Any] = profile.setdefault("confidence", {})
    weeks: list[Any] = profile.setdefault("weeks", [])
    history: list[Any] = profile.setdefault("type_history", [])
    week = iso_week_key(now)
    winner_key = ""
    winner_label = ""
    best = -1
    for i, option in enumerate(entries):
        if int(option.get("votes") or 0) > best and i < len(keys):
            best = int(option.get("votes") or 0)
            winner_key = keys[i]
            winner_label = str(option.get("text") or "")

    if not low:
        weights: dict[str, float] = dict(axes.get(axis) or {})
        discount = min(1.0, votes / (2 * MIN_SIGNAL_VOTES))
        alpha = EMA_ALPHA * discount
        total = max(1, sum(int(o.get("votes") or 0) for o in entries))
        for i, option in enumerate(entries):
            if i >= len(keys):
                break
            key = keys[i]
            share = int(option.get("votes") or 0) / total
            old = float(weights.get(key, 0.5))
            weights[key] = round((1 - alpha) * old + alpha * share, 4)
        axes[axis] = weights
        for key in keys:
            if key not in (profile.get("key_topics") or {}):
                profile.setdefault("key_topics", {})[key] = topics_map.get(key, [])
        confidence[axis] = round(min(1.0, float(confidence.get(axis, 0.0))
                                    + votes / (4 * MIN_SIGNAL_VOTES)), 4)
        human = winner_label or "the most popular choice"
        result = (f"Last week: {votes} votes picked “{human}”. "
                  f"We are leaning into that this week.")
    else:
        result = ("Last week: not enough votes to change the mix — "
                  "asking a simpler question this time.")

    weeks.append({
        "week": week, "type": spec_meta.get("type") or axis, "axis": axis,
        "votes": votes, "low_confidence": low, "winner_key": winner_key,
        "gemini_fallback": bool(spec_meta.get("gemini_fallback")),
    })
    del weeks[:-12]
    history.append(spec_meta.get("type") or axis)
    del history[-8:]
    profile["last_result"] = result
    profile["updated_at"] = iso_now()
    return result


def record_poll_result(store: StateStore, tally: dict[str, Any], poll: dict[str, Any]) -> dict[str, Any]:
    """Called by close_poll_command. Legacy polls (no keys) are archive only."""
    meta = {
        "type": poll.get("type") or "",
        "axis": poll.get("axis") or "",
        "option_keys": poll.get("option_keys") or [],
        "option_topics": poll.get("option_topics") or {},
        "gemini_fallback": bool(poll.get("gemini_fallback")),
    }
    if not meta["axis"] or not meta["option_keys"]:
        profile = _profile(store)
        profile["last_result"] = ("Last week: that poll was the old format, "
                                 "so we are starting fresh — your vote now "
                                 "shapes next week's picks.")
        profile["updated_at"] = iso_now()
        return {"ok": True, "legacy": True, "result": profile["last_result"]}
    result = _update_profile(store, tally, poll, meta)
    return {"ok": True, "legacy": False, "result": result}


def audience_ranking_model(profile: dict[str, Any] | None) -> dict[str, Any]:
    """Translate the taste profile into the model shape rank.py already uses.

    Deliberately gentle and confidence-weighted: a taste signal nudges
    near-equal candidates, it never overrules the gate or the score.
    """
    if not profile:
        return {}
    confidence = profile.get("confidence") or {}
    axes = profile.get("axes") or {}
    key_topics = profile.get("key_topics") or {}
    topic_weights: dict[str, float] = {}
    for axis, weights in axes.items():
        conf = float(confidence.get(axis, 0.0))
        if conf <= 0:
            continue
        for key, weight in (weights or {}).items():
            for topic in key_topics.get(key, []) or []:
                nudge = 1.0 + (float(weight) - 0.5) * 0.24 * conf
                topic_weights[topic] = max(0.9, min(1.12,
                    topic_weights.get(topic, 1.0) * nudge))
    languages = {
        str(k).lower(): max(0.9, min(1.12, 1.0 + (float(v) - 0.5) * 0.2))
        for k, v in (axes.get("language") or {}).items()
        if confidence.get("language", 0)
    }
    return {"language_multipliers": languages, "topic_multipliers": topic_weights}


def merge_models(base: dict[str, Any] | None, extra: dict[str, Any] | None) -> dict[str, Any]:
    """Combine learn's view-based model with the audience profile."""
    base = base or {}
    extra = extra or {}
    merged: dict[str, Any] = {}
    for field in ("language_multipliers", "topic_multipliers"):
        left = dict(base.get(field) or {})
        for key, value in (extra.get(field) or {}).items():
            left[key] = max(0.85, min(1.15, float(left.get(key, 1.0)) * float(value)))
        if left:
            merged[field] = left
    return merged
