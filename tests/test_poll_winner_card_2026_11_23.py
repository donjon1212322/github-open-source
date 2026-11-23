"""Owner 23.11: the readers'-choice card must survive anything.

The card renders once a week from data the channel does not fully control: a
poll option typed in a hurry, a repository described in Chinese, an emoji, a
90-character name, a repo that vanished, a date that is not a date. A card
that looks broken is worse than no card at all, so every degenerate input is
rendered here and checked for the same invariants:

* it exists, it is 1200×630, and nothing is drawn outside the safe frame;
* the text is readable — no boxes, no bare punctuation, no "published —";
* long text is wrapped, condensed or ellipsized, never cut mid-glyph;
* decorations give way to text instead of covering it;
* the same facts produce byte-identical files (deterministic layout).

Tofu squares deserve a note: Pillow's ``getbbox()`` returns a box for a
missing glyph as well, because DejaVu draws .notdef — the very square the
reader would see. The glyph check therefore compares the rendered mask with
that of U+10FFFD, a code point no font defines.
"""
from __future__ import annotations

import random
import string
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ghos_v6.cards import _font
from ghos_v6.cards_poll import (
    H,
    W,
    clean_text,
    ellipsize,
    meaningful,
    pretty_count,
    pretty_date,
    readable,
    render_poll_winner_card,
    renderable,
    safe_counts,
    stamp_placement,
    wrap_lines,
)

BASE = dict(
    question="What should we cover this week?",
    winner="Fast native notes app and rich-text editor",
    repo="siyuan-note/siyuan",
    description="A privacy-first, self-hosted knowledge base with a block editor",
    votes=6,
    total=11,
    closed="2026-11-19",
    published="2026-11-23",
    days=4,
    issue=61,
)

NASTY: dict[str, dict] = {
    "long_winner": {"winner": "Blazing-fast native notes app with a rich-text block editor, "
                              "markdown support, end-to-end encryption, offline sync and a "
                              "plugin system for macOS, Windows and Linux"},
    "one_huge_word": {"winner": "supercalifragilisticexpialidocious-" * 3},
    "cjk": {"winner": "一个注重隐私的自托管知识库，支持块编辑器",
            "description": "一个注重隐私的自托管知识库", "question": "这周写什么？"},
    "emoji": {"winner": "🚀🔥 Fast notes app 🧠✨ with rich-text editor 💾",
              "description": "Privacy-first 🛡️ knowledge base 📚 with sync 🔄"},
    "cyrillic": {"winner": "Быстрое приложение для заметок и редактор текста"},
    "arabic": {"winner": "تطبيق ملاحظات سريع ومحرر نصوص غني"},
    "hebrew": {"winner": "אפליקציית פתקים מהירה ועורך טקסט עשיר"},
    "empty": dict(question=None, winner=None, repo=None, description=None, votes=None,
                  total=None, closed=None, published=None, days=None, issue=None),
    "zero_votes": {"votes": 0, "total": 0},
    "negative_votes": {"votes": -5, "total": -1},
    "huge_votes": {"votes": 1234567, "total": 9999999},
    "total_below_winner": {"votes": 40, "total": 10},
    "tie": {"votes": 3, "total": 6, "tied": True},
    "control_chars": {"winner": "Fast\nnotes\tapp\u200b with \u200drich-text\u00a0editor \ufeff",
                      "description": "Line one\nLine two\r\nLine three\t\twith tabs"},
    "html": {"winner": "<script>alert(1)</script> notes & <b>editor</b>",
             "description": "<img src=x onerror=alert(1)> db & \"quotes\" 'apostrophes'"},
    "long_question": {"question": "What should we cover this week? " * 8},
    "long_repo": {"repo": "very-long-owner-name/another-really-long-repository-name-2026",
                  "description": "A privacy-first, self-hosted knowledge base with a block "
                                 "editor, markdown support, graph view, local-first sync, "
                                 "end-to-end encryption, plugin system and mobile clients"},
    "bad_dates": {"closed": "not-a-date", "published": "2026-11-23T14:05:00Z", "days": "-3"},
    "days_as_text": {"days": "four"},
    "symbols": {"winner": "100% local: notes/app (v2.1) — «quoted» & 'apos' [beta] #1 @home",
                "description": "Config: ~/.config/notes -> 2048 MB; a<b>c|d*e?f"},
    "fullwidth": {"winner": "Ｆａｓｔ　ｎｏｔｅｓ　ａｐｐ　ｗｉｔｈ　ｅｄｉｔｏｒ"},
    "everything_long": dict(BASE, winner="An open-source, self-hosted, privacy-first "
                                          "knowledge base with a block editor, graph view, "
                                          "plugins, mobile clients and offline sync",
                            repo="extremely-long-owner-handle/this-repository-name-is-long",
                            votes=987, total=1043),
}


