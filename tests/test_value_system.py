"""Anti-duplication, polls, measurement and learning."""
from datetime import datetime, timedelta, timezone

from ghos_v6 import dedup, insights, polls
from ghos_v6.rank import select_candidate, value_multiplier


# --- anti-duplication -----------------------------------------------------

BUILD = "A fast static site generator written in Rust for docs"


def test_same_repo_inside_cooldown_is_a_duplicate():
    data = {"published": {"acme/site": {"published_at": "2026-10-01T00:00:00Z", "stars": 100, "description": BUILD}}}
    verdict = dedup.check({"full_name": "acme/site", "stars": 120, "description": BUILD}, data)
    assert verdict["verdict"] == "duplicate"


def test_same_repo_after_cooldown_without_milestone_is_still_a_duplicate():
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    data = {"published": {"acme/site": {"published_at": old, "stars": 100, "description": BUILD}}}
    verdict = dedup.check({"full_name": "acme/site", "stars": 110, "description": BUILD}, data)
    assert verdict["verdict"] == "duplicate"


def test_star_milestone_earns_a_revisit():
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat()
    data = {"published": {"acme/site": {"published_at": old, "stars": 100, "description": BUILD}}}
    verdict = dedup.check({"full_name": "acme/site", "stars": 260, "description": BUILD}, data)
    assert verdict["verdict"] == "revisit"
    assert "stars" in verdict["reason"]


def test_renamed_repost_is_caught_by_description_similarity():
    data = {"published": {"old/name": {"published_at": "2026-10-01T00:00:00Z", "stars": 10, "description": BUILD}}}
    copy = "A blazing fast static site generator written in Rust for creating documentation sites easily"
    assert dedup.check({"full_name": "new/name", "description": copy}, data)["verdict"] == "duplicate"


def test_unrelated_project_is_new():
    data = {"published": {"old/name": {"published_at": "2026-10-01T00:00:00Z", "stars": 10, "description": BUILD}}}
    assert dedup.check({"full_name": "pg/proxy", "description": "Postgres wire proxy with query rewriting"}, data)["verdict"] == "new"


# --- measurement ----------------------------------------------------------

def test_views_parsing_handles_telegram_counters():
    assert insights.parse_views("16") == 16
    assert insights.parse_views("1.2K") == 1200
    assert insights.parse_views("12,4 тыс.") == 12400
    assert insights.parse_views("2.3M") == 2300000
    assert insights.parse_views("") == 0


def test_value_score_rewards_above_median_and_votes():
    baseline = 200.0
    quiet = insights.value_score({"views": 100}, median_views=baseline)
    busy = insights.value_score({"views": 400}, median_views=baseline)
    # A share without a usable sample is noise: 5 voters cannot move the score.
    assert insights.value_score(
        {"views": 400, "votes_share": 0.9, "total_voters": 5}, median_views=baseline
    ) == busy
    # With a real poll sample the same share does count.
    voted = insights.value_score(
        {"views": 400, "votes_share": 0.4, "total_voters": 200}, median_views=baseline
    )
    assert quiet < busy < voted


def test_value_score_survives_missing_click_layer():
    # No click_rate key at all: the score must still compute.
    assert insights.value_score({"views": 250}, median_views=200) > 0


def test_young_posts_are_not_punished_for_age():
    from ghos_v6.insights import views_at_reference

    fresh = views_at_reference(17, 1)      # 1 hour old
    aged = views_at_reference(208, 314)    # two weeks old
    assert fresh > aged, "a 1h post with 17 views is a hit, not a flop"


def test_normalisation_is_clamped():
    from ghos_v6.insights import views_at_reference

    assert views_at_reference(1000, 0.01) <= 6000
    assert views_at_reference(10, 100000) >= 3


def test_expected_views_grows_monotonically():
    from ghos_v6.insights import expected_views

    ages = [1, 2, 4, 8, 16, 24, 48, 96, 168, 720]
    values = [expected_views(a) for a in ages]
    assert values == sorted(values)


# --- polls ----------------------------------------------------------------

def test_poll_closing_respects_telegram_ten_day_cap():
    assert polls.MAX_POLL_DAYS == 10
    assert polls.DEFAULT_WEEK_POLL_HOURS <= polls.MAX_POLL_DAYS * 24


