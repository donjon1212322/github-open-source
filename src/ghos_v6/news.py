"""WorldofAI → verified AI Brief pipeline.

The YouTube channel is a discovery radar only. A brief can be produced
only when a fetchable primary source (official docs/blog, repository or
model card) supports the central claims.
"""
from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from defusedxml import ElementTree as ET

from .formatter import CHANNEL_TAG_DIVIDER, utf16_len, visible_text
from .Gemini import GeminiClient
from .http import USER_AGENT, UnsafeUrl, download_bytes, sanitize, validate_public_url

logger = logging.getLogger(__name__)

CHANNEL_ID = "UC2WmuBuFq6gL08QYG-JjXKw"
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
# YouTube's public RSS endpoint returns 404/500 for *every* channel during
# its recurring outages (2021, 2024, Dec 2025, Feb 2026, Oct 2026) - including
# Google's own channels. The channel's Videos tab still serves the list, so
# it is our second source. RSS stays first because it costs no extra request.
CHANNEL_VIDEOS_URL = "https://www.youtube.com/@intheworldofai/videos"
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
WATCH_ENRICH_LIMIT = 5


class NewsSourceError(RuntimeError):
    """Every WorldofAI discovery source is down, not an editorial rejection."""


_RELATIVE_AGE_RE = re.compile(r"(\d+)\s*(mo|[smhdwy])\s*ago", re.I)
_AGE_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "mo": 2592000, "y": 31536000}
_CONTENT_ID_RE = re.compile(r'"contentId":"([A-Za-z0-9_-]{11})"')
_TITLE_RE = re.compile(r'"lockupMetadataViewModel":\{"title":\{"content":"((?:[^"\\]|\\.)*)"')
_RELATIVE_TEXT_RE = re.compile(r'"text":\{"content":"([^"]*ago)"')
_SHORT_DESCRIPTION_RE = re.compile(r'"shortDescription":"((?:[^"\\]|\\.)*)"')
_PUBLISH_DATE_RE = re.compile(r'"publishDate":"([^"]+)"')

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

SPONSORED_TERMS = ("sponsored", "paid promotion", "thanks to our sponsor", "affiliate link")
LEAK_TERMS = ("leak", "leaked", "rumor", "rumour", "rumored", "rumoured")

# Owner decision: announcements may go out without a primary source, because
# a model launch covered by the video is news even before the blog post is
# readable. What must NOT go out is speculation dressed as news. These are
# the markers of a rumour round-up rather than a review or a launch.
RUMOUR_MARKERS = (
    "leak", "leaked", "rumor", "rumour", "rumored", "'s bаnned", "banned",
    "unbanned", "conspiracy", "drama", "shocked", "you won't believe",
    "what nobody tells you", "the truth about", "gone wrong", "exposed",
)
# A review/announcement has a subject, not a reaction. If the title is mostly
# reaction words, the video is commentary about the news cycle.
REACTION_MARKERS = (
    "insane", "insanely", "huge", "bombshell", "crazy", "mind-blowing",
    "best ever", "ultimate", "shocking", "unbelievable", "wtf", "omg",
)
# Videos we do not want even if they are about a model: tutorials with no
# news value, "top 10" listicles, and reaction content.
LAUNCH_MARKERS = (
    "launch", "launches", "launched", "release", "released", "introducing",
    "announc", "ships", "shipped", "now available", "available", "is out",
    "open sourced", "goes live", "debut",
)
OFF_TOPIC_MARKERS = (
    "top 10", "top ten", "tier list", "reaction", "reacts", "podcast",
    "livestream", "live stream", "giveaway", "q&a", "mailbag", "vlog",
    "i tried", "i tested", "my setup", "day in the life",
)
RELEVANCE_TERMS = (
    "open source",
    "open-source",
    "open weights",
    "github",
    "developer",
    "coding",
    "code",
    "api",
    "model",
    "llm",
    "agent",
    "release",
    "launch",
    "tool",
    "framework",
    "benchmark",
)
BLOCKED_DOMAINS = {
    "youtube.com",
    "youtu.be",
    "x.com",
    "twitter.com",
    "facebook.com",
    "instagram.com",
    "discord.gg",
    "discord.com",
    "t.me",
    "bit.ly",
    "tinyurl.com",
    "amzn.to",
    "amazon.com",
}
OFFICIAL_DOMAINS = {
    "anthropic.com",
    "openai.com",
    "ai.google.dev",
    "deepmind.google",
    "blog.google",
    "microsoft.com",
    "github.blog",
    "github.com",
    "huggingface.co",
    "nvidia.com",
    "developer.nvidia.com",
    "ollama.com",
    "mistral.ai",
    "ai.meta.com",
    "about.fb.com",
    "qwen.ai",
    "deepseek.com",
    "x.ai",
}

