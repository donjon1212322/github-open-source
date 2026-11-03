#!/usr/bin/env python3
"""Small tracked-file secret scanner for CI.

This is a conservative tripwire, not a replacement for GitHub secret
scanning. It looks for credential-shaped strings in the repository while
ignoring documented environment-variable names and empty examples.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

PATTERNS = [
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{30,}"),
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"),  # Telegram bot token shape
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),  # Google API key shape
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|app[_-]?password)\s*=\s*[A-Za-z0-9_./+=-]{20,}"),
]

ALLOW_SUBSTRINGS = (
    ".env.example",
    "TOKEN=",
    "API_KEY=",
    "PASSWORD=",
)


SKIP_DIRS = {
    ".git", ".venv", "__pycache__", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build",
}


def tracked_files() -> list[Path]:
    """Tracked files *and* untracked ones.

    A brand-new file is exactly where an accidentally pasted key is most
    likely to appear; scanning only ``git ls-files`` used to report a clean
    run while such a file sat unstaged.
    """
    try:
        names = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
        names += subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard"], text=True
        ).splitlines()
        return [
            Path(p) for p in names
            if p
            and not any(part in SKIP_DIRS for part in Path(p).parts)
            and Path(p).is_file()
        ]
    except Exception:
        return [
            p for p in Path(".").rglob("*")
            if p.is_file() and not any(part in SKIP_DIRS for part in p.parts)
        ]


def main() -> int:
    findings = []
    for path in tracked_files():
        if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if path.name == ".env.example" and line.strip().endswith("="):
                continue
            for pattern in PATTERNS:
                if pattern.search(line):
                    # Permit prose/code that names an env var without a real value.
                    if line.strip().endswith("=") or "os.getenv" in line or "secrets." in line:
                        continue
                    findings.append(f"{path}:{lineno}: credential-shaped string")
                    break
    if findings:
        print("\n".join(findings))
        return 1
    print("secret-scan: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