def test_winner_ignores_zero_vote_options():
    tally = {"options": [{"text": "A", "votes": 0}, {"text": "B", "votes": 4}, {"text": "C", "votes": 0}]}
    assert polls.winner(tally) == {"text": "B", "votes": 4}


# --- learning -------------------------------------------------------------

def test_multiplier_is_clamped_and_neutral_without_model():
    assert value_multiplier({"language": "Rust"}, None) == 1.0
    extreme = {"language_multipliers": {"rust": 5.0}, "topic_multipliers": {"cli": 5.0}}
    assert value_multiplier({"language": "Rust", "topics": ["cli"]}, extreme) <= 1.25


def test_learning_can_reorder_near_equal_candidates():
    queue = [
        {"full_name": "a/php", "score": 50, "language": "PHP"},
        {"full_name": "b/rust", "score": 48, "language": "Rust"},
    ]
    model = {"language_multipliers": {"rust": 1.15, "php": 0.9}, "topic_multipliers": {}}
    assert select_candidate(queue, [])["full_name"] == "a/php"
    assert select_candidate(queue, [], model)["full_name"] == "b/rust"


def test_learn_builds_a_model_from_measured_views():
    import tempfile

    from ghos_v6.pipeline import learn_command
    from ghos_v6.state import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(tmp)
        # A real sample: below MIN_MEASURED_POSTS the model must not be written,
        # so this test needs at least 20 measured posts.
        rows = [("Rust", 300), ("Rust", 280), ("Rust", 320), ("PHP", 90), ("PHP", 110), ("Go", 200)]
        rows += [("Rust", 260 + index) for index in range(8)]
        rows += [("PHP", 95 + index) for index in range(4)]
        rows += [("Go", 190 + index) for index in range(8)]
        for index, (language, views) in enumerate(rows):
            store.data["published"][f"o{index}/r{index}"] = {
                "full_name": f"o{index}/r{index}",
                "language": language,
                "views": views,
                "published_at": "2026-10-20T00:00:00Z",
                "topics": ["cli"],
            }
        model = learn_command(object(), store)

    assert model["learned"] is True
    assert model["language_multipliers"]["rust"] > 1.0
    assert 0.85 <= model["language_multipliers"]["php"] <= 1.15
    assert all(0.85 <= value <= 1.15 for value in model["language_multipliers"].values())


def test_learn_refuses_to_guess_from_two_posts():
    import tempfile

    from ghos_v6.pipeline import learn_command
    from ghos_v6.state import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(tmp)
        store.data["published"] = {
            "a/a": {"full_name": "a/a", "language": "Rust", "views": 100, "published_at": "2026-10-20T00:00:00Z"},
            "b/b": {"full_name": "b/b", "language": "Go", "views": 50, "published_at": "2026-10-20T00:00:00Z"},
        }
        result = learn_command(object(), store)
    assert result["learned"] is False
    assert result["reason"] == "not_enough_samples"


def test_channel_polls_must_be_anonymous():
    """Telegram rejects non-anonymous polls in channels — silent failure otherwise."""
    from ghos_v6.polls import is_channel

    assert is_channel("@GitHub_Open_Source")
    assert is_channel("-1009999999999")
    assert not is_channel(8652931211)


def test_facebook_handoff_row_matches_the_existing_make_scenario():
    """make.com already reads project_id/content/platform/media_urls."""
    from ghos_v6.sheets import HEADERS, row_for

    row = row_for(
        {"repo_id": 3961, "full_name": "acme/site", "name": "site", "image_url": "https://i.ibb.co/x.png"},
        "post body",
    )
    assert HEADERS[:4] == ("project_id", "content", "platform", "media_urls")
    assert row[:4] == ["3961", "post body", "facebook", "https://i.ibb.co/x.png"]
    assert len(row) == len(HEADERS)


def test_facebook_handoff_never_crashes_the_post_when_sheets_is_down():
    from ghos_v6.sheets import append_rows

    result = append_rows("sheet", [["a"]], credentials_json="{}")
    assert result["ok"] is False
    assert result["appended"] == 0


def test_facebook_never_hands_a_private_preview_to_the_page():
    from ghos_v6.content_adapters import facebook_media_url

    assert facebook_media_url({"image_url": "https://opengraph.githubassets.com/1/a/b"}) == ""
    assert facebook_media_url({"image_url": "https://i.ibb.co/x/card.png"}) == "https://i.ibb.co/x/card.png"


