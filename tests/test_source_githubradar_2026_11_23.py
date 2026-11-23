"""Owner 23.11: t.me/GitHubRadar joins the verified Telegram sources.

The owner asked for the channel to be added. The project's own rule for a
Telegram source is a *measurement*, not a guess: a channel is kept only if it
really posts every day and its posts really carry GitHub links (otherwise it
costs a fetch per run and adds nothing). Measured 2026-11-23 through the
public ``t.me/s`` preview:

* 20 posts over 13 days -> 1.5 posts/day, every day covered;
* 19 of 20 posts carry at least one github.com link (95 %);
* 19 distinct repositories, only 2 of them already covered by the five
  existing channels (11 % overlap) - so it adds candidates.

Also pinned here: the dead handle ``gitradar_`` (0 posts in the preview) must
stay out. It is a different channel from ``GitHubRadar`` and the two names
must not be confused by a future edit.
"""
from __future__ import annotations

from ghos_v6.sources import (
    DEFAULT_TELEGRAM_SOURCES,
    REJECTED_SOURCES,
    VERIFIED_DAILY_SOURCES,
)


def _github_radar():
    matches = [s for s in VERIFIED_DAILY_SOURCES if s.handle == "GitHubRadar"]
    assert len(matches) == 1, "GitHubRadar must be listed exactly once"
    return matches[0]


def test_github_radar_is_a_default_source():
    assert "GitHubRadar" in DEFAULT_TELEGRAM_SOURCES


def test_github_radar_carries_its_measurement():
    source = _github_radar()
    assert source.posts_per_day >= 1.0
    assert source.github_posts == source.github_links or source.github_links >= 15
    assert source.github_posts >= 15
    assert source.checked_on == "2026-11-23"
    assert "overlap" in source.note


def test_github_radar_does_not_displace_the_existing_five():
    for handle in (
        "open_source_friend",
        "github_repos",
        "gitbless",
        "bestofgithub",
        "github",
    ):
        assert handle in DEFAULT_TELEGRAM_SOURCES


def test_dead_gitradar_stays_rejected():
    assert "gitradar_" in REJECTED_SOURCES
    assert "gitradar_" not in DEFAULT_TELEGRAM_SOURCES
    assert "GitHubRadar" not in REJECTED_SOURCES


def test_telegram_web_collection_reads_repo_links_from_the_new_channel(monkeypatch):
    """The collector only needs github.com links, whatever language the post is in."""
    from ghos_v6 import sources as sources_module

    class _Response:
        status_code = 200
        text = (
            '<div class="tgme_widget_message_text">'
            "Сервер для передачи аудио и видео в реальном времени: "
            '<a href="https://github.com/mindsdb/mindshub">mindsdb/mindshub</a>'
            "</div>"
        )

    def fake_get(url, **_kwargs):
        assert url == "https://t.me/s/GitHubRadar"
        return _Response()

    monkeypatch.setattr(sources_module.requests, "get", fake_get)
    merged: dict[str, dict] = {}
    sources_module.collect_telegram_web(merged, ("GitHubRadar",))
    assert "mindsdb/mindshub" in merged
    assert merged["mindsdb/mindshub"]["sources"] == ["telegram:GitHubRadar"]
