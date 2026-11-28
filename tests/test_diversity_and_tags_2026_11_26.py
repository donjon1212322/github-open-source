"""Owner 26.11: "посты одни инструменты" и "3 хештега".

Two complaints, both checked against the live state (state branch):

* 24.11 went out three emulator/recompilation posts (Lichii, Xbox2Windows,
  Eglantine) - different owners and languages, same story, so the day-scoped
  language/topic bonus saw nothing wrong;
* ten of the last posts carried only three tags because their repositories
  have no GitHub topics at all, so nothing filled the fourth slot.

The fixes: a second post of the same family in one day costs
``SAME_FAMILY_TODAY_PENALTY`` (more than any gap in the live queue), the week
limit for one family drops from five to three, and a project without topics
describes itself with one word of its own name/description.
"""
from __future__ import annotations

from ghos_v6 import rank
from ghos_v6.formatter import _hashtags


def _record(full_name: str, description: str, topics: list[str] | None = None,
            language: str = "Rust", **extra) -> dict:
    record = {
        "full_name": full_name,
        "name": full_name.split("/")[-1],
        "description": description,
        "topics": topics or [],
        "language": language,
        "category": "DevTools",
        "score": 55.0,
    }
    record.update(extra)
    return record


def test_a_second_post_of_the_same_family_costs_more_than_any_score_gap():
    earlier = _record("zortos293/Lichii", "Experimental Switch emulator", ["emulator", "switch"])
    bonus = rank.diversity_bonus(
        _record("R0Y00/Xbox2Windows", "Translate Xbox software to run on Windows", ["emulator"]),
        published_today=[earlier],
    )
    assert bonus <= -rank.SAME_FAMILY_TODAY_PENALTY
    # A different family that day is not punished.
    other = rank.diversity_bonus(
        _record("iamajmeena/neonclipping", "Clip streams locally with AI", ["ai", "video"]),
        published_today=[earlier],
    )
    assert other > bonus


def test_three_posts_of_one_family_are_the_weekly_limit():
    assert rank.WEEK_FAMILY_LIMIT == 3


def test_a_project_without_topics_still_gets_four_tags():
    """seedds/nxnative and friends: no GitHub topics, three tags in the post."""
    tags = _hashtags({
        "name": "nxnative",
        "description": "Switch games running natively on iPhone, no emulation layer",
        "language": "Objective-C",
        "category": "DevTools",
        "topics": [],
    })
    assert len(tags.split()) == 4, tags
    assert "#emulator" in tags


def test_topics_stay_the_first_source_of_tags():
    tags = _hashtags({
        "name": "archify",
        "description": "Turn your codebase into interactive architecture diagrams",
        "language": "JavaScript",
        "category": "AI",
        "topics": ["agent-skills", "ai-agents", "architecture-diagram", "claude-code"],
    })
    assert tags.split()[0] == "#opensource"
    assert "#ai" in tags and "#emulator" not in tags


# --- the frozen value model: `learn` needs its own slot ---------------------

def test_thursday_has_a_learn_slot_the_external_clock_can_fire(monkeypatch, tmp_path):
    """`learn` rode along with poll-close on the native cron only, so the
    external clock (which dispatches poll-close alone) never refreshed the
    multipliers - the value model was frozen since 20.11."""
    import ghos_v6.cli as cli
    from ghos_v6.config import Config
    from ghos_v6.state import StateStore

    calls: list[str] = []

    def learn(config, store):
        calls.append("learn")
        return {"ok": True, "learned": True}

    monkeypatch.setattr(cli, "learn_command", learn)
    result = cli._run_scheduled(Config(), StateStore(tmp_path), "57 18 * * 4")
    assert calls == ["learn"] and result["ok"] is True


def test_the_external_worker_fires_learn_on_thursday_too():
    import re
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    worker = (repo / "tools" / "external_tick" / "worker.js").read_text(encoding="utf-8")
    assert re.search(r'\["18:57",\s*\{\s*command:\s*"learn"\s*\},\s*4\]', worker), \
        "the external clock lost the learn slot again"
    workflow = (repo / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    assert "57 18 * * 4" in workflow


# --- 28.11: "посты одни инструменты" - the generic #devtools on every post --

def test_a_console_game_port_is_not_tagged_as_another_developer_tool():
    """fxceless/victory-fork went out 27.11 with three tags, #devtools among
    them: the model calls almost every repository a DevTools project."""
    tags = _hashtags({
        "name": "victory-fork",
        "description": "Run an Xbox Series title natively on Windows with 11 fps "
                       "using an experimental Rust compatibility layer.",
        "language": "Rust", "category": "DevTools", "topics": []})
    assert "#games" in tags and "#devtools" not in tags, tags
    assert len(tags.split()) == 4


def test_a_video_client_keeps_its_own_field_instead_of_devtools():
    tags = _hashtags({
        "name": "Stream-Tube-Desktop",
        "description": "An ad-free YouTube client for Windows in a native 25 MB "
                       "Rust binary.",
        "language": "Rust", "category": "DevTools",
        "topics": ["dearrow", "youtube", "sponsorblock", "return-youtube-dislike"]})
    assert "#devtools" not in tags and "#youtube" in tags, tags


def test_a_real_developer_tool_still_says_devtools():
    tags = _hashtags({
        "name": "pdfcraft",
        "description": "An open-source PDF reader and editor written from scratch.",
        "language": "Rust", "category": "DevTools", "topics": ["pdf", "editor"]})
    assert "#devtools" in tags, tags


def test_a_project_with_nothing_to_say_still_gets_four_tags():
    tags = _hashtags({"name": "plain", "description": "A tiny static site generator",
                      "language": "Go", "category": "DevTools", "topics": []})
    assert len(tags.split()) == 4, tags


def test_one_repository_takes_one_slot_of_the_pipeline(tmp_path):
    """28.11: xcl-dev/xcl arrived from GitHub search and from a Telegram
    channel in the same run and held two of the 28 slots."""
    from ghos_v6.state import StateStore

    store = StateStore(tmp_path)
    store.set_queue([
        {"full_name": "xcl-dev/xcl", "repo_id": 1465852099, "sources": ["github_search"], "score": 59.8},
        {"full_name": "xcl-dev/xcl", "repo_id": 1465852099, "sources": ["telegram:github_repos"], "score": 59.8},
        {"full_name": "other/repo", "repo_id": 2, "sources": ["github_search"], "score": 50.0},
    ])
    queue = store.data["queue"]
    assert [c["full_name"] for c in queue] == ["xcl-dev/xcl", "other/repo"]
    assert queue[0]["sources"] == ["github_search", "telegram:github_repos"]