def _render(path: Path, **overrides) -> Path:
    facts = dict(BASE)
    facts.update(overrides)
    return render_poll_winner_card(path, **facts)


def _ink_near_border(path: Path, margin: int = 6) -> int:
    """Pixels that are not paper inside the frame — must stay zero."""
    from PIL import Image

    with Image.open(path) as image:
        grey = image.convert("L")
        paper = 233  # paper luminance is ~233; anything darker is ink
        strips = [
            grey.crop((0, 0, W, margin)),
            grey.crop((0, H - margin, W, H)),
            grey.crop((0, 0, margin, H)),
            grey.crop((W - margin, 0, W, H)),
        ]
        return sum(
            1 for strip in strips for pixel in strip.tobytes() if pixel < paper - 12
        )


# --- слой текста ------------------------------------------------------------

def test_whitespace_controls_become_spaces_not_glue():
    assert clean_text("fast\nnotes\tapp") == "fast notes app"


def test_zero_width_and_bom_are_removed():
    assert clean_text("rich\u200b-text\ufeff") == "rich-text"


def test_fullwidth_latin_is_folded_by_nfkc():
    assert clean_text("Ｎｏｔｅｓ") == "Notes"


def test_clean_text_survives_numbers_none_and_huge_input():
    assert clean_text(None) == ""
    assert clean_text(12345) == "12345"
    assert len(clean_text("x" * 5000, limit=400)) == 400


def test_a_missing_glyph_is_detected_not_drawn_as_a_box():
    font = _font("serif", 40, True)
    assert renderable("notes", font) == "notes"
    assert renderable("知识库", font) == ""


def test_punctuation_left_after_removal_is_dropped():
    font = _font("serif", 40, True)
    assert readable("一个知识库，支持块编辑器", font) == ""
    assert readable("notes — 知识库, editor", font) == "notes editor"
    assert meaningful(readable("知识库", font)) is False


# --- переносы ---------------------------------------------------------------

def test_no_wrapped_line_is_wider_than_the_column():
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = _font("serif", 30, True)
    text = "supercalifragilisticexpialidocious " * 4 + "短" * 40
    for line in wrap_lines(draw, text, font, 400):
        assert draw.textlength(line, font=font) <= 400


def test_an_unbreakable_token_breaks_at_a_separator_when_it_can():
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = _font("serif", 30, True)
    lines = wrap_lines(draw, "owner/this-is-a-very-long-repository-name-indeed",
                       font, 220)
    assert len(lines) > 1
    assert any(line.endswith(("/", "-")) for line in lines[:-1])
    # A separator belongs at the *end* of the line it closes. Cutting one
    # character earlier leaves a line that starts with "-", which reads as a
    # stray dash in the middle of a name - exactly what a naive character
    # break produces.
    assert not any(line.startswith(("-", "/", "_", ".")) for line in lines)
    assert "".join(lines) == "owner/this-is-a-very-long-repository-name-indeed"


def test_ellipsis_is_forced_when_a_column_runs_out_of_lines():
    from PIL import Image, ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = _font("serif", 20, False)
    assert ellipsize(draw, "short", font, 400) == "short"
    assert ellipsize(draw, "short", font, 400, force=True) == "short…"


# --- числа и даты -----------------------------------------------------------

def test_vote_counts_are_never_negative_and_never_below_the_winner():
    assert safe_counts(-5, -1) == (0, 0)
    assert safe_counts(40, 10) == (40, 40)
    assert safe_counts("7", "9.9") == (7, 9)
    assert safe_counts(None, None) == (0, 0)
    assert safe_counts("abc", 5) == (0, 5)


