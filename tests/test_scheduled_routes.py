"""Every cron in the workflow must have a route, and Thursday must learn."""
from __future__ import annotations

import re
from pathlib import Path

import ghos_v6.cli as cli
from ghos_v6.config import Config
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]


def _crons() -> set[str]:
    text = (REPO / ".github" / "workflows" / "pipeline.yml").read_text(encoding="utf-8")
    return set(re.findall(r'cron:\s*["\']([^"\']+)["\']', text))


def _route_keys() -> set[str]:
    src = (REPO / "src" / "ghos_v6" / "cli.py").read_text(encoding="utf-8")
    routes_block = src.split("routes: dict[str, Any] = {")[1].split("pairs = {")[0]
    routes = set(re.findall(r'"([^"]+)"\s*:\s*lambda', routes_block))
    pairs_block = src.split("pairs = {")[1].split("if normalized")[0]
    pairs = set(re.findall(r'"([^"]+)"\s*:', pairs_block))
    return routes | pairs


def test_every_workflow_cron_is_routed_and_no_dead_routes():
    crons, keys = _crons(), _route_keys()
    assert crons, "no crons found"
    assert crons <= keys, f"crons without a route: {sorted(crons - keys)}"
    assert not (keys - crons), f"dead schedule routes: {sorted(keys - crons)}"


def test_thursday_cron_closes_poll_and_updates_value_model(monkeypatch, tmp_path):
    calls: list[str] = []

    def close(config, store):
        calls.append("close")
        return {"ok": True, "closed": True}

    def learn(config, store):
        calls.append("learn")
        return {"ok": True, "updated": 1}

    monkeypatch.setattr(cli, "close_poll_command", close)
    monkeypatch.setattr(cli, "learn_command", learn)

    result = cli._run_scheduled(Config(), StateStore(tmp_path), "53 18 * * 4")
    assert calls == ["close", "learn"]
    assert result["ok"] is True
    assert len(result["steps"]) == 2


def test_production_target_resolves_with_canonical_variable(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLISH_TARGET", "main")
    monkeypatch.setenv("TELEGRAM_MAIN_CHANNEL_ID", "@GitHub_Open_Source")
    config = Config.from_env()
    assert config.is_main_target is True
    assert config.telegram_chat_id == "@GitHub_Open_Source"


def test_production_target_fails_closed_without_a_channel(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PUBLISH_TARGET", "main")
    monkeypatch.delenv("TELEGRAM_MAIN_CHANNEL_ID", raising=False)
    monkeypatch.delenv("MAIN_TELEGRAM_CHANNEL", raising=False)
    config = Config.from_env()
    try:
        config.telegram_chat_id
    except RuntimeError as exc:
        assert "TELEGRAM_MAIN_CHANNEL_ID" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("production target must not fall back to the test channel")
