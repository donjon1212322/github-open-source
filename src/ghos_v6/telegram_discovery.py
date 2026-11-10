"""Telegram-channel discovery for the AI Brief (owner decision 2026-11-07).

Replaces the YouTube / World-of-AI round-up as the *only* discovery input.
Owner corrections that are deliberately encoded here:

* No RSS feeds and no separate evidence/primary-feed layer. The only input is
  public ``t.me/s`` pages of independent, non-official channels.
* "Noise" is NOT filtered. Collections ("10 projects"), unusual model lists
  and IP trust-score pages are exactly the rare, informative material the
  owner asked for. Only advertising is removed.
* Rumours are KEPT, but always marked ``unconfirmed_rumour`` and kept apart
  from confirmed stories.
* Status is inferred from the domain of a link *inside the post*; there is no
  live HTTP verification of those links (owner decision). A missing link does
  not drop a post - it lowers its status.

The module is pure-Python and network-injectable: tests pass a ``fetcher``.
"""
from __future__ import annotations

import hashlib
import html
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable
from urllib.request import Request, urlopen

from .http import USER_AGENT
from .rumours import clean_rumour_text, is_rumour_relevant

# Six daily signal channels + two hint channels that carry the leaks the owner
# explicitly asked to keep. All are independent, non-official channels.
DAILY_CHANNELS: tuple[str, ...] = (
    "neuro_channel",
    "LLMScience",
    "machinelearning_interview",
    "data_secrets",
    "bestaitoolsai",
    "mlsecfeed",
)
RUMOUR_CHANNELS: tuple[str, ...] = ("Futuris", "testingcatalog")
CHANNELS: tuple[str, ...] = DAILY_CHANNELS + RUMOUR_CHANNELS

# Domains that count as a primary *signal* inside a channel post. This is a
# classification hint, not proof and not a fetched feed.
PRIMARY_DOMAINS: tuple[str, ...] = (
    "arxiv.org", "github.com", "huggingface.co", "openai.com", "anthropic.com",
    "blog.google", "deepmind.google", "thinkingmachines.ai", "metr.org",
    "cognition.com", "cursor.com", "openrouter.ai", "developers.openai.com",
    "ai.google.dev", "mistral.ai", "qwen.ai", "blogs.nvidia.com", "nvidia.com",
    "cdn.deepseek.com", "alpinelinux.org", "marktechpost.com",
)
RESEARCH_DOMAINS: tuple[str, ...] = (
    "arxiv.org", "metr.org", "cognition.com", "marktechpost.com",
    "deepmind.google",
)
SECONDARY_DOMAINS: tuple[str, ...] = (
    "x.com", "twitter.com", "medium.com", "telegra.ph", "vk.ru",
    "youtu.be", "youtube.com", "clck.ru", "clc.to", "rss.bz", "cnbeta",
)
PROMO_MARKERS: tuple[str, ...] = (
    "реклам", "сотрудничеств", "курс", "промокод", "партнёр", "скидк",
    "вебинар", "fast track", "#промо", "получите оффер", "за 800",
    "за 2 года", "подписывай", "в 48 раз", "оплачиваем", "ваканс",
)

# Explicit owner correction: these are KEPT, never filtered as "noise".
KEEP_COLLECTIONS: tuple[str, ...] = (
    "10 open source", "10 open-source", "подборк", "без встроенной цензуры",
    "trust score", "49 ", "11 skills", "сборник", "дайджест",
)

Fetcher = Callable[[str], str]

# Cluster by the SUBJECT, not just by the first URL: the same launch is often
# posted with a launch post, a model card and a docs link in different channels.
STORY_KEYS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("gemini-omni-1-2-flash", ("gemini omni 1.2", "gemini-omni-1-2-flash", "omni 1.2 flash")),
    ("gpt-6-cyber", ("gpt-6 cyber", "gpt-6-cyber", "948 уязвимостей", "кибермодел")),
    ("gpt-image-2-6", ("images 2.6", "gpt image 2.6", "image-prompting", "introducing-images-2-6")),
    ("inkling-2-small", ("inkling 2 small", "inkling-2-small")),
    ("metr-rnd", ("metr", "ускоряет разработку моделей")),
    ("rsa-260", ("rsa-260", "260-значн", "35-летнюю математическую")),
    ("flashprove", ("flashprove", "provable inference")),
    ("mistral-large-4", ("mistral large 4", "mistral-large-4")),
    ("ripple-65b", ("ripple 65b", "ripple-65b")),
    ("deepseek-super-agent", ("super agent", "dsh-superagent")),
    ("cursor-live-apps", ("build-live-apps", "облачного агента")),
    ("trust-score", ("trust score",)),
    ("shopping-agent-oss", ("агента по покупкам", "langchain-ai/langchain")),
    ("uncensored-models", ("без встроенной цензуры", "huihui-ai")),
)

