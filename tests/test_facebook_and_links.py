"""Two owner requirements, enforced as contracts:

1. Every outbound link/CTA points at the real production channel
   ``@GitHub_Open_Source`` — never at the private test channel.
2. Facebook is published only by make.com reading a Google Sheet row;
   v6 never calls the Graph API and needs no Page id/token.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from ghos_v6 import content_adapters as ca
from ghos_v6 import formatter, sheets, telegraph
from ghos_v6.config import Config, telegram_message_link
from ghos_v6.pipeline import digest_command, facebook_handoff_command, process_crossposts
from ghos_v6.state import StateStore

REPO = Path(__file__).resolve().parents[1]
REAL = "https://t.me/GitHub_Open_Source"
PRIVATE_ID = -1001234567890


def _record(*, private_link: bool = True) -> dict:
    return {
        "full_name": "owner/demo",
        "name": "demo",
        "description": "A small terminal tool for reading structured logs",
        "url": "https://github.com/owner/demo",
        "stars": 120,
        "topics": ["cli"],
        "telegram_link": telegram_message_link(PRIVATE_ID, 42) if private_link else "",
        "telegraph_url": "https://telegra.ph/owner-demo-11-03",
        "image_url": "https://i.ibb.co/abcdef/card.png",
        "generated": {},
    }


def test_private_test_channel_link_is_never_treated_as_public():
    record = _record()
    assert "/c/" in record["telegram_link"]
    assert ca.public_telegram_link(record) == ""
    assert ca.telegram_cta_url(record) == REAL
    assert REAL in ca.telegram_cta_line(record)
    assert REAL in ca.telegram_cta_line(record, compact=True)


def test_public_main_channel_link_is_kept_when_it_exists():
    record = _record(private_link=False)
    record["telegram_link"] = telegram_message_link("@GitHub_Open_Source", 42)
    assert ca.public_telegram_link(record) == f"{REAL}/42"
    assert ca.telegram_cta_url(record) == f"{REAL}/42"


@pytest.mark.parametrize("platform", ["bluesky", "facebook"])
def test_every_crosspost_copy_points_back_to_the_real_channel(platform):
    text = ca.build_crosspost_text(platform, _record())
    assert REAL in text
    assert "/c/" not in text
    assert "joinchat" not in text
    assert str(PRIVATE_ID) not in text


def test_devto_article_points_back_to_the_real_channel():
    body = ca.build_devto_body(_record(), {})
    assert REAL in body
    assert "/c/" not in body


def test_telegram_post_signature_points_at_the_real_channel():
    post = formatter.format_project_post(_record())
    assert REAL in post
    assert "/c/" not in post


def test_telegraph_author_url_is_the_real_channel():
    assert telegraph.AUTHOR_URL == REAL


def test_digest_uses_only_public_links():
    # 08.11: the post text lives in digest_text(), shared with the in-place
    # card fix, so the link policy is asserted on that builder.
    from ghos_v6.pipeline import digest_text
    src = inspect.getsource(digest_command) + inspect.getsource(digest_text)
    assert "public_telegram_link(record)" in src
    assert 'record.get("telegram_link")' not in src


def test_publishing_code_has_no_direct_facebook_client():
    assert "FacebookClient" not in inspect.getsource(process_crossposts)
    assert "FacebookClient" not in inspect.getsource(facebook_handoff_command)
    assert not hasattr(Config(), "feature_facebook")
    assert not hasattr(Config(), "Facebook_page_id")
    assert not hasattr(Config(), "Facebook_page_access_token")


def test_sheet_handoff_is_the_only_facebook_path_and_uses_the_documented_range():
    assert sheets.DEFAULT_RANGE == "facebook!A1"
    assert Config().sheets_range == sheets.DEFAULT_RANGE
    src = inspect.getsource(facebook_handoff_command)
    assert "append_rows" in src and "row_for" in src
    assert '"via": "make.com"' in src


def test_no_page_token_or_direct_flag_is_declared_anywhere():
    env_example = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "FACEBOOK_PAGE_ACCESS_TOKEN=" not in env_example
    assert "FACEBOOK_PAGE_ID=" not in env_example
    assert "FEATURE_FACEBOOK=" not in env_example
    for workflow in sorted((REPO / ".github" / "workflows").glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        assert "FACEBOOK_PAGE_ACCESS_TOKEN" not in text, workflow.name
        assert "FEATURE_FACEBOOK" not in text, workflow.name


def test_handoff_is_disabled_without_the_service_account(tmp_path):
    """No key yet: the command must be a no-op, not a broken publish."""
    result = facebook_handoff_command(Config(), StateStore(tmp_path))
    assert result["ok"] is True
    assert result["queued"] == 0
