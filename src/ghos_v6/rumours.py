"""Rumour hygiene for the AI Brief (owner 08.11, second duplicate incident).

The first fix compared the literal first sentence of a claim. It removed an
exact duplicate with a quoted aside, but a channel re-post that merely added
the word "multiplayer" produced a different key and slipped through: post
3575 printed the same Anthropic rumour twice. This module is the general fix:

* strip quoted asides, emoji repost-chains and clipped tails from raw channel
  text before it ever reaches Gemini, state or the formatter;
* compare claims by shared significant tokens (overlap coefficient), not by
  string equality, so re-wordings collapse;
* remember rumour keys from previous days in state, so a 48-hour discovery
  window cannot repeat yesterday's rumour in today's brief.

Pure stdlib: ``telegram_discovery`` imports this module without pulling in
Gemini, requests or the HTML formatter.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

# A channel post that quotes its own source appends "> ...", " — ..." or a
# "Yet 👀 ..." repost chain. Only the first claim belongs in the brief.
QUOTE_MARKERS = (">", " — ", " – ", " | ", " - ")
_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2190-\u21FF\uFE0F]")
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "is", "are", "was", "were", "be",
    "been", "being", "on", "in", "at", "to", "for", "of", "with", "from",
    "by", "as", "it", "its", "this", "that", "these", "those", "has", "have",
    "had", "will", "would", "could", "should", "may", "might", "can", "not",
    "no", "yet", "still", "just", "about", "into", "over", "after", "before",
    "via", "per", "one", "two", "their", "there", "here", "what", "which",
    "who", "when", "where", "how", "why", "all", "any", "some", "more",
    "most", "new", "up", "out", "now", "than", "then", "also",
})

SIMILARITY_THRESHOLD = 0.8   # overlap of significant tokens
MIN_TOKENS = 4               # shorter claims are compared literally
MEMORY_DAYS = 3              # a rumour may not return within three days
MEMORY_MAX_KEYS = 200        # bounded even after years of daily briefs

# Owner 09.11: once yesterday's rumours were filtered out, the "keep up to
# three rumours" fallback pulled personal/corporate gossip straight from the
# channels ("Jensen Huang accused of ties to a Chinese spy", "multi-billion
# dollar divorce for Nvidia CEO"). Those are neither model news nor exact
# technical names. A rumour belongs in the block only when it is about AI
# technology AND is not celebrity/political/financial gossip.
GOSSIP_MARKERS: tuple[str, ...] = (
    "divorce", "spy", "espionage", "accused", "allegation", "alleged",
    "affair", "wedding", "girlfriend", "boyfriend", "scandal", "celebrity",
    "arrest", "politics", "election", "president", "senator", "regulation",
    "ipo", "funding", "valuation", "stock", "shares", "layoff", "acquisition",
    "merger", "revenue", "dividend", "billionaire", "net worth", "dating",
    "personal life", "war", "military", "sanctions",
)
_TECH_RE = re.compile(
    r"\b(?:ai|llm|llms?|models?|gpt|claude|gemini|llama|qwen|mistral|"
    r"deepseek|codex|cursor|windsurf|copilot|openai|anthropic|huggingface|"
    r"astra|sonnet|opus|fable|mythos|grok|ripple|inkling|"
    r"openrouter|bedrock|aws|benchmarks?|parameters?|tokens?|context window|"
    r"apis?|sdk|checkpoint|weights|pricing|price|agents?|inference|training|"
    r"release|preview|beta|leaks?|leaked|screenshot|limits|reset|open source|"
    r"open-source|github|repo|library|framework|mcp|coding|dataset|fine-tune|"
    r"quantized|diffusion|transformer|neural|gpu|tpu|multimodal|reasoning|"
    r"version|update|ide|plugin|extension)\b",
    re.IGNORECASE,
)


def clean_rumour_text(value: Any, limit: int = 240) -> str:
    """One complete, quoteless claim — never a clipped repost chain."""
    text = html.unescape(str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    for marker in QUOTE_MARKERS:
        if marker in text:
            text = text.split(marker, 1)[0]
    text = _EMOJI_RE.sub("", text)
    sentence = re.match(r"(.+?[.!?])(?:\s|$)", text)
    if sentence:
        text = sentence.group(1)
    text = re.sub(r"\s+", " ", text).strip(" \t-–—:;,\"'")
    return text[:limit].strip()


def rumour_tokens(value: Any) -> frozenset[str]:
    """Significant words only: 'multiplayer' must not change a story's key."""
    words = re.findall(r"[a-z0-9]+", clean_rumour_text(value).lower())
    return frozenset(w for w in words if len(w) > 2 and w not in _STOP_WORDS)