def test_portrait_card_fits_telegram_photo_limits():
    from pathlib import Path
    import tempfile

    from PIL import Image

    from ghos_v6.cards_portrait import render_portrait_card

    with tempfile.TemporaryDirectory() as tmp:
        path = render_portrait_card(
            {"name": "obscura", "language": "Rust", "stars": 29900, "forks": 2254,
             "contributors_count": 100, "license": {"spdx_id": "Apache-2.0"},
             "topics": [], "pushed_at": "2026-10-31T00:00:00Z", "description": "x"},
            {"card_hook": "Browse the web without Chrome", "card_subline": "Short."},
            Path(tmp) / "p.png",
            issue_number=5,
        )
        size = Image.open(path).size
    assert size == (1080, 1350), "4:5 portrait is what fills a phone column"


def test_every_external_surface_carries_the_channel_loop():
    """Traffic rule: nothing leaves the system without a way back to the
    owner's channel. Checked for every platform, including unknown ones."""
    from ghos_v6.content_adapters import (
        build_Facebook_text,
        build_bluesky_text,
        build_crosspost_text,
        build_devto_body,
    )

    record = {
        "full_name": "t8y2/dbx",
        "name": "dbx",
        "url": "https://github.com/t8y2/dbx",
        "homepage": "https://dbxio.com",
        "stars": 28443,
        "forks": 2598,
        "language": "Rust",
        "license": {"spdx_id": "Apache-2.0"},
        "description": "Manage 100+ databases from one 25 MB client",
        "readme_summary": "DBX is a compact Rust client for relational and NoSQL engines.",
        "key_features": ["100+ databases", "desktop, CLI and Docker"],
        "discussion_question": "Which database do you query most?",
        "telegraph_url": "https://telegra.ph/dbx-11-01",
    }
    generated = {
        "hook": "Manage 100+ databases from one 25 MB binary",
        "summary": "compact Rust client",
        "key_features": ["100+ databases"],
        "verdict": "essential if you juggle engines",
    }
    surfaces = {
        "bluesky": build_bluesky_text(record, generated),
        "Facebook": build_Facebook_text(record, generated),
        "devto": build_devto_body(record, generated),
        "threads": build_crosspost_text("threads", record, generated),
        "hashnode": build_crosspost_text("hashnode", record, generated),
    }
    for platform, text in surfaces.items():
        assert "GitHub_Open_Source" in text, f"{platform} has no channel link"


def test_bluesky_keeps_the_channel_link_even_with_a_huge_hook():
    from ghos_v6.content_adapters import build_bluesky_text

    record = {
        "full_name": "owner/" + "r" * 60,
        "name": "r" * 60,
        "url": "https://github.com/owner/" + "r" * 60,
        "stars": 90000,
        "language": "Rust",
        "description": "word " * 60,
        "key_features": ["feature " * 20] * 3,
    }
    generated = {"hook": "h " * 50, "verdict": "v " * 40, "summary": "s " * 80}
    text = build_bluesky_text(record, generated)
    assert "GitHub_Open_Source" in text
    assert len(text) <= 300


def test_queue_is_a_pipeline_not_a_daily_list():
    """Three posts a day drain the queue faster than a nightly collect can
    refill it. A fresh collect must extend the pipeline, not replace it."""
    import tempfile

    from ghos_v6.state import StateStore

    with tempfile.TemporaryDirectory() as tmp:
        store = StateStore(tmp)
        store.set_queue([{"full_name": f"old/r{index}"} for index in range(5)])
        store.pop_queue_candidate("old/r0")  # published
        store.set_queue([{"full_name": "new/r1"}, {"full_name": "old/r3"}])
        names = [c["full_name"] for c in store.data["queue"]]

    assert names[0] == "new/r1"          # fresh data comes first
    assert "old/r3" in names             # refreshed, not duplicated
    assert names.count("old/r3") == 1
    assert "old/r1" in names             # yesterday's survivors are kept
    assert "old/r0" not in names         # published is not resurrected


