"""Contracts between artifacts: configuration, workflows, assets and docs.

Most production defects found in the reliability audit were not in an
uncovered Python line: a workflow Variable missing, a wheel without icons, a
cron that no route handles, or a doc contradicting the code. These tests
compare the artifacts with each other.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO / ".github" / "workflows"

# Supplied by the Actions runtime or used only for local overrides / legacy
# aliases; they do not have to be declared as repository Variables.
LOCAL_ONLY = {
    "GITHUB_TOKEN",
    "GITHUB_REPOSITORY",
    "GHOS_GITHUB_REPOSITORY",
    "GHOS_STATE_DIR",
    "FEATURE_Facebook",
}


def _config_env_names() -> set[str]:
    tree = ast.parse((REPO / "src" / "ghos_v6" / "config.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "getenv"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names.add(node.args[0].value)
    return names


def test_every_config_env_name_is_documented_and_wired_into_actions():
    env_example = (REPO / ".env.example").read_text(encoding="utf-8")
    workflows = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(WORKFLOW_DIR.glob("*.yml"))
    )
    names = _config_env_names() - LOCAL_ONLY
    assert names
    missing_doc = sorted(
        name for name in names
        if not re.search(rf"^{re.escape(name)}=", env_example, re.M)
    )
    missing_wire = sorted(name for name in names if name not in workflows)
    assert not missing_doc, f"undocumented Variables: {missing_doc}"
    assert not missing_wire, f"Variables not passed by any workflow: {missing_wire}"


def test_every_workflow_uses_python_313_and_no_hardcoded_private_id():
    files = sorted(WORKFLOW_DIR.glob("*.yml"))
    assert len(files) >= 6
    for path in files:
        text = path.read_text(encoding="utf-8")
        versions = re.findall(r'python-version:\s*"?([^"\s]+)"?', text)
        for version in versions:
            assert version.startswith("3.13"), f"{path.name}: {version}"
        assert "-1003909350927" not in text, f"{path.name} leaks a private channel id"


def test_state_writer_guards_survive_in_the_pipeline_workflow():
    text = (WORKFLOW_DIR / "pipeline.yml").read_text(encoding="utf-8")
    assert "persist-credentials: false" in text
    assert "retention-days: 1" in text
    tool = (REPO / "tools" / "state_branch.py").read_text(encoding="utf-8")
    assert "published count decreased" in tool, "do not commit a state that lost dedup keys"
    assert "tools/state_branch.py write" in text, "the guard must be the actual writer"
    assert "fetch-depth: 0" not in text, "phase B removed the full-history clone"
    assert "concurrency:" in text and "state-writer" in text
    assert "TELEGRAM_MAIN_CHANNEL_ID" in text
    assert "MAIN_TELEGRAM_CHANNEL" in text, "legacy alias stays as a fallback"


def test_all_icon_assets_exist_and_are_declared_as_package_data():
    icons = list((REPO / "src" / "ghos_v6" / "assets" / "icons").rglob("*.png"))
    assert len(icons) == 142, "74 root + 68 colour icons"
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "*.png" in pyproject, "icons must ship in the wheel, not only in the repo"


def test_crons_and_routes_still_agree_after_any_schedule_change():
    cli = (REPO / "src" / "ghos_v6" / "cli.py").read_text(encoding="utf-8")
    workflow = (WORKFLOW_DIR / "pipeline.yml").read_text(encoding="utf-8")
    crons = set(re.findall(r'cron:\s*["\']([^"\']+)["\']', workflow))
    routes_block = cli.split("routes: dict[str, Any] = {")[1].split("pairs = {")[0]
    pairs_block = cli.split("pairs = {")[1].split("if normalized")[0]
    keys = set(re.findall(r'"([^"]+)"\s*:\s*lambda', routes_block))
    keys |= set(re.findall(r'"([^"]+)"\s*:', pairs_block))
    assert crons == keys, f"cron/route mismatch: {sorted(crons ^ keys)}"
    # 2026-11-07: +53 19 * * * for the second daily dev.to article.
    assert len(crons) == 14


def test_documentation_agrees_with_the_current_launch_decision():
    owner = (REPO / "docs" / "OWNER_RULES.md").read_text(encoding="utf-8")
    assert "3 поста в день" in owner
    assert "1200×630" in owner
    assert "Английский язык, под Google" in owner
    assert "Не включайте публикующие workflow" not in owner, (
        "the 2026-11-03 owner decision lifted this ban; docs must not contradict it"
    )
    assert "2026-11-03" in owner


def test_runtime_lock_has_no_dev_tools():
    lock = (REPO / "requirements.lock").read_text(encoding="utf-8")
    dev_lock = (REPO / "requirements-dev.lock").read_text(encoding="utf-8")
    assert "pytest" not in lock.lower()
    assert "ruff" not in lock.lower()
    assert "pytest" in dev_lock.lower()


def test_hashed_locks_are_used_by_both_publishing_workflows():
    for name in ("pipeline.yml", "doctor.yml"):
        text = (WORKFLOW_DIR / name).read_text(encoding="utf-8")
        assert "--require-hashes -r requirements.lock" in text
        assert "python-version: \"3.13\"" in text
