"""v6 — long-form Telegram project post formatter (free, no caption limit).

Why this exists
---------------
A photo caption in Telegram is hard-capped at 1,024 characters.
A text message allows 4,096.  v6 therefore publishes projects as
*text messages* with a large link preview shown above the text
(Bot API ``link_preview_options``), which gives ~4x more room than a
caption while keeping a big visual on top of the post — for free.

Expected project dict keys (superset of the current collector output):
    name, description, url, homepage, language, stars, forks,
    open_issues, contributors_count, topics, license (str | dict),
    last_commit_date | pushed_at | updated_at (ISO-8601),
    primary_use_case, key_features (list[str]), readme_summary,
and optionally (new Gemini schema fields, all optional):
    hook, who_for, why_now, quick_start, verdict, discussion_question

The formatter is pure: no I/O, no network, no templates on disk.
All untrusted strings are HTML-escaped; only https:// links survive.
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any

#: Telegram counts UTF-16 code units of the *visible* text after entity
#: parsing.  Keep a safety margin below the 4,096 hard limit.
MAX_VISIBLE_UTF16 = 4000

_CATEGORY_EMOJI = [
    (("ai", "llm", "machine-learning", "ml", "agent"), "🤖"),
    (("cli", "terminal", "tui", "shell"), "🖥"),
    (("security", "pentest", "vulnerability"), "🛡️"),
    (("browser", "headless", "playwright", "puppeteer"), "🌐"),
    (("web", "frontend", "react", "vue", "api", "backend"), "🌐"),
    (("data", "database", "analytics", "etl"), "📊"),
    (("game", "gamedev", "unity", "godot"), "🎮"),
    (("android", "ios", "mobile", "flutter"), "📱"),
    (("devops", "docker", "kubernetes", "self-hosted", "selfhosted"), "⚙️"),
]

_LANG_HASHTAG = {"c++": "cpp", "c#": "csharp", "f#": "fsharp", "objective-c": "objc"}

#: One consistent icon per language/stack.  Icons are scanning aids,
#: never the only carrier of meaning: the language name stays in text.
_LANGUAGE_ICON = {
    "python": "🐍",
    "javascript": "🟨",
    "typescript": "🔷",
    "rust": "🦀",
    "go": "🐹",
    "java": "☕",
    "kotlin": "🟣",
    "swift": "🕊️",
    "dart": "🎯",
    "php": "🐘",
    "ruby": "💎",
    "c": "🔵",
    "c++": "➕",
    "c#": "♯",
    "shell": "🐚",
    "bash": "🐚",
    "html": "🌐",
    "css": "🎨",
    "vue": "💚",
    "svelte": "🧡",
    "lua": "🌙",
    "r": "📊",
    "scala": "🔴",
    "haskell": "λ",
    "elixir": "💧",
    "erlang": "📡",
    "zig": "⚡",
    "solidity": "⛓️",
    "dockerfile": "🐳",
    "jupyter notebook": "📓",
    "notebook": "📓",
}

_DIVIDER = "──────────────"
#: Visual break between the channel signature and the discovery hashtags.
#: The owner asked for a clear separator so the tags do not read as part of
#: the channel line when a post is forwarded or screenshotted.
CHANNEL_TAG_DIVIDER = "========================================"
_CHANNEL_SIGNATURE = (
    '📣 <a href="https://t.me/GitHub_Open_Source"><b>@GitHub_Open_Source</b></a> '
    "— daily open-source picks"
)
_PRIORITY_TOPICS = [
    "ai",
    "llm",
    "mcp",
    "cli",
    "terminal",
    "developer-tools",
    "devtools",
    "security",
    "self-hosted",
    "selfhosted",
    "api",
    "database",
    "web",
    "frontend",
    "backend",
    "devops",
    "automation",
    "browser",
    "browser-automation",
    "headless",
    "headless-browser",
    "web-scraping",
    "playwright",
    "puppeteer",
]

_CATEGORY_TAG_ALIASES = {
    "developer_tools": "devtools",
    "developer-tools": "devtools",
    "security_and_analysis": "security",
}
_BLOCKED_HASHTAG_TERMS = ("antidetect", "anti_detect", "undetected", "stealth", "bypass")


def _esc(value: Any) -> str:
    return html.escape(str(value or "").strip(), quote=False)


def _safe_url(value: Any) -> str:
    url = str(value or "").strip()
    return url if url.startswith("https://") else ""


def _fmt_num(value: Any) -> str:
    try:
        return f"{int(value):,}".replace(",", " ")
    except (TypeError, ValueError):
        return "0"


def _license_name(project: dict[str, Any]) -> str:
    lic = project.get("license")
    if isinstance(lic, dict):
        return str(lic.get("spdx_id") or lic.get("name") or "").strip()
    return str(lic or "").strip()


def _activity_parts(project: dict[str, Any]) -> tuple[str, str]:
    raw = project.get("last_commit_date") or project.get("pushed_at") or project.get("updated_at")
    if not raw:
        return "", ""
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return "", ""
    days = max(0, (datetime.now(timezone.utc) - dt).days)
    if days == 0:
        return "🟢", "commit today"
    if days == 1:
        return "🟢", "commit yesterday"
    if days < 30:
        return "🟢", f"commit {days}d ago"
    if days < 365:
        return "🟡", f"commit {days // 30}mo ago"
    return "🔴", f"commit {days // 365}y ago"


def _activity(project: dict[str, Any]) -> str:
    icon, label = _activity_parts(project)
    return f"{icon} {label}" if label else ""

def _emoji(project: dict[str, Any]) -> str:
    haystack = " ".join(
        [str(project.get("name", "")), str(project.get("language", ""))]
        + [str(t) for t in project.get("topics") or []]
    ).lower()
    for keys, emoji in _CATEGORY_EMOJI:
        if any(k in haystack for k in keys):
            return emoji
    return "🚀"


def _hashtags(project: dict[str, Any], limit: int = 4) -> str:
    """Telegram tags: four maximum, searchable topics before niche ones."""
    tags: list[str] = ["opensource"]

    def add(raw: Any) -> None:
        tag = re.sub(r"[^a-zA-Z0-9_]+", "_", str(raw).strip().lower()).strip("_")
        if any(term in tag for term in _BLOCKED_HASHTAG_TERMS):
            return  # never use evasion terms as discovery hashtags
        if tag and tag not in tags and tag != "open_source" and len(tags) < limit:
            tags.append(tag)

    lang = str(project.get("language") or "").strip().lower()
    if lang:
        add(_LANG_HASHTAG.get(lang, re.sub(r"[^a-z0-9]+", "", lang)))
    category = str(project.get("category") or "").strip()
    if category:
        category_key = re.sub(r"[^a-zA-Z0-9_]+", "_", category.lower()).strip("_")
        add(_CATEGORY_TAG_ALIASES.get(category_key, category_key))
    topics = [str(topic).strip().lower() for topic in project.get("topics") or []]
    for priority in _PRIORITY_TOPICS:
        if priority in topics:
            add(priority)
    for topic in topics:
        add(topic)
    return " ".join(f"#{t}" for t in tags[:limit])

def visible_text(rendered_html: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", rendered_html)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text)


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:limit]
    cut = text[: limit - 1].rstrip()
    if ". " in cut:
        sentence = cut.rsplit(". ", 1)[0]
        if len(sentence) > limit * 0.5:
            return sentence.rstrip(".") + "."
    word = cut.rsplit(" ", 1)[0]
    return (word or cut).rstrip(" ,;:") + "…"


def _language_icon(project: dict[str, Any]) -> str:
    lang = str(project.get("language") or "").strip().lower()
    if lang in _LANGUAGE_ICON:
        return _LANGUAGE_ICON[lang]
    topics = " ".join(str(topic).lower() for topic in project.get("topics") or [])
    if "docker" in topics or "kubernetes" in topics:
        return "🐳"
    return "💻"


def _is_placeholder_clone(command: str) -> bool:
    """True when the "install" line is only ``git clone`` with nothing else."""
    value = str(command or "").strip().lower()
    return value.startswith("git clone") and "&&" not in value and ";" not in value


_ALLOWED_INSTALL_PREFIXES = (
    "pip install",
    "pip3 install",
    "pipx install",
    "uv tool install",
    "npm install",
    "npm i ",
    "pnpm add",
    "yarn add",
    "cargo install",
    "go install",
    "brew install",
    "docker run",
    "docker pull",
    "git clone https://github.com/",
    "gh repo clone",
)

_DANGEROUS_INSTALL_RE = re.compile(
    r"\b(?:curl|wget)\b|\|\s*(?:ba|z|k)?sh\b|\beval\b|\bbase64\s+-d\b|\biex\b|\binvoke-expression\b",
    re.I,
)


def _quick_start_belongs_in_post(command: str) -> bool:
    """Should the install line sit in the post itself?

    Rule from the editorial agents: the post earns its place with a story
    and a judgement, not with a manual. A single short command is a useful
    proof ("this really is one command"); a wall of clone-and-compose is
    noise that pushes the verdict off the first screen.

    Also: ``git clone`` is what we print when the README documents nothing.
    It tells the reader nothing they do not already know, so it never takes
    a line of the post.

    Security: only known package-manager commands are allowed. A shell
    download (``curl … | sh``) or an arbitrary URL never reaches the post.
    """
    value = str(command or "").strip()
    if not value:
        return False
    if len(value) > 90 or value.count("\n") > 1:
        return False
    if _DANGEROUS_INSTALL_RE.search(value):
        return False
    lowered = value.lower()
    if lowered.startswith("git clone") and "&&" not in lowered and ";" not in lowered:
        return False  # placeholder, not an instruction
    return any(lowered.startswith(prefix) for prefix in _ALLOWED_INSTALL_PREFIXES)


def _story_html(project: dict[str, Any]) -> str:
    """The readable spine of the post: situation, cost, turn.

    A live channel post is read as a story or skipped as a spec sheet.
    These three beats come from the editor model (``scene``/``tension``/
    ``turn``); when they are missing the block is simply absent rather
    than faked, so a deterministic fallback never invents drama.
    """
    beats = []
    for key in ("scene", "tension", "turn"):
        value = str(project.get(key) or "").strip()
        if value:
            beats.append(_esc(value))
    if not beats:
        return ""
    if len(beats) == 1:
        return beats[0]
    return "\n\n".join(beats)


def _paragraphs(text: str) -> list[str]:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]
    if len(sentences) <= 2:
        return [text] if text else []
    first = " ".join(sentences[:2])
    rest = " ".join(sentences[2:])
    return [part for part in [first, rest] if part]


def _feature_html(feature: str) -> str:
    """Bold a real lead-in (``Term: rest``); otherwise keep clean text."""
    value = str(feature or "").strip()
    if ":" in value:
        lead, rest = value.split(":", 1)
        if 2 <= len(lead.strip()) <= 42 and rest.strip():
            return f"<b>{_esc(lead.strip())}:</b> {_esc(rest.strip())}"
    return _esc(value)


def _engagement_cta(project: dict[str, Any]) -> str:
    """One rotating engagement ask: react, forward, or save/comment.

    No star-begging for third-party repos: the channel's feedback loop
    is reactions, comments and forwards on *our* post.
    """
    import hashlib

    seed_source = str(project.get("full_name") or project.get("name") or "project")
    variant = int(hashlib.sha256(seed_source.encode("utf-8")).hexdigest()[:2], 16) % 3
    language = str(project.get("language") or "").strip()
    if variant == 0:
        label = f" <b>{_esc(language)}</b>" if language else ""
        return f"❤️ <b>React</b> if you want more{label} tools like this."
    if variant == 1:
        return "↗️ <b>Forward</b> this to a dev who should see it."
    return "🔖 <b>Save</b> it for later — and drop your use case in the comments."

def format_project_post(
    project: dict[str, Any],
    *,
    telegraph_url: str = "",
    expandable_deep_dive: bool = False,
    max_visible: int = MAX_VISIBLE_UTF16,
) -> str:
    """Render one project as Telegram HTML text (<= ``max_visible``).

    Layout v2 is componentized, not a wall of text:
    header (repo + path) -> bold hook -> divider -> icon proof lines ->
    What it does -> Key features -> visible short Quick start ->
    judgement sections -> divider -> engagement -> links.

    The judgement block (who/why/verdict) is open by default: the owner
    wants the whole post readable without a tap.  ``expandable_deep_dive``
    still renders Telegram's collapsible blockquote when a slot must stay
    short.
    """
    name_raw = str(project.get("name") or "Unknown project")
    name = _esc(name_raw)
    full_name_raw = str(project.get("full_name") or "")
    hook = _clip(str(project.get("hook") or project.get("description") or ""), 150)
    url = _safe_url(project.get("url") or project.get("html_url"))
    homepage = _safe_url(project.get("homepage"))
    telegraph_url = _safe_url(telegraph_url)

    lang = str(project.get("language") or "").strip()
    lic = _license_name(project)
    activity_icon, activity_label = _activity_parts(project)
    meta_lines = [
        f"⭐ <b>{_fmt_num(project.get('stars'))}</b> stars · 🍴 <b>{_fmt_num(project.get('forks'))}</b> forks"
    ]
    stack_bits: list[str] = []
    if lang:
        stack_bits.append(f"{_language_icon(project)} <b>{_esc(lang)}</b>")
    if lic:
        stack_bits.append(f"📜 <b>{_esc(lic)}</b>")
    if activity_label:
        stack_bits.append(f"{activity_icon} <b>{_esc(activity_label)}</b>")
    if stack_bits:
        meta_lines.append(" · ".join(stack_bits))
    try:
        contributors = int(project.get("contributors_count") or 0)
    except (TypeError, ValueError):
        contributors = 0
    if contributors >= 3:
        meta_lines.append(f"👥 <b>{_fmt_num(contributors)}</b> contributors")

    features = [_clip(str(f), 150) for f in (project.get("key_features") or []) if str(f).strip()][:6]
    summary = _clip(str(project.get("readme_summary") or project.get("primary_use_case") or ""), 650)
    quick_raw = str(project.get("quick_start") or "").strip()[:400]
    quick_visible_default = _quick_start_belongs_in_post(quick_raw)

    deep_parts: list[tuple[str, str, str, bool]] = []
    if project.get("who_for"):
        deep_parts.append(("🎯", "Who should try it", _clip(str(project["who_for"]), 300), False))
    if project.get("why_now"):
        deep_parts.append(("📌", "Why it matters now", _clip(str(project["why_now"]), 300), False))
    if project.get("verdict"):
        deep_parts.append(("✅", "Verdict", _clip(str(project["verdict"]), 280), False))
    # A placeholder clone command is not worth a line anywhere in the post,
    # collapsed or not: the README button is one tap away.
    if quick_raw and not quick_visible_default and not _is_placeholder_clone(quick_raw):
        deep_parts.append(("🚀", "Quick start", quick_raw, True))

    links: list[str] = []
    if url:
        links.append(f'🔗 <a href="{html.escape(url, quote=True)}">GitHub repository</a>')
    if telegraph_url:
        links.append(f'📖 <a href="{html.escape(telegraph_url, quote=True)}">Full breakdown</a>')
    if homepage and homepage != url:
        links.append(f'🌐 <a href="{html.escape(homepage, quote=True)}">Project homepage</a>')

    question = _clip(
        str(project.get("discussion_question") or "Would you use this in production? Tell us in the comments ↓"),
        170,
    )
    cta = _engagement_cta(project)

    def render(
        feats: list[str],
        deep: list[tuple[str, str, str, bool]],
        summ: str,
        show_quick: bool,
    ) -> str:
        header = f"{_emoji(project)} <b>{name}</b>"
        if full_name_raw and full_name_raw.lower() != name_raw.lower():
            header += f" · 📦 <code>{_esc(full_name_raw)}</code>"
        lines: list[str] = [header]
        if hook:
            lines.append(f"<b>{_esc(hook)}</b>")
        # "Why now" answers the reader's first question — "why should I
        # care today?" — before any feature bullets.  Kept short so it
        # never becomes a second hook.
        # When the story block is present its tension beat already answers
        # "why should I care today?", and `why_now` still appears in the
        # expandable block. Printing both made the post say it three times.
        story = _story_html(project)
        why_now_text = "" if story else _clip(str(project.get("why_now") or ""), 180)
        if why_now_text:
            lines += ["", f"⏱ <b>Why now:</b> {_esc(why_now_text)}"]
        lines.append(_DIVIDER)
        lines.extend(meta_lines)
        if project.get("is_dual_use"):
            lines += [
                "",
                "⚠️ <b>Dual-use security tool</b>",
                "For research &amp; education. Use only on software you own or are authorized to analyze.",
            ]
        if story:
            lines += ["", story]
        if summ:
            heading = "🧭 <b>How it works</b>" if story else "🧭 <b>What it does</b>"
            lines += ["", heading]
            for index, paragraph in enumerate(_paragraphs(summ)):
                if index:
                    lines.append("")
                lines.append(_esc(paragraph))
        if feats:
            lines += ["", "✨ <b>Key features</b>"] + [f"▸ {_feature_html(f)}" for f in feats]
        if show_quick and quick_raw:
            lines += ["", "⚡ <b>Quick start</b>", f"<code>{_esc(quick_raw)}</code>"]
        if deep:
            rendered_deep: list[str] = []
            for icon, title, body, is_code in deep:
                body_html = f"<code>{_esc(body)}</code>" if is_code else _esc(body)
                rendered_deep.append(f"{icon} <b>{title}</b>\n{body_html}")
            body = "\n\n".join(rendered_deep)
            if expandable_deep_dive:
                # Collapsed blockquote: a tap hides the verdict from most
                # readers, so this is opt-in for tight slots only.
                lines += ["", f"<blockquote expandable>{body}</blockquote>"]
            else:
                # Plain paragraphs, no quote frame: the judgement
                # (who / why / verdict / quick start) stays readable.
                lines += ["", body]
        lines += ["", _DIVIDER, "💬 <b>Your take?</b>", f"<i>{_esc(question)}</i>", cta]
        if links:
            lines += ["", *links]
        # Signature is the durable path back to the channel when a post
        # is forwarded, copied or screenshotted without its buttons.
        lines += ["", _CHANNEL_SIGNATURE]
        ht = _hashtags(project)
        if ht:
            lines += ["", CHANNEL_TAG_DIVIDER, ht]
        return "\n".join(lines)

    show_quick = quick_visible_default
    out = render(features, deep_parts, summary, show_quick)
    # Trim least-valuable material first; keep header, proof and CTA intact.
    while utf16_len(visible_text(out)) > max_visible and len(features) > 3:
        features.pop()
        out = render(features, deep_parts, summary, show_quick)
    while utf16_len(visible_text(out)) > max_visible and show_quick:
        show_quick = False
        out = render(features, deep_parts, summary, show_quick)
    while utf16_len(visible_text(out)) > max_visible and deep_parts:
        deep_parts.pop()
        out = render(features, deep_parts, summary, show_quick)
    while utf16_len(visible_text(out)) > max_visible and summary:
        shorter = max(80, len(summary) - 150)
        if shorter >= len(summary):
            break  # cannot shrink further; the hard fallback below applies
        summary = _clip(summary, shorter)
        out = render(features, deep_parts, summary, show_quick)
    if utf16_len(visible_text(out)) > max_visible:  # pathological metadata; hard fallback
        out = render(features[:3], [], _clip(summary, 200), False)
    return out


CAPTION_LIMIT = 1024  # Telegram's hard limit for photo captions


def format_project_caption(project: dict[str, Any], *, limit: int = CAPTION_LIMIT) -> str:
    """Photo caption that fits Telegram's 1024-character ceiling.

    A photo post and a text post are not the same medium: anything longer
    than ``CAPTION_LIMIT`` cannot be sent as a caption at all, and the old
    pipeline fell back to a link preview — which is displayed even smaller
    than the card itself. The caption therefore keeps only what a reader
    needs to decide whether to open the card: name, hook, proof line and a
    question. The full breakdown lives behind the button.
    """
    name = _esc(str(project.get("name") or "Unknown project"))
    hook = _esc(_clip(str(project.get("hook") or project.get("description") or ""), 190))
    lang = str(project.get("language") or "").strip()
    lic = _license_name(project)
    activity_icon, activity_label = _activity_parts(project)
    stats = (
        f"⭐ <b>{_fmt_num(project.get('stars'))}</b> stars · "
        f"🍴 <b>{_fmt_num(project.get('forks'))}</b> forks"
    )
    stack_bits = []
    if lang:
        stack_bits.append(f"{_language_icon(project)} <b>{_esc(lang)}</b>")
    if lic:
        stack_bits.append(f"📜 <b>{_esc(lic)}</b>")
    if activity_label:
        stack_bits.append(f"{activity_icon} <b>{_esc(activity_label)}</b>")
    parts = [f"🚀 <b>{name}</b>"]
    if hook:
        parts.append(f"<b>{hook}</b>")
    parts.append("──────────────")
    parts.append(stats)
    if stack_bits:
        parts.append(" · ".join(stack_bits))
    features = [_clip(str(f), 110) for f in (project.get("key_features") or []) if str(f).strip()][:3]
    if features:
        parts.append("")
        parts.append("✨ <b>What stands out</b>")
        parts.extend(f"▸ {_feature_html(feature)}" for feature in features)
    question = _clip(str(project.get("discussion_question") or ""), 150)
    if question:
        parts.append("")
        parts.append(f"💬 <i>{_esc(question)}</i>")
    parts.append("")
    parts.append('📣 <a href="https://t.me/GitHub_Open_Source"><b>@GitHub_Open_Source</b></a> — daily open-source picks')
    caption = "\n".join(parts)
    if utf16_len(visible_text(caption)) <= limit:
        return caption
    # Trim bullets first, then features, never the footer.
    while features and utf16_len(visible_text(caption)) > limit:
        features = features[:-1]
        parts = [p for p in parts if not str(p).startswith("▸ ")]
        if features:
            insert_at = parts.index("✨ <b>What stands out</b>") + 1
            for offset, feature in enumerate(features):
                parts.insert(insert_at + offset, f"▸ {_feature_html(feature)}")
        else:
            parts = [p for p in parts if p != "✨ <b>What stands out</b>"]
        caption = "\n".join(parts)
    return caption
