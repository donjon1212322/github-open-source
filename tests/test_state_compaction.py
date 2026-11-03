"""Long-term retention: the committed state must stay bounded."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from ghos_v6.dedup import check as dedup_check
from ghos_v6.state import StateStore


def _ts(days_ago: int) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return stamp.isoformat().replace("+00:00", "Z")


def _old_record() -> dict:
    return {
        "repo_id": 12345,
        "full_name": "owner/old",
        "description": "A small terminal tool for reading structured logs",
        "description_fp": "abcdef1234567890",
        "readme_fp": "1234567890abcdef",
        "published_at": _ts(200),
        "stars": 100,
        "score": 7.5,
        "telegram_message_id": 42,
        "telegram_chat_id": "test",
        "telegraph_url": "https://telegra.ph/old-11-03",
        "image_url": "https://i.ibb.co/abcdef/card.png",
        "generated": {"article_markdown": "x" * 6000},
        "articles": {"devto": {"id": 1}},
        "media_urls": ["https://example.com/og.png"] * 5,
        "history": [{"published_at": _ts(400 + i)} for i in range(9)],
    }


def test_old_published_record_is_compacted_but_still_blocks_duplicates(tmp_path):
    store = StateStore(tmp_path)
    store.data["published"] = {"owner/old": _old_record()}
    store.data["seen_repos"] = {
        "owner/stale": {"last_seen_at": _ts(400), "last_decision": "rejected:old"},
        "owner/fresh": {"last_seen_at": _ts(1), "last_decision": "queued"},
    }
    store.data["dead_letters"] = [{"i": i} for i in range(600)]
    store.data["news_topics"] = [{"i": i} for i in range(600)]
    store.save()

    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    compact = saved["published"]["owner/old"]

    assert compact["_compact"] is True
    assert "generated" not in compact
    assert "articles" not in compact
    assert "media_urls" not in compact
    assert len(json.dumps(compact)) < len(json.dumps(_old_record())) // 5
    assert len(compact["history"]) == 5
    # The commit-state workflow refuses a state that lost published records.
    assert len(saved["published"]) == 1
    assert "owner/stale" not in saved["seen_repos"]
    assert "owner/fresh" in saved["seen_repos"]
    assert len(saved["dead_letters"]) == 500
    assert len(saved["news_topics"]) == 500

    verdict = dedup_check(
        {
            "full_name": "owner/old",
            "repo_id": 12345,
            "description": "A small terminal tool for reading structured logs",
        },
        saved,
    )
    assert verdict["verdict"] == "duplicate"


def test_fresh_records_keep_their_full_payload(tmp_path):
    store = StateStore(tmp_path)
    record = _old_record()
    record["published_at"] = _ts(1)
    store.data["published"] = {"owner/fresh": record}
    store.save()

    saved = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    stored = saved["published"]["owner/fresh"]
    assert "_compact" not in stored
    assert stored["generated"]["article_markdown"] == "x" * 6000


def test_compaction_is_idempotent(tmp_path):
    store = StateStore(tmp_path)
    store.data["published"] = {"owner/old": _old_record()}
    store.save()
    first = (tmp_path / "state.json").read_text(encoding="utf-8")
    store.save()
    assert (tmp_path / "state.json").read_text(encoding="utf-8") == first
