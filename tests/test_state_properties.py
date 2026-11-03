"""Deterministic property-style checks over many generated states.

No new dependency: a fixed seed produces reproducible states, and the same
invariants are asserted for every one of them. The point is not to execute
more lines but to check that *no* generated shape can break the rules.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

from ghos_v6 import dedup, formatter
from ghos_v6.state import StateStore


def _ts(days_ago: int) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return stamp.isoformat().replace("+00:00", "Z")


def test_compaction_invariants_hold_across_generated_states(tmp_path):
    rng = random.Random(20261103)
    for case in range(40):
        state_dir = tmp_path / f"case-{case}"
        store = StateStore(state_dir)
        n_old = rng.randint(0, 10)
        n_fresh = rng.randint(0, 8)
        published: dict[str, dict] = {}
        for i in range(n_old + n_fresh):
            old = i < n_old
            record = {
                "repo_id": 1000 + i,
                "full_name": f"owner/repo{i}",
                "description": "Terminal dashboard for structured logs",
                "description_fp": f"fp{i:04d}",
                "readme_fp": f"rd{i:04d}",
                "published_at": _ts(400 if old else 1),
                "stars": rng.randint(1, 5000),
                "score": round(rng.random() * 10, 3),
                "telegram_message_id": 100 + i,
                "telegraph_url": f"https://telegra.ph/owner-repo-{i}",
                "generated": {"article_markdown": "x" * rng.randint(100, 3000)},
                "articles": {"devto": {"id": i}},
                "media_urls": ["https://example.com/og.png"] * rng.randint(0, 8),
                "history": [
                    {"published_at": _ts(500 + j)} for j in range(rng.randint(0, 12))
                ],
            }
            published[record["full_name"]] = record
        store.data["published"] = published
        store.data["seen_repos"] = {
            f"owner/seen{j}": {
                "last_seen_at": _ts(rng.choice((1, 100, 179, 181, 400))),
                "last_decision": "queued",
            }
            for j in range(rng.randint(0, 40))
        }
        for name in ("dead_letters", "news_topics", "poll_history"):
            store.data[name] = [{"i": i} for i in range(rng.randint(0, 700))]
        store.save()

        saved = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
        assert set(saved["published"]) == set(published), "no published key may be lost"
        for key, record in saved["published"].items():
            assert len(record.get("history", [])) <= store.HISTORY_MAX_ITEMS
            if record.get("_compact"):
                assert "generated" not in record
                assert "articles" not in record
                assert "media_urls" not in record
                for field in StateStore._PUBLISHED_INDEX_FIELDS:
                    if field in published[key]:
                        assert record[field] == published[key][field]
            else:
                assert "generated" in record, "fresh posts keep their full payload"
        for name in ("dead_letters", "news_topics", "poll_history"):
            assert len(saved.get(name, [])) <= store.LIST_MAX_ITEMS
        for seen in saved.get("seen_repos", {}).values():
            days = (datetime.now(timezone.utc) - datetime.fromisoformat(
                seen["last_seen_at"].replace("Z", "+00:00")
            )).days
            assert days <= store.SEEN_REPOS_TTL_DAYS + 1

        first = (state_dir / "state.json").read_text(encoding="utf-8")
        store.save()
        assert (state_dir / "state.json").read_text(encoding="utf-8") == first, "idempotent"


def test_similarity_is_symmetric_bounded_and_one_for_identical_texts():
    rng = random.Random(7)
    words = [
        "terminal", "dashboard", "parser", "vector", "renderer", "cache",
        "browser", "plugin", "compiler", "notebook", "proxy", "editor",
    ]
    for _ in range(200):
        first = " ".join(rng.choices(words, k=rng.randint(1, 12)))
        second = " ".join(rng.choices(words, k=rng.randint(1, 12)))
        score = dedup.similarity(first, second)
        assert 0.0 <= score <= 1.0
        assert abs(score - dedup.similarity(second, first)) < 1e-12
    long_text = " ".join(rng.choices(words, k=20))
    assert dedup.similarity(long_text, long_text) == 1.0


def test_description_fingerprint_survives_renames_and_case():
    first = "A fast static site generator written in Rust for documentation"
    second = "A FAST static site generator written in Rust for documentation!"
    assert dedup.description_fingerprint(first) == dedup.description_fingerprint(second)
    assert dedup.description_fingerprint("too short") == ""


def test_formatter_never_exceeds_the_visible_limit_for_generated_posts():
    rng = random.Random(99)
    words = ["terminal", "fast", "open source", "plugin", "cache", "dashboard", "Rust"]
    for _ in range(50):
        project = {
            "name": "demo",
            "full_name": "owner/demo",
            "description": " ".join(rng.choices(words, k=rng.randint(3, 40))),
            "url": "https://github.com/owner/demo",
            "homepage": "https://example.com",
            "stars": rng.randint(0, 50000),
            "topics": rng.sample(["cli", "ai", "web", "game"], k=2),
            "key_features": [" ".join(rng.choices(words, k=8)) for _ in range(6)],
            "hook": " ".join(rng.choices(words, k=30)),
            "summary": " ".join(rng.choices(words, k=60)),
        }
        post = formatter.format_project_post(project)
        assert formatter.utf16_len(formatter.visible_text(post)) <= 4000
        assert "<script" not in post.lower()
