"""Owner 23.11: a poll result must become a kept promise, not a nudge.

The 19.11 poll closed with a 3:3 tie, "Blazing-fast native notes app and
rich-text editor" was recorded as the winner — and ten posts later nothing of
the kind had been published. Two reasons, both fixed here:

* nothing in the code promised anything: the result only wrote soft
  multipliers into ``audience_profile``;
* the winning option's GitHub topic list was **empty** (that candidate had no
  topics), so even the soft multiplier was exactly 1.0.

``build_obligation`` turns the result into a dated commitment (7 days) with
terms taken from the option's topics *and* from the option text, and
``select_candidate`` prefers a matching candidate while it is open.
``collect`` searches those terms explicitly, so the promise does not depend
on today's rotation. Fulfilment, expiry and failure are all written to state
and to the weekly report - a promise that cannot be checked is not a promise.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ghos_v6 import pipeline, rank
from ghos_v6.polls_design import (
    OBLIGATION_DAYS,
    build_obligation,
    label_keywords,
    obligation_state,
)
from ghos_v6.sources import build_search_queries
from ghos_v6.state import StateStore


def _poll(**overrides):
    poll = {
        "type": "job",
        "axis": "job",
        "option_keys": ["job_rust_1", "job_ai-video_2", "job_3d_3"],
        "option_topics": {"job_rust_1": [], "job_ai-video_2": ["ai-video"], "job_3d_3": ["3d"]},
        "question": "Which of these would save you time this week?",
    }
    poll.update(overrides)
    return poll


def _tally(votes=(3, 3, 1), labels=(
    "Blazing-fast native notes app and rich-text editor for macOS, Windows, and Linux",
    "The open source AI video agent",
    "3D modeling in Python",
)):
    return {"options": [{"text": label, "votes": vote} for label, vote in zip(labels, votes)]}


# --- terms from the word "go" ------------------------------------------------


def test_label_keywords_drop_the_words_that_match_everything():
    words = label_keywords(
        "Blazing-fast native notes app and rich-text editor for macOS, Windows, and Linux"
    )
    assert words[:2] == ["notes", "editor"]
    assert "app" not in words and "native" not in words and "blazing" not in words
    assert "macos" in words and "linux" in words


def test_an_empty_topic_list_no_longer_voids_the_promise():
    """The exact 19.11 failure: the winner had no GitHub topics at all."""
    obligation = build_obligation(_tally(), _poll())
    assert obligation["status"] == "open"
    assert obligation["kind"] == "cover_topic"
    assert obligation["topics"] == []
    assert obligation["keywords"][0] == "notes"


def test_the_due_date_is_one_week_and_the_tie_is_admitted():
    now = datetime(2026, 11, 19, 19, 0, tzinfo=timezone.utc)
    obligation = build_obligation(_tally(), _poll(), now=now)
    due = datetime.fromisoformat(obligation["due_at"].replace("Z", "+00:00"))
    assert due - now == timedelta(days=OBLIGATION_DAYS)
    assert obligation["tied"] is True


def test_polls_that_promised_nothing_create_no_obligation():
    keep_mix = build_obligation(
        _tally(votes=(2, 5, 1), labels=("Show more devtools tools", "Keep the current mix", "It was the topic")),
        _poll(type="reality_check", axis="reality_check",
              option_keys=["more_of_it", "keep_mix", "topic_not_language"],
              option_topics={"more_of_it": ["devtools"], "keep_mix": ["devtools"],
                             "topic_not_language": ["devtools"]}),
    )
    assert keep_mix["status"] == "none"

    format_poll = build_obligation(
        _tally(votes=(4, 2, 1), labels=("More deep dives", "More quick picks", "More comparisons")),
        _poll(type="format", axis="format", option_keys=["deep_dive", "quick_picks", "comparison"],
              option_topics={}),
    )
    assert format_poll["status"] == "none"


def test_show_more_of_it_is_a_theme_promise_not_a_project_promise():
    obligation = build_obligation(
        _tally(votes=(6, 1, 0), labels=("Show more devtools tools", "Keep the current mix", "It was the topic")),
        _poll(type="reality_check", axis="reality_check",
              option_keys=["more_of_it", "keep_mix", "topic_not_language"],
              option_topics={"more_of_it": ["devtools"], "keep_mix": ["devtools"],
                             "topic_not_language": ["devtools"]}),
    )
    assert obligation["kind"] == "cover_theme"
    assert obligation["topics"] == ["devtools"]


def test_state_reports_open_then_expired():
    now = datetime(2026, 11, 19, 19, 0, tzinfo=timezone.utc)
    obligation = build_obligation(_tally(), _poll(), now=now)
    assert obligation_state(obligation, now=now) == "open"
    assert obligation_state(obligation, now=now + timedelta(days=6)) == "open"
    assert obligation_state(obligation, now=now + timedelta(days=8)) == "expired"


# --- selection ---------------------------------------------------------------


def _candidate(name, score, **overrides):
    payload = {
        "full_name": f"owner/{name}",
        "name": name,
        "description": "A desktop app",
        "topics": [],
        "score": score,
        "language": "Rust",
    }
    payload.update(overrides)
    return payload


def test_a_promised_project_wins_over_a_better_scoring_one():
    now = datetime(2026, 11, 19, 19, 0, tzinfo=timezone.utc)
    obligation = build_obligation(_tally(), _poll(), now=now)
    notes = _candidate("inkwell", 55.0, description="A local notes app and markdown editor")
    unrelated = _candidate("k8s-tool", 80.0, description="Kubernetes dashboard")
    chosen = rank.select_candidate([unrelated, notes], [], None, None, obligation)
    assert chosen["full_name"] == "owner/inkwell"


def test_the_promise_cannot_force_a_dead_repository():
    now = datetime(2026, 11, 19, 19, 0, tzinfo=timezone.utc)
    obligation = build_obligation(_tally(), _poll(), now=now)
    dead = _candidate("ghost-notes", 5.0, description="A notes app")
    healthy = _candidate("k8s-tool", 80.0)
    chosen = rank.select_candidate([healthy, dead], [], None, None, obligation)
    assert chosen["full_name"] == "owner/k8s-tool"


def test_an_expired_or_closed_promise_does_not_steer_selection():
    obligation = build_obligation(_tally(), _poll())
    obligation["status"] = "fulfilled"
    notes = _candidate("inkwell", 40.0, description="A notes app")
    unrelated = _candidate("k8s-tool", 80.0)
    chosen = rank.select_candidate([unrelated, notes], [], None, None, obligation)
    assert chosen["full_name"] == "owner/k8s-tool"


# --- promise lifecycle in the pipeline --------------------------------------


def _store_with_promise(tmp_path, obligation=None):
    store = StateStore(tmp_path)
    store.data = {"poll_obligation": obligation or build_obligation(_tally(), _poll())}
    store.save()
    return store


def test_fulfilment_is_recorded_when_the_promised_project_is_published(tmp_path):
    store = _store_with_promise(tmp_path)
    pipeline._fulfil_obligation(store, _candidate("inkwell", 60.0, description="A notes app"))
    assert store.data["poll_obligation"]["status"] == "fulfilled"
    assert store.data["poll_obligation"]["fulfilled_repo"] == "owner/inkwell"
    events = [line for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any("poll_obligation_fulfilled" in line for line in events)


def test_a_post_on_another_topic_leaves_the_promise_open(tmp_path):
    store = _store_with_promise(tmp_path)
    pipeline._fulfil_obligation(store, _candidate("k8s-tool", 60.0, description="Kubernetes dashboard"))
    assert store.data["poll_obligation"]["status"] == "open"
    assert store.data["poll_obligation"]["fulfilled_repo"] == ""


def test_an_overdue_promise_is_expired_loudly(tmp_path):
    now = datetime(2026, 11, 19, 19, 0, tzinfo=timezone.utc)
    obligation = build_obligation(_tally(), _poll(), now=now)
    store = _store_with_promise(tmp_path, obligation)
    # 26.11: the assertion must not depend on the wall clock - the promise is
    # built for 19.11 and its due date passes on 26.11, so a later test run
    # saw an expired promise and a green suite turned red on its own.
    assert pipeline._live_obligation(store, now=now) is not None
    later = now + timedelta(days=9)
    assert pipeline._live_obligation(store, now=later) is None
    assert store.data["poll_obligation"]["status"] == "expired"
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert any("poll_obligation_expired" in line for line in events)


def test_no_promise_means_ordinary_selection(tmp_path):
    store = StateStore(tmp_path)
    store.data = {}
    assert pipeline._live_obligation(store) is None


# --- where the pool comes from ----------------------------------------------


def test_collect_searches_the_promised_terms_explicitly():
    queries = build_search_queries(obligation_terms=["notes", "markdown editor"])
    joined = " ".join(query for query, _sort in queries)
    assert "topic:notes" in joined
    assert "notes in:name,description" in joined
    assert "markdown editor in:name,description" in joined


def test_the_promise_adds_at_most_three_targeted_queries():
    plain = len(build_search_queries())
    promised = len(build_search_queries(obligation_terms=["a", "b", "c", "d", "e"]))
    assert promised - plain == 6  # three terms x (topic + text) queries
    assert promised - plain <= 6


def test_collect_passes_the_open_promise_into_the_search(tmp_path, monkeypatch):
    store = _store_with_promise(tmp_path)
    captured: dict = {}

    def fake_collect_candidates(config, *, extra_pages=None, obligation_terms=None):
        captured["terms"] = obligation_terms
        return []

    monkeypatch.setattr(pipeline, "collect_candidates", fake_collect_candidates)
    monkeypatch.setattr(pipeline, "rank_candidates", lambda candidates, **kwargs: candidates)
    monkeypatch.setattr(pipeline, "enrich_candidates", lambda api, candidates, **kwargs: candidates)
    monkeypatch.setattr(pipeline, "GitHubApi", lambda token: object())
    pipeline.collect_command(_Config(), store)
    assert captured["terms"]
    assert "notes" in captured["terms"]


class _Config:
    GitHub_token = "test"
    telegram_source_channels = ()

    def __getattr__(self, _name):  # pragma: no cover - permissive stand-in
        return ""


def test_publishing_the_promised_project_marks_the_promise():
    """Contract: the hook must sit in the publish path, after the record."""
    from pathlib import Path

    body = Path("src/ghos_v6/pipeline.py").read_text(encoding="utf-8")
    publish = body.split("def publish_slot(")[1].split("\ndef ")[0]
    assert publish.index("store.record_published(record)") < publish.index(
        "_fulfil_obligation(store, candidate)"
    )


def test_the_weekly_report_tells_the_truth_about_the_promise(tmp_path):
    now = datetime(2026, 11, 19, 19, 0, tzinfo=timezone.utc)
    store = _store_with_promise(tmp_path, build_obligation(_tally(), _poll(), now=now))
    text = pipeline.report_command(_Config(), store, now=now)["text"]
    assert "Poll promise" in text and "open" in text

    # A fresh store for the second report: the weekly guard allows one per week.
    second = _store_with_promise(tmp_path / "kept", build_obligation(_tally(), _poll(), now=now))
    pipeline._fulfil_obligation(second, _candidate("inkwell", 60.0, description="A notes app"))
    second.save()
    text = pipeline.report_command(_Config(), second, now=now)["text"]
    assert "kept" in text and "owner/inkwell" in text


# --- the winner card: Telegram only, promise posts only ----------------------

def test_the_question_that_was_asked_is_kept_with_the_promise():
    obligation = build_obligation(_tally(), _poll())
    assert obligation["question"] == "Which of these would save you time this week?"


def _card_bytes(monkeypatch, tmp_path, *, url="https://i.ibb.co/x/poll-winner.png", by="imgbb"):
    seen: list[bytes] = []

    def fake_host(config, data, *, filename, content_type="image/png"):
        assert content_type == "image/png"
        assert filename.endswith(".png")
        seen.append(data)
        return url, by

    monkeypatch.setattr(pipeline, "_host_local_image", fake_host)
    return seen


def test_the_winner_card_rides_on_the_post_that_keeps_the_promise(tmp_path, monkeypatch):
    from PIL import Image
    import io

    store = _store_with_promise(tmp_path)
    seen = _card_bytes(monkeypatch, tmp_path)
    url = pipeline._poll_winner_preview(_Config(), store, _candidate("inkwell", 60.0, description="A notes app"))
    assert url == "https://i.ibb.co/x/poll-winner.png"
    with Image.open(io.BytesIO(seen[0])) as image:
        assert image.size == (1200, 630)
    assert store.data["poll_obligation"]["card_url"] == url
    events = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
    assert "poll_card_published" in events


def test_an_unrelated_post_keeps_the_standard_card(tmp_path, monkeypatch):
    store = _store_with_promise(tmp_path)
    _card_bytes(monkeypatch, tmp_path)
    assert pipeline._poll_winner_preview(_Config(), store, _candidate("k8s-tool", 60.0,
                                                                    description="Kubernetes dashboard")) == ""


def test_a_closed_promise_gets_no_card(tmp_path, monkeypatch):
    store = _store_with_promise(tmp_path)
    store.data["poll_obligation"]["status"] = "fulfilled"
    _card_bytes(monkeypatch, tmp_path)
    assert pipeline._poll_winner_preview(_Config(), store, _candidate("inkwell", 60.0,
                                                                    description="A notes app")) == ""


def test_a_failed_upload_falls_back_to_the_standard_card(tmp_path, monkeypatch):
    store = _store_with_promise(tmp_path)
    _card_bytes(monkeypatch, tmp_path, url="", by="")
    assert pipeline._poll_winner_preview(_Config(), store, _candidate("inkwell", 60.0,
                                                                    description="A notes app")) == ""
    assert "poll_card_unhosted" in (tmp_path / "events.jsonl").read_text(encoding="utf-8")


def test_a_broken_card_never_blocks_the_post(tmp_path, monkeypatch):
    store = _store_with_promise(tmp_path)

    def explode(*args, **kwargs):
        raise RuntimeError("no fonts on this box")

    monkeypatch.setattr("ghos_v6.cards_poll.render_poll_winner_card", explode)
    assert pipeline._poll_winner_preview(_Config(), store, _candidate("inkwell", 60.0,
                                                                    description="A notes app")) == ""
    assert "poll_card_failed" in (tmp_path / "events.jsonl").read_text(encoding="utf-8")


def test_the_card_never_leaks_into_cross_posts_or_the_article():
    """Contract: the card is a Telegram preview and nothing else.

    ``image_url`` feeds dev.to, Hashnode, Bluesky and the Telegraph article.
    The owner's rule is that those keep the standard card, so the poll card
    may only ever be assigned to ``preview_url``.
    """
    from pathlib import Path

    body = Path("src/ghos_v6/pipeline.py").read_text(encoding="utf-8")
    publish = body.split("def publish_slot(")[1].split("\ndef ")[0]
    assert "poll_card_url = _poll_winner_preview(" in publish
    assert "image_url = poll_card_url" not in publish
    assert "record[\"image_url\"] = poll_card_url" not in publish
    assert "cards_poll" not in Path("src/ghos_v6/devto_cover.py").read_text(encoding="utf-8")

def test_a_promise_with_no_counted_vote_gets_no_card(tmp_path, monkeypatch):
    """Owner 23.11: the newspaper prints a result only when there is one.

    An obligation can only be opened off a tally with votes (``build_obligation``
    returns nothing when the best option has none), so this state means the
    tally broke. The post must still go out - with the standard card, not with
    a front page claiming "the readers chose".
    """
    store = _store_with_promise(tmp_path)
    store.data["poll_obligation"]["votes"] = 0
    store.data["poll_obligation"]["total"] = 0
    seen = _card_bytes(monkeypatch, tmp_path)
    assert pipeline._poll_winner_preview(
        _Config(), store, _candidate("inkwell", 60.0, description="A notes app")
    ) == ""
    assert seen == []  # nothing was even uploaded
    assert "poll_card_skipped" in (tmp_path / "events.jsonl").read_text(encoding="utf-8")


def test_the_promise_post_text_carries_no_poll_line():
    """Owner 23.11: the kept promise is visible in the card, not in the words.

    The post that fulfils a poll must read exactly like any other post - no
    "you voted for this", no mention of a poll. The card is the only tell.
    """
    from ghos_v6.formatter import format_project_post

    candidate = _candidate("inkwell", 60.0, description="A notes app")
    candidate.update(
        {
            "full_name": "owner/inkwell",
            "url": "https://github.com/owner/inkwell",
            "stars": 900,
            "language": "Rust",
        }
    )
    generated = {
        "title": "A fast notes app",
        "hook": "Notes that open before you blink.",
        "body": ["It stores notes in a local file.", "Everything works offline."],
        "why": "Fast.",
    }
    text = format_project_post({"candidate": candidate, "generated": generated})
    assert len(text) > 200  # a real post, not an empty string passing by default
    assert "poll" not in text.lower()
    assert "vote" not in text.lower()
