"""Owner 23.11/25.11: the fake-installer network keeps changing clothes.

The 23.11 wave advertised itself in *topics* ("install-setup", "download-windows",
"repack", "keygen") and the gate learned that pattern. The 25.11 wave moved the
signature into the **description**: ten throwaway owners, all created on 23.11,
522-548 stars each, every text opening with the same advertising template
instead of a project name. Their topics are five or more near-synonyms, which is
keyword stuffing, not curation.

Evidence is taken from the live queue (state branch, 25.11):

    Free open-source FFmpeg GUI for Windows to convert video and audio...
    Free Windows AV1 video encoder GUI for aomenc, rav1e, SVT-AV1, NVENC...
    Free Steam idler for trading card farming, achievement management...
    Free offline file converter for Windows - convert PDF, documents...
"""
from __future__ import annotations

from ghos_v6 import gate


def _farm(name: str = "umbradev315/ffmpeg-video-audio-converter", **overrides) -> dict:
    project = {
        "full_name": name,
        "name": "ffmpeg-video-audio-converter",
        "description": "Free open-source FFmpeg GUI for Windows to convert video and audio, "
                       "batch process media, and analyze quality.",
        "topics": ["audio-converter", "audio-encoding", "audio-processing",
                   "batch-processing", "desktop-app", "ffmpeg"],
        "stars": 528,
        "license": {"spdx_id": "MIT", "name": "MIT License"},
        "created_at": "2026-11-23T00:00:00Z",
        "pushed_at": "2026-11-24T00:00:00Z",
        "has_readme": True,
        "readme": "A project. " * 60,
        "contributors_count": 2,
    }
    project.update(overrides)
    return project


def test_the_advertising_template_is_named():
    assert gate.farm_template_reason(_farm()) == "free_platform_template"


def test_the_wave_from_the_live_queue_is_caught():
    wave = [
        ("Umbra9179/clipboard-history-manager-windows",
         "Free open-source Windows clipboard manager with searchable history.",
         ["clipboard", "clipboard-manager", "windows", "productivity", "desktop"]),
        ("Nebula4207/av1-video-encoder-gui",
         "Free Windows AV1 video encoder GUI for aomenc, rav1e, SVT-AV1, NVENC.",
         ["aom", "aomenc", "av1", "av1-encoders", "encoder"]),
        ("Basil0879/steam-game-idler",
         "Free Steam idler for trading card farming, achievement management, "
         "playtime boosting and multi-account automation on Windows.",
         ["achievement-management", "archisteamfarm", "asf", "automation", "idle-master"]),
        ("zero5122/offline-file-converter-windows",
         "Free offline file converter for Windows – convert PDF, documents, images, "
         "audio and video privately.",
         ["converter", "documents", "file-conversion", "offline", "pdf"]),
    ]
    for name, description, topics in wave:
        assert gate.farm_template_reason(
            _farm(name, description=description, topics=topics)), name


def test_a_real_project_is_not_touched():
    honest = [
        # The project's own name comes first.
        _farm("qianyiai/kurocat",
              description="KuroCat — Free open-source desktop typing trainer for macOS.",
              topics=["typing", "education", "macos", "desktop", "trainer"]),
        # A sentence, not an adjective.
        _farm("EL4CTEO/fast-anything",
              description="Don't rewrite it in Rust. Rewrite the 5% that matters.",
              topics=["rust", "rewrite", "cli"], stars=3599),
        # Same phrasing family, but a seasoned project with a small topic list.
        _farm("freecad/freecad", description="Free and open-source 3D CAD modeler for Windows and Linux.",
              topics=["cad", "3d"], stars=25000),
    ]
    for project in honest:
        assert gate.farm_template_reason(project) == "", project["full_name"]


def test_the_gate_rejects_the_template_with_a_readable_reason():
    decision = gate.evaluate(_farm())
    assert decision.ok is False
    assert any(reason.startswith("farm_template:") for reason in decision.reasons), decision.reasons
