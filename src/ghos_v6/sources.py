"""Candidate sources: GitHub Search, Hacker News and public Telegram web.

All network sources are synchronous and dependency-light. They return
plain dictionaries so state serialization remains trivial.
"""
from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlparse

import requests

from .http import USER_AGENT, HttpError, request_json

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
_REPO_URL_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:[/?#\s\"'<>)]|$)",
    re.I,
)
_IMAGE_MD_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_IMAGE_HTML_RE = re.compile(r"<(?:img|source)[^>]+src=[\"']([^\"']+)[\"']", re.I)

BAD_MEDIA_MARKERS = (
    "shields.io",
    "badge",
    "codecov",
    "coveralls",
    "travis-ci",
    "circleci",
    "buy_me_a_coffee",
    "buymeacoffee",
    "ko-fi",
    "sponsor",
    "discord",
    "twitter",
    "github-sponsors",
    "actions/workflows",
)


def full_name_from_url(url: str) -> str:
    match = _REPO_URL_RE.search(str(url or ""))
    if not match:
        return ""
    owner, repo = match.group(1), match.group(2).removesuffix(".git")
    if owner.lower() in {"topics", "search", "features", "marketplace"}:
        return ""
    return f"{owner}/{repo}"


def repo_url(full_name: str) -> str:
    return f"https://github.com/{full_name}"


_TEXT_ONLY_TOPICS = {
    "awesome",
    "awesome-list",
    "list",
    "resources",
    "roadmap",
    "guide",
    "guides",
    "tutorial",
    "tutorials",
    "book",
    "books",
    "notes",
    "cheatsheet",
    "interview",
    "interview-questions",
    "learning",
    "course",
    "prompts",
    "prompt-library",
}
_MEDIA_TOPICS = {"wallpapers", "images", "photos", "videos", "music", "dataset"}


def non_software_reason(item: dict[str, Any]) -> str:
    """Return why a repository is content rather than software.

    GitHub's "language" is the primary signal: guides, prompt packs and
    media collections are almost always detected as Markdown or empty.
    """
    language = str(item.get("language") or "").strip().lower()
    topics = {str(topic).lower() for topic in item.get("topics") or []}
    description = str(item.get("description") or "").lower()
    name = str(item.get("name") or "").lower()

    if language in {"markdown", "tex", "asciidoc", "rst"}:
        return f"language:{language}"
    if topics & _TEXT_ONLY_TOPICS:
        return f"topic:{sorted(topics & _TEXT_ONLY_TOPICS)[0]}"
    if topics & _MEDIA_TOPICS:
        return f"topic:{sorted(topics & _MEDIA_TOPICS)[0]}"
    if name.startswith(("awesome-", "awesome_", "best-of-", "best_of-")):
        return "name:awesome"
    for marker in (
        "curated list",
        "a list of",
        "collection of prompts",
        "cheat sheet",
        "interview questions",
        "roadmap to",
    ):
        if marker in description:
            return f"description:{marker.replace(' ', '-')}"
    return ""


def map_repo(item: dict[str, Any], source: str) -> dict[str, Any]:
    full_name = str(item.get("full_name") or "")
    return {
        "repo_id": item.get("id"),
        "id": item.get("id"),
        "name": item.get("name") or full_name.split("/")[-1],
        "full_name": full_name,
        "url": item.get("html_url") or repo_url(full_name),
        "description": item.get("description") or "",
        "homepage": item.get("homepage") or "",
        "language": item.get("language") or "",
        "stars": item.get("stargazers_count") or 0,
        "stargazers_count": item.get("stargazers_count") or 0,
        "forks": item.get("forks_count") or 0,
        "forks_count": item.get("forks_count") or 0,
        "open_issues": item.get("open_issues_count") or 0,
        "open_issues_count": item.get("open_issues_count") or 0,
        "topics": list(item.get("topics") or []),
        "license": item.get("license") or {},
        "created_at": item.get("created_at") or "",
        "pushed_at": item.get("pushed_at") or "",
        "updated_at": item.get("updated_at") or "",
        "archived": bool(item.get("archived")),
        "fork": bool(item.get("fork")),
        "disabled": bool(item.get("disabled")),
        "default_branch": item.get("default_branch") or "main",
        "sources": [source],
        "source_name": source,
        "non_software_reason": non_software_reason(item),
    }


