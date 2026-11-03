"""Google Sheets hand-off for make.com.

The Page is published by make.com today, reading rows from a spreadsheet that
a Google service account fills. That chain works and costs nothing to keep,
so v6 does not replace it — it becomes the *producer* of those rows.

SHEET_HANDOFF=false keeps the old path untouched: nothing is written, and
``content_adapters.build_Facebook_text`` output is used only by the direct
Graph API client.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .http import sanitize

DEFAULT_RANGE = "facebook!A1"
HEADERS = ("project_id", "content", "platform", "media_urls", "title")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def row_for(record: dict[str, Any], text: str, *, media_url: str = "") -> list[str]:
    """One spreadsheet row, in the column order make.com already reads.

    The first four columns are exactly what the old pipeline wrote
    (project_id, content, platform, media_urls), so an existing make.com
    scenario keeps working without edits. ``title`` is appended last for the
    scenarios that want a separate headline.
    """
    project_id = str(record.get("repo_id") or record.get("id") or record.get("full_name") or "")
    media = media_url or str(record.get("image_url") or "")
    return [
        project_id,
        text,
        "facebook",
        media,
        str(record.get("name") or record.get("full_name") or ""),
    ]


def append_rows(
    spreadsheet_id: str,
    rows: list[list[str]],
    *,
    credentials_json: str,
    range_name: str = DEFAULT_RANGE,
) -> dict[str, Any]:
    """Append rows with a service account. Returns a small status dict."""
    if not rows:
        return {"ok": True, "appended": 0}
    if not spreadsheet_id or not credentials_json:
        return {"ok": False, "appended": 0, "error": "sheets_not_configured"}
    try:
        from google.oauth2 import service_account  # noqa: PLC0415 - optional dependency
        from googleapiclient.discovery import build  # noqa: PLC0415
    except ImportError:
        return {
            "ok": False,
            "appended": 0,
            "error": "google_libs_missing",
            "hint": "pip install -e '.[sheets]'",
        }
    try:
        info = json.loads(credentials_json)
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        body = {"values": rows}
        result = (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=range_name,
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body,
            )
            .execute()
        )
        updates = result.get("updates") or {}
        return {"ok": True, "appended": int(updates.get("updatedRows") or len(rows)), "range": updates.get("updatedRange", "")}
    except Exception as exc:  # noqa: BLE001 - a sheet outage must not kill the post
        # The exception can contain the service-account JSON or a token;
        # events.jsonl is committed to git, so it must be sanitized.
        return {
            "ok": False,
            "appended": 0,
            "error": sanitize(f"{type(exc).__name__}: {str(exc)[:200]}"),
        }
