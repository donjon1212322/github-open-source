"""Platform-native content adapters.

Gemini produces one canonical, fact-checked content object.  This
module turns that object into native copy for each surface instead of
copy/pasting the same paragraph everywhere:

* Bluesky: one sharp idea + repo link, hard 300-character budget.
* Facebook: plain-language Page post with context and a question.
* dev.to: searchable article wrapper with TL;DR and discussion CTA.

The voice is intentionally stable across surfaces: a curious senior
developer-editor.  No fake first-person testing, no inherited hype,
benefit first, proof points second, details progressively disclosed.
"""
from __future__ import annotations

import re
from typing import Any

from .formatter import _language_icon as _shared_language_icon

BLUESKY_LIMIT = 300
FACEBOOK_TARGET = 1800

_LANG_TAG = {
    "c++": "cpp",
    "c#": "csharp",
    "f#": "fsharp",
    "objective-c": "objc",
    "jupyter notebook": "jupyter",
}

_DEVTO_TAG_ALIASES = {
    "agent-skills": "ai",
    "developer-tools": "devtools",
    "security-and-analysis": "security",
    "security and analysis": "security",
    "developertools": "devtools",
    "machine-learning": "machinelearning",
    "open-source": "opensource",
}

_GENERIC_TOPICS = {"github", "awesome", "awesome-list", "open-source", "opensource"}
_BLOCKED_TAG_TERMS = ("antidetect", "undetected", "stealth", "bypass")


def _blocked_tag_value(value: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", str(value or "").lower())
    return any(term in normalized for term in _BLOCKED_TAG_TERMS)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clip_text(value: Any, limit: int) -> str:
    text = clean_text(value)
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:limit]
    cut = text[: limit - 1].rstrip()
    if ". " in cut:
        sentence = cut.rsplit(". ", 1)[0]
        if len(sentence) >= max(35, int(limit * 0.55)):
            return sentence.rstrip(".") + "."
    word = cut.rsplit(" ", 1)[0]
    return (word or cut).rstrip(" ,;:") + "…"


def _sentences(value: Any, limit: int) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    parts = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for part in parts:
        candidate = f"{out} {part}".strip()
        if len(candidate) > limit:
            break
        out = candidate
    return out or clip_text(text, limit)