NEWS_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "eligible": {"type": "BOOLEAN"},
        "headline": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "what_happened": {"type": "ARRAY", "items": {"type": "STRING"}},
        "why_devs": {"type": "STRING"},
        "confirmed": {"type": "ARRAY", "items": {"type": "STRING"}},
        "video_only_claims": {"type": "ARRAY", "items": {"type": "STRING"}},
        "official_name": {"type": "STRING"},
        "topic_key": {"type": "STRING"},
        "discussion_question": {"type": "STRING"},
        "rejection_reason": {"type": "STRING"},
    },
    "required": [
        "eligible",
        "headline",
        "summary",
        "what_happened",
        "why_devs",
        "confirmed",
        "video_only_claims",
        "official_name",
        "topic_key",
        "discussion_question",
        "rejection_reason",
    ],
}


def _text(element: ET.Element | None) -> str:
    return "" if element is None else "".join(element.itertext()).strip()


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_rss(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    entries: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", NS):
        link_el = entry.find("atom:link[@rel='alternate']", NS)
        link = link_el.get("href") if link_el is not None else ""
        video_id = _text(entry.find("yt:videoId", NS))
        if not video_id and link:
            match = re.search(r"[?&]v=([A-Za-z0-9_-]+)", link)
            video_id = match.group(1) if match else ""
        media_group = entry.find("media:group", NS)
        description = ""
        thumbnail = ""
        if media_group is not None:
            description = _text(media_group.find("media:description", NS))
            thumb_el = media_group.find("media:thumbnail", NS)
            thumbnail = thumb_el.get("url") if thumb_el is not None else ""
        if not description:
            description = _text(entry.find("media:description", NS)) or _text(entry.find("atom:summary", NS))
        published_raw = _text(entry.find("atom:published", NS))
        entries.append(
            {
                "video_id": video_id,
                "title": _text(entry.find("atom:title", NS)),
                "url": link or (f"https://www.youtube.com/watch?v={video_id}" if video_id else ""),
                "published_at": published_raw,
                "published": _parse_datetime(published_raw),
                "description": html.unescape(description),
                "thumbnail": thumbnail,
            }
        )
    return entries


def _json_string(raw: str) -> str:
    """Decode a JSON-escaped string taken out of YouTube's inline data."""
    try:
        return str(json.loads('"' + raw + '"'))
    except (ValueError, TypeError):
        return html.unescape(raw)


def parse_channel_page(html_text: str, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Read the Videos tab when YouTube's RSS feed is down.

    The current YouTube layout wraps each video in a ``lockupViewModel``
    block with a ``contentId``, a title and a relative age ("18h ago").
    Older layouts used ``videoRenderer``/``videoId``; both are accepted.
    """
    now = now or datetime.now(timezone.utc)
    entries: list[dict[str, Any]] = []
    blocks = html_text.split('"lockupViewModel":')[1:] or [html_text]
    for block in blocks:
        cid = _CONTENT_ID_RE.search(block) or re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', block)
        title_match = _TITLE_RE.search(block) or re.search(
            r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', block
        )
        if not (cid and title_match):
            continue
        video_id = cid.group(1)
        rel = _RELATIVE_TEXT_RE.search(block) or re.search(r'"publishedTimeText".{0,80}?"simpleText":"([^"]+)"', block)
        age_match = _RELATIVE_AGE_RE.search(rel.group(1)) if rel else None
        published = None
        if age_match:
            seconds = int(age_match.group(1)) * _AGE_SECONDS.get(age_match.group(2).lower(), 0)
            if seconds:
                published = now - timedelta(seconds=seconds)
        entries.append(
            {
                "video_id": video_id,
                "title": _json_string(title_match.group(1)),
                "url": WATCH_URL.format(video_id=video_id),
                "published_at": published.isoformat() if published else "",
                "published": published,
                "description": "",
                "thumbnail": "",
            }
        )
    return entries


def _enrich_from_watch_page(entry: dict[str, Any]) -> dict[str, Any]:
    """Restore the description and exact upload date that the RSS feed carried.

    The editorial prefilter needs the video description (relevance and the
    sponsor block); the Videos tab does not include it. One request per
    recent video is enough and is only made while RSS is broken.
    """
    try:
        response = requests.get(str(entry["url"]), headers={"User-Agent": USER_AGENT}, timeout=30)
        response.raise_for_status()
        text = response.text
    except Exception as exc:  # noqa: BLE001 - enrichment is best effort
        logger.warning("WorldofAI watch page unavailable: %s", sanitize(str(exc))[:160])
        return entry
    description = _SHORT_DESCRIPTION_RE.search(text)
    if description:
        entry["description"] = _json_string(description.group(1))
    date_match = _PUBLISH_DATE_RE.search(text)
    if date_match:
        parsed = _parse_datetime(date_match.group(1))
        if parsed:
            entry["published_at"] = parsed.isoformat()
            entry["published"] = parsed
    return entry


def fetch_worldofai_entries() -> list[dict[str, Any]]:
    """RSS first; the Videos tab plus watch pages while YouTube breaks RSS."""
    rss_error = ""
    try:
        response = requests.get(RSS_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
        response.raise_for_status()
        entries = parse_rss(response.text)
        if entries:
            return entries
        rss_error = "empty feed"
    except Exception as exc:  # noqa: BLE001 - the second source is the point
        rss_error = sanitize(str(exc))[:200]
    logger.warning("WorldofAI RSS unavailable (%s); reading the Videos tab", rss_error)
    try:
        response = requests.get(CHANNEL_VIDEOS_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
        response.raise_for_status()
        entries = parse_channel_page(response.text)
    except Exception as exc:  # noqa: BLE001
        raise NewsSourceError(
            f"WorldofAI source unavailable (rss: {rss_error}; channel: {sanitize(str(exc))[:160]})"
        ) from exc
    if not entries:
        raise NewsSourceError(f"WorldofAI source unavailable (rss: {rss_error}; empty channel page)")
    for entry in entries[:WATCH_ENRICH_LIMIT]:
        _enrich_from_watch_page(entry)
    return entries


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(term in lower for term in terms)


# Vendors and product families this channel covers. A title that names one
# of them with a version or a product word is reporting something real; a
# title full of adjectives and nobody's name is commentary.
_NAMED_SUBJECT_RE = re.compile(
    r"\b(claude|gpt-?\d|gemini|grok|llama|mistral|qwen|deepseek|kimi|glm|opus|sonnet|"
    r"haiku|fable|mythos|carbon|hydrogen|aurora|muse|react|rea|sora|veo|kling|"
    r"midtool|midjourney|gpt|chatgpt|openai|anthropic|google|meta|nvidia|"
    r"stability|perplexity|copilot|grok bot|unsloth|ollama)\b",
    re.I,
)


def _named_subject(title: str) -> bool:
    """True when the title names a concrete product, model or vendor."""
    return bool(_NAMED_SUBJECT_RE.search(title or ""))


def _sponsor_is_the_subject(title: str, sponsor_block: str) -> bool:
    """True when the sponsored product is what the video is about.

    A launch video is often paid promotion of the very thing it launches:
    the description says "Try X by @X" and the title is "X Is INSANE".
    What the owner wants out is a sponsor read bolted onto unrelated
    content, so the test is whether the product named in the sponsor line
    also appears in the title.
    """
    title_l = title.lower()
    for line in (sponsor_block or "").splitlines()[:6]:
        low = line.lower()
        if not any(term in low for term in SPONSORED_TERMS) and "://" not in low:
            continue
        # Pull the product name out of "Try Grok Bot by @Grok: https://x.ai/bot"
        cleaned = re.sub(r"https?://\S+", " ", line)
        cleaned = re.sub(r"[@#][\w.-]+|try|read the|launch post|sponsored by|this video is sponsored|by|\bpaid promotion\b|\baffiliate link\b", " ", cleaned.lower())
        words = [w for w in re.findall(r"[a-z0-9][a-z0-9.+-]{2,}", cleaned) if w not in {"the", "and", "for", "with", "your"}]
        if not words:
            continue
        joined = " ".join(words)
        if joined and joined in title_l:
            return True
        # The first two content words are usually the product: "Grok Bot".
        if len(words) >= 2 and f"{words[0]} {words[1]}" in title_l:
            return True
        if words[0] in title_l and len(words[0]) >= 5:
            return True
    return False


def prefilter_entry(
    entry: dict[str, Any],
    *,
    seen_video_ids: set[str] | None = None,
    recent_topic_keys: set[str] | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    now = now or datetime.now(timezone.utc)
    seen_video_ids = seen_video_ids or set()
    recent_topic_keys = recent_topic_keys or set()
    if not entry.get("video_id") or not entry.get("url"):
        return False, "missing_video_identity"
    if str(entry["video_id"]) in seen_video_ids:
        return False, "video_seen"
    published = entry.get("published") or _parse_datetime(str(entry.get("published_at") or ""))
    if not published:
        return False, "missing_publish_date"
    age_hours = (now - published).total_seconds() / 3600
    if age_hours < 0 or age_hours > 72:
        return False, "outside_72h"
    title = str(entry.get("title") or "")
    description = str(entry.get("description") or "")
    # Channel videos almost always open with a sponsorship block ("Sponsored
    # by X · Paid partnership"). Scanning the whole description rejected
    # 15 of 15 videos as sponsored, including the honest ones. A sponsor in
    # the first lines of the description is the signal; a sponsor mentioned
    # deep in the body is not the topic of the video.
    sponsor_block = description[:600]
    if _contains_any(f"{title}\n{sponsor_block}", SPONSORED_TERMS):
        # A sponsor that *is* the subject is not a sponsor read: "Try Grok
        # Bot" above a video called "Grok Bot Is INSANE" is a product
        # launch. Rejecting it silently cost the AI Brief every single one
        # of 15 videos on 2026-11-02 - the format would simply never post.
        if not _sponsor_is_the_subject(title, sponsor_block):
            return False, "sponsored"
    if _contains_any(title, LEAK_TERMS):
        return False, "leak_or_rumor_title"
    # Owner rule: reviews and launches yes, gossip and reaction content no.
    if _contains_any(title, RUMOUR_MARKERS):
        return False, "rumour_not_news"
    if _contains_any(title, OFF_TOPIC_MARKERS):
        return False, "not_a_review_or_launch"
    # Clickbait adjectives ("HUGE", "INSANE") are how this channel writes
    # *every* title, launches included: on 2026-11-02 they appeared in 11 of
    # 15 titles and rejected the real product launch along with the noise.
    # Reaction content is identified by the absence of a subject instead: a
    # title that names no product is commentary on the news, not the news.
    if _contains_any(title, REACTION_MARKERS) and not (
        _named_subject(title) or _contains_any(title, LAUNCH_MARKERS)
    ):
        return False, "reaction_content"
    if not _contains_any(f"{title} {description[:1200]}", RELEVANCE_TERMS):
        return False, "not_developer_relevant"
    rough_topic = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:80]
    if rough_topic in recent_topic_keys:
        return False, "recent_topic"
    return True, ""


def select_latest_news_entry(
    entries: list[dict[str, Any]],
    seen_video_ids: set[str],
    recent_topic_keys: set[str] | None = None,
    *,
    now: datetime | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Owner decision (2026-11-04): exactly one brief, from the newest video.

    * Only the latest video of the channel is considered, never a loop over
      all recent uploads.
    * It must be today's or yesterday's ("максимум вчерашний").
    * A video_id or topic already used by an earlier brief is never reused.
    Editorial judgement (ads, hype, rumours) is left to Gemini downstream.
    """
    now = now or datetime.now(timezone.utc)
    dated: list[tuple[datetime, dict[str, Any]]] = []
    for entry in entries:
        published = entry.get("published") or _parse_datetime(str(entry.get("published_at") or ""))
        if published and entry.get("video_id") and entry.get("url"):
            dated.append((published, entry))
    if not dated:
        return None, "no_dated_videos"
    published, latest = max(dated, key=lambda pair: pair[0])
    if published.date() < (now - timedelta(days=1)).date():
        return None, "video_too_old"
    if str(latest["video_id"]) in seen_video_ids:
        return None, "news_already_published"
    if recent_topic_keys:
        rough = re.sub(r"[^a-z0-9]+", "-", str(latest.get("title") or "").lower()).strip("-")[:80]
        if rough and rough in recent_topic_keys:
            return None, "news_already_published"
    return latest, ""


def clean_url(url: str) -> str:
    parsed = urlparse(str(url).strip().rstrip(".,;:)}]"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=False):
        lower = key.lower()
        if lower.startswith("utm_") or lower in {"ref", "referral", "aff", "affiliate", "clickid", "subid"}:
            continue
        query.append((key, value))
    return urlunparse(("https", parsed.netloc, parsed.path, "", urlencode(query), ""))


def _safe_public_url(url: str) -> bool:
    """HTTPS-only, DNS-resolved, no private addresses, no redirect bypass."""
    try:
        validate_public_url(url)
    except UnsafeUrl:
        return False
    return True


def _source_score(url: str) -> int:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if host in BLOCKED_DOMAINS:
        return -100
    score = 0
    if host == "github.com":
        score += 95 if len([part for part in path.split("/") if part]) >= 2 else 30
    if host == "huggingface.co":
        score += 90
    if host in OFFICIAL_DOMAINS or any(host.endswith("." + domain) for domain in OFFICIAL_DOMAINS):
        score += 90
    # Docs score above a landing page: a landing page advertises, a doc
    # page states what actually shipped. But +55 on top of an official
    # domain put a docs sub-page 43 points above the launch announcement
    # itself, so a real "introducing X" post was never chosen.
    if host.startswith("docs.") or "docs" in host.split("."):
        score += 10
    if host == "arxiv.org":
        score += 75
    if host.endswith(".edu"):
        score += 45
    if any(token in path for token in ("blog", "news", "announc", "release", "changelog", "docs")):
        score += 12
    if any(token in url.lower() for token in ("pricing", "shop", "store", "affiliate", "coupon")):
        score -= 80
    return score


def extract_primary_urls(entry: dict[str, Any], *, limit: int = 6) -> list[str]:
    raw_urls = re.findall(r"https?://[^\s<>)\"']+", str(entry.get("description") or ""))
    candidates: dict[str, int] = {}
    for raw in raw_urls:
        url = clean_url(html.unescape(raw))
        if not url or not _safe_public_url(url):
            continue
        score = _source_score(url)
        if score < 25:
            continue
        candidates[url] = max(score, candidates.get(url, -1000))
    return [url for url, _ in sorted(candidates.items(), key=lambda item: item[1], reverse=True)[:limit]]


def fetch_primary_text(url: str) -> dict[str, str] | None:
    # download_bytes validates the scheme, resolves DNS, rejects private
    # addresses and validates every redirect hop. It also bounds the body.
    try:
        validate_public_url(url)
    except UnsafeUrl:
        return None
    try:
        data = download_bytes(url, max_bytes=1_500_000, timeout=30)
    except Exception:
        return None
    raw = data.decode("utf-8", errors="replace")[:1_200_000]
    title_match = re.search(r"<title[^>]*>(.*?)</title>", raw, flags=re.I | re.S)
    title = html.unescape(re.sub(r"<[^>]+>", " ", title_match.group(1))).strip() if title_match else ""
    raw = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<noscript\b.*?</noscript>", " ", raw, flags=re.I | re.S)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 700:
        return None
    return {"url": url, "title": title[:240], "text": text[:12_000]}


def synthesize_brief(
    entry: dict[str, Any],
    primary: dict[str, str],
    client: GeminiClient,
    *,
    use_video: bool = True,
) -> dict[str, Any]:
    prompt = f"""
You are preparing a verified English AI Brief from a WorldofAI video plus a
primary source. The video is a discovery signal, not an authority.

Eligibility and editorial rules (owner decision 2026-11-04):
- This is the NEWEST video of the channel. News round-ups ARE allowed:
  pick the three to five most concrete shipped items (vendor, product,
  what changed) and ignore the rest. Do not reject a video because its
  clickbait title says "HUGE", "INSANE" or "LEAK" - judge the content.
- ADVERTISING MUST BE REMOVED. The video and its description contain
  sponsor reads, "Sponsored by ...", promo codes, affiliate/discount
  links, "try X free" offers, and calls to subscribe or buy. Delete all
  of it. Never mention a sponsor name, promo code, discount, referral or
  affiliate link ANYWHERE in the output. A normal sponsor read is NOT a
  reason to reject the video; the owner expects Gemini to cut that part.
- eligible=false ONLY when the entire video is an advert with no product
  news behind it, or when nothing factual can be extracted at all.
- A primary source is preferred but NOT required: without one, put every
  product claim under video_only_claims and keep what_happened to what the
  video itself states as fact (name, vendor, what shipped).
- Mark leaks, rumours and speculation as video_only_claims labelled
  "unconfirmed"; never present them as confirmed or as fact.
- Never repeat hype, superlatives, ALL CAPS, clickbait adjectives or
  exclamation marks. Rewrite the YouTube title into plain factual English.
- headline must be factual and specific.

Headline rules (the owner reads this first):
- Keep the PRODUCT'S TECHNICAL NAME exactly as the vendor writes it:
  "Claude 4.5", "GPT-6", "Gemini 4 Carbon", "DeepSeek V4.1". A renamed or
  translated model name makes the brief useless.
- Keep the vendor: OpenAI, Anthropic, Google DeepMind, xAI, Meta, Mistral.
- Shape: "<Vendor> ships <Product> for <what it does>" or
  "<Product>: <the one capability that changed>". No leading hype word,
  no ALL CAPS, no exclamation marks, no question marks.
- 60-110 characters.
- topic_key: lowercase slug of the product name plus version.

<video_metadata>
Title: {entry.get('title') or ''}
URL: {entry.get('url') or ''}
Published: {entry.get('published_at') or ''}
Description (untrusted):
{str(entry.get('description') or '')[:8000]}
</video_metadata>

<primary_source url="{primary.get('url') or ''}" title="{primary.get('title') or ''}">
{primary.get('text') or ''}
</primary_source>
""".strip()
    result = client.generate_json(
        prompt,
        schema=NEWS_SCHEMA,
        youtube_url=str(entry.get("url") or "") if use_video else "",
        temperature=0.2,
    )
    result["official_url"] = primary.get("url") or ""
    if not result.get("official_name"):
        result["official_name"] = primary.get("title") or urlparse(primary.get("url") or "").hostname or "Primary source"
    result["sourced"] = bool(primary.get("url"))
    result["video_id"] = entry.get("video_id")
    result["video_url"] = entry.get("url")
    result["video_title"] = entry.get("title")
    return result


def _esc(value: Any) -> str:
    return html.escape(str(value or "").strip(), quote=False)


def _clip(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    if limit <= 1:
        return "…"[:limit]
    cut = text[: limit - 1].rstrip()
    if ". " in cut:
        sentence = cut.rsplit(". ", 1)[0]
        if len(sentence) > limit * 0.45:
            return sentence.rstrip(".") + "."
    word = cut.rsplit(" ", 1)[0]
    return (word or cut).rstrip(" ,;:") + "…"


HYPE_WORDS = (
    "insane", "insanely", "huge", "massive", "bombshell", "breaking",
    "unbelievable", "crazy", "mind-blowing", "must-see", "ultimate",
    "best ever", "game changer", "game-changer", "revolutionary",
)


def clean_brief_headline(headline: str) -> str:
    """Repair a headline the model copied from a YouTube title.

    Two failures were measured on real output:
    1. "SpaceXSI Releases Grok Bot" — two brand names glued together
       ("SpaceX's" + "xAI"). A person reads that as one nonsense word.
    2. ALL-CAPS shout words and hype adjectives carried over from the
       video title, which the brief prompt forbids but the model copies
       when the title is written in that register.
    """
    text = re.sub(r"\s+", " ", str(headline or "")).strip()
    if not text:
        return ""
    # Two brand names glued together: "SpaceX's xAI" reached us as "SpaceXSI".
    # Guessing at any capitalised tail would also cut "OpenAI" and "xAI",
    # which are correct words, so match the specific known pairs only.
    text = re.sub(r"\bSpaceX(SI|XAI)\b", "SpaceX xAI", text)
    text = re.sub(r"\bOpen(SI)\b", "OpenAI", text)
    # Shout words become sentence case.
    text = re.sub(
        r"\b[A-Z]{4,}\b",
        lambda m: m.group(0).capitalize() if m.group(0).lower() not in {"API", "SDK", "CLI", "GPU", "CPU", "MP4", "HTML", "CSS", "JSON"} else m.group(0),
        text,
    )
    # Drop a leading hype word that carries no information. The word may be
    # followed by punctuation ("HUGE! Claude 4.5 ships"), so match the word
    # plus any trailing punctuation, not "word + space".
    lowered = text.lower()
    for word in sorted(HYPE_WORDS, key=len, reverse=True):
        if lowered.startswith(word):
            rest = text[len(word) :].lstrip(" !.,:-—")
            if rest:
                text = rest[:1].upper() + rest[1:]
                lowered = text.lower()
            break
    text = text.replace("?!", "?").replace("!!", "!")
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    return text.strip()


def format_ai_brief(brief: dict[str, Any]) -> str:
    """A small magazine page, not a wall of text (owner decision 04.11.2026).

    Short labelled sections, blank lines between blocks, one idea per line,
    explicit links (inline buttons were removed so Telegram can show native
    comments), and a durable channel signature at the end.
    """
    headline = _clip(clean_brief_headline(brief.get("headline") or "") or "Verified AI update", 150)
    happened = [_clip(item, 260) for item in brief.get("what_happened") or [] if str(item).strip()][:5]
    confirmed = [_clip(item, 210) for item in brief.get("confirmed") or [] if str(item).strip()][:3]
    video_only = [_clip(item, 210) for item in brief.get("video_only_claims") or [] if str(item).strip()][:2]
    why = _clip(brief.get("why_devs") or brief.get("summary"), 460)
    summary = _clip(brief.get("summary"), 300)
    question = _clip(brief.get("discussion_question") or "Will you try this in a real project?", 170)
    official_url = str(brief.get("official_url") or "").strip()
    official_name = _clip(brief.get("official_name") or "Primary source", 70)
    video_url = str(brief.get("video_url") or "").strip()
    divider = "──────────────"

    def link(url: str, label: str) -> str:
        return f'<a href="{html.escape(url, quote=True)}">{_esc(label)}</a>' if url.startswith("https://") else ""

    def render(happened_items, confirmed_items, video_items, why_text, summary_text) -> str:
        lines = [
            "🛰️ <b>AI Brief</b> · <i>no ads, no hype</i>",
            "",
            f"<b>{_esc(headline)}</b>",
        ]
        if summary_text:
            lines += ["", f"<blockquote>{_esc(summary_text)}</blockquote>"]
        lines += ["", divider, "", "📰 <b>What shipped</b>", ""]
        lines += [f"▸ {_esc(item)}" for item in happened_items] or ["▸ No concrete announcement could be verified."]
        if why_text:
            lines += ["", divider, "", "🎯 <b>Why developers should care</b>", "", _esc(why_text)]
        receipts = [f"✅ {_esc(item)}" for item in confirmed_items]
        receipts += [f"◻️ <i>Video-only:</i> {_esc(item)}" for item in video_items]
        if receipts:
            lines += ["", divider, "", "🔍 <b>Receipts</b>", "", *receipts]
        sources = [x for x in (
            link(official_url, official_name) if official_url.startswith("https://") else "",
            link(video_url, "WorldofAI video"),
        ) if x]
        if sources:
            lines += ["", divider, "", "🔗 <b>Sources</b>", ""]
            lines += [f"• {item}" for item in sources]
        lines += [
            "", divider, "",
            "💬 <b>Your take?</b>",
            f"<i>{_esc(question)}</i>",
            "❤️ <b>React</b> or leave a comment — it helps us pick the next brief.",
            "",
            '📣 <a href="https://t.me/GitHub_Open_Source"><b>@GitHub_Open_Source</b></a> — daily open-source picks',
            "", CHANNEL_TAG_DIVIDER, "#AINews #OpenSource",
        ]
        return "\n".join(lines)

    output = render(happened, confirmed, video_only, why, summary)
    budget = 3000  # Telegram counts the HTML markup too; stay well under 4096
    while utf16_len(visible_text(output)) > budget and video_only:
        video_only.pop()
        output = render(happened, confirmed, video_only, why, summary)
    while utf16_len(visible_text(output)) > budget and confirmed:
        confirmed.pop()
        output = render(happened, confirmed, video_only, why, summary)
    while utf16_len(visible_text(output)) > budget and len(happened) > 2:
        happened.pop()
        output = render(happened, confirmed, video_only, why, summary)
    while utf16_len(visible_text(output)) > budget and summary:
        summary = _clip(summary, max(120, len(summary) - 100))
        output = render(happened, confirmed, video_only, why, summary)
    while utf16_len(visible_text(output)) > budget and why:
        why = _clip(why, max(160, len(why) - 120))
        output = render(happened, confirmed, video_only, why, summary)
    return output