# Only genuine leaks/teasers become rumours. A link-less joke is kept in the
# corpus but must not be labelled "unconfirmed rumour".
RUMOUR_MARKERS: tuple[str, ...] = (
    "может выйти", "ждем", "ждут", "ждём", "upcoming", "еще не вышедш",
    "утечк", "слух", "инсайд", "leaker", "leak", "unannounced",
    "teaser", "hint", "astra", "sonnet 6", "gpt-6.2", "fable",
    "hidden ui", "testingcatalog", "ожидается", "возможно",
)



def default_fetcher(url: str) -> str:
    """Fetch one public Telegram preview page with a bounded timeout."""
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en,ru;q=0.8"})
    with urlopen(req, timeout=20) as response:  # noqa: S310 - public read-only URL
        return response.read().decode("utf-8", errors="replace")


def parse_views(raw: str | None) -> int | None:
    """Telegram renders views as ``188``, ``19.4K`` or ``1.2M``."""
    if not raw:
        return None
    match = re.match(r"^([\d.,]+)\s*([KM]?)$", raw.strip().replace("\xa0", ""), re.I)
    if not match:
        return None
    multiplier = {"": 1, "K": 1_000, "M": 1_000_000}[match.group(2).upper()]
    return int(float(match.group(1).replace(",", ".")) * multiplier)


def parse_channel_page(page: str, channel: str) -> list[dict[str, Any]]:
    """Extract posts from a public ``t.me/s/<channel>`` HTML page."""
    posts: list[dict[str, Any]] = []
    pattern = re.compile(
        r'data-post="' + re.escape(channel) + r'/(\d+)"(.*?)(?=data-post="|\Z)', re.S
    )
    for match in pattern.finditer(page):
        block = match.group(2)
        when = re.search(r'datetime="([^"]+)"', block)
        views = re.search(r"tgme_widget_message_views[^>]*>([^<]+)", block)
        body = re.search(r"tgme_widget_message_text[^>]*>(.*?)</div>", block, re.S)
        text = ""
        if body:
            text = html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body.group(1)))).strip()
        links = [
            html.unescape(link)
            for link in re.findall(r'href="(https?://[^"]+)"', block)
            if "t.me/" not in link
        ]
        posts.append(
            {
                "channel": channel,
                "post_id": match.group(1),
                "published_at": when.group(1) if when else "",
                "views": parse_views(views.group(1) if views else None),
                "text": text,
                "links": links,
            }
        )
    return posts


def is_promo(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in PROMO_MARKERS)


def canonical_url(url: str) -> str:
    return re.sub(r"[#?].*$", "", url).rstrip("/").lower()


def primary_links(post: dict[str, Any]) -> list[str]:
    return [link for link in post["links"] if any(dom in link.lower() for dom in PRIMARY_DOMAINS)]


def secondary_links(post: dict[str, Any]) -> list[str]:
    return [link for link in post["links"] if any(dom in link.lower() for dom in SECONDARY_DOMAINS)]


def infer_status(post: dict[str, Any]) -> str:
    """Coarse evidence level from the link *inside* the post. Never proof."""
    primary = primary_links(post)
    if any(any(dom in link.lower() for dom in RESEARCH_DOMAINS) for link in primary):
        return "research_report"
    if primary:
        return "official_release"
    blob = post["text"].lower()
    if (
        post["channel"] in RUMOUR_CHANNELS
        or any(marker in blob for marker in RUMOUR_MARKERS)
        or (secondary_links(post) and not primary_links(post))
    ):
        return "unconfirmed_rumour"
    return "demo_or_benchmark"


def cluster_key(post: dict[str, Any]) -> str:
    blob = (post["text"] + " " + " ".join(post["links"])).lower()
    for name, markers in STORY_KEYS:
        if any(marker in blob for marker in markers):
            return "story:" + name
    links = primary_links(post)
    if links:
        return canonical_url(links[0])
    words = re.sub(r"[^a-zа-я0-9 ]+", "", post["text"].lower()).split()[:7]
    return "text:" + "-".join(words) if words else f"post:{post['channel']}:{post['post_id']}"