def test_gemini_is_called_once_per_published_post_not_per_candidate():
    """Quota rule: deterministic ranking decides first, Gemini only writes
    the post that is actually going out. Generating copy for the whole
    queue would burn ~28 calls a run for ~25 unused texts."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "ghos_v6" / "pipeline.py").read_text(encoding="utf-8")
    body = source.split("def publish_slot(")[1].split("\ndef ")[0]
    # one call site inside the slot, after selection
    assert body.count("generate_project_content(") == 1
    assert body.index("select_candidate(") < body.index("generate_project_content(")
    # and the retry budget is bounded, not 10
    assert "max_attempts = 4" in body
    assert "while attempts < max_attempts" in body


def test_queue_stays_a_week_of_fuel_and_readme_does_not_ride_along():
    """State is committed on every run: 14 KB of README per queued candidate
    made a 700 KB file. The queue keeps an excerpt only, and stays ~a week
    and a half of slots."""
    from pathlib import Path

    pipeline = (Path(__file__).resolve().parents[1] / "src" / "ghos_v6" / "pipeline.py").read_text(encoding="utf-8")
    state = (Path(__file__).resolve().parents[1] / "src" / "ghos_v6" / "state.py").read_text(encoding="utf-8")
    assert 'candidate["readme"] = ""' in pipeline
    assert 'candidate["readme_excerpt"] = readme[:1_200]' in pipeline
    assert "queue = passing[:28]" in pipeline
    assert "merged[:28]" in state


def test_quick_start_only_takes_post_space_when_it_earns_it():
    """Editorial agents: the post earns its place with a story and a
    judgement, not with a manual. One short command proves a point; a
    wall of clone-and-compose belongs to the article, and a placeholder
    `git clone` tells the reader nothing at all."""
    from ghos_v6.formatter import _is_placeholder_clone, _quick_start_belongs_in_post

    assert _quick_start_belongs_in_post("cargo install dbx") is True
    assert _quick_start_belongs_in_post("docker run -d -p 8080:8080 ghcr.io/o/app") is True
    assert _quick_start_belongs_in_post("npm install -g @scope/tool") is True
    # placeholder clone: never printed
    assert _quick_start_belongs_in_post("git clone https://github.com/o/r.git") is False
    assert _is_placeholder_clone("git clone https://github.com/o/r.git") is True
    # long multi-step setup: article, not post
    wall = "git clone https://github.com/o/r && cd r && cp .env.example .env && docker compose up -d --build"
    assert _quick_start_belongs_in_post(wall) is False
    assert _quick_start_belongs_in_post("a" * 200) is False


def test_facebook_crosspost_never_falls_back_to_the_short_generic_text():
    """Regression: build_crosspost_text lowercased the platform name and then
    compared it to "Facebook".  The branch never matched, so the Facebook
    post silently degraded to a 169-char lead + link.  Both spellings must
    produce the real structured post."""
    from ghos_v6.content_adapters import build_Facebook_text, build_crosspost_text

    record = {"full_name": "AprilNEA/OpenLogi", "name": "OpenLogi", "url": "https://github.com/AprilNEA/OpenLogi",
              "description": "Native Logitech config", "language": "Rust", "stars": 24808, "forks": 892,
              "license": {"spdx_id": "Apache-2.0"}}
    generated = {"hook": "Remap your Logitech buttons without a heavy electron app.",
                 "summary": "OpenLogi talks HID++ directly and stores config in TOML.",
                 "key_features": ["Multi-step workflows", "Per-app profiles", "DPI presets", "SmartShift"],
                 "who_for": "Power users on Linux.", "why_now": "Vendor software is bloated.",
                 "quick_start": "cargo install openlogi", "discussion_question": "Which feature do you miss?"}
    expected = build_Facebook_text(record, generated)
    for spelling in ("Facebook", "facebook", "FACEBOOK"):
        assert build_crosspost_text(spelling, record, generated) == expected
    assert len(expected) > 500  # the real structured post, not the fallback


def test_each_surface_gets_its_own_headline_not_the_shared_hook():
    """Owner's question: 'shouldn't headlines be for ordinary people, and
    different on each platform?'  Measured before: Bluesky and Facebook
    opened with the same shared hook (29 of 29 words shared).
    Now each adapter prefers its own field and falls back to the old
    behaviour when the second call is missing."""
    from ghos_v6.content_adapters import (
        build_bluesky_text,
        build_Facebook_text,
        surface_field,
    )
    from ghos_v6.Gemini import jargon_hits

    record = {"full_name": "heygen-com/hyperframes", "name": "HyperFrames",
              "url": "https://github.com/heygen-com/hyperframes", "language": "TypeScript",
              "stars": 76759, "forks": 1200, "license": {"spdx_id": "MIT"}}
    generated = {
        "hook": "Turn HTML, CSS, and animations into deterministic MP4 videos straight from your terminal or AI coding agent.",
        "key_features": ["Render on the server", "Deterministic output", "No cloud"],
        "surfaces": {
            "bluesky_headline": "Turn web code into video files",
            "bluesky_lead": "A framework that converts HTML layouts into clean video files.",
            "Facebook_headline": "Turn website designs into video files",
            "Facebook_lead": "Point it at a page layout and get a video that looks the same every time.",
        },
    }
    bs = build_bluesky_text(record, generated)
    fb = build_Facebook_text(record, generated)
    assert bs.startswith("Turn web code into video files")
    assert "Turn website designs into video files" in fb
    # the plainest surfaces carry no jargon
    assert jargon_hits(generated["surfaces"]["bluesky_headline"]) == []
    assert jargon_hits(generated["surfaces"]["Facebook_headline"]) == []
    # and the old record without surfaces still renders
    legacy = {"hook": generated["hook"], "key_features": generated["key_features"]}
    assert build_bluesky_text(record, legacy)
    assert surface_field(legacy, "bluesky_headline") == ""


def test_jargon_audit_and_seo_fallbacks():
    from ghos_v6.Gemini import jargon_hits
    from ghos_v6.content_adapters import build_devto_title, build_seo_description

    assert jargon_hits("Query your codebase like a graph") == ["codebase"]
    assert jargon_hits("Turn your files into a map") == []
    record = {"name": "Graphify", "full_name": "Graphify-Labs/graphify",
              "url": "https://github.com/Graphify-Labs/graphify", "stars": 100}
    # SEO title from the surface copy wins when it fits the 50-60 window
    titled = {"hook": "x", "article_title": "Graphify: Turn Any Codebase into a Knowledge Graph",
              "surfaces": {"seo_title": "Turn codebases into knowledge graphs - Graphify"}}
    assert build_devto_title(record, titled) == "Turn codebases into knowledge graphs - Graphify"
    # description never ends in an ellipsis
    described = {"surfaces": {"seo_description": "A very long description " * 12}}
    assert not build_seo_description(record, described).endswith("…")


def test_sponsor_block_at_the_top_does_not_reject_the_whole_video():
    """Every WorldofAI video opens with a sponsor line, so scanning the whole
    description rejected 15 of 15 videos as 'sponsored'.  The signal is a
    sponsor in the opening block; a sponsor deep in the body is not the
    topic of the video."""
    from datetime import datetime, timedelta, timezone

    from ghos_v6.news import prefilter_entry

    now = datetime.now(timezone.utc)
    base = {
        "video_id": "abc123",
        "url": "https://www.youtube.com/watch?v=abc123",
        "published": now - timedelta(hours=5),
        "title": "Grok Bot AI Teammates for developers",
        "description": "Intro about open-source coding agents and GitHub workflows.",
    }
    ok, reason = prefilter_entry(base, now=now)
    assert (ok, reason) == (True, "")

    # sponsor line at the top: rejected, and for the right reason
    top = dict(base, description="Sponsored by Printify - Paid partnership\n\n" + base["description"])
    assert prefilter_entry(top, now=now) == (False, "sponsored")

    # sponsor mentioned far into the body: still accepted
    deep = dict(base, description=base["description"] + "\n" + "context " * 120 + "\nThis video is sponsored by someone.")
    assert prefilter_entry(deep, now=now)[0] is True


def test_news_prefilter_is_time_based_not_time_of_day_dependent():
    """A video published 20 hours ago must pass at any hour. The old check
    compared against midnight UTC and rejected recent videos in the evening."""
    from datetime import datetime, timedelta, timezone

    from ghos_v6.news import prefilter_entry

    for hour in (0, 6, 12, 18, 23):
        now = datetime.now(timezone.utc).replace(hour=hour, minute=0, second=0, microsecond=0)
        entry = {
            "video_id": "v1",
            "url": "https://www.youtube.com/watch?v=v1",
            "published": now - timedelta(hours=20),
            "title": "Open-source coding agent ships on GitHub",
            "description": "A developer tool.",
        }
        assert prefilter_entry(entry, now=now) == (True, ""), f"failed at {hour}:00"


def test_launch_announcement_beats_its_own_docs_page():
    """A 'docs' bonus of +55 on top of the official-domain bonus made every
    documentation sub-page outrank the launch post that announced the thing.
    The brief was then written from reference docs, which state what exists
    (not what changed) — and it produced garbled headlines."""
    from ghos_v6.news import _source_score

    announcement = _source_score("https://x.ai/news/introducing-grok-bot")
    docs = _source_score("https://docs.x.ai/grok-bot/overview")
    landing = _source_score("https://x.ai/bot")
    assert announcement > docs, "the announcement must win"
    assert docs > landing or docs >= landing
    # a docs page on a non-official host must not outrank an official blog
    assert _source_score("https://blog.example.com/x") < _source_score("https://openai.com/blog/x")


def test_brief_headline_is_repaired_but_real_acronyms_survive():
    """Measured on live output: 'SpaceXSI Releases Grok Bot' (two brands
    glued) and ALL-CAPS hype carried over from the YouTube title."""
    from ghos_v6.news import clean_brief_headline as clean

    assert clean("SpaceXSI Releases Grok Bot AI Teammates") == "SpaceX xAI Releases Grok Bot AI Teammates"
    assert clean("HUGE GPT-6.2 BEL LEAK! Opus 6") == "GPT-6.2 BEL Leak! Opus 6"
    assert clean("Insane New Video Model Beats Everything") == "New Video Model Beats Everything"
    # real acronyms are not sentence-cased away
    assert "API" in clean("OpenAI ships API for GPT-6 in the CLI")
    assert clean("") == ""


def test_transient_model_errors_are_retried_and_permanent_ones_are_not():
    from ghos_v6.Gemini import HttpError, generate_with_backoff, is_transient_error

    assert is_transient_error(HttpError(429, "You exceeded your current quota"))
    assert is_transient_error(HttpError(503, "This model is currently experiencing high demand"))
    assert not is_transient_error(HttpError(400, "Invalid schema"))
    assert not is_transient_error(ValueError("bad json"))

    waits: list[float] = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise HttpError(503, "high demand")
        return "ok"

    assert generate_with_backoff(flaky, sleep=waits.append) == "ok"
    assert calls["n"] == 3
    assert waits == [20.0, 40.0]  # linear backoff, not a tight loop

    def broken():
        raise HttpError(400, "bad request")

    calls["n"] = 0
    try:
        generate_with_backoff(broken, sleep=waits.append)
    except HttpError:
        pass
    else:
        raise AssertionError("permanent errors must not be swallowed")


def test_production_target_fails_loudly_when_the_channel_is_missing():
    """The switch to production must break at startup, not half an hour later
    in the middle of a publish slot. config.telegram_chat_id resolves the id
    and raises when production is selected without one."""
    import pytest

    from ghos_v6.config import Config

    prod = Config(telegram_bot_token="x", app_env="production", publish_target="main")
    with pytest.raises(RuntimeError):
        _ = prod.telegram_chat_id

    ready = Config(
        telegram_bot_token="x",
        app_env="production",
        publish_target="main",
        telegram_main_channel_id=-1008888888888,
    )
    assert ready.is_main_target
    assert ready.telegram_chat_id == -1008888888888
    # in test mode the main channel id is ignored on purpose
    test = Config(telegram_bot_token="x", app_env="test", publish_target="test",
                  telegram_test_channel_id=-1009999999999, telegram_main_channel_id=-1008888888888)
    assert test.telegram_chat_id == -1009999999999


def test_every_cron_in_the_workflow_has_a_route_in_the_code():
    """A cron with no route raises 'Unknown schedule' at runtime, and the
    only place that shows up is a failed run in Actions. Both lists used to
    drift silently: the workflow said 19:10 and the router still said 15:40
    on Mon/Wed/Fri, which would have killed the daily brief in production."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    cli = (root / "src" / "ghos_v6" / "cli.py").read_text(encoding="utf-8")

    crons = set(re.findall(r'cron:\s*"([^"]+)"', workflow))
    routes = set(re.findall(r'^\s*"([^"]+)":\s*lambda', cli, flags=re.M))
    # Crons that must run two commands in order live in ``pairs`` instead of
    # ``routes`` (Thursday: close the poll, then update the value model).
    pairs = set(re.findall(r'"([^"]+)":\s*\(', cli))
    routed = routes | pairs
    missing = crons - routed
    assert not missing, f"cron without a route in cli.py: {sorted(missing)}"
    # the daily brief specifically
    assert "15 19 * * *" in crons and "15 19 * * *" in routed


