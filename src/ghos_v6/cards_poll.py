"""The reader's-choice card ("Tabloid"): 1200×630, Telegram only.

Why this card exists
--------------------
Owner 23.11: a poll winner has to *come out*, and the post that keeps that
promise deserves a card of its own. It is a front page, not a project file:
the channel promised, the readers voted, the post is the delivery.

Why it is not the normal card
-----------------------------
The same ``image_url`` feeds the Telegram link preview, the dev.to cover and
the cross-posts. A card that says "readers' choice" in a place where nobody
voted is nonsense, so this renderer is called **only** for the Telegram
preview of an obligation post; the project card stays the master everywhere
else.

Why it is defensive
-------------------
This card renders once a week, from data the channel does not fully control:
a poll option written in a hurry, a repository description in Chinese, an
emoji, a 90-character name, a repo that vanished. A broken card is worse than
no card, so every input passes through the same funnel:

* ``clean_text`` — Unicode NFKC, control and zero-width characters dropped,
  whitespace collapsed, ``\\n`` and tabs become single spaces;
* ``usable_text`` — characters the loaded font cannot draw (CJK, Arabic,
  colour emoji) are removed rather than rendered as boxes;
* ``wrap_lines`` — a word too long for the column is broken by characters
  instead of overflowing it (CJK has no spaces at all);
* ``fit_text`` — the size shrinks until the whole text fits its box; if it
  still does not, the last line gets an ellipsis, never a half-drawn word;
* ``squeeze_paste`` — a headline that must keep its size is condensed
  horizontally, with a floor, so it can never cross the page edge;
* decorations (the stamp) are drawn only if they fit, text always wins.

Everything is deterministic: the same facts produce byte-identical PNGs, so
tests can compare files and a re-run never "fixes" the layout by accident.
"""
from __future__ import annotations

import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageDraw

from .cards import W, H, _font

#: Card palette — newspaper stock, ink and one red.
PAPER = (239, 234, 220)
INK = (22, 20, 18)
RED = (170, 28, 24)
MUTED = (120, 112, 100)

MARGIN = 40
COLUMNS = 3
COLUMN_WIDTH = 320
COLUMN_GAP = 70          # 40 -> 430 -> 820
MASTHEAD_Y = 26
COLOPHON_Y = 98
BODY_TOP = 126
FOOTER_Y = 596

#: Card is English-only by owner rule, whatever language the repository uses.
FALLBACK_WINNER = "The readers' pick"
FALLBACK_REPO = "unknown repository"
FALLBACK_DESC = "A project the channel promised to cover."


# --- слой 1: текст как данные ------------------------------------------------

def clean_text(value: Any, *, limit: int = 400) -> str:
    """Normalise any input into one safe line of text.

    NFKC folds full-width letters (Chinese-influenced Latin, Arabic-Indic
    digits) into ASCII where it can; control characters and zero-width
    joiners are removed because they break measurement silently; every kind
    of whitespace becomes a single space.
    """
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    # whitespace controls become spaces *before* the control sweep, otherwise
    # "fast\nnotes" would be glued into "fastnotes"
    text = "".join(
        " " if char in "\t\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029" else char
        for char in text
    )
    cleaned: list[str] = []
    for char in text:
        category = unicodedata.category(char)
        if category in {"Cc", "Cf", "Cs", "Co", "Cn"}:
            continue
        cleaned.append(char)
    text = "".join(cleaned)
    text = " ".join(text.split())
    return text[:limit].strip()


#: A code point no real font carries: DejaVu draws it as the .notdef box.
_MISSING_PROBE = "\U0010FFFD"
_MISSING_CACHE: dict[int, bytes] = {}


def _missing_mask(font) -> bytes:
    key = id(font)
    if key not in _MISSING_CACHE:
        _MISSING_CACHE[key] = bytes(font.getmask(_MISSING_PROBE))
    return _MISSING_CACHE[key]


def _has_glyph(font, char: str) -> bool:
    """True when the font can actually draw the character.

    ``getbbox()`` is not a test: a missing glyph still has a box, because
    DejaVu draws .notdef — the very tofu square the reader would see. The only
    reliable check is to compare the rendered mask with that of a code point
    no font defines (U+10FFFD).
    """
    try:
        mask = bytes(font.getmask(char))
    except Exception:  # noqa: BLE001 - a font may refuse any character
        return False
    return bool(mask.strip(b"\x00")) and mask != _missing_mask(font)