_SEARCH_SPACING_SECONDS = 2.2


def _rate_limit_wait(response: Any, *, default: float = 8.0) -> float:
    """Respect Retry-After / X-RateLimit-Reset when GitHub throttles us."""
    headers = getattr(response, "headers", None) or {}
    retry_after = str(headers.get("Retry-After") or "").strip()
    if retry_after.isdigit():
        return min(60.0, max(1.0, float(retry_after)))
    reset = str(headers.get("X-RateLimit-Reset") or "").strip()
    if reset.isdigit():
        delta = float(reset) - time.time()
        if 0 < delta <= 60:
            return max(1.0, delta + 0.5)
    return default


class GitHubApi:
    def __init__(self, token: str = ""):
        self.token = token.strip()
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        if self.token:
            self.session.headers["Authorization"] = f"Bearer {self.token}"

    def get(self, path: str, *, params: dict[str, Any] | None = None, accept: str | None = None):
        headers = {}
        if accept:
            headers["Accept"] = accept
        response = self.session.get(GITHUB_API + path, params=params, headers=headers, timeout=30)
        return response

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        accept: str = "",
    ) -> tuple[int, Any, Any]:
        response = self.get(path, params=params, accept=accept)
        try:
            parsed = response.json() if response.content else {}
        except ValueError:
            parsed = {}
        return response.status_code, parsed, response

    def search(
        self,
        query: str,
        *,
        sort: str = "stars",
        per_page: int = 25,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        params = {
            "q": query,
            "sort": sort,
            "order": "desc",
            "per_page": per_page,
            "page": max(1, min(page, 10)),
        }
        # Search API allows 30 requests/minute authenticated.  Spacing the
        # calls keeps every lane alive instead of losing half of them to
        # HTTP 403 secondary rate limits.
        for attempt in range(3):
            status, data, response = self.get_json("/search/repositories", params=params)
            if status == 200 and isinstance(data, dict):
                time.sleep(_SEARCH_SPACING_SECONDS)
                return list(data.get("items") or [])
            if status not in (403, 429):
                break
            wait = _rate_limit_wait(response, default=8 * (attempt + 1))
            logger.info(
                "GitHub search rate limited (status=%s); sleeping %.1fs before retry",
                status,
                wait,
            )
            time.sleep(wait)
        logger.warning("GitHub search failed status=%s query=%r", status, query)
        return []

    def _get_with_retry(self, path: str, *, accept: str = "", params: dict[str, Any] | None = None):
        """Fetch a REST path, waiting out the secondary rate limit.

        Enrichment hits ``/repos/...`` for every candidate.  Without this
        retry the first 403 burst silently emptied the whole Telegram
        lane: 20 repositories arrived with ``stars=0``, the gate rejected
        them, and the queue looked as if the channels had nothing.
        """
        last: tuple[int, Any, Any] = (0, None, None)
        for attempt in range(3):
            status, data, response = self.get_json(path, accept=accept, params=params)
            last = (status, data, response)
            if status not in (403, 429):
                return last
            wait = _rate_limit_wait(response, default=6 * (attempt + 1))
            logger.info(
                "GitHub REST rate limited on %s (status=%s); sleeping %.1fs",
                path,
                status,
                wait,
            )
            time.sleep(wait)
        return last

    def repo(self, full_name: str) -> dict[str, Any] | None:
        status, data, _ = self._get_with_retry(f"/repos/{full_name}")
        if status != 200 or not isinstance(data, dict):
            logger.info("GitHub repo lookup failed %s status=%s", full_name, status)
            return None
        return data

    def contributors_count(self, full_name: str) -> int:
        status, data, response = self._get_with_retry(
            f"/repos/{full_name}/contributors",
            params={"per_page": 1, "anon": "true"},
        )
        if status != 200 or not isinstance(data, list):
            return 0
        link = response.headers.get("Link", "") if response is not None else ""
        match = re.search(r"[?&]page=(\d+)>;\s*rel=\"last\"", link)
        if match:
            return int(match.group(1))
        return len(data)

    def readme(self, full_name: str) -> str:
        response = self.get(
            f"/repos/{full_name}/readme",
            accept="application/vnd.github.raw",
        )
        attempts = 0
        while response.status_code in (403, 429) and attempts < 2:
            attempts += 1
            wait = _rate_limit_wait(response, default=6 * attempts)
            logger.info(
                "GitHub readme rate limited on %s (status=%s); sleeping %.1fs",
                full_name,
                response.status_code,
                wait,
            )
            time.sleep(wait)
            response = self.get(
                f"/repos/{full_name}/readme",
                accept="application/vnd.github.raw",
            )
        if response.status_code != 200:
            return ""
        response.encoding = "utf-8"
        return response.text[:80_000]


def build_search_queries(now: datetime | None = None) -> list[tuple[str, str]]:
    """Rotation that favours *new arrivals* over the same evergreen hits.

    The previous rotation sorted broad queries by "stars", so every run
    returned the same top-100 by popularity (LangChain, Unsloth, …).
    Because the pipeline deduplicates by repository forever, that
    silently consumed the whole universe of "famous" repos and the feed
    would run dry.  Now most queries are bounded by creation date and
    sorted by recency/velocity, with a small "evergreen but actively
    developed" lane so older projects are not lost entirely.
    """
    now = now or datetime.now(timezone.utc)
    pushed_14 = (now - timedelta(days=14)).date().isoformat()
    pushed_45 = (now - timedelta(days=45)).date().isoformat()
    created_7 = (now - timedelta(days=7)).date().isoformat()
    created_30 = (now - timedelta(days=30)).date().isoformat()
    created_90 = (now - timedelta(days=90)).date().isoformat()

    languages_by_day = [
        ["Python", "TypeScript", "Rust"],
        ["Go", "JavaScript", "Java"],
        ["Python", "Kotlin", "PHP"],
        ["TypeScript", "Rust", "C++"],
        ["Python", "Go", "Ruby"],
        ["TypeScript", "Java", "Shell"],
        ["Rust", "Python", "Swift"],
    ]
    languages = languages_by_day[now.weekday() % len(languages_by_day)]

    queries: list[tuple[str, str]] = []
    # 1. fresh arrivals, any language, sorted by momentum
    queries.append((f"created:>={created_7} stars:>=10 archived:false fork:false", "stars"))
    queries.append((f"created:>={created_30} stars:>=50 archived:false fork:false", "stars"))
    queries.append((f"created:>={created_90} stars:>=150 archived:false fork:false", "stars"))
    # 2. per-language fresh lane (birthday problem avoided: daily rotation)
    for language in languages:
        queries.append(
            (f"created:>={created_30} stars:>=25 archived:false fork:false language:{language}", "stars")
        )
    # 3. topic firehose: new projects in areas our audience cares about
    topics_by_day = [
        ["ai-agents", "llm", "developer-tools"],
        ["self-hosted", "cli", "automation"],
        ["security", "observability", "database"],
        ["mcp", "rag", "productivity"],
        ["webdev", "terminal", "rust"],
        ["ai-agents", "self-hosted", "devops"],
        ["llm", "developer-tools", "homelab"],
    ]
    for topic in topics_by_day[now.weekday() % len(topics_by_day)]:
        queries.append(
            (f"topic:{topic} created:>={created_30} stars:>=20 archived:false fork:false", "stars")
        )
    # 4. evergreen lane: older but genuinely active projects, mid-size
    queries.append((f"pushed:>={pushed_14} stars:300..6000 archived:false fork:false", "updated"))
    for language in languages[:2]:
        queries.append(
            (f"pushed:>={pushed_45} stars:100..5000 archived:false fork:false language:{language}", "updated")
        )
    return queries


def merge_candidate(target: dict[str, dict[str, Any]], candidate: dict[str, Any]) -> None:
    full_name = str(candidate.get("full_name") or full_name_from_url(str(candidate.get("url") or "")))
    if not full_name:
        return
    candidate["full_name"] = full_name
    key = full_name.lower()
    if key not in target:
        target[key] = candidate
        return
    existing = target[key]
    sources = list(existing.get("sources") or [])
    for source in candidate.get("sources") or []:
        if source not in sources:
            sources.append(source)
    existing["sources"] = sources
    if candidate.get("hn_points"):
        existing["hn_points"] = max(int(existing.get("hn_points") or 0), int(candidate["hn_points"]))
    for field in ("repo_id", "id", "name", "description", "homepage", "language", "license"):
        if not existing.get(field) and candidate.get(field):
            existing[field] = candidate[field]


def collect_GitHub_search(
    api: GitHubApi,
    merged: dict[str, dict[str, Any]],
    *,
    pages: list[int] | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    page_list = pages or [(now.timetuple().tm_yday % 4) + 1]
    for query, sort in build_search_queries(now):
        for page in page_list:
            for item in api.search(query, sort=sort, per_page=25, page=page):
                merge_candidate(merged, map_repo(item, "github_search"))
        time.sleep(0.15)


def collect_hn(merged: dict[str, dict[str, Any]], *, days: int = 7) -> None:
    """Hacker News stories that link to a repository.

    Pages 1-3 of the last N days; each hit carries its points so rank.py
    can value community validation instead of raw star count.
    """
    since = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    for page in range(3):
        status, data, _ = request_json(
            "GET",
            "https://hn.algolia.com/api/v1/search_by_date",
            params={
                "query": "github.com",
                "tags": "story",
                "hitsPerPage": 100,
                "page": page,
                "numericFilters": f"created_at_i>{since},points>=5",
            },
            retries=1,
        )
        if status != 200 or not isinstance(data, dict):
            logger.warning("HN Algolia failed status=%s page=%s", status, page)
            return
        hits = data.get("hits") or []
        if not hits:
            return
        for hit in hits:
            url = str(hit.get("url") or "")
            full_name = full_name_from_url(url)
            if not full_name:
                continue
            merge_candidate(
                merged,
                {
                    "full_name": full_name,
                    "name": full_name.split("/")[-1],
                    "url": repo_url(full_name),
                    "hn_points": int(hit.get("points") or 0),
                    "sources": ["hacker_news"],
                    "source_name": "hacker_news",
                },
            )


@dataclass(frozen=True)
class SourceChannel:
    """A Telegram channel that is a *verified* daily source.

    Recorded so the choice is auditable instead of a name in an env var:
    measured posts/day and GitHub-link density decide whether a channel
    stays in the rotation, and the note says why it was kept.
    """

    handle: str
    posts_per_day: float
    github_links: int
    github_posts: int
    checked_on: str
    note: str = ""


# Measured 2026-11-01 through the public t.me/s preview (no account
# needed). Only channels that post every day are kept; a dead channel costs
# a fetch per run and adds nothing.
VERIFIED_DAILY_SOURCES: tuple[SourceChannel, ...] = (
    SourceChannel("open_source_friend", 3.2, 20, 20, "2026-11-01", "owner's channel"),
    SourceChannel("github_repos", 4.2, 20, 20, "2026-11-01", ""),
    SourceChannel("gitbless", 5.8, 19, 20, "2026-11-01", ""),
    SourceChannel("bestofgithub", 1.1, 20, 20, "2026-11-01", ""),
    SourceChannel("github", 7.6, 19, 20, "2026-11-02", "highest volume of the set"),
)

# Dropped, with the measurement that dropped them. Kept here so nobody
# re-adds a dead channel by guessing.
REJECTED_SOURCES: dict[str, str] = {
    "githubtrending": "last post 2026-09-23 (~40 days of silence)",
    "gitradar_": "0 posts in the public preview",
    "githubawesome": "last post 2026-03-12",
    "opensource_ai": "3 posts in 16 days, 0 GitHub links",
    "ai_newz": "4 GitHub links in 18 days — a news channel, not a project source",
    "opensourceprojects": "last post 2020",
}


def verified_channel_handles() -> tuple[str, ...]:
    """Handles of the verified daily sources, in priority order."""
    return tuple(source.handle for source in VERIFIED_DAILY_SOURCES)


def collect_telegram_web(merged: dict[str, dict[str, Any]], channels: tuple[str, ...]) -> None:
    for channel in channels:
        url = f"https://t.me/s/{channel}"
        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
            if response.status_code != 200:
                logger.info("Telegram web source %s status=%s", channel, response.status_code)
                continue
            text = html.unescape(response.text)
        except requests.RequestException as exc:
            logger.warning("Telegram web source %s failed: %s", channel, type(exc).__name__)
            continue
        found: set[str] = set()
        for owner, repo in _REPO_URL_RE.findall(text):
            full_name = f"{owner}/{repo.removesuffix('.git')}"
            if full_name.lower() in found:
                continue
            found.add(full_name.lower())
            merge_candidate(
                merged,
                {
                    "full_name": full_name,
                    "name": full_name.split("/")[-1],
                    "url": repo_url(full_name),
                    "sources": [f"telegram:{channel}"],
                    "source_name": f"telegram:{channel}",
                },
            )


# Measured 2026-11-01 via the public t.me/s/ preview.  The owner's rule is
# "channels that really post every day, not ones that wait weeks":
#   open_source_friend   3.2 posts/day, 20/20 GitHub links  (owner's channel)
#   github_repos         4.2 posts/day, 20/20 links
#   gitbless             5.8 posts/day, 19/20 links
#   bestofgithub         1.1 posts/day, 20/20 links
# Dropped because they are dead or empty:
#   githubtrending  last post 2026-09-23 (~40 days), 2.2/day while alive
#   gitradar_       0 posts in the preview
#   githubawesome   last post 2026-03-12
#   opensource_ai   3 posts in 16 days, 0 GitHub links
#   ai_newz         4 GitHub links in 18 days — a news channel, not a source
#   opensourceprojects  last post 2020
# Defaults must be channels that actually answer, so a missing env var
# cannot quietly empty the Telegram half of the fuel supply.
DEFAULT_TELEGRAM_SOURCES = verified_channel_handles()


def collect_candidates(
    config: Any,
    *,
    extra_pages: list[int] | None = None,
) -> list[dict[str, Any]]:
    api = GitHubApi(config.GitHub_token)
    merged: dict[str, dict[str, Any]] = {}
    collect_GitHub_search(api, merged, pages=extra_pages)
    collect_hn(merged)
    channels = tuple(config.telegram_source_channels or DEFAULT_TELEGRAM_SOURCES)
    collect_telegram_web(merged, channels)

    # Repo farms: one owner shipping a dozen near-identical repos should
    # not take over the queue.  Counting here keeps rank/gate cheap.
    per_owner: dict[str, int] = {}
    for candidate in merged.values():
        owner = str(candidate.get("full_name") or "").split("/")[0].lower()
        if owner:
            per_owner[owner] = per_owner.get(owner, 0) + 1
    for candidate in merged.values():
        owner = str(candidate.get("full_name") or "").split("/")[0].lower()
        candidate["owner_repo_count"] = per_owner.get(owner, 0)
    return list(merged.values())


def resolve_media_url(raw: str, full_name: str, default_branch: str) -> str:
    value = html.unescape(str(raw or "").strip().strip("<>"))
    if not value or value.startswith("data:"):
        return ""
    if value.startswith("//"):
        value = "https:" + value
    if value.startswith("http://"):
        value = "https://" + value[len("http://"):]
    if value.startswith("https://github.com/") and "/blob/" in value:
        value = value.replace("https://github.com/", "https://raw.githubusercontent.com/").replace("/blob/", "/")
    if value.startswith("https://"):
        return value
    value = unquote(value)
    while value.startswith("./"):
        value = value[2:]
    value = value.lstrip("/")
    return f"https://raw.githubusercontent.com/{full_name}/{default_branch}/{value}"


def extract_media_urls(readme: str, full_name: str, default_branch: str = "main") -> list[str]:
    raw_urls: list[str] = []
    raw_urls.extend(_IMAGE_MD_RE.findall(readme or ""))
    raw_urls.extend(_IMAGE_HTML_RE.findall(readme or ""))
    scored: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_urls):
        url = resolve_media_url(raw, full_name, default_branch)
        if not url or url in seen:
            continue
        seen.add(url)
        lower = url.lower()
        if any(marker in lower for marker in BAD_MEDIA_MARKERS):
            continue
        parsed = urlparse(url)
        path = parsed.path.lower()
        if not (
            path.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
            or "user-images.githubusercontent.com" in parsed.netloc
            or "githubusercontent.com" in parsed.netloc
        ):
            continue
        score = 0
        if path.endswith(".gif"):
            score += 40
        for word in ("demo", "screenshot", "screen", "preview", "banner", "hero", "cover"):
            if word in lower:
                score += 15
        if any(word in lower for word in ("logo", "icon", "avatar")):
            score -= 30
        scored.append((score, -index, url))
    scored.sort(reverse=True)
    return [url for _, _, url in scored[:8]]


def enrich_candidate(
    api: GitHubApi, candidate: dict[str, Any], *, force: bool = False
) -> dict[str, Any]:
    full_name = str(candidate.get("full_name") or full_name_from_url(str(candidate.get("url") or "")))
    if not full_name:
        return candidate
    candidate["full_name"] = full_name
    if force or not candidate.get("repo_id") or not candidate.get("license"):
        raw_repo = api.repo(full_name)
        if raw_repo:
            mapped = map_repo(raw_repo, str((candidate.get("sources") or ["unknown"])[0]))
            sources = candidate.get("sources") or []
            hn_points = candidate.get("hn_points")
            candidate.update(mapped)
            candidate["sources"] = sources or mapped.get("sources") or []
            if hn_points is not None:
                candidate["hn_points"] = hn_points
    if not candidate.get("repo_id"):
        # Enrichment failed: starring data is unknown, not zero. Marking it
        # keeps the gate from rejecting a live project as "0 stars".
        candidate["enrich_failed"] = True
        logger.warning("Enrichment incomplete for %s: repository metadata unavailable", full_name)
    candidate["contributors_count"] = api.contributors_count(full_name)
    readme = api.readme(full_name)
    candidate["has_readme"] = bool(readme.strip())
    candidate["readme"] = readme
    candidate["readme_excerpt"] = readme[:6000]
    candidate["media_urls"] = extract_media_urls(
        readme,
        full_name,
        str(candidate.get("default_branch") or "main"),
    )
    return candidate


def _telegram_lane_rank(candidate: dict[str, Any]) -> tuple[int, int]:
    """Priority inside the Telegram lane: channels first, then the rest."""
    sources = [str(s) for s in (candidate.get("sources") or [])]
    owner_channel = any(s == "telegram:open_source_friend" for s in sources)
    telegram = any(s.startswith("telegram:") for s in sources)
    return (0 if owner_channel else 1 if telegram else 2, 0)


def enrich_candidates(
    api: GitHubApi,
    candidates: list[dict[str, Any]],
    *,
    max_enrich: int = 90,
    telegram_reserve: int = 30,
) -> list[dict[str, Any]]:
    """Enrich the ranked list, but never starve the Telegram lane.

    ``collect_candidates`` returns Telegram projects without stars (the
    public preview has no numbers), so ranking by popularity pushed every
    one of them past ``max_enrich`` and the owner's own channel never
    reached the gate. A reserve slice is therefore filled from Telegram
    sources first: owner channel, then other channels, then everyone by
    rank.
    """
    ordered = list(candidates[:max_enrich])
    chosen = {str(c.get("full_name") or "").lower() for c in ordered}
    reserve: list[dict[str, Any]] = []
    if telegram_reserve > 0:
        free_slots = max(0, telegram_reserve)
        pool = sorted(
            (c for c in candidates if any(str(s).startswith("telegram:") for s in (c.get("sources") or []))),
            key=_telegram_lane_rank,
        )
        for candidate in pool:
            if free_slots <= 0:
                break
            key = str(candidate.get("full_name") or "").lower()
            if key in chosen:
                continue
            reserve.append(candidate)
            chosen.add(key)
            free_slots -= 1
    target = ordered + reserve
    enriched: list[dict[str, Any]] = []
    for candidate in target:
        try:
            enriched.append(enrich_candidate(api, candidate))
        except (requests.RequestException, HttpError) as exc:
            logger.warning("Enrichment failed for %s: %s", candidate.get("full_name"), type(exc).__name__)
    return enriched
