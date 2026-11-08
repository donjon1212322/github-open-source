"""Owner 08.11 level 3: adaptive weekly polls, name-free options, Gemini fallback."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from ghos_v6 import pipeline, polls_design
from ghos_v6.config import Config
from ghos_v6.rank import value_multiplier
from ghos_v6.state import StateStore

NOW = datetime(2026, 11, 15, 16, 13, tzinfo=timezone.utc)


def _candidate(i: int, *, cluster: str, name: str = "") -> dict:
    return {
        "full_name": f"org/{name or f'tool-{i}'}",
        "name": name or f"tool-{i}",
        "description": f"Do a useful {cluster} job automatically without a cloud service, number {i}.",
        "topics": [cluster, "open-source"],
        "language": "Python", "category": "DevTools", "score": 90 - i,
        "stars": 1000 + i,
    }


def _queue() -> list[dict]:
    # Reproduce the real 08.11 skew: 9 candidates share one cluster.
    items = [_candidate(i, cluster="claude-code") for i in range(9)]
    items += [_candidate(20 + i, cluster=c) for i, c in
              enumerate(("ai-agents", "rust", "macos", "3d", "android"))]
    return items


class GoodGemini:
    def generate_json(self, prompt: str, **kwargs):
        return {"labels": ["Reverse-engineer any video shot by shot",
                           "Switch coding agents between models",
                           "Play a console game natively on a Mac",
                           "Replace an ad-filled TV home screen"]}


class BrokenGemini:
    def generate_json(self, prompt: str, **kwargs):
        raise RuntimeError("503 UNAVAILABLE")


class RepoNameGemini:
    def generate_json(self, prompt: str, **kwargs):
        return {"labels": ["org/tool-0 on GitHub with 1000 stars",
                           "Use tool-1 for local AI agents",
                           "Design 3D parts in an open-source CAD",
                           "Run a local model without an API key"]}


def test_sanitize_rejects_repo_names_and_service_copy():
    c = _candidate(0, cluster="rust")
    for bad in ("org/tool-0 on GitHub", "GitHub stars 1000", "tool-0", "http://x", "AB"):
        label = polls_design.sanitize_label(bad, c)
        assert "tool-0" not in label.lower() or label != bad
        assert "github" not in label.lower()
        assert "/" not in label
        assert 12 <= len(label) <= 100


def test_gemini_failure_keeps_a_valid_poll():
    labels, fallback = polls_design.job_labels(_queue()[:4], BrokenGemini())
    assert fallback is True and len(labels) == 4
    assert all(len(x) <= 100 and x.count(" ") >= 2 for x in labels)


def test_gemini_labels_are_used_when_clean():
    labels, fallback = polls_design.job_labels(_queue()[:4], GoodGemini())
    assert fallback is False
    assert labels[0] == "Reverse-engineer any video shot by shot"


def test_repo_name_from_gemini_is_replaced_by_fallback():
    labels, _ = polls_design.job_labels(_queue()[:4], RepoNameGemini())
    assert "github" not in labels[0].lower() and "stars" not in labels[0].lower()
    assert "org/tool-0" not in labels[0]


def test_options_come_from_distinct_clusters(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    store.data["queue"] = _queue()
    spec = polls_design.build_week_poll(store, client=GoodGemini())
    assert spec["type"] == "job"
    assert len(set(spec["clusters"])) == len(spec["clusters"]), "no four identical AI agents"
    assert len(set(spec["families"])) == len(spec["families"]), "no four AI-agent-only options"
    assert len(spec["options"]) == 4
    assert all("claude-code" not in o.lower() for o in spec["options"])
    assert all(len(o) <= 100 for o in spec["options"])
    assert len(spec["question"]) <= 300


def test_first_week_collects_preferences_with_a_job_poll(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    store.data["queue"] = _queue()
    poll_type, simplify = polls_design.choose_type(store, now=NOW)
    assert poll_type == "job" and simplify is False


def test_type_never_repeats_last_week(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    store.data["queue"] = _queue()
    store.data["audience_profile"] = {
        "axes": {"format": {"deep_dive": 0.6}}, "confidence": {"format": 0.4},
        "weeks": [{"week": "2026-W45", "type": "format", "votes": 12,
                   "low_confidence": False}],
    }
    poll_type, _ = polls_design.choose_type(store, now=NOW)
    assert poll_type != "format"


def test_reality_check_when_views_shift(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    store.data["queue"] = _queue()
    base = NOW - timedelta(days=12)
    pub = {}
    for i in range(12):
        pub[f"p{i}"] = {
            "published_at": (base + timedelta(days=i)).isoformat(),
            "language": "Rust", "category": "DevTools",
            "views_at_24h": 10 if i < 6 else 100,
            "views": 10 if i < 6 else 100,
        }
    store.data["published"] = pub
    store.data["audience_profile"] = {
        "axes": {"job": {"a": 0.6}}, "confidence": {"job": 0.5},
        "weeks": [{"week": "2026-W45", "type": "job", "votes": 10,
                   "low_confidence": False}],
    }
    assert polls_design.choose_type(store, now=NOW)[0] == "reality_check"


def _tally(votes: list[int]) -> dict:
    total = max(1, sum(votes))
    return {"total_voters": total, "question": "q",
            "options": [{"text": f"o{i}", "votes": v,
                         "share": v / total} for i, v in enumerate(votes)]}


def _designed_poll() -> dict:
    return {"type": "job", "axis": "job",
            "option_keys": ["k0", "k1", "k2", "k3"],
            "option_topics": {"k0": ["rust"], "k1": ["ai-agents"],
                              "k2": ["macos"], "k3": ["3d"]},
            "gemini_fallback": False}


def test_low_turnout_is_archive_only(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    result = polls_design.record_poll_result(store, _tally([1, 0, 0, 0]), _designed_poll())
    assert result["legacy"] is False
    profile = store.data["audience_profile"]
    assert profile["weeks"][-1]["low_confidence"] is True
    assert not profile["axes"], "a single vote must not move the ranking"
    assert profile["confidence"] == {}
    assert "not enough votes" in profile["last_result"]
    assert "metadata" not in profile["last_result"]


def test_enough_votes_updates_ema_and_confidence(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    result = polls_design.record_poll_result(store, _tally([9, 2, 1, 0]), _designed_poll())
    assert "not enough" not in result["result"]
    profile = store.data["audience_profile"]
    weights = profile["axes"]["job"]
    assert weights["k0"] > weights["k1"] > weights["k2"]
    assert profile["confidence"]["job"] > 0
    assert profile["weeks"][-1]["low_confidence"] is False


def test_legacy_poll_without_metadata_is_archive(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    result = polls_design.record_poll_result(store, _tally([1, 0]),
                                             {"message_id": 3572, "repos": {}})
    assert result["legacy"] is True
    assert not store.data["audience_profile"].get("axes")
    assert "old format" in store.data["audience_profile"]["last_result"]


def test_digest_text_uses_human_lines_and_no_service_labels():
    text = pipeline.digest_text(
        [{"name": "Concat", "stars": 14124, "score": 95,
          "generated": {"hook": "Edit videos locally."}}],
        poll_line="🗳️ Which job we cover next week — vote below.",
        result_line="Last week: not enough votes to change the mix.",
    )
    assert "Which job we cover next week" in text
    assert "Last week: not enough votes" in text
    assert "Project of the Week" not in text
    assert "views" not in text.lower()


def test_ranking_profile_nudges_but_stays_clamped(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    polls_design.record_poll_result(store, _tally([9, 2, 1, 0]), _designed_poll())
    model = polls_design.audience_ranking_model(store.data["audience_profile"])
    merged = polls_design.merge_models({}, model)
    assert all(0.85 <= v <= 1.15 for v in
               (merged.get("topic_multipliers") or {}).values())
    c = {"language": "Rust", "topics": ["rust"], "score": 90}
    assert 0.8 <= value_multiplier(c, merged) <= 1.25


def test_poll_preview_publishes_nothing(monkeypatch, tmp_path: Path):
    calls = []

    class FakeTelegram:
        def __init__(self, *a, **k):
            pass

        def send_text(self, *a, **k):
            calls.append("send")
            return {"message_id": 1}

        def api(self, method, payload=None):
            calls.append(method)
            return {"message_id": 1}

    monkeypatch.setattr(pipeline, "TelegramClient", FakeTelegram)
    store = StateStore(tmp_path / "state.json")
    store.data["queue"] = _queue()
    result = pipeline.poll_preview_command(Config(), store)
    assert result["published"] is False
    assert result["options"] and result["question"]
    assert calls == []


def test_live_style_skew_still_gives_four_different_subjects(tmp_path: Path):
    """Reproduce 08.11: 9 claude-code, 7 ai-agents, 6 codex in the queue."""
    store = StateStore(tmp_path / "state.json")
    items = [_candidate(i, cluster="claude-code") for i in range(9)]
    items += [_candidate(20 + i, cluster="ai-agents") for i in range(7)]
    items += [_candidate(40 + i, cluster="codex") for i in range(6)]
    items += [_candidate(60 + i, cluster=c) for i, c in
              enumerate(("macos", "rust", "subtitles", "gamedev", "windows"))]
    store.data["queue"] = items
    spec = polls_design.build_week_poll(store, client=GoodGemini())
    assert len(set(spec["families"])) == 4
    assert spec["families"].count("ai_tools") <= 1


def test_winner_copy_is_human_not_an_internal_key(tmp_path: Path):
    store = StateStore(tmp_path / "state.json")
    result = polls_design.record_poll_result(store, _tally([9, 2, 1, 0]), _designed_poll())
    assert "Last week: 12 votes picked" in result["result"]
    assert "job_" not in result["result"] and "k0" not in result["result"]


def _replace_state(tmp_path: Path) -> StateStore:
    store = StateStore(tmp_path / "state.json")
    names = ["Concat", "universal-modder", "Filee", "ffmpeg-skill", "m3e-canvas"]
    store.data["published"] = {
        f"p{i}": {"full_name": f"org/{n}", "name": n, "stars": 1000 + i,
                  "score": 90 - i, "published_at": (NOW - timedelta(days=1)).isoformat(),
                  "generated": {"hook": f"Do something useful with {n}."}}
        for i, n in enumerate(names)
    }
    store.data["queue"] = _queue()
    store.data["active_poll"] = {
        "chat_id": "@GitHub_Open_Source", "message_id": 3572,
        "question": "Which project should win Project of the Week?",
        "repos": {n: f"org/{n}" for n in names},
        "opened_at": "2026-11-08T16:13:56Z",
    }
    store.data["last_runs"] = {"digest": {
        "week": "2026-W45", "telegram_message_id": 3571,
        "poll_message_id": 3572, "card_metric": "stars",
        "image_url": "https://i.ibb.co/CARD/sunday-digest-stars.png",
    }}
    return store


class ReplaceTelegram:
    def __init__(self, *a, **k):
        self.calls: list[dict] = []

    def api(self, method, payload=None):
        self.calls.append({"op": method, "payload": dict(payload or {})})
        if method == "stopPoll":
            return {"question": "Which project should win Project of the Week?",
                    "total_voter_count": 5,
                    "options": [{"text": n, "voter_count": v} for n, v in
                                (("Concat", 1), ("universal-modder", 2),
                                 ("Filee", 0), ("ffmpeg-skill", 2), ("m3e-canvas", 0))]}
        if method == "sendPoll":
            return {"message_id": 3574}
        return {"ok": True}

    def edit_text(self, chat_id, message_id, text, *, preview_url=""):
        self.calls.append({"op": "edit_text", "message_id": message_id,
                           "text": text, "preview_url": preview_url})
        return {"message_id": message_id}

    def send_text(self, *a, **k):
        self.calls.append({"op": "send_text"})
        return {"message_id": 9999}


def test_poll_replace_archives_votes_and_points_at_the_new_poll(monkeypatch, tmp_path: Path):
    fake = ReplaceTelegram()
    monkeypatch.setattr(pipeline, "TelegramClient", lambda *a, **k: fake)
    for key, value in (("TELEGRAM_BOT_TOKEN", "test-tg"),
                       ("TELEGRAM_TEST_CHANNEL_ID", "-1001234567890")):
        monkeypatch.setenv(key, value)
    store = _replace_state(tmp_path)
    result = pipeline.poll_replace_command(Config.from_env(), store)

    assert result["ok"] is True
    assert result["old_message_id"] == 3572 and result["new_message_id"] == 3574
    assert result["archived_votes"] == 5 and result["digest_edited"] is True
    ops = [c["op"] for c in fake.calls]
    assert "stopPoll" in ops and "deleteMessage" in ops and "sendPoll" in ops
    assert "edit_text" in ops and "send_text" not in ops, "no duplicate digest post"

    edit = next(c for c in fake.calls if c["op"] == "edit_text")
    assert edit["message_id"] == 3571
    assert edit["preview_url"] == "https://i.ibb.co/CARD/sunday-digest-stars.png"
    assert "Project of the Week" not in edit["text"]
    assert "shapes next week's picks" in edit["text"]
    assert "⭐ 1 000" in edit["text"]

    poll = store.data["active_poll"]
    assert poll["message_id"] == 3574 and poll["replaced_from"] == 3572
    assert poll["option_keys"] and poll["type"] == "job"
    assert store.data["last_runs"]["digest"]["poll_message_id"] == 3574
    archived = store.data["poll_history"][-1]
    assert archived["total_voters"] == 5 and archived["replaced"] is True


def test_poll_replace_publishes_nothing_if_deletion_is_denied(monkeypatch, tmp_path: Path):
    class Denied(ReplaceTelegram):
        def api(self, method, payload=None):
            if method == "deleteMessage":
                raise RuntimeError("not enough rights")
            return super().api(method, payload)

    fake = Denied()
    monkeypatch.setattr(pipeline, "TelegramClient", lambda *a, **k: fake)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-tg")
    monkeypatch.setenv("TELEGRAM_TEST_CHANNEL_ID", "-1001234567890")
    store = _replace_state(tmp_path)
    result = pipeline.poll_replace_command(Config.from_env(), store)
    assert result["ok"] is False and result["reason"] == "delete_failed"
    assert not [c for c in fake.calls if c["op"] in ("sendPoll", "edit_text", "send_text")]
    assert store.data["active_poll"]["message_id"] == 3572