def _rumour_key(value: Any) -> str:
    return " ".join(sorted(rumour_tokens(value)))


def _similar(left: frozenset[str], right: frozenset[str]) -> bool:
    if not left or not right:
        return False
    smaller = min(len(left), len(right))
    if smaller < MIN_TOKENS:
        return left == right
    return len(left & right) / smaller >= SIMILARITY_THRESHOLD


def dedupe_claims(items: Iterable[Any] | None, limit: int = 3) -> list[str]:
    """Keep the first, fullest wording of each distinct rumour.

    ``limit`` is a ceiling, not a target: two real rumours are better than
    three slots with one of them duplicated (post 3575, 08.11).
    """
    out: list[str] = []
    seen: list[frozenset[str]] = []
    for raw in items or []:
        text = clean_rumour_text(raw)
        if not text:
            continue
        tokens = rumour_tokens(text)
        if any(_similar(tokens, previous) for previous in seen):
            continue
        seen.append(tokens)
        out.append(text[:400])
        if len(out) >= limit:
            break
    return out


def _memory(store: Any) -> dict[str, str]:
    raw = store.data.get("rumour_memory")
    return dict(raw) if isinstance(raw, dict) else {}


def _day_bounds(now: datetime | None, days: int) -> tuple[str, str]:
    moment = now or datetime.now(timezone.utc)
    today = moment.date()
    return today.isoformat(), (today - timedelta(days=days)).isoformat()


def filter_recent_rumours(store: Any, claims: Iterable[Any] | None, *,
                          now: datetime | None = None,
                          days: int = MEMORY_DAYS) -> list[str]:
    """Drop rumours already printed on a PREVIOUS day.

    Same-day keys are deliberately ignored: ``news --refresh`` edits today's
    post in place and must not empty its rumour block. Old keys are pruned as
    a side effect, so the memory never grows without bound.
    """
    today, cutoff = _day_bounds(now, days)
    memory = _memory(store)
    fresh = {k: v for k, v in memory.items()
             if isinstance(k, str) and isinstance(v, str) and v >= cutoff}
    store.data["rumour_memory"] = fresh
    prior = [frozenset(k.split()) for k, v in fresh.items() if v < today]
    out: list[str] = []
    for raw in claims or []:
        text = clean_rumour_text(raw)
        if not text:
            continue
        if any(_similar(rumour_tokens(text), key) for key in prior):
            continue
        out.append(text[:400])
    return out


def is_rumour_relevant(value: Any) -> bool:
    """True only for a technology rumour, never personal/corporate gossip."""
    text = clean_rumour_text(value).lower()
    if not text:
        return False
    if any(marker in text for marker in GOSSIP_MARKERS):
        return False
    return bool(_TECH_RE.search(text))


def prune_rumour_memory(store: Any, keep_claims: Iterable[Any] | None, *,
                        now: datetime | None = None) -> int:
    """Drop TODAY's memory keys that did not survive the final rumour block.

    Owner 09.11: gossip that was published before the fix must not stay in
    state as if it were a legitimate rumour. Keys from previous days stay.
    """
    today, _cutoff = _day_bounds(now, MEMORY_DAYS)
    keep = {_rumour_key(c) for c in keep_claims or []}
    memory = _memory(store)
    stale = [k for k, day in memory.items() if day == today and k not in keep]
    for key in stale:
        del memory[key]
    store.data["rumour_memory"] = memory
    return len(stale)


def remember_rumours(store: Any, claims: Iterable[Any] | None, *,
                     now: datetime | None = None,
                     days: int = MEMORY_DAYS) -> int:
    """Record the rumours that actually reached the channel."""
    today, cutoff = _day_bounds(now, days)
    memory = _memory(store)
    for key in [k for k, v in memory.items()
                if not isinstance(v, str) or v < cutoff]:
        del memory[key]
    for raw in claims or []:
        key = _rumour_key(raw)
        if key:
            memory[key] = today
    if len(memory) > MEMORY_MAX_KEYS:
        for key in sorted(memory, key=lambda k: (memory[k], k))[:len(memory) - MEMORY_MAX_KEYS]:
            del memory[key]
    store.data["rumour_memory"] = memory
    return len(memory)
