"""Production card renderer for the adopted v6 design system.

Primary: Editorial Daily. Neon Terminal is used only for CLI/TUI
projects. Glass Aurora is reserved for AI Brief. The Sunday digest uses
an Editorial "Sunday Edition" variant.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .gate import parse_dt

W, H = 1200, 630
BRAND = "@GitHub_Open_Source"
ICON_DIR = Path(__file__).resolve().parent / "assets" / "icons"
_ICON_CACHE: dict[tuple[str, int], Image.Image | None] = {}

# GitHub's language names → Simple Icons slugs (files were baked into
# assets/icons at build time; runtime never touches the network).
_LANGUAGE_ICONS = {
    "rust": "rust", "python": "python", "typescript": "typescript",
    "javascript": "javascript", "go": "go", "c++": "cplusplus", "c": "c",
    "java": "openjdk", "swift": "swift", "php": "php",
    "ruby": "ruby", "shell": "gnubash", "bash": "gnubash", "html": "html5",
    "css": "css", "c#": "sharp",
    "objective-c": "apple",
    "kotlin": "kotlin", "zig": "zig", "lua": "lua", "dart": "dart",
    "scala": "scala", "elixir": "elixir", "haskell": "haskell", "r": "r",
    "julia": "julia", "perl": "perl", "vue": "vuedotjs", "svelte": "svelte",
}
# topic → stack icon, so a card can show "what is inside" without a changelog
_STACK_ICONS = {
    "docker": "docker", "kubernetes": "kubernetes", "postgresql": "postgresql",
    "postgres": "postgresql", "sqlite": "sqlite", "redis": "redis",
    "mongodb": "mongodb", "react": "react", "nodejs": "nodedotjs",
    "node": "nodedotjs", "deno": "deno", "bun": "bun", "tailwindcss": "tailwindcss",
    "nextjs": "nextdotjs", "vite": "vite", "webpack": "webpack",
    "terraform": "terraform", "ansible": "ansible", "nginx": "nginx",
    "graphql": "graphql", "prisma": "prisma", "supabase": "supabase",
    "firebase": "firebase", "electron": "electron", "tauri": "tauri",
    "flutter": "flutter", "gtk": "gtk", "qt": "qt", "opencv": "opencv",
    "pytorch": "pytorch", "tensorflow": "tensorflow", "numpy": "numpy",
    "pandas": "pandas", "fastapi": "fastapi", "django": "django", "flask": "flask",
    "spring": "spring", "dotnet": "dotnet", "laravel": "laravel", "rails": "rubyonrails",
}
# Human labels for the stack row: a card shows "K8s", never "Kubernetes".
_STACK_LABELS = {
    "kubernetes": "K8s", "postgresql": "Postgres", "tailwindcss": "Tailwind",
    "nextjs": "Next.js", "nodejs": "Node", "node": "Node", "vuedotjs": "Vue",
    "rubyonrails": "Rails", "graphql": "GraphQL", "nodemon": "Node", "dotnet": ".NET",
    "mongodb": "MongoDB", "tensorflow": "TensorFlow", "openjdk": "Java",
}
_FONT_DIRS = [
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/truetype/liberation"),
    Path("/usr/share/fonts/truetype/liberation2"),
    Path("/usr/share/fonts/truetype/freefont"),
    Path("/usr/share/fonts/truetype/noto"),
    Path("/usr/local/share/fonts"),
]
_FONT_FILES = {
    ("sans", False): ["DejaVuSans.ttf", "LiberationSans-Regular.ttf"],
    ("sans", True): ["DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"],
    ("serif", False): ["DejaVuSerif.ttf", "LiberationSerif-Regular.ttf"],
    ("serif", True): ["DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf"],
    ("mono", False): ["DejaVuSansMono.ttf", "LiberationMono-Regular.ttf"],
    ("mono", True): ["DejaVuSansMono-Bold.ttf", "LiberationMono-Bold.ttf"],
    ("serif-condensed", True): ["DejaVuSerifCondensed-Bold.ttf", "LiberationSerif-Bold.ttf"],
}
_FONT_CACHE: dict[tuple[str, int, bool], object] = {}


def _slug_file(kind: str, name: str, sub: str = "") -> Path:
    safe = name.replace("+", "p").replace("#", "s").replace(".", "_")
    return ICON_DIR / sub / f"{kind}_{safe}.png" if sub else ICON_DIR / f"{kind}_{safe}.png"


def load_brand_icon(name: str, size: int) -> Image.Image | None:
    """Brand icon for a language/tool, or None when we have no artwork."""
    key = (f"brand:{name}", size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    path = _slug_file("brand", name)
    icon: Image.Image | None = None
    if path.exists():
        try:
            icon = Image.open(path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        except OSError:
            icon = None
    _ICON_CACHE[key] = icon
    return icon


def load_colour_icon(name: str, size: int) -> Image.Image | None:
    """Brand icon in its own brand colour (already darkened for cream paper)."""
    key = (f"colour:{name}", size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    path = _slug_file("brand", name, sub="colour")
    icon: Image.Image | None = None
    if path.exists():
        try:
            icon = Image.open(path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        except OSError:
            icon = None
    _ICON_CACHE[key] = icon
    return icon or load_brand_icon(name, size)


def load_ui_icon(name: str, size: int) -> Image.Image | None:
    """Hand-drawn system icon (star, fork, people, license, clock, trend)."""
    key = (f"ui:{name}", size)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]
    path = _slug_file("ui", name)
    icon: Image.Image | None = None
    if path.exists():
        try:
            icon = Image.open(path).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
        except OSError:
            icon = None
    _ICON_CACHE[key] = icon
    return icon


def language_icon(language: str, size: int) -> Image.Image | None:
    slug = _LANGUAGE_ICONS.get(str(language or "").strip().lower())
    return load_brand_icon(slug, size) if slug else None


def stack_icons(candidate: dict[str, Any], limit: int = 4) -> list[tuple[str, Image.Image]]:
    """Best-effort "built with" row: language first, then detected tools."""
    return [(label, icon) for label, _slug, icon, _colour in stack_entries(candidate, limit)]


def stack_entries(
    candidate: dict[str, Any], limit: int = 4, size: int = 34
) -> list[tuple[str, str, Image.Image, Image.Image | None]]:
    """``[(label, slug, ink_icon, colour_icon)]`` for the stack row."""
    seen: set[str] = set()
    found: list[tuple[str, str, Image.Image, Image.Image | None]] = []

    def add(label: str, slug: str) -> None:
        if not slug or slug in seen or len(found) >= limit:
            return
        icon = load_brand_icon(slug, size)
        if icon is not None:
            seen.add(slug)
            found.append((label, slug, icon, load_colour_icon(slug, size)))

    language = str(candidate.get("language") or "")
    add(language or "", _LANGUAGE_ICONS.get(language.strip().lower(), ""))
    haystack = " ".join(
        [str(candidate.get("description") or ""), " ".join(candidate.get("topics") or [])]
    ).lower()
    for key, slug in _STACK_ICONS.items():
        if key in haystack:
            add(_STACK_LABELS.get(key, key.capitalize()), slug)
    return found


def _glyph_safe(text: str, font) -> str:
    """Replace characters this font cannot draw (em dash in Liberation, emoji).

    Drawing an unsupported character produces a hollow tofu box, which reads
    as a placeholder bug on a printed card.
    """
    if not text:
        return text
    return "".join(ch if ch.isspace() or _has_glyph(font, ch) else "-" for ch in text)


def _has_glyph(font, char: str) -> bool:
    """True when the font actually has a glyph for ``char``.

    DejaVu/Liberation cover Latin and punctuation but not emoji; drawing
    an unsupported character silently produces a hollow box on the card.
    """
    if not char:
        return True
    try:
        mask = font.getmask(char)
        reference = font.getmask("\uffff")
        # DejaVu draws the same hollow "missing glyph" box for every
        # character it does not have, so identical pixels mean "no glyph".
        return mask.size != reference.size or mask.getbbox() != reference.getbbox()
    except Exception:  # noqa: BLE001
        return False


def _text_font(kind: str, size: int, bold: bool, text: str):
    """Pick the requested font, but never one that boxes out characters."""
    font = _font(kind, size, bold)
    if all(_has_glyph(font, ch) for ch in text if not ch.isspace()):
        return font
    for fallback_kind in ("sans", "mono", "serif"):
        candidate = _font(fallback_kind, size, bold)
        if all(_has_glyph(candidate, ch) for ch in text if not ch.isspace()):
            return candidate
    return font


def _font(kind: str = "sans", size: int = 32, bold: bool = False):
    cached = _FONT_CACHE.get((kind, size, bold))
    if cached is not None:
        return cached
    for directory in _FONT_DIRS:
        for filename in _FONT_FILES.get((kind, bold), []):
            path = directory / filename
            if path.exists():
                _FONT_CACHE[(kind, size, bold)] = ImageFont.truetype(str(path), size)
                return _FONT_CACHE[(kind, size, bold)]
    try:
        fallback = ImageFont.load_default(size=size)
    except TypeError:
        fallback = ImageFont.load_default()
    _FONT_CACHE[(kind, size, bold)] = fallback
    return fallback


def _fit(draw: ImageDraw.ImageDraw, text: str, kind: str, size: int, max_width: int, bold: bool = True, minimum: int = 30):
    current = size
    while current > minimum and draw.textlength(text, font=_font(kind, current, bold)) > max_width:
        current -= 4
    return _font(kind, current, bold)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int = 3) -> list[str]:
    """Greedy word wrap that never invents a trailing ellipsis.

    Callers that must not lose words should use ``_fit_block``, which
    shrinks the font instead of cutting the sentence.
    """
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=font) <= max_width:
            current = candidate
            continue
        if current:
            lines.append(current)
            if len(lines) >= max_lines:
                return lines
        current = word
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines


def _fits(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> bool:
    return all(draw.textlength(word, font=font) <= max_width for word in str(text or "").split())


def _fit_block(
    draw: ImageDraw.ImageDraw,
    text: str,
    kind: str,
    size: int,
    max_width: int,
    max_lines: int,
    bold: bool = True,
    minimum: int = 18,
) -> tuple[object, list[str]]:
    """Find the largest font size that fits ``text`` in full.

    Unlike ``_wrap`` this never truncates: it reduces the type size
    until every word fits inside ``max_lines``.  A headline that ends
    in "…" reads like an unfinished sentence and performs badly in a
    feed, so the card must either show the whole line or the caller
    must hand over shorter copy (``card_hook``).
    """
    text = " ".join(str(text or "").split())
    current = size
    while current >= minimum:
        font = _font(kind, current, bold)
        if _fits(draw, text, font, max_width):
            lines = _wrap(draw, text, font, max_width, max_lines + 1)
            if len(lines) <= max_lines:
                return font, lines
        current -= 2
    font = _font(kind, minimum, bold)
    lines = _wrap(draw, text, font, max_width, max_lines + 1)
    if len(lines) <= max_lines:
        return font, lines
    # Last resort: drop whole trailing phrases at a clause boundary so
    # the card never shows a chopped word or a dangling "…".
    words = text.split()
    while len(words) > 3:
        words = words[:-1]
        trimmed = " ".join(words).rstrip(" ,;:.—-")
        lines = _wrap(draw, trimmed, font, max_width, max_lines + 1)
        if len(lines) <= max_lines:
            return font, lines
    return font, _wrap(draw, " ".join(words), font, max_width, max_lines)


_TRANSLITERATE = {
    "—": "-", "–": "-", "‑": "-", "−": "-", "’": "'", "‘": "'",
    "“": '"', "”": '"', "…": "", "×": "x", "•": "-",
}


def _ascii_safe(text: str) -> str:
    """Replace typographic characters the mono font cannot draw."""
    result = str(text or "")
    for source, target in _TRANSLITERATE.items():
        result = result.replace(source, target)
    # drop any remaining non-Latin-1 symbol that would render as a box
    return "".join(ch if ord(ch) < 0x2500 or ch.isascii() else " " for ch in result)


def _short_copy(text: str, limit: int) -> str:
    """Trim ``text`` to ``limit`` characters at a clause boundary.

    Used only when the model did not supply dedicated short copy.  It
    prefers cutting on a comma/dash/dot so the result reads as a
    complete thought rather than a chopped sentence, and never adds
    an ellipsis.
    """
    text = " ".join(str(text or "").split()).rstrip(" .,;:…")
    if len(text) <= limit:
        return text
    # A comma clause is usually the most natural place to stop ("Run
    # documents and slides on your own hardware", not "…on your own").
    # Look slightly past the limit so a comma sitting exactly at the
    # boundary still counts, but never return more than ``limit`` chars.
    window = text[: limit + 2]
    for separator in (", ", "; ", ": ", " — ", ". "):
        cut = window.rfind(separator)
        if cut > 0 and len(text[:cut]) >= limit * 0.6:
            return text[:cut].rstrip(" ,;:.—-")
    cut = window.rfind(" ")
    return (window[:cut] if cut > 0 else window).rstrip(" ,;:.—-")


def _rounded(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _pill(draw, x, y, text, foreground, background, font=None, pad=13, outline=None):
    font = font or _font("sans", 20, True)
    width = int(draw.textlength(text, font=font) + 2 * pad)
    _rounded(draw, (x, y, x + width, y + 38), 19, fill=background, outline=outline, width=2 if outline else 1)
    draw.text((x + pad, y + 8), text, font=font, fill=foreground)
    return width + 10


def _lang_code(language: str) -> str:
    mapping = {
        "Python": "Py",
        "TypeScript": "TS",
        "JavaScript": "JS",
        "Go": "Go",
        "Rust": "Rs",
        "Kotlin": "Kt",
        "Java": "Java",
        "C++": "C++",
        "C#": "C#",
        "Shell": "Sh",
        "Ruby": "Rb",
        "PHP": "PHP",
        "Swift": "Sw",
    }
    return mapping.get(language, (language or "OS")[:2].upper())


def _fmt(value: Any) -> str:
    try:
        number = int(float(value or 0))
    except (TypeError, ValueError):
        return str(value or "0")
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M".replace(".0", "")
    if number >= 10_000:
        return f"{number / 1_000:.1f}k".replace(".0", "")
    return f"{number:,}"


def _freshness(candidate: dict[str, Any]) -> str:
    pushed = parse_dt(candidate.get("pushed_at") or candidate.get("updated_at"))
    if not pushed:
        return "active repository"
    days = max(0, (datetime.now(timezone.utc) - pushed).days)
    if days == 0:
        return "commit today"
    if days == 1:
        return "commit yesterday"
    if days < 30:
        return f"commit {days} days ago"
    if days < 365:
        return f"commit {max(1, days // 30)} months ago"
    return f"commit {days // 365} years ago"


def _license(candidate: dict[str, Any]) -> str:
    lic = candidate.get("license")
    if isinstance(lic, dict):
        return str(lic.get("spdx_id") or lic.get("name") or "—")
    return str(lic or "—")


def _is_terminal(candidate: dict[str, Any], generated: dict[str, Any]) -> bool:
    topics = {str(topic).lower() for topic in candidate.get("topics") or []}
    category = str(generated.get("category") or "").lower()
    return bool(topics & {"cli", "tui", "terminal", "shell", "command-line", "command-line-tool"}) or any(
        token in category for token in ("cli", "tui", "terminal")
    )


_TITLE_MAX_W = 752  # 56 → 808: leaves a gutter before the hero mark at 824
HERO_BOX = (824, 176, 1176, 396)  # ink-safe area of the brand mark


def _condensed_text(
    image: Any,
    xy: tuple[int, int],
    text: str,
    font,
    fill: str,
    max_width: int,
    *,
    min_scale: float = 0.62,
) -> None:
    """Draw ``text`` horizontally condensed so it never crosses the hero mark.

    Shrinking the type size to fit a 40-character repository name would make
    it unreadable, so past a point we squeeze the glyphs instead of the size.
    """
    width = int(font.getlength(text))
    if width <= max_width:
        image.text(xy, text, font=font, fill=fill)
        return
    scale = max(min_scale, max_width / width)
    mask = Image.new("L", (max(width, 1), int(font.size * 1.5)), 0)
    ImageDraw.Draw(mask).text((0, 0), text, font=font, fill=255)
    mask = mask.crop(mask.getbbox())
    target_w = int(mask.width * scale)
    scaled = mask.resize((target_w, mask.height), Image.Resampling.LANCZOS)
    if scaled.width > max_width:
        scaled = scaled.crop((0, 0, max_width, scaled.height))
    ink = Image.new("RGBA", scaled.size, fill)
    image.paste(ink, xy, scaled)


def _stack_label(label: str) -> str:
    """Row labels are lowercase tool names by convention; single letters stay caps."""
    text = label.strip()
    if len(text) <= 1:
        return text.upper()
    return text if not text.isupper() else text


def _draw_stack_row(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    candidate: dict[str, Any],
    *,
    right_limit: int,
    top: int = 412,
    size: int = 32,
) -> None:
    """Language + tools, in brand colour, kept inside the right rule.

    Degrades in three steps so a 4-item stack never collides with the page
    edge: full labels → shorter labels → icons only.
    """
    entries = stack_entries(candidate, limit=4, size=size)
    if len(entries) <= 1:
        return
    font = _font("sans", 15, True)
    gaps = {"icon_text": 7, "item": 30, "icon_only": 16}
    plans: list[tuple[str, Image.Image]] = []
    for limit_chars in (None, 8, 0):
        cursor = 62
        plans = []
        for label, _slug, ink, colour in entries:
            text = _stack_label(label)
            if limit_chars == 0:
                plans.append(("", colour or ink))
                cursor += size + gaps["icon_only"]
                continue
            if limit_chars is not None:
                text = text[:limit_chars]
            width = int(draw.textlength(text, font=font)) if text else 0
            plans.append((text, colour or ink))
            cursor += size + (gaps["icon_text"] + width + gaps["item"] if text else gaps["icon_only"])
        if cursor - gaps["item"] <= right_limit:
            break
    cursor = 62
    for text, glyph in plans:
        image.paste(glyph, (cursor, top), glyph)
        if text:
            draw.text((cursor + size + gaps["icon_text"], top + 9), text, font=font, fill="#4a4a4a")
            cursor += size + gaps["icon_text"] + int(draw.textlength(text, font=font)) + gaps["item"]
        else:
            cursor += size + gaps["icon_only"]


def render_editorial_card(
    candidate: dict[str, Any],
    generated: dict[str, Any],
    output_path: Path,
    *,
    issue_number: int | None = None,
) -> Path:
    image = Image.new("RGB", (W, H), (247, 239, 222))
    draw = ImageDraw.Draw(image)
    date_line = datetime.now(timezone.utc).strftime("%A · %B %d, %Y").upper()
    issue = f"No. {issue_number}" if issue_number else "OPEN SOURCE FILE"
    draw.text((600, 36), "THE OPEN SOURCE DAILY", font=_font("serif", 34, True), fill="#1b1710", anchor="ma")
    draw.line((55, 92, 1145, 92), fill="#1b1710", width=4)
    draw.line((55, 103, 1145, 103), fill="#b3342a", width=3)
    draw.text((58, 116), date_line, font=_font("sans", 18, True), fill="#5c5c5c")
    # 24 px serif renders ~20 px per character in caps: 300 px is not enough
    # for "OPEN SOURCE FILE" and the label would cross the right rule.
    issue_font = _fit(draw, issue, "serif", 24, 470, True, 14)
    draw.text((1142, 112), issue, font=issue_font, fill="#b3342a", anchor="ra")
    kicker = "HIDDEN GEM FILE" if candidate.get("is_hidden_gem") else "PROJECT FILE"
    draw.text((58, 145), kicker, font=_font("sans", 17, True), fill="#b3342a")

    name = str(candidate.get("name") or "Open Source Project")
    title_font = _fit(draw, name, "serif", 104, 720, True, 44)
    # The hero mark starts at x=824; a 44 px fallback would still cross it,
    # so long names condense horizontally instead of shrinking to nothing.
    if draw.textlength(name, font=title_font) > _TITLE_MAX_W:
        _condensed_text(image, (56, 165), name, title_font, "#17130d", _TITLE_MAX_W)
    else:
        draw.text((56, 165), name, font=title_font, fill="#111111")

    hook = _short_copy(
        str(generated.get("card_hook") or generated.get("hook") or candidate.get("description") or ""),
        74,
    )
    subline = _short_copy(
        str(generated.get("card_subline") or generated.get("summary") or candidate.get("description") or ""),
        150,
    )

    # Deterministic vertical layout: the copy block must fit between the
    # project name and the stats rule (y=462) without overlaps or dead
    # strips, whatever the name length does to the title size.  When a
    # "built with" row is present the copy band gives up its space.
    stack = stack_icons(candidate, limit=4)
    has_stack = len(stack) > 1
    band_top = 165 + int(title_font.size * 1.26) + 10
    band_bottom = 398 if has_stack else 452
    hook_font = sub_font = None
    hook_lines: list[str] = []
    sub_lines: list[str] = []
    for hook_size in (40, 38, 36, 34, 32, 30, 28, 26):
        for sub_size in (25, 24, 23, 22, 21, 20, 19, 18):
            h_font = _font("serif", hook_size, True)
            s_font = _font("sans", sub_size, False)
            h_lines = _wrap(draw, hook, h_font, 720, 3)
            s_lines = _wrap(draw, subline, s_font, 705, 3)
            h_h = len(h_lines) * int(hook_size * 1.22)
            s_h = len(s_lines) * int(sub_size * 1.32)
            if band_top + h_h + 12 + s_h <= band_bottom and len(h_lines) <= 2 and len(s_lines) <= 2:
                hook_font, sub_font = h_font, s_font
                hook_lines, sub_lines = h_lines, s_lines
                break
        if hook_font:
            break
    if not hook_font:
        hook_font, hook_lines = _fit_block(draw, hook, "serif", 26, 720, 2, minimum=20)
        sub_font, sub_lines = _fit_block(draw, subline, "sans", 18, 705, 2, bold=False, minimum=15)

    hook_height = len(hook_lines) * int(hook_font.size * 1.22)  # type: ignore[union-attr]
    sub_height = len(sub_lines) * int(sub_font.size * 1.32)  # type: ignore[union-attr]
    # Spread any leftover room between the two blocks instead of leaving
    # a dead strip above the stats rule on short names.
    # Copy sits directly under the name: reading order matters more than
    # filling the band. Any leftover space stays above the stats rule,
    # which acts as the visual anchor of the lower half.
    # Without a stack row the band has spare height; push the copy down a
    # little so the gap above the stats rule does not read as a mistake.
    if not has_stack:
        leftover = band_bottom - (band_top + hook_height + 12 + sub_height)
        y_offset = int(min(46, max(0, leftover) * 0.36))
    else:
        y_offset = 0
    y = band_top + y_offset
    for index, line in enumerate(hook_lines):
        color = "#b3342a" if index == len(hook_lines) - 1 and len(hook_lines) > 1 else "#393126"
        draw.text((62, y), line, font=hook_font, fill=color)  # type: ignore[arg-type]
        y += int(hook_font.size * 1.22)  # type: ignore[union-attr]
    y += 12
    for line in sub_lines:
        draw.text((62, y), line, font=sub_font, fill="#4a4a4a")
        y += int(sub_font.size * 1.32)  # type: ignore[union-attr]

    language = str(candidate.get("language") or "Open Source")
    # Real brand mark, no decorative ring. Falls back to the typographic
    # monogram when we have no artwork for the language.
    icon = language_icon(language, 176)
    if icon is not None:
        image.paste(icon, (1000 - 88, 184), icon)
        draw.text(
            (1000, 388),
            language.upper(),
            font=_fit(draw, language.upper(), "sans", 19, 190, True, 12),
            fill="#1b1710",
            anchor="ma",
        )
    else:
        # No artwork: a typographic monogram, ringless like the real marks.
        # Rules as a frame, ink badge on top — no circle anywhere.
        code = _lang_code(language)
        draw.line((880, 214, 1120, 214), fill="#b3342a", width=3)
        draw.line((880, 338, 1120, 338), fill="#b3342a", width=3)
        mono_font = _fit(draw, code, "serif", 76, 150, True, 46)
        badge_w = int(draw.textlength(code, font=mono_font)) + 44
        badge_h = int(mono_font.size * 1.36)
        _rounded(draw, (1000 - badge_w // 2, 276 - badge_h // 2, 1000 + badge_w // 2, 276 + badge_h // 2), 6, fill="#b3342a")
        draw.text((1000, 276), code, font=mono_font, fill="#f7efde", anchor="mm")
        draw.text(
            (1000, 362),
            language.upper(),
            font=_fit(draw, language.upper(), "sans", 17, 190, True, 12),
            fill="#1b1710",
            anchor="ma",
        )

    # "Built with" row: language + tools detected from topics/description.
    if has_stack:
        _draw_stack_row(image, draw, candidate, right_limit=1140)
        draw.line((55, 460, 1145, 460), fill="#cdbfa6", width=3)

    y = 492
    draw.line((55, y, 1145, y), fill="#1b1710", width=3)
    stats = [
        ("star", "STARS", _fmt(candidate.get("stars") or candidate.get("stargazers_count"))),
        ("fork", "FORKS", _fmt(candidate.get("forks") or candidate.get("forks_count"))),
        ("people", "CONTRIB", _fmt(candidate.get("contributors_count")) if candidate.get("contributors_count") else "—"),
        ("license", "LICENSE", _license(candidate)),
    ]
    for index, (glyph_name, label, value) in enumerate(stats):
        x = 70 + index * 275
        glyph = load_ui_icon(glyph_name, 20)
        if glyph is not None:
            image.paste(glyph, (x, y + 19), glyph)
            label_x = x + 28
        else:
            label_x = x
        draw.text((label_x, y + 17), label, font=_font("sans", 18, True), fill="#5c5c5c")
        value_font = _fit(draw, str(value), "serif", 35, 220, True, 20)
        draw.text((x, y + 44), _glyph_safe(str(value), value_font), font=value_font, fill="#1b1710")
    draw.line((55, 585, 1145, 585), fill="#1b1710", width=3)
    clock = load_ui_icon("clock", 16)
    if clock is not None:
        image.paste(clock, (58, 596), clock)
        draw.text((80, 594), _freshness(candidate), font=_font("sans", 17, True), fill="#4a4a4a")
    else:
        draw.text((58, 594), _freshness(candidate), font=_font("sans", 17, True), fill="#4a4a4a")
    draw.text((1145, 594), BRAND, font=_font("sans", 19, True), fill="#b3342a", anchor="ra")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path


def render_terminal_card(
    candidate: dict[str, Any],
    generated: dict[str, Any],
    output_path: Path,
) -> Path:
    image = Image.new("RGB", (W, H), (2, 7, 4))
    draw = ImageDraw.Draw(image)
    for y in range(0, H, 6):
        draw.line((0, y, W, y), fill=(0, 22, 8), width=1)
    _rounded(draw, (35, 30, 1165, 600), 14, outline="#00ff66", width=3)
    draw.rectangle((35, 30, 1165, 82), fill="#04150a")
    for index, color in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        draw.ellipse((58 + index * 34, 47, 78 + index * 34, 67), fill=color)
    draw.text((175, 45), "~/open-source — zsh", font=_font("mono", 21), fill="#8dffb0")

    name = str(candidate.get("name") or "project")
    quick = str(generated.get("quick_start") or "").splitlines()[0] if generated.get("quick_start") else ""
    command = quick if quick and len(quick) <= 64 else f"$ {name.lower()}"
    if not command.startswith("$"):
        command = "$ " + command
    command = command.replace("—", "-").replace("–", "-")
    draw.text((65, 108), command, font=_fit(draw, command, "mono", 32, 760, True, 20), fill="#00ff66")
    title = "> " + _ascii_safe(name.upper())
    draw.text((62, 160), title, font=_fit(draw, title, "mono", 88, 760, True, 38), fill="#eaffef")

    hook = "// " + _ascii_safe(str(generated.get("hook") or candidate.get("description") or ""))
    y = 278
    _hook_font, _hook_lines = _fit_block(draw, _short_copy(hook, 140), "mono", 27, 760, 3, bold=False, minimum=18)
    for line in _hook_lines:
        draw.text((66, y), line, font=_font("mono", 27), fill="#76f79a")
        y += 36

    rows = [
        ("license", _license(candidate)),
        ("language", candidate.get("language") or "—"),
        ("stars", _fmt(candidate.get("stars") or candidate.get("stargazers_count"))),
        ("forks", _fmt(candidate.get("forks") or candidate.get("forks_count"))),
        ("status", _freshness(candidate)),
    ]
    y = 390
    for key, value in rows:
        draw.text((66, y), f"{key:<12}", font=_font("mono", 24), fill="#59d977")
        draw.text((315, y), str(value), font=_font("mono", 24, True), fill="white")
        y += 32

    _rounded(draw, (875, 170, 1115, 410), 18, outline="#00ff66", width=3)
    draw.text((995, 225), ">_", font=_font("mono", 96, True), fill="#00ff66", anchor="ma")
    draw.text((995, 345), "TERMINAL", font=_font("mono", 20, True), fill="#8dffb0", anchor="ma")
    draw.rectangle((65, 552, 86, 580), fill="#00ff66")
    draw.text((100, 553), BRAND, font=_font("mono", 20), fill="#8dffb0")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path


def _gradient(first: tuple[int, int, int], second: tuple[int, int, int]) -> Image.Image:
    base = Image.new("RGB", (W, H), first)
    top = Image.new("RGB", (W, H), second)
    mask = Image.linear_gradient("L").resize((W, H)).rotate(90)
    return Image.composite(top, base, mask)


# Owner feedback 04.11.2026: the first card had a truncated headline, a
# three-line standfirst drawn on top of the stat band, three colliding stat
# columns and 15-17 px type that turned to mush in Telegram's 940 px preview.
# These bands are FIXED and never overlap; all type is at least 21 px.
AI_BRIEF_FRAME = (70, 48, 1130, 582)
AI_BRIEF_LEFT = 108
AI_BRIEF_WIDTH = 984
AI_BRIEF_HEAD_TOP = 140
AI_BRIEF_BAND_TOP = 470
AI_BRIEF_BAND_BOTTOM = 568
AI_BRIEF_MIN_FONT = 21


def _font_size(font: object) -> int:
    """The font cache is deliberately untyped; only the pixel size is needed."""
    return int(getattr(font, "size", 30))


def _card_hook_layout(
    draw: ImageDraw.ImageDraw, text: str, allowed_height: int
) -> tuple[object, list[str], int]:
    """Fit the WHOLE hook inside its band; never cut a phrase mid-sentence.

    `_fit_block`'s last resort silently drops trailing words - that produced
    a card ending in "a 1.6T model's weights". Here a layout is accepted
    only if every word is still present and the lines fit vertically.
    """
    clean = " ".join(str(text or "").split())
    if not clean:
        return _font("sans", 26, False), [], 34
    for size in (30, 28, 26, 24, 22):
        font = _font("sans", size, False)
        line_height = int(size * 1.32)
        for max_lines in (1, 2, 3, 4):
            if max_lines * line_height > allowed_height:
                continue
            lines = _wrap(draw, clean, font, AI_BRIEF_WIDTH, max_lines)
            if " ".join(lines) == clean:
                return font, lines, line_height
    # Only an unreasonably long hook gets here: one readable line, clipped at
    # a word boundary with an explicit ellipsis rather than a dangling phrase.
    font = _font("sans", 22, False)
    words = clean.split()
    while words and draw.textlength(" ".join(words) + "…", font=font) > AI_BRIEF_WIDTH:
        words.pop()
    return font, [" ".join(words).rstrip(" ,;:.——") + "…"] if words else [], 29


def ai_brief_card_geometry(
    draw: ImageDraw.ImageDraw,
    headline: str,
    standfirst: str,
) -> dict[str, Any]:
    """Pure vertical geometry: every text block gets its own non-overlapping band."""
    head_font, head_lines = _fit_block(
        draw, headline, "sans", 58, AI_BRIEF_WIDTH, 3, bold=True, minimum=36
    )
    head_lh = int(_font_size(head_font) * 1.18)
    head_bottom = AI_BRIEF_HEAD_TOP + len(head_lines) * head_lh
    stand_top = head_bottom + 22

    allowed = (AI_BRIEF_BAND_TOP - 16) - stand_top
    stand_font, stand_lines, stand_lh = _card_hook_layout(draw, standfirst, allowed)
    stand_bottom = stand_top + len(stand_lines) * stand_lh
    return {
        "head_font": head_font,
        "head_lines": head_lines,
        "head_top": AI_BRIEF_HEAD_TOP,
        "head_lh": head_lh,
        "head_bottom": head_bottom,
        "stand_font": stand_font,
        "stand_lines": stand_lines,
        "stand_top": stand_top,
        "stand_lh": stand_lh,
        "stand_bottom": stand_bottom,
        "band_top": AI_BRIEF_BAND_TOP,
        "band_bottom": AI_BRIEF_BAND_BOTTOM,
    }


def ai_brief_card_columns(models_line: str, stories: int) -> tuple[tuple[int, int, str, str], ...]:
    """Two wide, non-overlapping stat columns (pure, so tests can measure)."""
    # Two short values only: long copy forced the old card down to 15 px.
    short_models = " · ".join((models_line or "").split(" · ")[:2]) or "Frontier model watch"
    short_format = f"{stories} stories · ad-free" if stories else "No ads · no hype"
    return (
        (116, 620, "MODELS IN THIS BRIEF", short_models),
        (772, 320, "FORMAT", short_format),
    )


def render_ai_brief_card(
    headline: str,
    standfirst: str,
    models_line: str,
    output_path: Path,
    *,
    stories: int = 0,
) -> Path:
    image = _gradient((12, 8, 40), (45, 15, 78)).convert("RGBA")
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    for x, y, radius, color in [
        (995, 55, 230, (0, 210, 255, 145)),
        (120, 510, 260, (255, 65, 180, 120)),
        (650, 90, 130, (70, 255, 170, 80)),
    ]:
        glow_draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    image = Image.alpha_composite(image, glow.filter(ImageFilter.GaussianBlur(55)))

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle(AI_BRIEF_FRAME, radius=34, fill=(10, 16, 42, 150), outline=(255, 255, 255, 130), width=2)
    _pill(
        od, 108, 82, "AI BRIEF · MODEL WATCH", "#ffffff",
        (255, 255, 255, 38), _font("sans", 22, True),
        outline=(255, 255, 255, 95),
    )
    # Solid band: small white type needs real contrast, not 40% black.
    od.rounded_rectangle(
        (90, AI_BRIEF_BAND_TOP, 1110, AI_BRIEF_BAND_BOTTOM),
        radius=22, fill=(6, 10, 26, 215), outline=(255, 255, 255, 95), width=2,
    )
    image = Image.alpha_composite(image, overlay)
    draw = ImageDraw.Draw(image)
    draw.text((1092, 84), BRAND, font=_font("mono", 22), fill=(220, 235, 255), anchor="ra")

    geo = ai_brief_card_geometry(draw, headline, standfirst)
    y = geo["head_top"]
    for line in geo["head_lines"]:
        draw.text((AI_BRIEF_LEFT, y), line, font=geo["head_font"], fill="white", anchor="lt")
        y += geo["head_lh"]
    y = geo["stand_top"]
    for line in geo["stand_lines"]:
        draw.text((AI_BRIEF_LEFT, y), line, font=geo["stand_font"], fill=(232, 240, 255), anchor="lt")
        y += geo["stand_lh"]

    # Two generous columns instead of three colliding ones. No source
    # attribution anywhere (owner decision 04.11.2026).
    label_font = _font("sans", AI_BRIEF_MIN_FONT, True)
    value_size = 27
    for x, width, label, value in ai_brief_card_columns(models_line, stories):
        draw.text((x, 486), label, font=label_font, fill=(150, 205, 255), anchor="lt")
        value_font = _fit(draw, value, "sans", value_size, width, True, AI_BRIEF_MIN_FONT + 1)
        draw.text((x, 518), value, font=value_font, fill="white", anchor="lt")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path)
    return output_path


def render_digest_card(records: Iterable[dict[str, Any]], output_path: Path) -> Path:
    image = Image.new("RGB", (W, H), (247, 239, 222))
    draw = ImageDraw.Draw(image)
    draw.text((600, 34), "THE OPEN SOURCE DAILY", font=_font("serif", 30, True), fill="#1b1710", anchor="ma")
    draw.line((55, 82, 1145, 82), fill="#1b1710", width=4)
    draw.line((55, 92, 1145, 92), fill="#b3342a", width=3)
    draw.text((58, 110), "SUNDAY EDITION · WEEK IN REVIEW", font=_font("sans", 18, True), fill="#b3342a")
    draw.text((56, 138), "Top 5 of the Week", font=_font("serif", 72, True), fill="#111111")
    y = 240
    for index, record in enumerate(list(records)[:5], 1):
        name = str(record.get("name") or record.get("full_name") or "Project")
        meta = " · ".join(part for part in [str(record.get("language") or ""), str(record.get("category") or "")] if part)
        if record.get("views_at_24h"):
            metric = f"{int(record['views_at_24h']):,} views".replace(",", " ")
        elif record.get("views"):
            metric = f"{int(record['views']):,} views".replace(",", " ")
        else:
            metric = f"score {float(record.get('score') or 0):.0f}"
        draw.text((62, y), str(index), font=_font("serif", 38, True), fill="#b3342a")
        draw.text((115, y), name, font=_fit(draw, name, "sans", 32, 560, True, 19), fill="#111111")
        draw.text((700, y + 4), meta, font=_font("sans", 21), fill="#5c5c5c")
        draw.text((1135, y), metric, font=_font("serif", 29, True), fill="#111111", anchor="ra")
        y += 58
        draw.line((60, y - 10, 1140, y - 10), fill="#cdbfa6", width=3)
    top = list(records)[:5]
    measured = sum(1 for r in top if r.get("views_at_24h") or r.get("views"))
    if measured == len(top) and measured:
        note = "Ranked by measured 24-hour views"
    elif measured:
        note = "Views where measured · editorial score otherwise"
    else:
        note = "Ranked by editorial score until view metrics are connected"
    draw.text((58, 562), note, font=_font("sans", 19), fill="#5c5c5c")
    draw.text((1145, 590), BRAND, font=_font("sans", 19, True), fill="#b3342a", anchor="ra")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path


def render_project_card(
    candidate: dict[str, Any],
    generated: dict[str, Any],
    output_path: Path,
    *,
    issue_number: int | None = None,
    portrait: bool = False,
) -> tuple[Path, str]:
    """Render the card for a Telegram post.

    Landscape (1200x630) is the owner's choice as of 2026-11-01. The 4:5
    portrait layout stays available behind ``portrait=True`` for feed tests;
    it fills the same phone column with roughly twice the type size, but the
    horizontal card is what the channel uses.
    """
    # A project name with a newline used to break the text layout; the
    # exception was caught and the post silently lost its card. Flatten it.
    candidate = dict(candidate)
    candidate["name"] = " ".join(str(candidate.get("name") or "").split())
    candidate["full_name"] = " ".join(str(candidate.get("full_name") or "").split())
    if _is_terminal(candidate, generated):
        return render_terminal_card(candidate, generated, output_path), "neon_terminal"
    if portrait:
        from .cards_portrait import render_portrait_card

        return render_portrait_card(candidate, generated, output_path, issue_number=issue_number), "editorial_portrait"
    return render_editorial_card(candidate, generated, output_path, issue_number=issue_number), "editorial_daily"


def render_ai_brief_card_v4(
    headline: str,
    standfirst: str,
    models_line: str,
    output_path: Path,
    *,
    stories: int = 0,
    rumours: int = 0,
) -> Path:
    """Owner-approved card D (2026-11-07): narrative hook, no service labels.

    Replaces the old "Glass Aurora / 05 STORIES VERIFIED" card, which the
    owner rejected. Dark grid, one large intrigue phrase, gold contrast line,
    a small factual footer. No external fonts or network resources.
    """
    from PIL import Image, ImageDraw  # local: keeps the module import light

    cyan = (77, 216, 255)
    mint = (126, 240, 200)
    gold = (255, 205, 120)
    white = (240, 246, 255)
    dim = (146, 168, 200)
    image = Image.new("RGB", (W, H), (8, 12, 22))
    draw = ImageDraw.Draw(image)
    for x in range(0, W, 80):
        draw.line([(x, 0), (x, H)], fill=(14, 23, 40))
    for y in range(0, H, 80):
        draw.line([(0, y), (W, y)], fill=(14, 23, 40))
    draw.rectangle([0, 0, 10, H], fill=cyan)

    mono = _font("mono", 17, True)
    mono_s = _font("mono", 17, False)
    draw.text((64, 50), "AI BRIEF  /  TELEGRAM SOURCES", font=mono, fill=cyan)
    handle = "@GitHub_Open_Source"
    draw.text((W - 64 - draw.textlength(handle, font=mono_s), 52), handle, font=mono_s, fill=dim)
    draw.line([(64, 104), (W - 64, 104)], fill=(34, 54, 88))

    big = _font("sans", 62, True)
    phrase = " ".join(str(standfirst or headline or "A 35-year-old math problem fell on a Tuesday.").split())

    def wrap(text: str, font: Any, max_width: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for word in text.split():
            trial = (current + " " + word).strip()
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines[:3]

    lines = wrap(phrase, big, 1030)
    top = 150
    for index, line in enumerate(lines):
        draw.text((64, top + index * 76), line, font=big, fill=gold if index == len(lines) - 1 else white)
    sub_top = top + len(lines) * 76 + 30
    sub = _font("sans", 25, False)
    sub_lines = wrap(
        "Telegram channels, not video round-ups. Rare finds stay in.", sub, 1000
    )
    for index, line in enumerate(sub_lines):
        draw.text((66, sub_top + index * 36), line, font=sub, fill=dim)
    draw.line([(64, 530), (W - 64, 530)], fill=(34, 54, 88))
    footer = _font("mono", 19, True)
    facts = f"{stories or 5} stories  ·  {rumours} rumours kept  ·  no ads"
    draw.text((64, 556), facts, font=footer, fill=mint)
    draw.text(
        (W - 64 - draw.textlength("no hype", font=footer), 556), "no hype", font=footer, fill=dim
    )
    image.save(output_path)
    return output_path
