"""Audience fit: would a developer scrolling the feed stop on this?

The owner's rule: the channel publishes *interesting projects*, not
"technical libraries or something for a very narrow circle".  The gate
already keeps obvious junk out (no licence, stale, prompt dumps), but it
said nothing about whether a repository is a **product** or a **building
block**.

Two judgements live here:

* ``narrow_kind`` — a hard classification of packages that exist to be
  imported by other software: SDKs, bindings, API clients, ORMs,
  boilerplate, adapters.  They are almost never read-worthy on their own.
* ``appeal_score`` — a count of signals that a human can *do* something
  with the project: a desktop app, a GUI, an editor, self-hosting, a
  platform people recognise, a creative medium.

Nothing here invents facts: every signal is a word the repository itself
uses in its name, description or topics.
"""
from __future__ import annotations

import re
from typing import Any

# Packages that only make sense inside another codebase.  A project whose
# own words say this is a building block, not a story.
_LIBRARY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("sdk", re.compile(r"\b(?:sdk|client library|api client|client for the)\b", re.I)),
    ("bindings", re.compile(r"\b(?:bindings?|bindings for|ffi|foreign function)\b", re.I)),
    ("adapter", re.compile(r"\b(?:adapter|adapter for|connector for|driver for|plugin for)\b", re.I)),
    ("orm", re.compile(r"\b(?:orm|query builder|migration (?:tool|library)|schema builder)\b", re.I)),
    ("boilerplate", re.compile(r"\b(?:boilerplate|starter (?:kit|template)|template for|scaffold)\b", re.I)),
    ("middleware", re.compile(r"\b(?:middleware|shim|polyfill|interop layer)\b", re.I)),
    ("protocol-impl", re.compile(r"\b(?:reference implementation|protocol implementation|codec)\b", re.I)),
    # most packages say it in their own first line
    ("plugin-ecosystem", re.compile(
        r"\b(?:plugin (?:market|marketplace|ecosystem|aggregation)|plugins? (?:for|that))\b",
        re.I,
    )),
    ("generic-library", re.compile(
        r"\b(?:a|an|the)\s+(?:[a-z0-9-]+\s+){0,3}(?:library|crate|package|module|dependency)\s+(?:for|to|that)\b",
        re.I,
    )),
    ("generic-library", re.compile(r"\b(?:is|as)\s+an?\s+(?:[a-z0-9-]+\s+){0,2}library\s+for\b", re.I)),
    # "An ergonomic HTTP Client for Rust", "A runtime for writing ..."
    ("generic-library", re.compile(
        r"^\s*(?:a|an)\s+(?:[a-z0-9+.-]+\s+){1,4}(?:client|runtime|framework|wrapper|toolkit)\s+for\b",
        re.I,
    )),
)

_NARROW_TOPICS = {
    "library",
    "sdk",
    "python-library",
    "rust-crate",
    "crate",
    "npm-package",
    "package",
    "bindings",
    "ffi",
    "orm",
    "boilerplate",
    "template",
    "middleware",
    "api-client",
}

