"""Read-only audience measurement: how a post actually performed.

Two free signals are available without a Telegram user session:

* **Views** — the public embed widget ``t.me/<channel>/<id>?embed=1``
  exposes them per post. No MTProto, no extra credentials.
* **Votes** — ``stopPoll`` on a poll the bot created, before it expires
  (Telegram caps polls at 10 days and cannot create one retroactively).

Clicks are deliberately *not* measured: Telegram does not expose them, and
buying them would mean routing every GitHub link through a third-party
domain. The owner rejected that trade — a reader should see ``github.com``.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import requests

from .http import sanitize

EMBED_URL = "https://t.me/{channel}/{message_id}"
_VIEWS_RE = re.compile(r'tgme_widget_message_views">([^<]+)</span>')
_REACT_RE = re.compile(r'tgme_reaction">\s*([^<]*)</span>')

# Weight of each signal in the composite value score. Views are the only
# signal that exists for every post, so they carry the baseline; votes and
# clicks are bonuses that move a post up when they are available.
# Views accumulate roughly linearly for the first week and then flatten, so a
# raw view count compares a 1-hour post with a 14-day post. Measured on the
# live channel (2026-11-01): ~12 views/h in the first hour, ~2/h at 40 h,
# ~0.7/h after a week. We therefore normalise to views-per-hour at a fixed
# reference age instead of trusting the raw number.
REFERENCE_AGE_HOURS = 24.0
_AGE_CURVE = [(1.0, 12.0), (2.0, 8.0), (4.0, 6.0), (8.0, 4.0), (16.0, 3.0),
              (24.0, 2.6), (48.0, 1.8), (96.0, 1.1), (168.0, 0.8), (720.0, 0.6)]

W_VIEWS = 1.0
W_VOTES = 6.0
W_REACTIONS = 2.0

# Sample-size floors. These are not tuning knobs picked by feel: they come
# from the observed spread on the live channel (views 36/49/55, sd≈8, so
# CV≈0.16).  With that spread a 95% half-width of ±7 views already needs
# n≈5, and anything under ~20 posts cannot separate a 20% difference from
# noise. See docs/agents/SAMPLE_SIZE.md for the arithmetic.
MIN_MEASURED_POSTS = 20      # before `learn` writes any multiplier at all
MIN_GROUP_POSTS = 10         # per language/topic before that group gets a weight
MIN_POLL_VOTES = 100         # a share below this is not a share, it is noise


def parse_views(value: str) -> int:
    """``'1.2K'``/``'12,4 тыс.'`` style counters → int."""
    text = (value or "").strip().replace("\u00a0", " ").replace("\u202f", " ")
    match = re.match(r"^([\d.,]+)\s*([KkMm]|тыс\.?|млн\.?)?", text)
    if not match:
        return 0
    number = float(match.group(1).replace(",", ".")) if "," in match.group(1) and "." not in match.group(1) else float(match.group(1).replace(",", ""))
    suffix = (match.group(2) or "").lower()
    multiplier = 1_000 if suffix in {"k", "тыс", "тыс."} else 1_000_000 if suffix in {"m", "млн", "млн."} else 1
    return int(number * multiplier)


def fetch_post_stats(channel: str, message_id: int, *, timeout: int = 20) -> dict[str, Any]:
    """Views (and reactions, when present) for one public-channel post."""
    url = EMBED_URL.format(channel=str(channel).lstrip("@"), message_id=message_id)
    try:
        response = requests.get(url, params={"embed": "1", "mode": "tme"}, timeout=timeout,
                                headers={"User-Agent": "Mozilla/5.0 (compatible; ghos-v6)"})
    except requests.RequestException as exc:
        return {"ok": False, "error": sanitize(f"{type(exc).__name__}")[:120]}
    if response.status_code != 200:
        return {"ok": False, "error": f"http {response.status_code}"}
    views = _VIEWS_RE.search(response.text)
    reactions = _REACT_RE.findall(response.text)
    total_reactions = 0
    for raw in reactions:
        total_reactions += parse_views(raw.split()[-1]) if raw.strip() else 0
    return {
        "ok": bool(views),
        "views": parse_views(views.group(1)) if views else 0,
        "reactions": total_reactions,
        "fetched_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def expected_views(age_hours: float) -> float:
    """Expected cumulative views for a post of this age (linear interp)."""
    age_hours = max(0.25, float(age_hours))
    points = _AGE_CURVE
    if age_hours <= points[0][0]:
        return age_hours * points[0][1]
    for (age_a, rate_a), (age_b, rate_b) in zip(points, points[1:]):
        if age_a <= age_hours <= age_b:
            share = (age_hours - age_a) / (age_b - age_a)
            return (age_a * rate_a) + (age_b * rate_b - age_a * rate_a) * share
    return points[-1][0] * points[-1][1] + (age_hours - points[-1][0]) * points[-1][1]


def views_at_reference(views: int, age_hours: float, *, reference: float = REFERENCE_AGE_HOURS) -> float:
    """Scale a post's current views to what a reference-age post would show.

    Keeps a 1-hour-old post from looking like a failure and a 2-week-old post
    from looking like a hit. Falls back to the raw count when the age is
    unknown.
    """
    if views <= 0 or age_hours <= 0:
        return float(views)
    expected_now = expected_views(age_hours) / max(age_hours, 0.25) * age_hours
    expected_ref = expected_views(reference)
    ratio = expected_ref / expected_now if expected_now else 1.0
    # Never let normalisation inflate a post more than 6x or shrink below 0.3x.
    ratio = max(0.3, min(6.0, ratio))
    return views * ratio


def age_hours(published_at: Any, now: datetime | None = None) -> float:
    try:
        published = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - published).total_seconds() / 3600.0)


def value_score(sample: dict[str, Any], *, median_views: float | None = None) -> float:
    """Composite 0..100 score used to learn what the audience values.

    Views are normalised against the channel median so the score means
    "better than a typical post", not "big number".
    """
    views = float(sample.get("views") or 0)
    if sample.get("published_at"):
        views = views_at_reference(int(views), age_hours(sample.get("published_at")))
    baseline = float(median_views or 0) or 1.0
    view_part = min(100.0, 50.0 * (views / baseline)) if views else 0.0
    score = view_part * W_VIEWS
    voters = int(sample.get("total_voters") or 0)
    share = float(sample.get("votes_share") or 0)
    # A 60% share out of 5 voters is indistinguishable from a coin toss.
    # Votes only enter the score once the poll has a usable sample, and are
    # discounted by how far the sample is from a stable one.
    if voters >= MIN_POLL_VOTES:
        score += min(20.0, share * 20.0) * min(1.0, voters / (2 * MIN_POLL_VOTES)) * W_VOTES
    score += min(10.0, float(sample.get("reactions") or 0)) * W_REACTIONS
    return round(score, 2)


def median_views(samples: list[dict[str, Any]]) -> float:
    values = sorted(float(s.get("views") or 0) for s in samples if s.get("views"))
    if not values:
        return 0.0
    middle = len(values) // 2
    if len(values) % 2:
        return values[middle]
    return (values[middle - 1] + values[middle]) / 2