def test_brief_filters_take_reviews_and_launches_but_reject_gossip():
    """Owner rule: announcements yes, 'left-field' videos no. Measured on the
    live feed: a rumour round-up ('LEAK'), a listicle ('I Tested 8'), and
    reaction content were all reaching the pipeline."""
    from datetime import datetime, timedelta, timezone

    from ghos_v6.news import prefilter_entry

    now = datetime.now(timezone.utc)

    def check(title: str, description: str = "open-source model launch for developers"):
        return prefilter_entry(
            {
                "video_id": title[:10],
                "url": "https://www.youtube.com/watch?v=x",
                "published": now - timedelta(hours=3),
                "title": title,
                "description": description,
            },
            now=now,
        )

    # wanted: a launch, a review with a product behind it
    assert check("OpenAI launches GPT-6 with a 1M context window")[0] is True
    assert check("Claude 4.5 Sonnet review: what changed for coding")[0] is True

    # unwanted: gossip, listicles, reaction content
    assert check("HUGE Claude LEAK! Everything we know")[1] == "leak_or_rumor_title"
    assert check("Anthropic BOMBSHELL: staff drama explained")[1] == "rumour_not_news"
    assert check("I Tested 8 of the BEST AI Video Models")[1] == "not_a_review_or_launch"
    assert check("Top 10 AI tools of the week")[1] == "not_a_review_or_launch"
    # A clickbait adjective over a *named* model is not reaction by itself:
    # this channel writes every launch that way ("Grok Bot Is INSANE" was a
    # real product launch). Substance is the LLM's call - it rejected
    # "GPT-6 Is INSANE" for having no benchmarks or details at all. The
    # cheap prefilter only rejects a title that names nobody.
    assert check("GPT-6 Is INSANE")[0] is True
    # Rejected either way; the rumour filter names the reason first.
    assert check("This Is INSANE and You Won't Believe It")[0] is False


