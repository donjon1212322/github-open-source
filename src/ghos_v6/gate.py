"""Autonomous quality and safety gate.

There is no human approval step, so this gate is intentionally
conservative. Doubt means rejection; legitimate dual-use security
projects can pass only with an explicit flag that the formatter turns
into a warning.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .audience import is_narrow

BLOCK_PATTERNS = [
    r"\bstealer\b",
    r"\bkeylogger\b",
    r"\btoken grabber\b",
    r"\bcookie stealer\b",
    r"\bcredential harvesting\b",
    r"\bphishing (?:kit|page|tool)\b",
    r"\bwallet drainer\b",
    r"\bcrypto drainer\b",
    r"\bpassword cracker\b",
    r"\baccount cracker\b",
    r"\bkeygen\b",
    r"\bsms bomber\b",
    r"\bddos (?:tool|script|attack)\b",
    r"\bremote access trojan\b",
    r"\baimbot\b",
    r"\bgame cheat\b",
    r"\bbypass paywall\b",
    r"\bspam bot\b",
    r"\bcarding\b",
    r"\bchecker combo\b",
    r"\bfake (?:followers|likes)\b",
    r"\bransomware\b",
    r"\bbotnet\b",
    # jailbreak / uncensoring prompt packs: popular in 2026 but not a
    # product our audience should be pointed at
    r"\bjailbreak\b",
    r"\buncensored (?:prompt|model|llm)\b",
    r"\bbypass (?:safety|guardrails|filter)\b",
    r"\bprompt injection (?:pack|collection|tools)\b",
    # piracy and licence-laundering
    r"\bpirated? (?:games?|movies?|software|content)\b",
    r"\bbypass (?:drm|licen[cs]e|activation)\b",
    r"\bgame (?:rom|dump)s? download\b",
    # pure link-list repositories are not projects
    r"\bcurated list of (?:viral|trending|awesome)\b",
    r"\bcollection of (?:viral|trending) (?:videos|prompts|links)\b",
    # free-tier abuse: "use every paid model without an account/key"
    r"\bno (?:api )?key (?:needed|required)\b",
    r"\bwithout (?:an )?(?:account|subscription|api key)\b",
    r"\bfree (?:premium|paid) (?:access|api)\b",
    # obvious malware-distribution channels
    r"\bmega\.nz\b",
    r"\bmediafire\b",
    r"\bgofile\.io\b",
    r"\banonfiles\b",
    r"\bcracked (?:software|app|game|tool)\b",
    r"\bkeygen\b",
    r"\bactivation crack\b",
]

#: Hosts that distribute binaries rather than source. A link to one of these
#: in a README is a strong signal that the "project" is a download funnel.
_EXTERNAL_DOWNLOAD_RE = re.compile(
    r"(?:mega\.nz|mediafire\.com|dropbox\.com|gofile\.io|anonfiles\.com|"
    r"mega\.co\.nz|rapidgator\.net|nitroflare\.com)",
    re.I,
)

#: "password for archive" / "passphrase: ..." is a classic malware pattern.
_ARCHIVE_PASSWORD_RE = re.compile(
    r"(?:archive|zip|rar|7z)[^\n]{0,40}(?:password|passphrase)|"
    r"(?:password|passphrase)[^\n]{0,40}(?:archive|zip|rar|7z)",
    re.I,
)
_BLOCK_RE = re.compile("|".join(f"(?:{p})" for p in BLOCK_PATTERNS), re.I)

_DUAL_USE_TERMS = {
    "security",
    "penetration-testing",
    "pentest",
    "vulnerability",
    "vulnerability-scanner",
    "exploit",
    "osint",
    "network-scanner",
    "password-audit",
    "red-team",
    "ctf",
}


@dataclass(frozen=True)
class GateConfig:
    min_stars: int = 100
    gem_min_stars: int = 30
    gem_min_stars_per_day: float = 2.0
    max_staleness_days: int = 180
    min_readme_chars: int = 500
    min_contributors: int = 2
    min_forks: int = 5
    # an "awesome-*"/awesome list is curation, not software
    max_files: int = 0
    # velocity alone should not buy airtime: a 40k-star repo that is one
    # day old is almost always manufactured
    star_velocity_ceiling: float = 2500.0
    # and a sustained >6k stars/day across a whole month is a campaign
    star_daily_ceiling: float = 6000.0
    # the owner's rule: "interesting projects, not technical libraries for a
    # very narrow circle".  A project that calls itself a library/SDK/client
    # must show at least this many "a person can use it" signals.
    narrow_min_appeal: int = 1
    # below this many appeal signals a project is technically passable but
    # not worth a slot while better material is queued.
    interesting_min_appeal: int = 1


@dataclass
class GateDecision:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    is_hidden_gem: bool = False
    is_dual_use: bool = False


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def license_spdx(project: dict[str, Any]) -> str:
    lic = project.get("license")
    if isinstance(lic, dict):
        spdx = str(lic.get("spdx_id") or "").strip()
        if spdx.upper() in {"NOASSERTION", "NONE"}:
            return ""
        return spdx
    text = str(lic or "").strip()
    return "" if text.upper() in {"NOASSERTION", "NONE"} else text


def _int(project: dict[str, Any], *keys: str) -> int:
    for key in keys:
        try:
            if project.get(key) is not None:
                return int(project[key])
        except (TypeError, ValueError):
            continue
    return 0


def is_dual_use(project: dict[str, Any]) -> bool:
    topics = {str(t).lower() for t in project.get("topics") or []}
    if topics & _DUAL_USE_TERMS:
        return True
    safety = str(project.get("safety") or "").lower()
    if safety == "dual_use":
        return True
    haystack = f"{project.get('name') or ''} {project.get('description') or ''}".lower()
    return any(term.replace("-", " ") in haystack for term in ("penetration testing", "vulnerability scanner", "red team"))


def evaluate(
    project: dict[str, Any],
    cfg: GateConfig = GateConfig(),
    *,
    require_safety: bool = False,
) -> GateDecision:
    reasons: list[str] = []
    now = datetime.now(timezone.utc)

    # The pre-LLM gate runs before the model has assigned safety, so a missing
    # field is only fatal when the caller says safety must already be there
    # (the post-LLM gate). A present-but-invalid value is always fatal, and
    # "reject" is always fatal.
    if "safety" in project:
        safety = str(project.get("safety") or "").lower()
        if safety not in {"ok", "dual_use"}:
            reasons.append(f"safety:{safety or 'missing'}")
    elif require_safety:
        reasons.append("safety:missing")

    haystack = " ".join(
        [
            str(project.get("name") or ""),
            str(project.get("full_name") or ""),
            str(project.get("description") or ""),
            str(project.get("primary_use_case") or ""),
        ]
        + [str(t) for t in project.get("topics") or []]
    )
    blocked = _BLOCK_RE.search(haystack)
    if blocked:
        reasons.append(f"blocklist:{blocked.group(0).lower()}")

    if project.get("archived"):
        reasons.append("archived")
    if project.get("fork"):
        reasons.append("fork")
    if project.get("disabled"):
        reasons.append("disabled")
    if not license_spdx(project):
        reasons.append("no_license")

    created = parse_dt(project.get("created_at"))
    if created is not None and created > now:
        reasons.append("created_in_future")

    pushed = parse_dt(project.get("pushed_at") or project.get("last_commit_date") or project.get("updated_at"))
    if pushed is None:
        reasons.append("no_push_date")
    elif pushed > now:
        reasons.append("pushed_in_future")
    elif (now - pushed).days > cfg.max_staleness_days:
        reasons.append(f"stale>{cfg.max_staleness_days}d")

    contributors = _int(project, "contributors_count")
    forks = _int(project, "forks", "forks_count")
    issues = _int(project, "open_issues", "open_issues_count")
    community = contributors >= cfg.min_contributors or forks >= cfg.min_forks or issues >= 1
    if not community:
        reasons.append("no_community")

    readme = str(project.get("readme") or project.get("readme_excerpt") or "")
    if len(readme.strip()) < cfg.min_readme_chars:
        reasons.append("no_readme")
    if readme:
        if _EXTERNAL_DOWNLOAD_RE.search(readme):
            reasons.append("external_download_host")
        if _ARCHIVE_PASSWORD_RE.search(readme):
            reasons.append("archive_password")

    # The channel publishes in English.  A README written almost only in
    # CJK (or any non-Latin script) cannot be summarised faithfully and
    # the linked article would not help an English-reading audience.
    description = str(project.get("description") or "")
    if description:
        desc_letters = [ch for ch in description if ch.isalpha()]
        if len(desc_letters) >= 20:
            latin = sum(1 for ch in desc_letters if ch.isascii())
            if latin / len(desc_letters) < 0.6:
                reasons.append("description_not_english")
    if readme:
        sample = readme[:4000]
        letters = [ch for ch in sample if ch.isalpha()]
        if len(letters) >= 200:
            latin = sum(1 for ch in letters if ch.isascii())
            if latin / len(letters) < 0.6:
                reasons.append("readme_not_english")

    stars = _int(project, "stars", "stargazers_count")
    is_gem = False
    if stars < cfg.min_stars:
        if created is None or created > now:
            reasons.append(f"stars<{cfg.min_stars}")
        else:
            age_days = max(1, (now - created).days)
            velocity = stars / age_days
            if stars >= cfg.gem_min_stars and velocity >= cfg.gem_min_stars_per_day:
                is_gem = True
            else:
                reasons.append(f"stars<{cfg.min_stars}")

    # Manufactured popularity: a very young repository with an extreme
    # star count is a launch campaign, not a discovery.  Keep it out of
    # the daily slot so the channel does not become a hype mirror.
    if created is not None and created <= now and stars > 0:
        age_days = max(1, (now - created).days)
        velocity = stars / age_days
        ceiling = (
            cfg.star_velocity_ceiling
            if age_days <= 14
            else cfg.star_daily_ceiling
        )
        if velocity > ceiling:
            reasons.append("velocity_too_high")

    # Non-software repositories: guides, notes, prompt dumps, media
    # collections.  A "living guide" can have 70k stars and still be a
    # Markdown file, which is not what this channel publishes.
    non_software = str(project.get("non_software_reason") or "")
    if non_software:
        reasons.append(f"not_software:{non_software}")

    # Curation repositories: "awesome-x", "best-of-x", link farms.
    name = str(project.get("name") or "").lower()
    description = str(project.get("description") or "").lower()
    if name.startswith(("awesome", "best-of", "best_of")) or "curated list" in description and "list" in name:
        reasons.append("curation_repo")

    # Narrow audience: building blocks with nothing a reader can open.
    narrow, narrow_reason = is_narrow(project, min_appeal=cfg.narrow_min_appeal)
    if narrow:
        reasons.append(f"narrow:{narrow_reason}")

    dual = is_dual_use(project)
    return GateDecision(
        ok=not reasons,
        reasons=reasons,
        is_hidden_gem=is_gem and not reasons,
        is_dual_use=dual,
    )