def test_big_numbers_are_spelled_like_a_front_page():
    assert pretty_count(11) == "11"
    assert pretty_count(1234567) == "1\u2009234\u2009567"


def test_dates_parse_iso_and_refuse_garbage():
    assert pretty_date("2026-11-19") == "Nov 19"
    assert pretty_date("2026-11-23T14:05:00Z") == "Nov 23"
    assert pretty_date("not-a-date") == ""
    assert pretty_date(None) == ""


# --- сама карточка ----------------------------------------------------------

@pytest.mark.parametrize("name", sorted(NASTY))
def test_every_degenerate_case_renders_inside_the_frame(name, tmp_path):
    path = _render(tmp_path / f"{name}.png", **NASTY[name])
    from PIL import Image

    with Image.open(path) as image:
        assert image.size == (W, H)
    assert _ink_near_border(path) == 0, f"{name}: ink outside the safe frame"


def test_the_same_facts_produce_identical_bytes(tmp_path):
    first = _render(tmp_path / "a.png")
    second = _render(tmp_path / "b.png")
    assert first.read_bytes() == second.read_bytes()


def test_a_card_written_in_an_undrawable_script_still_says_something(tmp_path):
    """The label is Chinese, so the headline falls back to the repository."""
    path = _render(tmp_path / "cjk.png", **NASTY["cjk"])
    from PIL import Image

    with Image.open(path) as image:
        # the headline block must contain a lot of ink: a fallback headline was
        # drawn, not an empty slot
        band = image.convert("L").crop((40, 126, W - 40, 240))
        dark = sum(1 for pixel in band.tobytes() if pixel < 120)
    assert dark > 5000


def test_an_empty_tally_never_claims_the_readers_chose(tmp_path):
    """The degenerate zero-vote card must not invent an election.

    This state is unreachable through the pipeline (a promise needs votes), but
    a fallback that says "the readers chose" over an empty tally is exactly the
    kind of confident lie the card must never be able to print.
    """
    from ghos_v6 import cards_poll

    out = tmp_path / "zero.png"
    cards_poll.render_poll_winner_card(
        out,
        question="Which project should we cover?",
        winner="Fast notes app",
        repo="owner/notes",
        votes=0,
        total=0,
    )
    assert out.exists()
    # The phrase itself must not exist anywhere in the module.
    source = Path(cards_poll.__file__).read_text(encoding="utf-8")
    assert "readers chose" not in source
    assert "The poll closed before a single vote was counted." in source
    assert cards_poll.safe_counts(0, 0) == (0, 0)


def test_missing_everything_still_produces_a_full_card(tmp_path):
    path = _render(tmp_path / "empty.png", **NASTY["empty"])
    from PIL import Image

    with Image.open(path) as image:
        dark = sum(1 for pixel in image.convert("L").tobytes() if pixel < 120)
    assert dark > 8000  # masthead, headline, columns, footer — all present


def test_the_stamp_never_covers_text(tmp_path):
    """When the columns reach the footer, the stamp is dropped, not overlaid."""
    assert stamp_placement(340) == (510, True)
    assert stamp_placement(540)[1] is False
    assert stamp_placement(200, stamp_w=2000)[1] is False


def test_fuzz_random_strings_never_break_the_renderer(tmp_path):
    """40 random strings from an unfriendly alphabet — no exception, no spill."""
    alphabet = string.printable + "知识库🚀تطبيقאבג\u200b\u0301«»—…"
    rng = random.Random(23)
    for index in range(40):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
        path = _render(
            tmp_path / f"fuzz{index}.png",
            winner=text,
            description=text,
            question=text,
            repo=text or "owner/repo",
        )
        assert path.exists()
        assert _ink_near_border(path) == 0, f"fuzz{index}: {text!r}"


def test_the_renderer_is_fast_enough_for_a_publish_slot(tmp_path):
    started = datetime.now(timezone.utc)
    _render(tmp_path / "speed.png", **NASTY["everything_long"])
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    assert elapsed < 3.0, f"card took {elapsed:.2f}s"