def test_brief_headline_keeps_the_vendor_and_version():
    """The owner reads the model name first. Hype removal and brand repair
    must not touch technical names or versions."""
    from ghos_v6.news import clean_brief_headline as clean

    assert clean("HUGE! Claude 4.5 Ships for Coding") == "Claude 4.5 Ships for Coding"
    kept = clean("OpenAI ships GPT-6 with a 1M context window")
    assert "GPT-6" in kept and "OpenAI" in kept
    v = clean("DeepSeek V4.1 Flash now available")
    assert "V4.1" in v


def test_briefs_never_enter_the_ranking_or_poll_system():
    """Owner question: should video briefs feed the voting and learning
    machinery?  No. A brief has no repository to rank, so its views say
    nothing about which projects to pick. It is stored in news_topics,
    never in published, which is what metrics, learn and the weekly poll
    read."""
    from ghos_v6.state import StateStore

    store = StateStore("/tmp/never_used_state")
    store.data = {"published": {}, "news_topics": [], "queue": []}
    store.data["news_topics"].append({"video_id": "v1", "topic_key": "gpt-6", "headline": "GPT-6 ships"})
    assert store.published_records() == []
    # the ranking learns only from records that carry a repository score
    assert all("full_name" not in r for r in store.published_records())

    source = open("src/ghos_v6/pipeline.py", encoding="utf-8").read()
    metrics_body = source.split("def metrics_command")[1].split("def close_poll_command")[0]
    assert "published_records()" in metrics_body
    assert "news_topics" not in metrics_body.split("return")[0] or "AI Briefs live in news_topics" in metrics_body


