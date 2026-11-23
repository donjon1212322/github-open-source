"""Owner 23.11: no lookalike installers, and diversity measured over a week.

Two findings from the live state (23.11, 60 published posts, 27 queued):

1. Four queue entries were one template published by four different owners -
   PhpStorm / CLion / Microsoft Visio / iMobie AnyTrans descriptions, a real
   MIT license to satisfy the gate, exactly 336 stars each, and topics that
   advertise a setup file (``jetbrains-phpstorm-install-setup-2026``,
   ``clion-download-windows`` ...). Those are download funnels, and the
   channel would have handed the audience a fake installer.

2. "The same themes again": 17 of the last 22 posts were DevTools and 15 of
   22 were about AI agents. The diversity band only compared a candidate with
   *today's* posts, so the first slot of every day had no diversity pressure
   at all. The selector now also receives the seven-day window.

Both checks are penalties/rejections with narrow rules: a thin queue must
still publish, and an ordinary project must never be caught by mistake.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ghos_v6.gate import evaluate, lookalike_installer_reason
from ghos_v6.rank import (
    diversity_bonus,
    select_candidate,
    week_diversity_bonus,
)

LOOKALIKES = [
    {
        "name": "PhpStorm Desktop",
        "full_name": "adhamslim/phpstorm-desktop-edition",
        "description": "PhpStorm Desktop: Native PHP IDE with Debugging, Refactoring, and Version Control",
        "language": "TypeScript",
        "stars": 336,
        "topics": [
            "automation",
            "desktop",
            "developer-tools",
            "jetbrains-phpstorm",
            "jetbrains-phpstorm-install-setup-2026",
        ],
    },
    {
        "name": "CLion IDE",
        "full_name": "samiirnor0-arch/clion-ide",
        "description": "CLion IDE: Cross-Platform C and C++ Development Environment",
        "language": "TypeScript",
        "stars": 336,
        "topics": ["automation", "clion-2026", "clion-download-windows", "cmake-ide", "cpp-ide"],
    },
    {
        "name": "Microsoft Visio",
        "full_name": "jorgezaltoo-design/microsoft-visio",
        "description": "Microsoft Visio: Diagramming and Vector Graphics Desktop Application",
        "language": "HTML",
        "stars": 336,
        "topics": [
            "automation",
            "bentley-microstation-install-setup-windows-2026",
            "developer-tools",
            "diagram-software",
        ],
    },
    {
        "name": "iMobie AnyTrans",
        "full_name": "BratzieTiffany/imobie-anytrans",
        "description": "iMobie AnyTrans: Cross-Device Content Management and Data Transfer Suite",
        "language": "HTML",
        "stars": 336,
        "topics": ["anytrans-download-iphone-manager", "automation", "desktop", "developer-tools"],
    },
]


def _project(**overrides):
    now = datetime.now(timezone.utc)
    project = {
        "name": "ExampleTool",
        "full_name": "example/tool",
        "description": "A useful developer tool",
        "url": "https://github.com/example/tool",
        "language": "Python",
        "stars": 1500,
        "forks": 120,
        "open_issues": 4,
        "contributors_count": 8,
        "topics": ["cli", "developer-tools"],
        "license": {"spdx_id": "MIT"},
        "created_at": (now - timedelta(days=400)).isoformat(),
        "pushed_at": (now - timedelta(days=2)).isoformat(),
        "has_readme": True,
        "readme": "x" * 1200,
    }
    project.update(overrides)
    return project


# --- 1. lookalike installers -------------------------------------------------


def test_all_four_live_lookalikes_are_named():
    for payload in LOOKALIKES:
        reason = lookalike_installer_reason(payload)
        assert reason, f"{payload['full_name']} must be flagged"
        assert reason.startswith("topic:")


def test_gate_rejects_a_lookalike_with_the_right_reason():
    decision = evaluate(_project(**LOOKALIKES[1]))
    assert not decision.ok
    assert any(r.startswith("lookalike_installer:") for r in decision.reasons)


def test_gate_keeps_accepting_ordinary_topics():
    for topics in (
        ["cli", "developer-tools"],
        ["setup", "installer", "automation"],
        ["download-manager", "torrent", "python"],
        ["windows", "macos", "linux", "cross-platform"],
        ["install", "shell", "automation"],
    ):
        assert lookalike_installer_reason(_project(topics=topics)) == ""
        assert evaluate(_project(topics=topics)).ok


def test_healthy_project_of_the_day_is_untouched():
    """The post published on 23.11 must not be caught by the new rule."""
    payload = _project(
        name="neonclipping",
        full_name="iamajmeena/neonclipping",
        description="Paste a link, get captioned 9:16 clips in ~2 min.",
        topics=["ai-video-clipper", "clipping", "faster-whisper", "ffmpeg"],
        language="Python",
    )
    assert evaluate(payload).ok


# --- 2. week-level diversity -------------------------------------------------


def _record(**overrides):
    record = {
        "full_name": "someone/thing",
        "language": "Python",
        "category": "DevTools",
        "topics": ["cli"],
        "published_at": "2026-11-20T16:24:00Z",
    }
    record.update(overrides)
    return record


def test_week_view_is_off_when_no_week_is_passed():
    week = [_record(full_name="someone/thing")] * 5
    assert week_diversity_bonus({"full_name": "someone/thing"}, week) < 0
    assert diversity_bonus({"full_name": "someone/thing"}, []) == 0.0


def test_same_owner_within_a_week_is_penalised():
    week = [_record(full_name="storytold/cadcraft", published_at="2026-11-21T18:14:00Z")]
    candidate = {"full_name": "storytold/gridcraft", "language": "Go", "category": "Security", "topics": []}
    assert week_diversity_bonus(candidate, week) <= -12.0


def test_repeated_language_and_flooded_theme_are_penalised():
    """Live week 16-23.11: DevTools 17/22, AI-agent posts 15/22."""
    week = [
        _record(full_name=f"a{i}/x", language="Rust", topics=["ai-agents", f"topic{i}"])
        for i in range(4)
    ]
    week += [
        _record(full_name=f"b{i}/y", language="Go", topics=["mcp", f"other{i}"])
        for i in range(2)
    ]
    flooded = {"full_name": "new/repo", "language": "Rust", "topics": ["ai-agents"]}
    # language limit (3) and family limit (5 of 6 are AI-agent posts) both hit:
    # -5 language, -8 family. A single shared topic is not enough for the
    # overlap rule, which needs two.
    assert week_diversity_bonus(flooded, week) == -13.0


def test_theme_family_is_derived_without_gemini():
    from ghos_v6.rank import theme_family

    assert theme_family({"topics": ["ai-agents", "claude-code"]}) == "ai-agents"
    assert theme_family({"name": "NOIR", "description": "Android privacy audit via adb",
                         "topics": ["adb", "android"]}) == "privacy"
    assert theme_family({"description": "CAD drafting in pure Rust"}) == ""
    assert theme_family({"topics": ["video", "clipping"]}) == "media"


def test_a_flooded_theme_does_not_block_the_only_candidate():
    week = [_record(full_name=f"a{i}/x", topics=["ai-agents"]) for i in range(6)]
    only = {"full_name": "z/agent", "score": 70.0, "language": "Go", "topics": ["ai-agents"]}
    assert select_candidate([only], [], None, week) is only


def test_topic_overlap_needs_two_shared_topics():
    week = [_record(full_name="x/one", topics=["ai-agents"]), _record(full_name="x/two", topics=["mcp"])]
    one = {"full_name": "z/one", "language": "Zig", "category": "Other", "topics": ["ai-agents"]}
    two = {"full_name": "z/two", "language": "Zig", "category": "Other", "topics": ["ai-agents", "mcp"]}
    assert week_diversity_bonus(one, week) == 0.0
    assert week_diversity_bonus(two, week) == -2.0


def test_first_slot_of_the_day_now_sees_yesterday():
    """published_today == [] used to mean "no diversity check at all"."""
    week = [_record(full_name="storytold/figcraft", published_at="2026-11-19T18:14:00Z")]
    candidate = {"full_name": "storytold/gridcraft", "language": "Rust", "category": "DevTools", "topics": []}
    assert diversity_bonus(candidate, [], week) == -12.0


def test_selection_prefers_a_fresh_owner_over_a_slightly_higher_score():
    week = [_record(full_name="storytold/figcraft", language="Rust", topics=["cad"])]
    repeated = {"full_name": "storytold/gridcraft", "score": 88.0, "language": "Rust", "topics": ["cad"]}
    fresh = {"full_name": "newco/cadthing", "score": 84.0, "language": "Go", "topics": []}
    assert select_candidate([repeated, fresh], [], None, week)["full_name"] == "newco/cadthing"
    # Without the week window the behaviour is exactly what it was before.
    assert select_candidate([repeated, fresh], [])["full_name"] == "storytold/gridcraft"


def test_a_much_better_candidate_still_wins():
    """The penalty must not overrule a genuinely better project."""
    week = [_record(full_name="storytold/figcraft", language="Rust", topics=["cad"])]
    repeated = {"full_name": "storytold/gridcraft", "score": 95.0, "language": "Rust", "topics": ["cad"]}
    fresh = {"full_name": "newco/cadthing", "score": 80.0, "language": "Go", "topics": []}
    assert select_candidate([repeated, fresh], [], None, week)["full_name"] == "storytold/gridcraft"


def test_penalties_never_empty_a_thin_queue():
    week = [_record(full_name="only/one")] * 7
    only = {"full_name": "only/one", "score": 70.0, "language": "Rust", "category": "DevTools", "topics": ["cli"]}
    assert select_candidate([only], [], None, week) is only


def test_publish_path_really_passes_the_week_window():
    """A contract test: the fix is worthless if the slot forgets the week."""
    from pathlib import Path

    body = Path("src/ghos_v6/pipeline.py").read_text(encoding="utf-8")
    body = body.split("def publish_slot(")[1].split("\ndef ")[0]
    assert "published_week = _recent_records(store, days=7)" in body
    assert body.index("published_week = _recent_records") < body.index("select_candidate(")
    assert "published_week," in body
