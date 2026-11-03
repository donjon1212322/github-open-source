"""Portrait editorial card: 1080×1350 (4:5), drawn with a single scale factor.

Why a second layout exists
--------------------------
A 1200×630 card is a *link preview* shape. Telegram shows it small in the
feed, so a 15 px label lands at about 5 px on a phone and every hairline
turns into a grey smear — measured from a real channel screenshot: the
image was displayed ~420 px wide, a 2.8× reduction.

The fix is not more contrast, it is more pixels per letter: a 4:5 card uses
the full column width, so the same text is displayed roughly twice as
large. Everything here is expressed in fractions of ``W`` and multiplied by
``scale``, so the exact same code can render any portrait size without
coordinates drifting.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw

from .cards import (
    BRAND,
    _fit,
    _fmt,
    _font,
    _freshness,
    _glyph_safe,
    _license,
    _short_copy,
    _wrap,
    load_brand_icon,
    load_ui_icon,
    stack_entries,
)

PW, PH = 1080, 1350  # 4:5, the tallest ratio Telegram does not crop
PAPER = (247, 239, 222)
INK = "#111111"
SOFT = "#4a4a4a"
RULE = "#cdbfa6"
ACCENT = "#b3342a"


class _Canvas:
    """Scale-aware drawing wrapper: no raw pixel coordinates anywhere."""

    def __init__(self, width: int, height: int) -> None:
        self.image = Image.new("RGB", (width, height), PAPER)
        self.draw = ImageDraw.Draw(self.image)
        self.width = width
        self.height = height
        self.scale = width / PW

    def px(self, value: float) -> int:
        return int(round(value * self.scale))

    def font(self, kind: str, size: float, bold: bool = False):
        return _font(kind, self.px(size), bold)

    def text(self, xy: tuple[float, float], text: str, *, font, fill: str, anchor: str | None = None) -> None:
        x, y = self.px(xy[0]), self.px(xy[1])
        if anchor:
            self.draw.text((x, y), text, font=font, fill=fill, anchor=anchor)
        else:
            self.draw.text((x, y), text, font=font, fill=fill)

    def line(self, points: Iterable[float], *, fill: str, width: float) -> None:
        x1, y1, x2, y2 = points
        self.draw.line(
            (self.px(x1), self.px(y1), self.px(x2), self.px(y2)),
            fill=fill,
            width=max(2, self.px(width)),
        )

    def paste(self, icon: Image.Image, xy: tuple[float, float]) -> None:
        self.image.paste(icon, (self.px(xy[0]), self.px(xy[1])), icon)

    def icon(self, loader, *args, size: float):
        """Load an icon already at the pixel size this canvas will draw it."""
        return loader(*args, self.px(size))


def _wrap_fit(canvas: _Canvas, text: str, kind: str, sizes: list[float], max_width: float, lines_limit: int, bold: bool = True):
    """Largest size whose wrapped text still fits the line budget."""
    width_px = canvas.px(max_width)
    for size in sizes:
        font = canvas.font(kind, size, bold)
        lines = _wrap(canvas.draw, text, font, width_px, lines_limit)
        if lines and len(lines) <= lines_limit and all(
            canvas.draw.textlength(line, font=font) <= width_px for line in lines
        ):
            return font, lines
    font = canvas.font(kind, sizes[-1], bold)
    return font, _wrap(canvas.draw, text, font, width_px, lines_limit)


def render_portrait_card(
    candidate: dict[str, Any],
    generated: dict[str, Any],
    output_path: Path,
    *,
    issue_number: int | None = None,
    width: int = PW,
    height: int = PH,
) -> Path:
    canvas = _Canvas(width, height)
    scale = canvas.scale

    # --- masthead -------------------------------------------------------
    canvas.text((PW / 2, 46), "THE OPEN SOURCE DAILY", font=canvas.font("serif", 44, True), fill=INK, anchor="ma")
    canvas.line((45, 118, PW - 45, 118), fill=INK, width=6)
    canvas.line((45, 132, PW - 45, 132), fill=ACCENT, width=4)
    date_line = datetime.now(timezone.utc).strftime("%A · %B %d, %Y").upper()
    canvas.text((48, 148), date_line, font=canvas.font("sans", 24, True), fill=SOFT)
    issue = f"No. {issue_number}" if issue_number else "OPEN SOURCE FILE"
    canvas.text((PW - 48, 144), issue, font=canvas.font("serif", 32, True), fill=ACCENT, anchor="ra")

    kicker = "HIDDEN GEM FILE" if candidate.get("is_hidden_gem") else "PROJECT FILE"
    canvas.text((48, 196), kicker, font=canvas.font("sans", 24, True), fill=ACCENT)

    # --- title: full width, big ----------------------------------------
    name = str(candidate.get("name") or "Open Source Project")
    title_font, title_lines = _wrap_fit(canvas, name, "serif", [132, 120, 108, 96, 84, 72], PW - 96, 2)
    y = 232
    for line in title_lines:
        canvas.text((44, y), line, font=title_font, fill=INK)
        y += int(title_font.size * 1.08)

    # --- hook + subline -------------------------------------------------
    hook = _short_copy(str(generated.get("card_hook") or generated.get("hook") or candidate.get("description") or ""), 74)
    subline = _short_copy(str(generated.get("card_subline") or generated.get("summary") or candidate.get("description") or ""), 150)
    y += 14
    hook_font, hook_lines = _wrap_fit(canvas, hook, "serif", [56, 52, 48, 44, 40], PW - 96, 3)
    for index, line in enumerate(hook_lines):
        colour = ACCENT if index == len(hook_lines) - 1 and len(hook_lines) > 1 else "#2b2b2b"
        canvas.text((48, y), line, font=hook_font, fill=colour)
        y += int(hook_font.size * 1.18)
    y += 10
    sub_font, sub_lines = _wrap_fit(canvas, subline, "sans", [36, 34, 32, 30, 28], PW - 96, 3, bold=False)
    for line in sub_lines:
        canvas.text((50, y), line, font=sub_font, fill=SOFT)
        y += int(sub_font.size * 1.3)

    # --- hero brand mark + stack row, centred in the space that is left --
    BOTTOM_BLOCK = 340  # stats (two rows) + footer rule
    content_bottom = PH - BOTTOM_BLOCK
    language = str(candidate.get("language") or "")
    mark_size = 200
    entries = stack_entries(candidate, limit=3, size=canvas.px(52))
    show_stack = len(entries) > 1
    hero_block = mark_size + 44 + (58 if show_stack else 0)
    available_top = y + 18
    hero_y = available_top + max(0.0, (content_bottom - 16 - available_top - hero_block) / 2)

    icon = canvas.icon(load_brand_icon, _language_slug(language), size=mark_size) if language else None
    if icon is not None:
        canvas.paste(icon, ((PW - mark_size) / 2, hero_y))
        canvas.text((PW / 2, hero_y + mark_size + 8), language.upper(),
                    font=canvas.font("sans", 24, True), fill=INK, anchor="ma")
    else:
        canvas.line((PW / 2 - 150, hero_y + 10, PW / 2 + 150, hero_y + 10), fill=ACCENT, width=4)
        canvas.text((PW / 2, hero_y + 26), _language_code(language), font=canvas.font("serif", 92, True), fill=ACCENT, anchor="ma")
        canvas.line((PW / 2 - 150, hero_y + 150, PW / 2 + 150, hero_y + 150), fill=ACCENT, width=4)

    if show_stack:
        row_y = hero_y + mark_size + 44
        gap = 26
        cells = []
        for label, _slug, ink_icon, colour in entries:  # type: ignore
            glyph = colour or ink_icon
            label_font = canvas.font("sans", 26, True)
            cells.append((glyph, label, int(canvas.draw.textlength(label, font=label_font)), label_font))
        total = sum(g.width + 14 + w + gap for g, _l, w, _f in cells) - gap  # type: ignore[union-attr, misc]
        x = (width - total) / 2 / scale
        for glyph, label, w, label_font in cells:
            canvas.paste(glyph, (x, row_y))  # type: ignore[arg-type]
            x += (glyph.width / scale) + 14  # type: ignore[union-attr]
            canvas.text((x, row_y + 11), label, font=label_font, fill=SOFT)
            x += w / scale + gap

    # --- stats: two columns of two, big numerals ------------------------
    stats_top = PH - BOTTOM_BLOCK
    canvas.line((45, stats_top, PW - 45, stats_top), fill=INK, width=5)
    stats = [
        ("star", "STARS", _fmt(candidate.get("stars") or candidate.get("stargazers_count"))),
        ("fork", "FORKS", _fmt(candidate.get("forks") or candidate.get("forks_count"))),
        ("people", "CONTRIB", _fmt(candidate.get("contributors_count")) if candidate.get("contributors_count") else "—"),
        ("license", "LICENSE", _license(candidate)),
    ]
    for index, (glyph_name, label, value) in enumerate(stats):
        col, row = index % 2, index // 2
        x = 60 + col * 500
        yy = stats_top + 30 + row * 100
        glyph = canvas.icon(load_ui_icon, glyph_name, size=28)
        if glyph is not None:
            canvas.paste(glyph, (x, yy + 2))
            label_x = x + 40
        else:
            label_x = x
        canvas.text((label_x, yy), label, font=canvas.font("sans", 24, True), fill=SOFT)
        value_font = _fit(canvas.draw, str(value), "serif", canvas.px(52), canvas.px(420), True, canvas.px(30))
        canvas.text((x, yy + 34), _glyph_safe(str(value), value_font), font=value_font, fill=INK)

    # --- footer ---------------------------------------------------------
    canvas.line((45, PH - 92, PW - 45, PH - 92), fill=INK, width=5)
    clock = canvas.icon(load_ui_icon, "clock", size=24)
    if clock is not None:
        canvas.paste(clock, (48, PH - 70))
        canvas.text((80, PH - 74), _freshness(candidate), font=canvas.font("sans", 24, True), fill=SOFT)
    else:
        canvas.text((48, PH - 74), _freshness(candidate), font=canvas.font("sans", 24, True), fill=SOFT)
    canvas.text((PW - 48, PH - 76), BRAND, font=canvas.font("sans", 26, True), fill=ACCENT, anchor="ra")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.image.save(output_path)
    return output_path


def _language_slug(language: str) -> str:
    from .cards import _LANGUAGE_ICONS

    return _LANGUAGE_ICONS.get(language.strip().lower(), "")


def _language_code(language: str) -> str:
    from .cards import _lang_code

    return _lang_code(language or "OS")
