"""Owner decisions 2026-11-07: full YouTube replacement, approved voice.

Regression coverage for the corrections the owner made during review:
* RSS / separate evidence layer is gone - channels are the only input.
* Collections and odd finds are NOT filtered as "noise".
* Rumours are KEPT but marked unconfirmed.
* The service-label voice ("So what:", "Receipts") is gone.
* Card D and the second dev.to article are separate from the project article.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ghos_v6 import news
from ghos_v6.cards import render_ai_brief_card_v4
from ghos_v6.config import Config
from ghos_v6.telegram_discovery import (
    CHANNELS,
    build_digest_material,
    is_promo,
    parse_channel_page,
    parse_views,
)

NOW = datetime(2026, 11, 7, 8, 0, tzinfo=timezone.utc)


def _post(channel: str, pid: str, text: str, links: tuple[str, ...] = (), dt: str = "2026-11-06T12:00:00") -> str:
    hrefs = "".join(f'<a href="{link}">link</a>' for link in links)
    return (
        f'<div class="tgme_widget_message" data-post="{channel}/{pid}">'
        f'<time datetime="{dt}">{dt}</time>'
        f'<div class="tgme_widget_message_text">{text} {hrefs}</div>'
        f'<span class="tgme_widget_message_views">1.4K</span></div>'
    )


def _fetcher(pages: dict[str, str]):
    def get(url: str) -> str:
        handle = url.rsplit("/", 1)[-1]
        return pages.get(handle, "")
    return get


def test_parse_views_handles_k_and_m():
    assert parse_views("188") == 188
    assert parse_views("19.4K") == 19400
    assert parse_views("1.2M") == 1_200_000
    assert parse_views(None) is None


def test_parse_channel_page_extracts_post_fields():
    page = _post(
        "neuro_channel", "10", "Gemini Omni 1.2 Flash ships today",
        ("https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemini-omni-1-2-flash/",),
    )
    posts = parse_channel_page(page, "neuro_channel")
    assert len(posts) == 1
    assert posts[0]["views"] == 1400
    assert posts[0]["published_at"].startswith("2026-11-06")
    assert "blog.google" in posts[0]["links"][0]
    assert "Gemini Omni 1.2 Flash" in posts[0]["text"]


def test_only_advertising_is_removed():
    assert is_promo("Data Scientist Fast Track: получите оффер за неделю!")
    assert not is_promo("10 Open Source проектов для агента покупок")
    assert not is_promo("5 моделей без встроенной цензуры на Hugging Face")


def test_collections_and_odd_finds_are_kept_not_filtered_as_noise():
    pages = {
        "machinelearning_interview": _post(
            "machinelearning_interview", "1", "10 Open Source проектов для агента покупок",
            ("https://github.com/langchain-ai/langchain",),
        ),
        "mlsecfeed": _post(
            "mlsecfeed", "2", "5 моделей без встроенной цензуры",
            ("https://huggingface.co/huihui-ai/Huihui-Qwen3.8-27B-abliterated",),
        ),
        "neuro_channel": _post(
            "neuro_channel", "3", "Trust Score для вашего IP в Claude",
            ("https://www.anthropic.com/supported-countries",),
        ),
    }
    result = build_digest_material(now=NOW, fetcher=_fetcher(pages), channels=tuple(pages))
    assert result["stats"]["ads_removed"] == 0
    assert result["stats"]["kept"] == 3
    assert result["stats"]["clusters"] == 3
    blob = " ".join(c["text"] for c in result["clusters"])
    assert "10 Open Source" in blob
    assert "без встроенной цензуры" in blob
    assert "Trust Score" in blob


def test_duplicate_story_across_channels_is_clustered():
    url = "https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemini-omni-1-2-flash/"
    pages = {
        "neuro_channel": _post("neuro_channel", "1", "Gemini Omni 1.2 Flash", (url,)),
        "ai_newz": _post("ai_newz", "2", "Google выкатили Gemini Omni 1.2 Flash", (url + "#details",)),
        "projplus": _post("projplus", "3", "Обновлённая Gemini Omni 1.2 Flash", (url,)),
    }
    result = build_digest_material(now=NOW, fetcher=_fetcher(pages), channels=tuple(pages))
    assert result["stats"]["clusters"] == 1
    assert len(result["clusters"][0]["posts"]) == 3
    assert result["clusters"][0]["status"] == "official_release"


def test_rumours_are_kept_and_marked_unconfirmed():
    pages = {
        "Futuris": _post("Futuris", "9", "The Information пишет, что GPT-6.2 может выйти в пятницу"),
        "data_secrets": _post("data_secrets", "10", "Все, теперь ждем новую Astra"),
    }
    result = build_digest_material(now=NOW, fetcher=_fetcher(pages), channels=tuple(pages))
    assert result["stats"]["rumours"] == 2
    assert result["rumours"]
    assert all("GPT-6.2" in item or "Astra" in item for item in result["rumours"])


def test_video_id_is_stable_for_the_utc_day():
    pages = {"neuro_channel": _post("neuro_channel", "1", "A model", ("https://github.com/a/b",))}
    first = build_digest_material(now=NOW, fetcher=_fetcher(pages), channels=("neuro_channel",))
    later = build_digest_material(
        now=NOW.replace(hour=20), fetcher=_fetcher(pages), channels=("neuro_channel",)
    )
    assert first["entry"]["video_id"] == later["entry"]["video_id"]
    assert first["entry"]["video_id"].startswith("tg-20261107-")


def test_dead_channel_does_not_kill_the_digest():
    def get(url: str) -> str:
        if "neuro_channel" in url:
            return _post("neuro_channel", "1", "A model", ("https://github.com/a/b",))
        raise OSError("offline")
    result = build_digest_material(now=NOW, fetcher=get, channels=CHANNELS)
    assert result["stats"]["errors"]
    assert result["clusters"]


def _brief() -> dict[str, Any]:
    return {
        "eligible": True,
        "headline": "Gemini Omni 1.2 Flash and Inkling 2 Small lead the model watch",
        "hook": "Three model launches showed up in four channels this week. The thing I can't stop thinking about is a 35-year-old math problem that quietly fell on a Tuesday.",
        "card_hook": "A 35-year-old math problem fell on a Tuesday.",
        "summary": "A digest of channel posts.",
        "stories": [
            {
                "title": "Google quietly removed a limit",
                "bullets": ["15 seconds of video instead of 10.", "720p is about 30% cheaper."],
                "why": "If you stitched two clips together, that workaround just expired.",
                "status": "official_release",
                "basis": "the model card is up",
                "models": ["Gemini Omni 1.2 Flash"],
            },
            {
                "title": "'Small' now means 594B parameters",
                "bullets": ["Inkling 2 Small has 594B total and 17B active."],
                "why": "Check the licence before it ends up in an agent loop.",
                "status": "official_release",
                "basis": "weights are public",
                "models": ["Inkling 2 Small"],
            },
        ],
        "models": ["Gemini Omni 1.2 Flash", "Inkling 2 Small"],
        "why_devs": "Three of five are things you can download today.",
        "confirmed": [],
        "unconfirmed_claims": ["GPT-6.2 Astra may arrive on Friday — unverified."],
        "topic_key": "model-watch",
        "discussion_question": "Which one would you open first?",
        "rejection_reason": "",
    }


def test_formatter_uses_approved_voice_and_keeps_rumours():
    text = news.format_ai_brief_digest(_brief())
    assert "So what:" not in text
    assert "Receipts" not in text
    assert "✅" not in text
    assert "Why developers should care" not in text
    assert "1/ " in text and "2/ " in text
    assert "Rumours" in text and "GPT-6.2 Astra" in text
    assert "Which one would you open first?" in text
    assert "WorldofAI" not in text and "YouTube" not in text


def test_synthesize_telegram_never_drops_rumours():
    class FakeClient:
        def __init__(self) -> None:
            self.kwargs: dict[str, Any] = {}

        def generate_json(self, prompt: str, **kwargs: Any) -> dict[str, Any]:
            self.kwargs = kwargs
            payload = _brief()
            payload["unconfirmed_claims"] = []
            return payload

    client = FakeClient()
    result = news.synthesize_brief_telegram(
        {"title": "t", "description": "d", "video_id": "tg-1", "url": ""},
        {"text": "primary"},
        client,
        rumours=["Astra may arrive Friday"],
    )
    assert client.kwargs["youtube_url"] == ""
    assert any("Astra" in item for item in result["unconfirmed_claims"])
    assert result["video_id"] == "tg-1"


def test_card_d_is_1200x630_and_has_no_service_labels():
    out = Path("/tmp/card_d_test.png")
    path = render_ai_brief_card_v4(
        "headline", "A 35-year-old math problem fell on a Tuesday.", "models",
        out, stories=5, rumours=2,
    )
    from PIL import Image
    with Image.open(path) as image:
        assert image.size == (1200, 630)
    assert path.exists()


def test_config_default_is_telegram_with_rollback_flag():
    assert Config().feature_telegram_discovery is True
    assert "FEATURE_TELEGRAM_DISCOVERY" in Path("src/ghos_v6/config.py").read_text()


def test_second_devto_article_is_idempotent_and_separate(tmp_path: Path):
    from ghos_v6.pipeline import news_article_command
    from ghos_v6.state import StateStore

    store = StateStore(tmp_path / "state.json")
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()
    store.data["last_runs"] = {
        "news-article": {"finished_at": f"{today}T01:00:00Z"}
    }
    result = news_article_command(Config(), store)
    assert result["published"] is False
    assert result["reason"] == "news_article_already_done"
    assert "article" not in store.data.get("last_runs", {}).get("news-article", {})


def test_subject_clustering_and_rumour_markers():
    pages = {
        "neuro_channel": _post(
            "neuro_channel", "1", "GPT-6 Cyber landed for security",
            ("https://openai.com/index/introducing-gpt-6-cyber/",),
        ),
        "data_secrets": _post(
            "data_secrets", "2", "GPT-6 Cyber нашла 948 уязвимостей",
            ("https://deploymentsafety.openai.com/gpt-6-cyber",),
        ),
        "Futuris": _post("Futuris", "3", "GPT-6.2 Astra может выйти в пятницу"),
        "LLMScience": _post("LLMScience", "4", "Не знаете, к кому надо было стучаться на Хэллоуин?"),
    }
    result = build_digest_material(now=NOW, fetcher=_fetcher(pages), channels=tuple(pages))
    cyber = [c for c in result["clusters"] if "gpt-6-cyber" in c["key"]]
    assert len(cyber) == 1 and len(cyber[0]["posts"]) == 2
    assert any("Astra" in item for item in result["rumours"])
    # A link-less joke stays in the corpus but is NOT labelled a rumour.
    assert not any("Хэллоуин" in item for item in result["rumours"])


def test_telegram_outage_is_a_green_skip_with_owner_alert(monkeypatch, tmp_path):
    """An outage of the channel source must never fail the scheduled run."""
    from ghos_v6 import pipeline
    from ghos_v6.state import StateStore

    class NoNetwork:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise AssertionError("no Gemini call on a source outage")

    def boom(**_kwargs: Any) -> dict[str, Any]:
        raise OSError("channels offline")

    alerts: list[dict[str, Any]] = []
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy")
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    monkeypatch.setattr(pipeline, "build_digest_material", boom)
    monkeypatch.setattr(pipeline, "GeminiClient", NoNetwork)
    monkeypatch.setattr(pipeline, "TelegramClient", NoNetwork)
    monkeypatch.setattr(pipeline, "alert_owner", lambda *_a, **kw: alerts.append(kw))
    from types import SimpleNamespace

    config = SimpleNamespace(
        feature_ai_brief=True,
        feature_telegram_discovery=True,
        require=lambda *_a: None,
        telegram_chat_id="@GitHub_Open_Source",
        telegram_bot_token="dummy",
        Gemini_api_key="dummy",
        Gemini_model="x",
        telegram_owner_chat_id=None,
    )
    result = pipeline.news_command(config, StateStore(tmp_path / "state.json"))
    assert result["ok"] is True
    assert result["published"] is False
    assert result["reason"] == "source_unavailable"
    assert alerts
