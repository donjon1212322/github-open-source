#!/usr/bin/env python3
"""Self-contained mutation gate: every intentional defect must be caught.

No extra dependency, so the hashed lock files stay untouched. The repository
is copied to a temporary directory, one textual mutation is applied at a
time, and the test suite must fail. A surviving mutation means the suite can
go green while an owner decision is broken - something line coverage cannot
detect.

Run locally with ``python tools/mutation_check.py``; CI runs it on every push.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# (relative path, original text, mutated text, why this is a real defect)
MUTATIONS: list[tuple[str, str, str, str]] = [
    ("src/ghos_v6/pipeline.py", "MAX_PROJECT_POSTS_PER_DAY = 3",
     "MAX_PROJECT_POSTS_PER_DAY = 4", "owner rule: 3 posts a day becomes 4"),
    ("src/ghos_v6/dedup.py", "COOLDOWN_DAYS = 90", "COOLDOWN_DAYS = 30",
     "duplicate cooldown 90 days becomes 30"),
    ("src/ghos_v6/dedup.py", "CONTENT_SIMILARITY = 0.55", "CONTENT_SIMILARITY = 0.95",
     "repost detection threshold weakened"),
    ("src/ghos_v6/dedup.py", "if len(a) < 3 or len(b) < 3:",
     "if len(a) < 2 or len(b) < 2:", "shingle containment guard lowered"),
    ("src/ghos_v6/state.py", "EVENTS_KEEP_LINES = 2000", "EVENTS_KEEP_LINES = 200",
     "audit trail rotation truncates to 200 lines"),
    ("src/ghos_v6/state.py", "SEEN_REPOS_TTL_DAYS = 180", "SEEN_REPOS_TTL_DAYS = 1",
     "state TTL collapses to one day"),
    ("src/ghos_v6/state.py",
     "if isinstance(items, list) and len(items) > self.LIST_MAX_ITEMS:",
     "if isinstance(items, list) and len(items) > self.LIST_MAX_ITEMS * 2:",
     "bounded-list guard disabled"),
    ("src/ghos_v6/state.py", "if stamp is None or stamp >= cutoff:",
     "if stamp is None or stamp >= cutoff or True:", "published compaction disabled"),
    ("src/ghos_v6/state.py", "if changed:", "if changed and False:",
     "compaction audit event suppressed"),
    ("src/ghos_v6/config.py", "feature_telegraph: bool = True",
     "feature_telegraph: bool = False", "Telegraph off by default"),
    ("src/ghos_v6/config.py", 'or "gemini-3.5-flash-lite").strip().lower()',
     'or "gemini-nonexistent-model").strip().lower()', "default Gemini model invalid"),
    ("src/ghos_v6/Gemini.py", 'model: str = "gemini-3.5-flash-lite"',
     'model: str = "gemini-nonexistent-model"', "Gemini client default invalid"),
    ("src/ghos_v6/cli.py", '"53 18 * * 4": (close_poll_command, learn_command)',
     '"53 18 * * 4": (close_poll_command,)', "Thursday learning silently removed"),
    ("src/ghos_v6/cards.py", "W, H = 1200, 630", "W, H = 1080, 1080",
     "square cards, explicitly rejected by the owner"),
    ("src/ghos_v6/telegram_payload.py", '"prefer_large_media": True',
     '"prefer_large_media": False', "large preview above the text disabled"),
    (".github/workflows/pipeline.yml", 'cron: "13 7 * * *"', 'cron: "11 7 * * *"',
     "a scheduled slot drifts away from its route"),
    (".env.example", "FEATURE_TELEGRAPH=true", "FEATURE_TELEGRAPH=false",
     "documented default contradicts the owner rule"),
    ("docs/OWNER_RULES.md", "3 поста в день", "4 поста в день",
     "documented owner decision changed"),
    ("src/ghos_v6/content_adapters.py",
     'TELEGRAM_CHANNEL_URL = "https://t.me/GitHub_Open_Source"',
     'TELEGRAM_CHANNEL_URL = "https://t.me/wrong_channel"',
     "return loop points at the wrong channel"),
    ("src/ghos_v6/content_adapters.py",
     'if "/c/" in link or "/+" in link or "joinchat" in link:',
     "if False:", "private test-channel links leak into public copy"),
    ("src/ghos_v6/pipeline.py", '"via": "make.com"', '"via": "graph.facebook.com"',
     "Facebook hand-off attributed to the direct Graph API"),
    ("src/ghos_v6/sheets.py", 'DEFAULT_RANGE = "facebook!A1"',
     'DEFAULT_RANGE = "Sheet1!A1"', "sheet range breaks the make.com layout"),
    (".env.example", "FEATURE_SHEETS_HANDOFF=false", "FEATURE_SHEETS_HANDOFF=true",
     "documented Sheets hand-off default flipped"),
    ("src/ghos_v6/content_adapters.py",
     '    raw = re.sub(r"\\s+#{2,3}\\s+", "\\n\\n## ", raw)',
     "    raw = raw", "dev.to headings flattened back into a wall of text"),
    ("src/ghos_v6/formatter.py",
     'CHANNEL_TAG_DIVIDER = "========================================"',
     'CHANNEL_TAG_DIVIDER = ""', "channel/tag separator removed"),
    ("src/ghos_v6/Gemini.py",
     'MARKDOWN_SURFACE_KEYS = frozenset({"devto_body", "Facebook_body"})',
     "MARKDOWN_SURFACE_KEYS = frozenset()", "Markdown surfaces flattened again"),
    ("src/ghos_v6/content_adapters.py",
     'return (intro + "\\n\\n---\\n\\n" if intro else "") + markdown + footer',
     'return (intro + "\\n\\n" if intro else "") + markdown + footer',
     "TL;DR loses its visual rule"),
    ("src/ghos_v6/pipeline.py",
     '    enabled = {\n        "bluesky": config.feature_bluesky,\n',
     '    enabled = {\n        "bluesky": config.feature_bluesky,\n        "mastodon": False,\n',
     "Mastodon reintroduced into crossposts"),
    (".github/workflows/pipeline.yml",
     "article|article-update|article-unseries|devto-remove|news",
     "article|article-unseries|devto-remove|news",
     "article-update command removed from the workflow"),
    ("src/ghos_v6/platforms.py",
     '            "PUT", f"https://dev.to/api/articles/{article_id}",',
     '            "POST", "https://dev.to/api/articles",',
     "article update creates a duplicate post"),
    ("src/ghos_v6/platforms.py", '        for key in ("devto_cover_url", "image_url"):',
     '        for key in ("image_url",):',
     "dev.to cover ignored and the card is cropped again"),
    ("src/ghos_v6/devto_cover.py", "COVER_W, COVER_H = 1000, 420",
     "COVER_W, COVER_H = 1200, 630",
     "dev.to cover returns to the ratio dev.to crops"),
    ("tools/state_branch.py", 'STATE_BRANCH = "state"',
     'STATE_BRANCH = "main"',
     "live state written back into main's history"),
    ("tools/state_branch.py", '{"sha": commit, "force": False})',
     '{"sha": commit, "force": True})',
     "state ref updated without compare-and-swap, concurrent writes lost"),
    ("tools/state_branch.py", "        if after < before:",
     "        if False:",
     "a state that lost published records is committed"),
    ("tools/state_branch.py",
     '    commit = _commit_tree(files, "chore(state): monthly squash [skip ci]", [])',
     '    commit = _commit_tree(files, "chore(state): monthly squash [skip ci]", ["0" * 40])',
     "monthly squash no longer produces a parentless root commit"),
    (".github/workflows/pipeline.yml",
     "          fetch-depth: 1\n          persist-credentials: false",
     "          fetch-depth: 0\n          persist-credentials: false",
     "publishing pipeline clones the whole history again"),
    ("src/ghos_v6/platforms.py",
     '            "tags": self._tags(record, generated),\n        }\n        # Owner decision (2026-11-03)',
     '            "tags": self._tags(record, generated),\n            "series": "Open Source Finds",\n        }\n        # Owner decision (2026-11-03)',
     "daily dev.to posts grouped into a growing series again"),
    ("src/ghos_v6/platforms.py",
     '            "series": None,  # detach any legacy series; standalone post only',
     '            "series": "Open Source Finds",  # detach any legacy series; standalone post only',
     "article update re-joins the series instead of removing it"),
    ("src/ghos_v6/platforms.py",
     'json={"article": {"published": True, "series": None}},',
     'json={"article": {"published": True, "series": ""}},',
     "empty string is not Forem's documented series removal"),
    (".github/workflows/pipeline.yml",
     "              collect|publish|crossposts|article|article-update|article-unseries|devto-remove|news",
     "              collect|publish|crossposts|article|article-update|devto-remove|news",
     "article-unseries command removed from the workflow whitelist"),
    (".github/workflows/codeql.yml",
     "- uses: github/codeql-action/analyze@793b778da9c9275a0f276522b27cb5fc14bba279 # v4",
     "- uses: github/codeql-action/analyze@093c0af4c358e174c90d33c5672595fb14c89183 # v4",
     "CodeQL init and analyze use different action versions"),
    ("src/ghos_v6/news.py",
     "        entries = parse_channel_page(response.text)",
     "        entries = []",
     "WorldofAI RSS outage no longer falls back to the channel page"),
    ("src/ghos_v6/pipeline.py",
     '        return {"ok": True, "published": False, "reason": "source_unavailable", "error": reason}',
     '        return {"ok": False, "published": False, "reason": "source_unavailable", "error": reason}',
     "news source outage fails the scheduled workflow again"),
    ("src/ghos_v6/pipeline.py",
     "        if published_at and published_at.date().isoformat() == day:",
     "        if False:",
     "second dev.to article of the same day allowed again"),
    ("src/ghos_v6/pipeline.py",
     "        if needle in blob:",
     "        if False:",
     "same repository published twice on dev.to again"),
    ("src/ghos_v6/pipeline.py",
     '                store.event("card_host_failed", repo=key, fallback="no_preview")',
     """                image_url = f\"https://opengraph.githubassets.com/1/{candidate.get('full_name')}\"\n"""
     """                store.event(\"card_host_failed\", repo=key, fallback=\"opengraph\")""",
     "GitHub default OpenGraph card returns on hosting failure"),
    ("src/ghos_v6/media_host.py",
     "        for attempt in range(4):",
     "        for attempt in range(1):",
     "transient ImgBB 503 is no longer retried"),
    ("src/ghos_v6/telegram.py",
     "        include_buttons: bool = False,",
     "        include_buttons: bool = True,",
     "inline keyboard returns and native comments disappear again"),
    ("src/ghos_v6/pipeline.py",
     "        if value in protected:",
     "        if False:",
     "dev.to removal can delete the live article referenced by state"),
    ("src/ghos_v6/news.py",
     "    published, latest = max(dated, key=lambda pair: pair[0])",
     "    published, latest = min(dated, key=lambda pair: pair[0])",
     "AI Brief picks the oldest video instead of the newest"),
    ("src/ghos_v6/news.py",
     '        return None, "video_too_old"',
     "        pass  # video_too_old",
     "AI Brief accepts a video older than yesterday"),
    ("src/ghos_v6/news.py",
     '    if str(latest["video_id"]) in seen_video_ids:',
     "    if False:",
     "the same WorldofAI video can be published twice"),
    ("src/ghos_v6/news.py",
     "ADVERTISING MUST BE REMOVED",
     "ADVERTISING MAY BE KEPT",
     "Gemini is no longer told to strip sponsor reads"),
    ("src/ghos_v6/news.py",
     '    divider = "──────────────"',
     '    divider = ""',
     "AI Brief layout loses its section dividers"),
    ("src/ghos_v6/platforms.py",
     'json={"article": {"published": False}}',
     'json={"article": {"published": True}}',
     "privilege-gated unpublish fallback republishes the duplicate instead"),
    # Owner feedback, 04.11.2026 second pass: the brief must be a digest,
    # keep verbatim model names and never cite the source.
    ("src/ghos_v6/news.py",
     "        return stories[:7]",
     "        return stories[:1]",
     "round-up collapses back into one story"),
    ("src/ghos_v6/news.py",
     "- A video with ten or more chapters yields AT LEAST FIVE stories.",
     "- One story is enough for any video.",
     "Gemini collapses a 20-chapter round-up into one story again"),
    ("src/ghos_v6/news.py",
     "- Every story title must name at least one exact technical identifier and be",
     "- Story titles should avoid technical names.",
     "model names vanish from the story headings"),
    ("src/ghos_v6/news.py",
     "- EXACT TECHNICAL NAMES ARE SACRED.",
     "- Technical names may be shortened for readability.",
     "Gemini is allowed to rename Claude Fable 5.5 and GPT-6 Astra"),
    ("src/ghos_v6/news.py",
     "- NO SOURCE ATTRIBUTION.",
     "- Always cite the video URL and the channel name.",
     "AI Brief starts naming its source again"),
    ("src/ghos_v6/news.py",
     "    return _restore_names(protected.strip(), slots)",
     "    return protected.strip()",
     "exact model identifiers are destroyed by headline cleanup"),
    ("src/ghos_v6/pipeline.py",
     "    if len(stories) < 2:",
     "    if len(stories) < 1:",
     "a one-story brief is published again"),
    ("src/ghos_v6/pipeline.py",
     "    if refresh and old_message_id:",
     "    if False and old_message_id:",
     "refresh posts a duplicate instead of editing the weak post in place"),
    ("src/ghos_v6/pipeline.py",
     '    # owner explicitly does not want. Only our own card is ever attached.\n    preview_url = ""',
     '    # owner explicitly does not want. Only our own card is ever attached.\n    preview_url = str(entry.get("url") or "")',
     "the YouTube video preview returns as source attribution"),
    ("src/ghos_v6/cards.py",
     '(116, 620, "MODELS IN THIS BRIEF", short_models),',
     '(116, 620, "OFFICIAL SOURCE", short_models),',
     "AI Brief card names an official source again"),
    # Owner corrections, 04.11.2026 third pass: readable card and honest facts.
    ("src/ghos_v6/news.py",
     "    out = dict(brief)",
     "    return brief  # verification disabled",
     "a leaked checkpoint is published as a release again"),
    ("src/ghos_v6/news.py",
     "VERIFY BEFORE YOU CLAIM (owner correction 2026-11-04):",
     "Ignore verification and write the most exciting version:",
     "Gemini is no longer told that a leak is not a release"),
    ("src/ghos_v6/news.py",
     '    "leaked_checkpoint": "◻️ Leaked checkpoint · unconfirmed",',
     '    "leaked_checkpoint": "✅ Official release",',
     "unverified checkpoint gets the official-launch badge"),
    ("src/ghos_v6/news.py",
     '    hook = _clip_preserving(editorial_hook(brief) or brief.get("summary"), 300, names)',
     '    hook = "This digest covers recent AI releases and updates."',
     "the dry opener replaces the hook"),
    ("src/ghos_v6/cards.py",
     "AI_BRIEF_BAND_TOP = 470",
     "AI_BRIEF_BAND_TOP = 340",
     "the stat band moves up and collides with the hook"),
    ("src/ghos_v6/cards.py",
     "AI_BRIEF_MIN_FONT = 21",
     "AI_BRIEF_MIN_FONT = 12",
     "15 px type returns to the card"),
    ("src/ghos_v6/cards.py",
     '        (772, 320, "FORMAT", short_format),',
     '        (300, 820, "FORMAT", short_format),',
     "stat columns collide again"),
    ("src/ghos_v6/cards.py",
     "    head_font, head_lines = _fit_block(",
     '    head_font, head_lines = _font("sans", 58, True), _wrap(draw, headline, _font("sans", 58, True), AI_BRIEF_WIDTH, 3)  # _fit_block(',
     "the headline is truncated again"),
    ("src/ghos_v6/pipeline.py",
     "            standfirst = card_hook_text(brief)",
     '            standfirst = str(brief.get("summary") or "")',
     "the card falls back to the dry summary instead of the hook"),
    ("src/ghos_v6/news.py",
     "    names = brief_model_names(brief)\n    hook = \" \".join(str(brief.get(\"hook\") or \"\").split())",
     '    return " ".join(str(brief.get("hook") or "").split())  # free-text hook trusted again',
     "a fluent wrong hook reassigns GPT-6 Astra's result to Fable 5.5"),
    ("src/ghos_v6/news.py",
     "        status = _audit_status(story)",
     '        status = str(story.get("status") or "").strip().lower()',
     "an S-1 filing is published as an official launch again"),
    ("src/ghos_v6/news.py",
     "    out[\"hook\"] = editorial_hook(out)",
     '    out["hook"] = " ".join(str(brief.get("hook") or "").split())',
     "the dry corporate recap returns as the hook"),
    ("src/ghos_v6/news.py",
     '    if names and _CORPORATE_RE.match(headline):',
     '    if False and names and _CORPORATE_RE.match(headline):',
     "the cover headline opens on the S-1 filing again"),
    ("src/ghos_v6/news.py",
     '    "official_document": "📄 Official filing · not a product launch",',
     '    "official_document": "✅ Official release",',
     "a policy filing gets the official-launch badge"),
    ("src/ghos_v6/news.py",
     "            parts.append(f\"📄 {announced} filing / announced, not shipped\")",
     '            parts.append(f"✅ {announced} official")',
     "the receipts tally counts filings as launches"),
    ("src/ghos_v6/cards.py",
     "    stand_font, stand_lines, stand_lh = _card_hook_layout(draw, standfirst, allowed)",
     "    stand_font, stand_lines = _fit_block(draw, standfirst, \"sans\", 30, AI_BRIEF_WIDTH, 2, bold=False, minimum=24); stand_lh = int(_font_size(stand_font) * 1.32)",
     "the card cuts the hook off mid-sentence again"),
]

