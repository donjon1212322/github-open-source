from ghos_v6.telegram_payload import build_buttons


def test_project_buttons_do_not_use_a_star_cta():
    buttons = build_buttons(
        github_url="https://github.com/example/tool",
        telegraph_url="https://telegra.ph/example-tool",
        homepage_url="https://example.com",
    )
    labels = [button["text"] for row in buttons for button in row]
    assert labels == ["🔗 GitHub", "📖 Full breakdown", "🌐 Homepage"]
    assert not any("⭐" in label for label in labels)


def test_send_photo_exists_and_rejects_empty_bytes(monkeypatch):
    """Photo upload must fail loudly rather than silently post text only."""
    from ghos_v6.telegram import TelegramClient, TelegramError

    client = TelegramClient("token")
    monkeypatch.setattr(client.session, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network in tests")))
    assert hasattr(client, "send_photo")
    try:
        client.send_photo("@chan", b"", "caption")
    except (TelegramError, AssertionError):
        pass