def renderable(text: str, font) -> str:
    """Drop characters the font cannot draw instead of drawing boxes.

    DejaVu covers Latin, Greek and Cyrillic, but not CJK, Arabic or colour
    emoji. A repository description like "一个知识库 🚀" would otherwise turn
    into a row of tofu squares — unreadable, and it looks broken.
    """
    kept = [char for char in text if char.isspace() or _has_glyph(font, char)]
    return " ".join("".join(kept).split())


def _has_letter_or_digit(text: str) -> bool:
    return any(unicodedata.category(char)[0] in {"L", "N"} for char in text)


def readable(text: str, font) -> str:
    """``renderable`` plus: drop tokens that lost every letter.

    Removing CJK out of "一个，一个" leaves a bare "，"; removing it out of a
    description leaves "— ;" behind. A token with no letter or digit in it is
    debris, not text, so it goes — that is why a Chinese description yields an
    empty string here and the caller can fall back, instead of a giant comma
    standing in the headline.
    """
    words = [word for word in renderable(text, font).split() if _has_letter_or_digit(word)]
    return " ".join(words)


def meaningful(text: str) -> bool:
    return _has_letter_or_digit(text)


# --- слой 2: перенос и подгонка ---------------------------------------------

def wrap_lines(draw, text: str, font, max_width: int, max_lines: int = 99) -> list[str]:
    """Word wrap that also survives a single unbreakable "word".

    CJK text has no spaces, and a pasted URL has no spaces either; splitting
    such a token by characters is the only way to keep it inside the column.
    """
    if not text:
        return []
    lines: list[str] = []
    current = ""
    for word in text.split():
        probe = f"{current} {word}".strip()
        if draw.textlength(probe, font=font) <= max_width:
            current = probe
            continue
        if current:
            lines.append(current)
            current = ""
            if len(lines) >= max_lines:
                return lines
        if draw.textlength(word, font=font) <= max_width:
            current = word
            continue
        # one token wider than the column: break it by characters, but at a
        # sane separator when one is close by ("owner/repo", "my-project")
        chunk = ""
        for char in word:
            if draw.textlength(chunk + char, font=font) <= max_width:
                chunk += char
                continue
            cut = max((chunk.rfind(sep) for sep in "-/_.:"), default=-1)
            if cut >= len(chunk) // 2:
                head, rest = chunk[: cut + 1], chunk[cut + 1 :]
            else:
                head, rest = chunk, ""
            if head:
                lines.append(head)
                if len(lines) >= max_lines:
                    return lines
            chunk = rest + char
        current = chunk
    if current:
        lines.append(current)
    return lines[:max_lines]


def ellipsize(draw, line: str, font, max_width: int, *, force: bool = False) -> str:
    """Cut a line at a word boundary and mark the cut with an ellipsis.

    ``force`` marks a line that fits but is not the end of the text — the case
    where a column ran out of lines and the reader must see that.
    """
    if not force and draw.textlength(line, font=font) <= max_width:
        return line
    trimmed = line
    while trimmed and draw.textlength(trimmed + "…", font=font) > max_width:
        trimmed = trimmed[:-1]
    trimmed = trimmed.rstrip(" ,;:-")
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (trimmed + "…") if trimmed else "…"