def cluster_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for post in posts:
        buckets[cluster_key(post)].append(post)
    clusters: list[dict[str, Any]] = []
    for key, group in buckets.items():
        urls: list[str] = []
        for post in group:
            for link in primary_links(post):
                if canonical_url(link) not in {canonical_url(u) for u in urls}:
                    urls.append(link)
        clusters.append(
            {
                "key": key,
                "posts": group,
                "channels": sorted({post["channel"] for post in group}),
                "primary_urls": urls[:3],
                "status": min(
                    (infer_status(post) for post in group),
                    key=lambda s: {
                        "official_release": 0,
                        "research_report": 1,
                        "demo_or_benchmark": 2,
                        "unconfirmed_rumour": 3,
                    }.get(s, 4),
                ),
                "text": " ".join(post["text"] for post in group)[:1600],
                "views": max((post["views"] or 0) for post in group),
            }
        )
    clusters.sort(key=lambda c: (-len(c["posts"]), -c["views"]))
    return clusters


def _iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_digest_material(
    *,
    now: datetime | None = None,
    fetcher: Fetcher | None = None,
    channels: tuple[str, ...] = CHANNELS,
    window_hours: int = 48,
    exclude_keys: Iterable[str] = (),
) -> dict[str, Any]:
    """Read channels, drop only ads, cluster duplicates, keep rumours.

    ``exclude_keys`` removes clusters already printed in a recent brief, so a
    48-hour window cannot refill today's corpus with yesterday's stories.
    Returns a payload compatible with the existing AI Brief pipeline plus
    ``rumours``, ``clusters`` and ``stats`` for the new formatter.
    """
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(hours=window_hours)
    get = fetcher or default_fetcher
    raw: list[dict[str, Any]] = []
    errors: list[str] = []
    for channel in channels:
        try:
            raw.extend(parse_channel_page(get(f"https://t.me/s/{channel}"), channel))
        except Exception as exc:  # noqa: BLE001 - one dead channel must not kill the brief
            errors.append(f"{channel}:{type(exc).__name__}")

    recent = [post for post in raw if post["published_at"] and post["published_at"] >= cutoff.isoformat()]
    ads = [post for post in recent if is_promo(post["text"])]
    kept = [post for post in recent if not is_promo(post["text"])]
    clusters = cluster_posts(kept)
    excluded = set(exclude_keys or ())
    removed = len([c for c in clusters if c["key"] in excluded])
    clusters = [c for c in clusters if c["key"] not in excluded]
    confirmed = [c for c in clusters if c["status"] != "unconfirmed_rumour"]
    rumours = [
        c for c in clusters
        if c["status"] == "unconfirmed_rumour"
        and is_rumour_relevant(c["text"], english_only=False)
    ]

    # Model news first, at most one corporate/IPO story; collections stay in.
    ordered = sorted(
        confirmed,
        key=lambda c: (
            0 if c["status"] == "official_release" else 1,
            -len(c["posts"]),
            -c["views"],
        ),
    )
    selected = ordered[:7]
    corpus = "\n\n".join(
        f"[{c['status']}] {c['text']}\nlinks: {', '.join(c['primary_urls']) or 'none'}"
        for c in selected + rumours[:3]
    )[:8000]
    digest_day = current.astimezone(timezone.utc).strftime("%Y%m%d")
    fingerprint = hashlib.sha256(
        "|".join(c["key"] for c in selected).encode("utf-8")
    ).hexdigest()[:10]
    entry = {
        "title": "Telegram channel digest",
        "description": corpus,
        "published_at": _iso(current),
        "video_id": f"tg-{digest_day}-{fingerprint}",
        "url": "https://t.me/GitHub_Open_Source",
    }
    return {
        "entry": entry,
        "primary": {"text": corpus, "url": "", "title": "channel posts"},
        # Owner 08.11 (second duplicate incident): channel clusters can be a
        # chain of "> ..." quotes and emoji reposts. Keep one clean claim.
        "rumours": [clean_rumour_text(c["text"], limit=400) for c in rumours[:3]],
        "clusters": selected,
        "rumour_keys": [c["key"] for c in rumours[:3]],
        "stats": {
            "raw": len(raw),
            "recent": len(recent),
            "ads_removed": len(ads),
            "kept": len(kept),
            "clusters": len(clusters),
            "confirmed": len(confirmed),
            "rumours": len(rumours),
            "channels": len(channels),
            "errors": errors,
            "excluded": removed,
        },
    }