COPY_ITEMS = (
    "src", "tests", "docs", "state", "tools", ".github", "pyproject.toml",
    "requirements.in", "requirements.lock", "requirements-dev.in",
    "requirements-dev.lock", "README.md", "SECURITY.md", "LICENSE", ".env.example",
    ".env.keys.template",
)


def _copy_repo(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for name in COPY_ITEMS:
        source = REPO / name
        if not source.exists():
            continue
        if source.is_dir():
            shutil.copytree(
                source, dest / name,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".venv"),
            )
        else:
            shutil.copy2(source, dest / name)


def _run_tests(root: Path, timeout: int = 60) -> int:
    try:
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "--tb=no",
                "-c", str(root / "pyproject.toml"), str(root / "tests"),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        # A mutation that sends the suite into a real network call can hang.
        # A suite that never finishes is not green, so this counts as killed.
        return 2
    return result.returncode


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ghos-mut-") as tmp:
        base = Path(tmp) / "baseline"
        _copy_repo(base)
        if _run_tests(base) != 0:
            print("BASELINE IS RED - mutation results would be meaningless", file=sys.stderr)
            return 1
        print(f"baseline green: {len(MUTATIONS)} mutations to check\n")

        survived: list[str] = []
        for index, (path, old, new, label) in enumerate(MUTATIONS):
            work = Path(tmp) / f"mut-{index}"
            shutil.copytree(base, work)
            target = work / path
            text = target.read_text(encoding="utf-8")
            if old not in text:
                print(f"SKIP     {label} (anchor missing in {path})")
                survived.append(label)
                continue
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
            if _run_tests(work) == 0:
                print(f"SURVIVED {label}")
                survived.append(label)
            else:
                print(f"KILLED   {label}")

        killed = len(MUTATIONS) - len(survived)
        print(f"\nmutation score: {killed}/{len(MUTATIONS)} killed")
        if survived:
            print("survivors (add a test that encodes the intent):")
            for label in survived:
                print(f"  - {label}")
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