def test_only_the_best_post_of_the_day_is_crossposted():
    """Owner decision after the agents' warning about blanket publishing:
    three posts a day must not all go to Bluesky and Facebook.
    Only the top-scored post of each day leaves Telegram; the rest are
    marked 'held' so state does not accumulate a backlog."""
    from datetime import datetime, timedelta, timezone

    from ghos_v6.config import Config
    from ghos_v6.pipeline import process_crossposts
    from ghos_v6.state import StateStore

    store = StateStore("/tmp/crosspost_hold_state")
    store.data = {"published": {}, "events": []}
    now = datetime.now(timezone.utc)
    posts = [
        ("midday", 71.0, 2),
        ("morning", 88.0, 3),   # strongest of the day
        ("evening", 60.0, 4),
    ]
    for name, score, hours_ago in posts:
        store.data["published"][f"owner/{name}"] = {
            "full_name": f"owner/{name}",
            "name": name,
            "score": score,
            "published_at": (now.replace(hour=0, minute=0, second=0, microsecond=0)
                             + timedelta(minutes=hours_ago)).isoformat(),
            "crossposts": {
                "bluesky": {"status": "pending", "attempts": 0, "due_at": (now - timedelta(minutes=5)).isoformat()},
            },
        }

    config = Config(telegram_bot_token="x", feature_bluesky=True,
                    bluesky_handle="o", bluesky_app_password="p")
    process_crossposts(config, store)

    statuses = {key: rec["crossposts"]["bluesky"]["status"] for key, rec in store.data["published"].items()}
    assert statuses["owner/morning"] != "held", "the best post of the day must go out"
    assert statuses["owner/midday"] == "held"
    assert statuses["owner/evening"] == "held"
    # and held posts do not pile up as pending work for tomorrow
    assert store.data["published"]["owner/midday"]["crossposts"]["bluesky"]["held_reason"] == "not_best_of_day"