def _fmt_num(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


def license_name(record: dict[str, Any]) -> str:
    lic = record.get("license")
    if isinstance(lic, dict):
        return clean_text(lic.get("spdx_id") or lic.get("name") or "")
    return clean_text(lic)


def record_name(record: dict[str, Any]) -> str:
    return clean_text(record.get("name") or record.get("full_name") or "Open-source project")


def repo_url(record: dict[str, Any]) -> str:
    url = clean_text(record.get("url") or record.get("html_url") or "")
    if url.startswith("https://"):
        return url
    full_name = clean_text(record.get("full_name") or "")
    return f"https://github.com/{full_name}" if "/" in full_name else ""


def public_telegram_link(record: dict[str, Any]) -> str:
    link = clean_text(record.get("telegram_link") or "")
    if not link.startswith("https://t.me/"):
        return ""
    if "/c/" in link or "/+" in link or "joinchat" in link:
        return ""
    return link


def breakdown_url(record: dict[str, Any]) -> str:
    telegraph = clean_text(record.get("telegraph_url") or "")
    if telegraph.startswith("https://"):
        return telegraph
    return public_telegram_link(record)


TELEGRAM_CHANNEL_URL = "https://t.me/GitHub_Open_Source"


def language_icon(record: dict[str, Any]) -> str:
    return _shared_language_icon(record)


def telegram_cta_url(record: dict[str, Any]) -> str:
    """Best public Telegram destination: this post, else the channel."""
    return public_telegram_link(record) or TELEGRAM_CHANNEL_URL


def telegram_cta_line(record: dict[str, Any], *, compact: bool = False) -> str:
    """Return-loop CTA for external surfaces.

    External posts must grow the Telegram channel, not only send
    readers to a third-party repository. Private ``t.me/c`` test links
    are filtered by :func:`public_telegram_link`, so test crossposts
    fall back to the public channel URL.
    """
    specific = public_telegram_link(record)
    if specific:
        return f"Discuss: {specific}" if compact else f"💬 Discuss on Telegram: {specific}"
    return (
        f"Daily picks: {TELEGRAM_CHANNEL_URL}"
        if compact
        else f"📣 Daily open-source picks on Telegram: {TELEGRAM_CHANNEL_URL}"
    )


def facebook_media_url(record: dict[str, Any]) -> str:
    """Public image URL for the Page post, or ``""``.

    ``opengraph.githubassets.com`` is reachable only from GitHub's own
    pages for the repo owner context — Meta often fails to fetch it, and the
    result is a Page post with no picture. Better to hand make.com an empty
    string and let the scenario fall back than to ship a dead URL.
    """
    url = str(record.get("image_url") or "")
    if not url.startswith("https://"):
        return ""
    if "opengraph.githubassets.com" in url:
        return ""
    return url


def Facebook_link_url(record: dict[str, Any]) -> str:
    return breakdown_url(record) or repo_url(record)


def surface_field(generated: dict[str, Any] | None, key: str) -> str:
    """Read a per-platform rewrite produced by GeminiClient.generate_surfaces.

    Falls back to an empty string so every caller keeps its old behaviour
    when the second call is unavailable (no key, quota, older record).
    """
    surfaces = (generated or {}).get("surfaces") or {}
    if not isinstance(surfaces, dict):
        return ""
    return clean_text(surfaces.get(key) or "")


def surface_markdown(generated: dict[str, Any] | None, key: str) -> str:
    """Read a Markdown body without flattening its paragraphs or headings.

    ``surface_field`` deliberately collapses whitespace for one-line copy.
    Applying it to an article turns every ``##`` heading into inline text,
    which dev.to then renders as one unreadable wall of prose.
    """
    surfaces = (generated or {}).get("surfaces") or {}
    if not isinstance(surfaces, dict):
        return ""
    return str(surfaces.get(key) or "").strip()


def _surface_or(fallback: str, value: str, limit: int) -> str:
    value = clean_text(value)
    if not value:
        return fallback
    return clip_text(value, limit)


def _generated(record: dict[str, Any], generated: dict[str, Any] | None) -> dict[str, Any]:
    if generated is not None:
        return generated
    value = record.get("generated")
    return value if isinstance(value, dict) else {}


def _hook(record: dict[str, Any], generated: dict[str, Any]) -> str:
    return clip_text(
        generated.get("hook") or generated.get("social_text") or record.get("description") or "",
        160,
    )


def _summary(record: dict[str, Any], generated: dict[str, Any]) -> str:
    return clean_text(generated.get("summary") or record.get("description") or "")


def _features(generated: dict[str, Any]) -> list[str]:
    features: list[str] = []
    for item in generated.get("key_features") or []:
        value = clip_text(item, 120)
        if value and value not in features:
            features.append(value)
    return features


def _lead(record: dict[str, Any], generated: dict[str, Any]) -> str:
    name = record_name(record)
    hook = _hook(record, generated).rstrip(".")
    if not hook:
        return name
    if hook.lower().startswith(name.lower()):
        return hook
    return f"{name} — {hook[0].lower() + hook[1:] if len(hook) > 1 else hook.lower()}"


def _detail(record: dict[str, Any], generated: dict[str, Any]) -> str:
    summary = _sentences(_summary(record, generated), 220)
    hook = _hook(record, generated)
    if summary and summary.lower() not in hook.lower() and hook.lower() not in summary.lower():
        return summary
    for key in ("why_now", "verdict", "who_for"):
        value = clip_text(generated.get(key), 190)
        if value:
            return value
    features = _features(generated)
    return features[0] if features else summary


_RAW_TAG_ALIASES = {
    "agent-skills": "ai agents",
    "developer-tools": "devtools",
    "security-and-analysis": "security",
    "security and analysis": "security",
}

_TAG_WORD_SPECIAL = {
    "ai": "AI",
    "api": "API",
    "cli": "CLI",
    "css": "CSS",
    "devtools": "DevTools",
    "html": "HTML",
    "javascript": "JavaScript",
    "llm": "LLM",
    "mcp": "MCP",
    "ml": "ML",
    "opensource": "OpenSource",
    "typescript": "TypeScript",
    "ui": "UI",
    "ux": "UX",
}


def _tag_words(value: Any) -> list[str]:
    raw = clean_text(value).lower()
    raw = _RAW_TAG_ALIASES.get(raw, raw)
    raw = _LANG_TAG.get(raw, raw)
    return [word for word in re.split(r"[^a-z0-9]+", raw) if word]


def _camel_tag(value: Any) -> str:
    words = _tag_words(value)
    if not words:
        return ""
    if words == ["opensource"] or words == ["open", "source"]:
        return "OpenSource"
    return "".join(_TAG_WORD_SPECIAL.get(word, word[:1].upper() + word[1:]) for word in words)

def _lower_tag(value: Any) -> str:
    raw = clean_text(value).lower()
    raw = _LANG_TAG.get(raw, raw)
    return re.sub(r"[^a-z0-9_]+", "_", raw).strip("_")


def _raw_tag_values(record: dict[str, Any], generated: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for value in (
        record.get("language"),
        generated.get("category") or record.get("category"),
        *(record.get("topics") or []),
    ):
        text = clean_text(value)
        if (
            text
            and not _blocked_tag_value(text)
            and text.lower() not in _GENERIC_TOPICS
            and text not in values
        ):
            values.append(text)
    return values


def hashtags(
    record: dict[str, Any],
    generated: dict[str, Any] | None = None,
    *,
    limit: int = 4,
    camel: bool = True,
) -> str:
    generated = _generated(record, generated)
    formatter = _camel_tag if camel else _lower_tag
    tags: list[str] = []
    seen: set[str] = set()
    for value in ["open source", *_raw_tag_values(record, generated)]:
        tag = formatter(value)
        key = tag.lower()
        if tag and key not in seen:
            tags.append(tag)
            seen.add(key)
        if len(tags) >= limit:
            break
    return " ".join(f"#{tag}" for tag in tags[:limit])


def devto_tags(record: dict[str, Any], generated: dict[str, Any] | None = None) -> list[str]:
    generated = _generated(record, generated)
    tags: list[str] = []

    def add(value: Any) -> None:
        if _blocked_tag_value(value):
            return
        raw = clean_text(value).lower()
        raw = _DEVTO_TAG_ALIASES.get(raw, raw)
        tag = re.sub(r"[^a-z0-9]+", "", _LANG_TAG.get(raw, raw))[:30]
        if tag and tag not in tags:
            tags.append(tag)

    add("opensource")
    add(record.get("language"))
    add(generated.get("category") or record.get("category"))
    for topic in record.get("topics") or []:
        add(topic)
        if len(tags) >= 4:
            break
    if len(tags) < 4:
        add("GitHub")
    return tags[:4]


def image_alt_text(record: dict[str, Any], generated: dict[str, Any] | None = None) -> str:
    generated = _generated(record, generated)
    name = clean_text(record.get("full_name") or record_name(record))
    hook = clip_text(_hook(record, generated) or _summary(record, generated), 180)
    facts = [
        clean_text(record.get("language") or ""),
        license_name(record),
        f"⭐ {_fmt_num(record.get('stars'))}" if record.get("stars") is not None else "",
    ]
    fact_text = ", ".join(part for part in facts if part)
    kind = clean_text(record.get("visual_kind") or "")
    if kind == "readme":
        prefix = f"Demo or screenshot from the README of {name}."
    elif kind == "card":
        prefix = f"Branded cover card for {name}."
    elif kind == "opengraph":
        prefix = f"GitHub preview image for {name}."
    else:
        prefix = f"Cover image for {name}."
    return clip_text(" ".join(part for part in [prefix, hook, fact_text] if part), 420)


def _fit_parts(parts: list[str], limit: int) -> str:
    text = "\n\n".join(part for part in parts if part).strip()
    if len(text) <= limit:
        return text
    # Preserve the final link/tag block.  Shorten everything before it.
    if len(parts) < 2:
        return clip_text(text, limit)
    suffix = parts[-1]
    body = "\n\n".join(parts[:-1])
    budget = max(0, limit - len(suffix) - 2)
    return (clip_text(body, budget) + "\n\n" + suffix).strip()[:limit].rstrip()


def build_bluesky_text(record: dict[str, Any], generated: dict[str, Any] | None = None) -> str:
    """One complete Bluesky post: hook + detail + repo + Telegram loop.

    The Telegram return CTA outranks hashtags; both are included only
    when the 300-character budget leaves room for a useful body.
    """
    generated = _generated(record, generated)
    url = repo_url(record) or breakdown_url(record)
    tg_line = telegram_cta_line(record, compact=True)
    tags = hashtags(record, generated, limit=1, camel=True)
    suffix_candidates: list[str] = []
    if url and tg_line and tags:
        suffix_candidates.append(f"{url}\n{tg_line} {tags}")
    if url and tg_line:
        suffix_candidates.append(f"{url}\n{tg_line}")
    if url:
        suffix_candidates.append(url)
    elif tg_line:
        suffix_candidates.append(tg_line)
    suffix = next(
        (candidate for candidate in suffix_candidates if BLUESKY_LIMIT - len(candidate) - 2 >= 95),
        suffix_candidates[-1] if suffix_candidates else "",
    )

    # Headline written for this surface when we have one; the shared hook
    # is a Telegram sentence and reads like a manual in a feed.
    lead = surface_field(generated, "bluesky_headline")
    if not lead:
        lead = _lead(record, generated)
    icon = language_icon(record)
    if lead and icon and not surface_field(generated, "bluesky_headline"):
        lead = f"{icon} {lead}"
    body_budget = max(60, BLUESKY_LIMIT - len(suffix) - 2)
    lead = clip_text(lead, max(70, min(132, body_budget - 55)))
    remaining = body_budget - len(lead) - 2
    parts = [lead]
    if remaining >= 45:
        # Prefer a complete concrete detail over an ellipsized summary.
        candidates = [
            surface_field(generated, "bluesky_lead"),
            *(_features(generated)[:2]),
            clip_text(generated.get("verdict"), 180),
            clip_text(generated.get("social_text"), 180),
            clip_text(generated.get("why_now"), 190),
            _detail(record, generated),
        ]
        detail = ""
        for candidate in candidates:
            candidate = clean_text(candidate)
            if candidate and candidate.lower() not in lead.lower() and len(candidate) <= remaining:
                detail = candidate
                break
        if not detail:
            detail = clip_text(_detail(record, generated), remaining)
        if detail:
            parts.append(detail)
    if suffix:
        parts.append(suffix)
    return _fit_parts(parts, BLUESKY_LIMIT)


def build_Facebook_text(record: dict[str, Any], generated: dict[str, Any] | None = None) -> str:
    """Structured plain-text Facebook post with a Telegram return loop."""
    generated = _generated(record, generated)
    name = record_name(record)
    fb_headline = surface_field(generated, "Facebook_headline")
    fb_lead = surface_field(generated, "Facebook_lead")
    hook = fb_headline or _hook(record, generated)
    icon = language_icon(record)
    title = (
        f"{icon} {name} — {hook}"
        if hook and not hook.lower().startswith(name.lower())
        else f"{icon} {hook or name}"
    ).strip()
    proof_bits = [
        f"⭐ {_fmt_num(record.get('stars'))} stars" if record.get("stars") is not None else "",
        f"🍴 {_fmt_num(record.get('forks'))} forks" if record.get("forks") is not None else "",
        f"{icon} {clean_text(record.get('language') or '')}" if clean_text(record.get("language") or "") else "",
        f"📜 {license_name(record)}" if license_name(record) else "",
    ]
    proof = " · ".join(bit for bit in proof_bits if bit)
    # A body written for this surface beats reassembling shared fields:
    # Facebook readers are the least technical audience we have, and a
    # "WHAT IT DOES / WHAT STANDS OUT" wall of feature bullets is the exact
    # shape they scroll past.
    fb_body = surface_field(generated, "Facebook_body")
    summary = _sentences(fb_body or fb_lead or _summary(record, generated), 900)
    why = clip_text(generated.get("why_now") or generated.get("verdict"), 280)
    who = clip_text(generated.get("who_for"), 240)
    quick = str(generated.get("quick_start") or record.get("quick_start") or "").strip()
    quick_lines = "\n".join(line.rstrip() for line in quick.splitlines()[:5])[:360]
    question = clip_text(
        generated.get("discussion_question") or "Would you try this in a real project? Why?",
        180,
    )
    features = _features(generated)[:4]
    parts = [title]
    if proof:
        parts.append(proof)
    parts.append("━━━━━━━━━━━━")
    if record.get("is_dual_use") or generated.get("safety") == "dual_use":
        parts.append("⚠️ Dual-use security tool — shared for security research and education.")
    if summary:
        parts.append(f"🧭 WHAT IT DOES\n{summary}")
    if features and not fb_body:
        parts.append("✨ WHAT STANDS OUT\n" + "\n".join(f"• {feature}" for feature in features))
    if why:
        parts.append(f"📌 WHY IT MATTERS\n{why}")
    if who:
        parts.append(f"🎯 GOOD FIT FOR\n{who}")
    if quick_lines:
        parts.append("⚡ QUICK START\n" + quick_lines)

    final_lines = ["━━━━━━━━━━━━"]
    if question:
        final_lines.append(f"💬 {question}")
    final_lines.append(telegram_cta_line(record))
    url = repo_url(record)
    if url:
        final_lines.append(f"GitHub: {url}")
    breakdown = breakdown_url(record)
    if breakdown and breakdown != url:
        final_lines.append(f"Full breakdown: {breakdown}")
    tags = hashtags(record, generated, limit=3, camel=True)
    if tags:
        final_lines.append(tags)
    parts.append("\n".join(final_lines))

    text = "\n\n".join(part for part in parts if part).strip()
    if len(text) <= FACEBOOK_TARGET:
        return text
    # The final block (question + Telegram loop + links) is one part,
    # so fitting preserves the funnel instead of clipping it away.
    return _fit_parts(parts, FACEBOOK_TARGET)

def build_crosspost_text(
    platform: str,
    record: dict[str, Any],
    generated: dict[str, Any] | None = None,
) -> str:
    # Normalise hard: the caller passes "Facebook", the state file may hold
    # "facebook".  Comparing a lowercased key against a capitalised literal
    # is exactly how the Facebook branch used to be skipped, silently
    # degrading a 1 558-char post into a 169-char fallback.
    key = clean_text(platform).lower()
    if key == "bluesky":
        return build_bluesky_text(record, generated)
    if key == "facebook":
        return build_Facebook_text(record, generated)
    # Any future platform still has to carry the return loop: a crosspost
    # that does not point back to the channel is a donation, not growth.
    lead = clip_text(_lead(record, _generated(record, generated)), 380)
    return f"{lead}\n\n{telegram_cta_line(record)}"


def build_devto_title(record: dict[str, Any], generated: dict[str, Any] | None = None) -> str:
    generated = _generated(record, generated)
    name = record_name(record)
    hook = _hook(record, generated).rstrip(".")
    seo_title = surface_field(generated, "seo_title")
    if 35 <= len(seo_title) <= 70:
        return seo_title
    generated_title = clean_text(generated.get("article_title") or "")
    generic = "what it does and why" in generated_title.lower()
    if generated_title and not generic and len(generated_title) <= 125:
        return generated_title
    if hook and not hook.lower().startswith(name.lower()):
        candidate = f"{name}: {hook[0].upper() + hook[1:] if len(hook) > 1 else hook.upper()}"
        if len(candidate) <= 125:
            return candidate
    if generated_title and not generic:
        return clip_text(generated_title, 125)
    return clip_text(f"{name}: a practical open-source breakdown", 125)


def build_seo_description(record: dict[str, Any], generated: dict[str, Any] | None = None) -> str:
    """Meta description for the article page: 140-160 characters, plain words."""
    generated = _generated(record, generated)
    value = surface_field(generated, "seo_description") or _summary(record, generated)
    # A meta description that ends in "…" reads as a truncated snippet in
    # the search result, which is exactly the impression we are avoiding.
    # Cut on a sentence when possible, otherwise drop the ellipsis and end
    # on a whole word: Google truncates on its own, we do not hand it a stub.
    text = _sentences(value, 160) or clip_text(value, 160)
    return text.rstrip("… ").rstrip(",;:").strip()


def _strip_leading_h1(markdown: str) -> str:
    lines = markdown.strip().splitlines()
    if lines and lines[0].lstrip().startswith("# "):
        lines = lines[1:]
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


ARTICLE_SECTIONS = (
    "What it is", "Why it matters", "How it works", "Key features",
    "Quick start", "Getting started", "When not to use it",
    "Who should try it", "Verdict",
)


def normalize_article_markdown(text: Any) -> str:
    """Turn an LLM article into readable Markdown, even if it was flattened.

    Gemini sometimes returns one long line (``## What it is Horizon is ...``),
    and older saved surfaces were flattened by a whitespace-collapsing
    cleaner. Restore H2 headings, paragraph breaks and bullet lists so dev.to
    renders sections instead of a single block of text.
    """
    raw = str(text or "").replace("\\#\\#", "##").replace("\\#", "#")
    # A heading marker must start its own line, with air around it.
    raw = re.sub(r"\s+#{2,3}\s+", "\n\n## ", raw)
    raw = re.sub(
        r"(?m)^(##\s*)(%s)\b[ \t]*" % "|".join(re.escape(x) for x in ARTICLE_SECTIONS),
        lambda m: f"\n\n## {m.group(2)}\n\n",
        raw,
    )
    # " - " separated feature lists become real Markdown bullets.
    raw = re.sub(r"\s+-\s+", "\n- ", raw)
    raw = re.sub(r"(?m)^(##[^\n]*)\n(?!\n)", r"\1\n\n", raw)
    lines = [line.rstrip() for line in raw.splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def build_devto_body(record: dict[str, Any], generated: dict[str, Any] | None = None) -> str:
    generated = _generated(record, generated)
    # dev.to publishes its own technical article. Telegraph keeps the shared
    # editorial breakdown, so the two indexed pages do not target the same
    # query — the SEO agent's cannibalization rule. When the per-surface
    # call is unavailable we fall back to the shared article rather than
    # publishing nothing.
    devto_body = surface_markdown(generated, "devto_body")
    markdown = normalize_article_markdown(
        _strip_leading_h1(str(devto_body or generated.get("article_markdown") or ""))
    )
    summary = _sentences(_summary(record, generated), 330)
    who = clip_text(generated.get("who_for"), 220)
    proof_bits = [
        f"⭐ {_fmt_num(record.get('stars'))} stars" if record.get("stars") is not None else "",
        f"🍴 {_fmt_num(record.get('forks'))} forks" if record.get("forks") is not None else "",
        clean_text(record.get("language") or ""),
        license_name(record),
    ]
    proof = " · ".join(bit for bit in proof_bits if bit)
    intro_lines = []
    if summary:
        intro_lines.append(f"> **TL;DR:** {summary}")
    if who:
        intro_lines.append(f"> **Best for:** {who}")
    if proof:
        intro_lines.append(f"> **Repository snapshot:** {proof}")
    intro_lines.append(
        f"> **Daily picks:** [Follow GitHub Open Source on Telegram]({TELEGRAM_CHANNEL_URL})"
    )
    links = []
    if public_telegram_link(record):
        links.append(f"- [Discuss this pick on Telegram]({telegram_cta_url(record)})")
    else:
        links.append(f"- [More open-source finds on Telegram]({TELEGRAM_CHANNEL_URL})")
    url = repo_url(record)
    if url:
        links.append(f"- [GitHub repository]({url})")
    breakdown = breakdown_url(record)
    if breakdown and breakdown != url:
        links.append(f"- [Full breakdown]({breakdown})")
    question = clip_text(
        generated.get("discussion_question") or "Would you use this in production? Why?",
        190,
    )
    footer = (
        "\n\n---\n\n## Discuss\n\n"
        f"{question}\n\n"
        + "\n".join(links)
        + "\n\n*Curated from the repository README and metadata. "
        "No hands-on test is claimed; evaluate it in a sandbox before production use.*\n"
    )
    intro = "\n".join(intro_lines).strip()
    # A rule after the TL;DR block and before the first H2 keeps the article
    # scannable: summary, body with real sections, then the discussion block.
    return (intro + "\n\n---\n\n" if intro else "") + markdown + footer
