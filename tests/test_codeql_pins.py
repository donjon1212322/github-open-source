"""CodeQL requires one and the same action version for all of its steps.

On 2026-11-03 Dependabot opened separate PRs for `codeql-action/init` and
`codeql-action/analyze`; each PR branch then mixed v4.39.1 with v4.39.3 and
CodeQL failed with "configuration error", which produced error emails to the
owner. Both PRs are closed, but the invariant must stay enforced in CI.
"""
from __future__ import annotations

import re
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _codeql_steps() -> list[tuple[str, str]]:
    text = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")
    return re.findall(r"github/codeql-action/(init|analyze)@([0-9a-f]{40})", text)


def test_init_and_analyze_share_one_pinned_sha():
    steps = _codeql_steps()
    assert {name for name, _ in steps} == {"init", "analyze"}
    shas = {sha for _, sha in steps}
    assert len(shas) == 1, f"mixed CodeQL action versions: {sorted(shas)}"


def test_every_action_in_codeql_workflow_is_sha_pinned():
    text = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")
    uses = re.findall(r"^\s*- uses:\s*(\S+)", text, flags=re.MULTILINE)
    assert len(uses) >= 4
    for ref in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), f"unpinned: {ref}"


def test_codeql_can_be_run_on_demand():
    text = (WORKFLOWS / "codeql.yml").read_text(encoding="utf-8")
    assert re.search(r"^\s*workflow_dispatch:", text, flags=re.MULTILINE)
