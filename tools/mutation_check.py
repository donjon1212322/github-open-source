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
    ("src/ghos_v6/cli.py", '"50 18 * * 4": (close_poll_command, learn_command)',
     '"50 18 * * 4": (close_poll_command,)', "Thursday learning silently removed"),
    ("src/ghos_v6/cards.py", "W, H = 1200, 630", "W, H = 1080, 1080",
     "square cards, explicitly rejected by the owner"),
    ("src/ghos_v6/telegram_payload.py", '"prefer_large_media": True',
     '"prefer_large_media": False', "large preview above the text disabled"),
    (".github/workflows/pipeline.yml", 'cron: "10 7 * * *"', 'cron: "11 7 * * *"',
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
     "article|article-update|article-unseries|news",
     "article|article-unseries|news",
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
     "              collect|publish|crossposts|article|article-update|article-unseries|news",
     "              collect|publish|crossposts|article|article-update|news",
     "article-unseries command removed from the workflow whitelist"),
    (".github/workflows/codeql.yml",
     "- uses: github/codeql-action/analyze@793b778da9c9275a0f276522b27cb5fc14bba279 # v4",
     "- uses: github/codeql-action/analyze@093c0af4c358e174c90d33c5672595fb14c89183 # v4",
     "CodeQL init and analyze use different action versions"),
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
