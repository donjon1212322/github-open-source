"""Owner 24.11: the hashtags on the channel must actually be hashtags.

Checked against the live channel (`t.me/s/GitHub_Open_Source`): project posts
carry `#opensource #rust #devtools ...`, the AI Brief carries
`#AINews #OpenSource`, and the Sunday Top 5 carried **none** - the one post the
channel asks readers to forward. Two rules follow:

1. Every post type ends with the divider and a tag line, and the digest's tags
   come from what the week contained (its two most common languages).
2. A tag that does not start with a letter is dropped, not published:
   "#3d_modeling" or "#7z" are dead text on Telegram, Mastodon, Bluesky and
   dev.to, so a topic like "3d-modeling" must not become a fake hashtag.
"""
from __future__ import annotations

from ghos_v6 import content_adapters, pipeline
from ghos_v6.formatter import CHANNEL_TAG_DIVIDER, _hashtags

RECORDS = [
    {"name": "A", "language": "Rust", "topics": ["cli"], "score": 90},
    {"name": "B", "language": "Rust", "topics": ["tui"], "score": 80},
    {"name": "C", "language": "Python", "topics": ["ai"], "score": 70},
    {"name": "D", "language": "Go", "topics": ["network"], "score": 60},
]


def test_the_sunday_digest_ends_with_real_hashtags():
    text = pipeline.digest_text(RECORDS, poll_line="🗳️ x")
    tail = text.splitlines()[-3:]
    assert tail[0] == ""
    assert tail[1] == CHANNEL_TAG_DIVIDER
    tags = tail[2].split()
    assert tags[0] == "#opensource" and "#weekly" in tags
    # The week's own content, not a fixed list: Rust appears twice.
    assert tags[:3] == ["#opensource", "#rust", "#python"]
    assert len(tags) <= 4


def test_a_digest_without_languages_still_has_tags():
    text = pipeline.digest_text([{"name": "A"}], poll_line="🗳️ x")
    assert text.splitlines()[-1] == "#opensource #weekly"


def test_a_tag_that_starts_with_a_digit_is_dropped():
    # "3d-modeling" would render as "#3d_modeling": plain text, not a link.
    tags = _hashtags({"language": "Python", "topics": ["3d-modeling", "7z", "cli"]})
    assert tags.split()[0] == "#opensource"
    assert "3d_modeling" not in tags and "7z" not in tags
    assert "#cli" in tags


def test_crosspost_tags_follow_the_same_rule():
    record = {"full_name": "owner/demo", "language": "Python", "topics": ["3d-modeling"]}
    tags = content_adapters.hashtags(record, {"category": "DevTools"})
    assert tags.startswith("#OpenSource") or tags.startswith("#open_source")
    assert "3d_modeling" not in tags
    devto = content_adapters.devto_tags(record, {"category": "DevTools"})
    assert all(tag[0].isalpha() for tag in devto), devto