# Words that mean a person can open it and use it.
_APPEAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("desktop", re.compile(r"\b(?:desktop (?:app|application|client)|native app|gui|tray app)\b", re.I)),
    ("editor", re.compile(r"\b(?:editor|ide|studio|workbench)\b", re.I)),
    ("self_hosted", re.compile(r"\b(?:self[- ]hosted|self[- ]host|docker compose|one[- ]click deploy)\b", re.I)),
    ("platform", re.compile(r"\b(?:windows|macos|linux|android|ios|iphone|ipad)\b", re.I)),
    ("browser", re.compile(r"\b(?:browser extension|chrome extension|firefox extension|web app|in the browser)\b", re.I)),
    ("creative", re.compile(r"\b(?:photo|video|audio|music|3d|render|animation|design|vector|cad|drawing|paint)\b", re.I)),
    ("replacement", re.compile(r"\b(?:replacement for|alternative to|instead of)\b", re.I)),
    # installable skills/tools for coding agents: the owner explicitly wants
    # these when they do something a person can use ("skills for Claude Code")
    ("agent_skill", re.compile(r"\bskills?\b.*\b(?:claude code|codex|cursor|agents?)\b|\b(?:claude code|codex|cursor)\s+skills?\b", re.I)),
    ("media", re.compile(r"\b(?:stream|player|media library|gallery|subtitle|transcod|ffmpeg)\b", re.I)),
    ("productivity", re.compile(r"\b(?:notes?|tasks?|calendar|todo|kanban|wiki|read it later|bookmarks?)\b", re.I)),
    ("game", re.compile(r"\b(?:game|emulator|minecraft|mod loader|game engine)\b", re.I)),
    ("ai_tool", re.compile(r"\b(?:chatbot|assistant|agent (?:app|workspace)|local llm|llm (?:client|desktop))\b", re.I)),
    ("hardware", re.compile(r"\b(?:keyboard|mouse|webcam|firmware|microcontroller|raspberry pi|hid)\b", re.I)),
    ("network", re.compile(r"\b(?:vpn|proxy server|tailscale|mesh network|dns server|adblock)\b", re.I)),
    ("files", re.compile(r"\b(?:backup|sync|file manager|cloud storage|archiver|compression)\b", re.I)),
    ("messaging", re.compile(r"\b(?:messenger|chat app|matrix client|telegram client|email client)\b", re.I)),
)

_NARROW_TOPIC_HINTS = {
    "plugin",
    "plugins",
    "plugin-marketplace",
    "prompt-library",
    "dataset",
    "curated-list",
    "awesome-list",
    "tutorial",
    "course",
    "dotfiles",
    "config",
}


def _haystack(project: dict[str, Any], *, include_topics: bool = True) -> str:
    parts = [
        str(project.get("name") or ""),
        str(project.get("full_name") or ""),
        str(project.get("description") or ""),
        str(project.get("readme_summary") or ""),
        str(project.get("primary_use_case") or ""),
    ]
    if include_topics:
        parts.extend(str(t) for t in project.get("topics") or [])
    return " ".join(parts)


def narrow_kind(project: dict[str, Any]) -> str:
    """Return a short label when the project is a building block ('' if not).

    Topics are checked first: a repository that tags itself ``library`` or
    ``sdk`` is telling us what it is.  Name/description patterns are the
    fallback, and they need a fairly explicit phrase so that a product
    which merely *uses* an SDK is not misread as one.
    """
    topics = {str(t).lower() for t in project.get("topics") or []}
    for topic in sorted(topics & _NARROW_TOPICS):
        return topic
    name = str(project.get("name") or "")
    description = str(project.get("description") or "")
    # The name is the strongest signal when it reads like a package handle.
    for label, pattern in _LIBRARY_PATTERNS:
        if pattern.search(name):
            return label
    for label, pattern in _LIBRARY_PATTERNS:
        if pattern.search(description):
            return label
    return ""


def appeal_signals(project: dict[str, Any]) -> list[str]:
    """Signals that a person can actually use the project."""
    haystack = _haystack(project)
    topics = {str(t).lower() for t in project.get("topics") or []}
    found: list[str] = []
    for label, pattern in _APPEAL_PATTERNS:
        if pattern.search(haystack) or label in topics:
            found.append(label)
    return found


def appeal_score(project: dict[str, Any]) -> int:
    """How many independent "a human can use this" signals the project has."""
    return len(appeal_signals(project))


def is_narrow(project: dict[str, Any], *, min_appeal: int = 1) -> tuple[bool, str]:
    """Decide whether the project is too narrow for the channel feed.

    Returns ``(is_narrow, reason)``.  A package is narrow when it declares
    itself a building block **and** offers no reason for a reader to open
    it (no GUI, no platform, no medium).  A library with a strong visual
    product story (``ffmpeg`` wrapper with a GUI) stays.
    """
    topics = {str(t).lower() for t in project.get("topics") or []}
    # Prompt/dataset/dotfile topics are never a product, whatever else is
    # in the description.
    if topics & _NARROW_TOPIC_HINTS:
        return True, f"topic:{sorted(topics & _NARROW_TOPIC_HINTS)[0]}"
    kind = narrow_kind(project)
    if kind and appeal_score(project) < min_appeal:
        return True, f"library:{kind}"
    return False, ""