def fit_text(
    draw,
    text: str,
    *,
    kind: str,
    sizes: Iterable[int],
    max_width: int,
    max_height: int,
    bold: bool = True,
    line_factor: float = 1.06,
    ellipsis: bool = True,
) -> tuple[Any, list[str]]:
    """Largest size at which the whole text fits the box, else truncated.

    Returns the font and the wrapped lines. The caller only ever draws what
    came back, so a layout can never spill out of its box.
    """
    text = text.strip()
    best_font, best_lines = _font(kind, min(sizes, default=20), bold), []
    for size in sizes:
        font = _font(kind, size, bold)
        lines = wrap_lines(draw, text, font, max_width)
        if len(lines) * size * line_factor <= max_height:
            return font, lines
        best_font, best_lines = font, lines
    if not ellipsis or not best_lines:
        return best_font, best_lines
    fitted = ellipsize(draw, best_lines[0], best_font, max_width)
    room = max(1, int(max_height // (best_font.size * line_factor)))
    return best_font, [fitted] + best_lines[1:room]


def fit_lines(
    draw,
    text: str,
    *,
    kind: str,
    size: int,
    max_width: int,
    max_lines: int,
    bold: bool = False,
) -> tuple[Any, list[str]]:
    """Keep one size, clamp the number of lines, ellipsize what is cut."""
    font = _font(kind, size, bold)
    lines = wrap_lines(draw, text, font, max_width, max_lines)
    if not lines:
        return font, []
    consumed = " ".join(lines)
    if len(lines) >= max_lines and consumed.split() != text.split()[: len(consumed.split())]:
        lines = lines[:max_lines]
    if len(lines) == max_lines and " ".join(lines) != text:
        lines[-1] = ellipsize(draw, lines[-1], font, max_width, force=True)
    return font, lines


def squeeze_paste(image, xy, text: str, font, fill, max_width: int,
                  min_scale: float = 0.34) -> None:
    """Draw condensed type so a headline never crosses the page edge."""
    draw = ImageDraw.Draw(image)
    width = draw.textlength(text, font=font)
    if width <= max_width:
        draw.text(xy, text, font=font, fill=fill)
        return
    scale = max(min_scale, max_width / width)
    mask = Image.new("L", (int(width) + 8, int(font.size * 1.8)), 0)
    ImageDraw.Draw(mask).text((0, 0), text, font=font, fill=255)
    box = mask.getbbox()
    if not box:
        return
    mask = mask.crop(box)
    scaled = mask.resize((max(1, int(mask.width * scale)), mask.height),
                         Image.Resampling.LANCZOS)
    if scaled.width > max_width:
        scaled = scaled.crop((0, 0, max_width, scaled.height))
    ink = Image.new("RGBA", scaled.size, fill)
    image.paste(ink, (xy[0], xy[1] + (font.size - scaled.height) // 2), scaled)


# --- слой 3: числа и даты ---------------------------------------------------

def safe_counts(votes: Any, total: Any) -> tuple[int, int]:
    """Vote numbers are never trusted: no negative, no total below the winner."""
    def _int(value: Any) -> int:
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return 0
        return max(0, number)

    winner = _int(votes)
    everyone = max(_int(total), winner)
    return winner, everyone


def pretty_count(value: int) -> str:
    """1 234 567 instead of 1234567: a front page spells numbers out."""
    return f"{value:,}".replace(",", "\u2009") if value >= 10_000 else str(value)


def pretty_date(value: Any) -> str:
    """ISO string, datetime or date → "Nov 19"; anything else passes through."""
    text = clean_text(value, limit=40)
    if not text:
        return ""
    token = text[:10]
    for candidate in (token, text):
        try:
            parsed = date.fromisoformat(candidate)
        except ValueError:
            continue
        return f"{parsed.strftime('%b')} {parsed.day}"
    # "not-a-date" in a newspaper footer looks like a bug, not like a fact
    return ""


# --- слой 4: сама карточка --------------------------------------------------

def _wrap_simple(draw, text: str, font, max_width: int) -> list[str]:
    return wrap_lines(draw, text, font, max_width)


def stamp_placement(column_bottom: int, stamp_h: int = 74, stamp_w: int = 400) -> tuple[int, bool]:
    """Where the stamp goes — or that it does not go anywhere.

    The stamp is decoration; the text is the reason the card exists. It is
    pinned to the footer line, and if the columns have grown into that space
    it is dropped instead of drawn over a sentence or off the page (the
    rotated stub is taller than the unrotated rectangle, which is exactly how
    a stamp used to leak past the bottom edge).
    """
    y = FOOTER_Y - stamp_h - 12
    fits = column_bottom + 6 <= y and stamp_w <= W - 2 * MARGIN
    return y, fits


def render_poll_winner_card(
    output_path: Path,
    *,
    question: Any,
    winner: Any,
    repo: Any,
    description: Any = "",
    votes: Any = 0,
    total: Any = 0,
    closed: Any = "",
    published: Any = "",
    days: Any = None,
    issue: Any = None,
    tied: bool = False,
) -> Path:
    """Render the reader's-choice front page. Never raises on bad input."""
    image = Image.new("RGB", (W, H), PAPER)
    draw = ImageDraw.Draw(image)

    winner_votes, total_votes = safe_counts(votes, total)
    title_font_probe = _font("serif", 40, True)
    question_text = readable(clean_text(question, limit=180), title_font_probe)
    winner_text = readable(clean_text(winner, limit=180), title_font_probe)
    repo_text = readable(clean_text(repo, limit=120), title_font_probe)
    desc_text = readable(clean_text(description, limit=300), title_font_probe)
    if not meaningful(question_text):
        question_text = "Which project should we cover?"
    if not meaningful(winner_text):
        # the label was written in a script we cannot draw; the repository is
        # what the post is about, and GitHub names are ASCII by rule
        winner_text = repo_text if meaningful(repo_text) else FALLBACK_WINNER
    if not meaningful(repo_text):
        repo_text = FALLBACK_REPO
    if not meaningful(desc_text):
        desc_text = FALLBACK_DESC
    closed_text = pretty_date(closed)
    published_text = pretty_date(published)
    try:
        days_text = str(max(0, int(days))) if days is not None else ""
    except (TypeError, ValueError):
        days_text = ""
    issue_text = clean_text(issue, limit=12)

    # --- мастаба
    masthead = _font("serif", 38, True)
    draw.text((W // 2, MASTHEAD_Y), renderable("THE OPEN SOURCE DAILY", masthead),
              font=masthead, fill=INK, anchor="ma")
    draw.line((MARGIN, 78, W - MARGIN, 78), fill=INK, width=5)
    draw.line((MARGIN, 90, W - MARGIN, 90), fill=INK, width=2)

    small = _font("sans", 15, True)
    left_meta = renderable(f"VOL. VI · No. {issue_text}" if issue_text else "VOL. VI", small)
    draw.text((MARGIN + 2, COLOPHON_Y), left_meta, font=small, fill=INK)
    tagline = "READERS DECIDE, CHANNEL DELIVERS"
    draw.text((W // 2, COLOPHON_Y), tagline, font=small, fill=INK, anchor="ma")
    right_meta = renderable(
        (published_text.upper() + ", 2026") if published_text else "2026", small
    )
    draw.text((W - MARGIN - 2, COLOPHON_Y), right_meta, font=small, fill=INK, anchor="ra")
    draw.line((MARGIN, 120, W - MARGIN, 120), fill=INK, width=2)

    # --- заголовок: кегль подбирается под три строки И под высоту блока
    head_bottom = 372
    head_text = readable(winner_text.upper(), _font("serif", 104, True))
    if not head_text:
        # the winner label was written in a script we cannot draw: the
        # repository name is ASCII by GitHub's own rules, so it always works
        head_text = readable(repo_text.upper(), _font("serif", 104, True))
    if not head_text:
        head_text = FALLBACK_WINNER.upper()
    head_width = W - 2 * MARGIN - 8
    head, lines = _font("serif", 24, True), []
    for size in range(104, 22, -2):
        candidate = _font("serif", size, True)
        candidate_lines = _wrap_simple(draw, head_text, candidate, head_width)
        # +1 строка — место под красное "IS NOW LIVE"
        needed = (len(candidate_lines) + 1) * size * 0.94 + 14
        if len(candidate_lines) <= 3 and BODY_TOP + 6 + needed <= head_bottom:
            head, lines = candidate, candidate_lines
            break
    else:
        head = _font("serif", 22, True)
        lines = _wrap_simple(draw, head_text, head, head_width)[:3]
    if len(lines) > 3:
        lines = lines[:3]
    # что не влезло — честное многоточие, а не половина слова
    joined = " ".join(lines).split()
    if joined != head_text.split():
        lines[-1] = ellipsize(draw, lines[-1], head, head_width)

    y = BODY_TOP
    for line in lines:
        squeeze_paste(image, (MARGIN - 2, y), line, head, INK, W - 2 * MARGIN - 4)
        y += int(head.size * 0.94)
    squeeze_paste(image, (MARGIN - 2, y + 4), "IS NOW LIVE", head, RED,
                  W - 2 * MARGIN - 4, min_scale=0.4)
    underline_y = min(head_bottom, y + int(head.size * 0.94) + 14)

    # --- линейка и колонки
    draw.line((MARGIN, underline_y, W - MARGIN, underline_y), fill=INK, width=3)
    columns_top = underline_y + 14
    columns_bottom = FOOTER_Y - 30
    available = columns_bottom - columns_top

    # берём самый крупный кегль, при котором три колонки помещаются в отведённую
    # высоту: пустая нижняя полоса выглядит как ошибка вёрстки, а не как воздух
    probe_texts = (question_text, desc_text, tagline)
    body_size = 22
    while body_size > 11:
        probe = _font("serif", body_size, False)
        longest = max(
            (len(wrap_lines(draw, part, probe, COLUMN_WIDTH)) for part in probe_texts),
            default=1,
        )
        if longest * body_size * 1.3 + 60 <= available:
            break
        body_size -= 1
    body = _font("serif", body_size, False)

    max_body_lines = max(1, int((available - 44) / (body_size * 1.3)))
    # Каждое предложение собирается только из известных фактов: пустое поле
    # должно убрать фразу, а не оставить "published —".
    if total_votes:
        vote_text = (f"{pretty_count(winner_votes)} of {pretty_count(total_votes)} "
                     "readers picked this one")
        vote_text += f" on {closed_text}" if closed_text else ""
        vote_text += ". "
    else:
        # Reachable only through a direct call or a broken tally - and even then
        # the card must not claim a choice that no vote supports.
        vote_text = "The poll closed before a single vote was counted. "
    if tied:
        vote_text += "A tie: the first option was taken. "
    vote_text += "The poll was open all week."

    pick_text = repo_text
    if desc_text and desc_text != FALLBACK_DESC:
        pick_text += f" — {desc_text[0].lower() + desc_text[1:]}".rstrip(" .;,:")
    if published_text:
        pick_text += f"; published {published_text}"
        pick_text += f", {days_text} days later." if days_text else "."
    else:
        pick_text += "."

    blocks = [
        ("THE VOTE", renderable(vote_text, body)),
        ("THE PICK", renderable(pick_text, body)),
        ("THE PROMISE", "Every option on our polls is a promise: the winner gets a full "
                        "post within seven days."),
    ]
    # сколько места займут колонки — заранее, чтобы центрировать блок целиком
    def _block_height(text: str) -> int:
        lines = wrap_lines(draw, text, body, COLUMN_WIDTH, max_body_lines)
        return 32 + len(lines) * int(body_size * 1.3)

    tallest = max(_block_height(renderable(text, body)) for _title, text in blocks)
    leftover = available - tallest
    if leftover > 40:
        columns_top += min(72, leftover // 3)

    column_bottom = columns_top
    for index, (title, text) in enumerate(blocks):
        x = MARGIN + index * (COLUMN_WIDTH + COLUMN_GAP)
        draw.text((x, columns_top), title, font=_font("sans", 16, True), fill=RED)
        draw.line((x, columns_top + 22, x + COLUMN_WIDTH, columns_top + 22), fill=INK, width=1)
        font, lines = fit_lines(draw, text, kind="serif", size=body_size,
                                max_width=COLUMN_WIDTH, max_lines=max_body_lines)
        cy = columns_top + 32
        for line in lines:
            draw.text((x, cy), line, font=font, fill=INK)
            cy += int(body_size * 1.3)
        column_bottom = max(column_bottom, cy)

    # --- штамп: поворачиваем, обрезаем по чернилам и только потом решаем,
    # помещается ли он вообще
    stamp_text = "PROMISE KEPT"
    stamp_font = _font("sans", 34, True)
    stamp_w = int(draw.textlength(stamp_text, font=stamp_font)) + 44
    stamp_h = 74
    stamp = Image.new("RGBA", (stamp_w, stamp_h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)
    sd.rectangle((2, 2, stamp_w - 3, stamp_h - 3), outline=(*RED, 255), width=4)
    sd.text((stamp_w // 2, stamp_h // 2), stamp_text, font=stamp_font,
            fill=(*RED, 255), anchor="mm")
    stamp = stamp.rotate(-7, expand=True, resample=Image.Resampling.BICUBIC)
    ink_box = stamp.getbbox()
    if ink_box:
        stamp = stamp.crop(ink_box)
    stamp_y, stamp_fits = stamp_placement(column_bottom, stamp.height, stamp.width)
    if stamp_fits:
        image.paste(stamp, (W - MARGIN - stamp.width, stamp_y), stamp)

    brand = _font("sans", 16, True)
    draw.text((MARGIN + 2, FOOTER_Y), "@GitHub_Open_Source", font=brand, fill=RED)
    if question_text:
        caption_font = _font("sans", 13)
        caption = renderable(f"asked in the poll: {question_text}", caption_font)
        caption = ellipsize(draw, caption, caption_font, 620)
        draw.text((MARGIN + 2, FOOTER_Y - 22), caption, font=caption_font, fill=MUTED)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path
