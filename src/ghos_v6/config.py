"""Environment configuration and publication-target safety.

The target lock is deliberately paranoid: unless the process is in a real
production environment *and* explicitly pointed at ``main``, every
publisher resolves to the private test channel. There is no fallback from
an empty test ID to the main channel.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_or_none(name: str) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _csv(name: str) -> tuple[str, ...]:
    raw = os.getenv(name) or ""
    return tuple(part.strip().lstrip("@") for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Config:
    app_env: str = "test"
    publish_target: str = "test"
    state_dir: Path = Path("state")

    telegram_bot_token: str = ""
    telegram_test_channel_id: int | None = None
    telegram_main_channel_id: str = ""
    telegram_owner_chat_id: int | None = None
    telegram_source_channels: tuple[str, ...] = ()

    GitHub_token: str = ""
    GitHub_repository: str = ""
    imgbb_api_key: str = ""
    Gemini_api_key: str = ""
    Gemini_model: str = "gemini-3.5-flash-lite"
    telegraph_access_token: str = ""

    dev_api_key: str = ""
    bluesky_handle: str = ""
    bluesky_app_password: str = ""
    # Facebook is published by make.com from a sheet row: v6 never calls the
    # Graph API and therefore needs no Page id or Page token.
    sheets_spreadsheet_id: str = ""
    sheets_credentials_json: str = ""
    sheets_range: str = "facebook!A1"

    feature_telegraph: bool = True
    feature_devto: bool = False
    feature_bluesky: bool = False
    feature_ai_brief: bool = False
    feature_telegram_discovery: bool = True
    feature_sheets_handoff: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            app_env=(os.getenv("APP_ENV") or "test").strip().lower(),
            publish_target=(os.getenv("PUBLISH_TARGET") or "test").strip().lower(),
            state_dir=Path(os.getenv("GHOS_STATE_DIR") or "state"),
            telegram_bot_token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip(),
            telegram_test_channel_id=_int_or_none("TELEGRAM_TEST_CHANNEL_ID"),
            telegram_main_channel_id=(
                os.getenv("TELEGRAM_MAIN_CHANNEL_ID")
                or os.getenv("MAIN_TELEGRAM_CHANNEL")
                or ""
            ).strip(),
            telegram_owner_chat_id=_int_or_none("TELEGRAM_OWNER_CHAT_ID"),
            telegram_source_channels=_csv("TELEGRAM_SOURCE_CHANNELS"),
            GitHub_token=(os.getenv("GH_API_TOKEN") or os.getenv("GITHUB_TOKEN") or "").strip(),
            GitHub_repository=(
                os.getenv("GHOS_GITHUB_REPOSITORY") or os.getenv("GITHUB_REPOSITORY") or ""
            ).strip(),
            imgbb_api_key=(os.getenv("IMGBB_API_KEY") or "").strip(),
            Gemini_api_key=(os.getenv("GEMINI_API_KEY") or "").strip(),
            Gemini_model=(os.getenv("GEMINI_MODEL") or "gemini-3.5-flash-lite").strip().lower(),
            telegraph_access_token=(os.getenv("TELEGRAPH_ACCESS_TOKEN") or "").strip(),
            dev_api_key=(os.getenv("DEV_API_KEY") or "").strip(),
            bluesky_handle=(os.getenv("BLUESKY_HANDLE") or "").strip().lstrip("@"),
            bluesky_app_password=(os.getenv("BLUESKY_APP_PASSWORD") or "").strip(),
            sheets_spreadsheet_id=(os.getenv("FACEBOOK_SHEETS_ID") or "").strip(),
            sheets_credentials_json=(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON") or "").strip(),
            sheets_range=(os.getenv("FACEBOOK_SHEETS_RANGE") or "facebook!A1").strip(),
            feature_telegraph=_bool("FEATURE_TELEGRAPH", True),
            feature_devto=_bool("FEATURE_DEVTO", False),
            feature_bluesky=_bool("FEATURE_BLUESKY", False),
            feature_ai_brief=_bool("FEATURE_AI_BRIEF", False),
            feature_telegram_discovery=_bool("FEATURE_TELEGRAM_DISCOVERY", True),
            feature_sheets_handoff=_bool("FEATURE_SHEETS_HANDOFF", False),
        )

    @property
    def is_main_target(self) -> bool:
        return self.app_env == "production" and self.publish_target == "main"

    @property
    def telegram_chat_id(self) -> int | str:
        """Resolved publication chat. Raises rather than guessing."""
        if self.is_main_target:
            if not self.telegram_main_channel_id:
                raise RuntimeError("TELEGRAM_MAIN_CHANNEL_ID is required for production/main")
            return self.telegram_main_channel_id
        if self.telegram_test_channel_id is None:
            raise RuntimeError(
                "TELEGRAM_TEST_CHANNEL_ID is not set; press Start on the bot and post "
                "'setup' in the private test channel so doctor can discover it"
            )
        return self.telegram_test_channel_id

    def require(self, *names: str) -> None:
        missing = []
        values = {
            "TELEGRAM_BOT_TOKEN": self.telegram_bot_token,
            "GEMINI_API_KEY": self.Gemini_api_key,
            "GH_API_TOKEN": self.GitHub_token,
            "GITHUB_REPOSITORY": self.GitHub_repository,
            "IMGBB_API_KEY": self.imgbb_api_key,
            "TELEGRAPH_ACCESS_TOKEN": self.telegraph_access_token,
            "DEV_API_KEY": self.dev_api_key,
            "BLUESKY_APP_PASSWORD": self.bluesky_app_password,
        }
        for name in names:
            if not values.get(name):
                missing.append(name)
        if missing:
            raise RuntimeError("Missing required environment: " + ", ".join(missing))


def telegram_message_link(chat_id: int | str, message_id: int, *, main: bool = False) -> str:
    """Build a t.me link for a public username or a private supergroup/channel ID."""
    raw = str(chat_id).strip()
    if raw.startswith("@"):
        return f"https://t.me/{raw[1:]}/{message_id}"
    if raw.startswith("-100"):
        return f"https://t.me/c/{raw[4:]}/{message_id}"
    if main:
        return f"https://t.me/GitHub_Open_Source/{message_id}"
    return ""