def test_facebook_and_devto_get_their_own_bodies():
    """Owner question turned into a decision: every surface writes its own
    body, but on top of one shared brief. Facebook was reassembling generic
    fields into a WHAT IT DOES / WHAT STANDS OUT wall; dev.to published the
    same article text as Telegraph, which makes the two indexed pages
    compete for one query."""
    from ghos_v6.content_adapters import build_devto_body, build_Facebook_text

    record = {"full_name": "AprilNEA/OpenLogi", "name": "OpenLogi",
              "url": "https://github.com/AprilNEA/OpenLogi", "language": "Rust", "stars": 24808,
              "license": {"spdx_id": "Apache-2.0"}}
    shared_article = "## What it is\n\nThe shared editorial breakdown."
    generated = {
        "hook": "Remap your Logitech buttons.",
        "summary": "Generic shared summary about HID++.",
        "key_features": ["Multi-step workflows", "Per-app profiles", "DPI presets"],
        "article_markdown": shared_article,
        "surfaces": {
            "Facebook_body": "Your mouse says one thing and the vendor app says another.\n\nThis tool fixes the settings locally, with no account.",
            "devto_body": "## What it is\n\nA technical article written for developers who will clone it today.",
            "telegraph_angle": "Explain the problem and the trade-offs.",
        },
    }
    fb = build_Facebook_text(record, generated)
    assert "Your mouse says one thing" in fb
    # with its own body, the generic feature wall is gone
    assert "WHAT STANDS OUT" not in fb

    devto = build_devto_body(record, generated)
    assert "written for developers who will clone it" in devto
    assert "shared editorial breakdown" not in devto

    # fallback: no per-surface copy -> old behaviour, never an empty page
    legacy = {k: v for k, v in generated.items() if k != "surfaces"}
    assert "shared editorial breakdown" in build_devto_body(record, legacy)


def test_surface_fields_have_per_field_limits():
    """First live run of the new bodies cut a 450-650 word article at 400
    characters, mid-word. A single cap for every field cannot work when one
    field is a headline and another is an article."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "ghos_v6" / "Gemini.py").read_text(encoding="utf-8")
    limits = source.split("limits = {")[1].split("}")[0]
    assert '"devto_body": 9_000' in limits
    assert '"Facebook_body": 2_600' in limits
    assert '"bluesky_headline": 90' in limits
    # and the cleaning call uses the per-field limit, not a constant
    assert "limits.get(key, 400)" in source


def test_readme_image_is_hosted_on_imgbb_not_left_as_a_vendor_link():
    """Owner asked whether cards land on ImgBB and whether a README picture
    wins over our card. The order was right (README -> card -> OpenGraph),
    but a README picture was passed through as the vendor's own URL, so
    nothing reached ImgBB while the article and every cross-post reused that
    URL. Now the picture is shrunk and hosted, with the original kept as a
    fallback when hosting fails."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "ghos_v6" / "pipeline.py").read_text(encoding="utf-8")
    block = source.split('if visual.kind == "readme" and visual.data:')[1][:900]
    assert "_host_local_image(" in block
    assert "shrink_for_web_preview(visual.data)" in block
    assert "if hosted:" in block  # fallback keeps the vendor URL

    from ghos_v6.media import choose_visual
    import inspect

    order = inspect.getsource(choose_visual)
    # README picture first, then our card, then OpenGraph
    assert order.index("media_urls") < order.index("render_project_card") < order.index("opengraph.githubassets")
