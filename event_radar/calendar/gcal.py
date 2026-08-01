"""Google Calendar integration (spec section 9).

OAuth from the user's own account (a Desktop client), NOT a service account —
service-account calendars share poorly to normal Gmail. A dedicated SECONDARY
calendar is created on first run; its id is cached in app_state.

Idempotent: every event's gcal_event_id is stored in `sync`. Re-running updates
the existing entry (on price/score change) rather than creating a duplicate. Each
push also logs an `impressions` row (surface='calendar').

The google client libraries are imported lazily so the rest of the CLI (fetch,
score, dry-run) works without them installed.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_state(connection: sqlite3.Connection, key: str) -> str | None:
    row = connection.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_state(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO app_state (key, value, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, _now_iso()),
    )
    connection.commit()


class GoogleCalendar:
    def __init__(self, credentials_file: str, token_file: str, connection: sqlite3.Connection):
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.connection = connection
        self._service = None

    def _authorize(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        import os

        creds = None
        if os.path.exists(self.token_file):
            creds = Credentials.from_authorized_user_file(self.token_file, SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, SCOPES)
                # Opens the browser once; catches the redirect on a local port.
                creds = flow.run_local_server(port=0)
            with open(self.token_file, "w", encoding="utf-8") as handle:
                handle.write(creds.to_json())
        return creds

    def service(self):
        if self._service is None:
            from googleapiclient.discovery import build

            self._service = build("calendar", "v3", credentials=self._authorize(), cache_discovery=False)
        return self._service

    # -- calendar lifecycle ----------------------------------------------

    def ensure_calendar(self, name: str) -> str:
        """Return the secondary calendar id, creating it once and caching the id."""
        cached = _get_state(self.connection, "calendar_id")
        if cached:
            return cached
        created = self.service().calendars().insert(
            body={"summary": name, "timeZone": "Europe/Rome"}
        ).execute()
        calendar_id = created["id"]
        _set_state(self.connection, "calendar_id", calendar_id)
        logger.info("Created calendar %s (%s)", name, calendar_id)
        return calendar_id

    def list_events(self, calendar_id: str) -> list[dict]:
        """Return calendar events with their colorId and attendees (for feedback sync)."""
        events: list[dict] = []
        page_token = None
        while True:
            response = self.service().events().list(
                calendarId=calendar_id,
                singleEvents=True,
                maxResults=250,
                pageToken=page_token,
            ).execute()
            events.extend(response.get("items", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return events

    def share(self, calendar_id: str, email: str, role: str = "reader") -> None:
        self.service().acl().insert(
            calendarId=calendar_id,
            body={"role": role, "scope": {"type": "user", "value": email}},
        ).execute()
        logger.info("Shared calendar with %s (%s)", email, role)

    # -- idempotent push -------------------------------------------------

    def push_event(self, calendar_id: str, event: dict, title: str, description: str, score: float) -> str:
        """Create or update the calendar entry for one event. Returns gcal_event_id."""
        body = {
            "summary": title,
            "description": description,
            "location": event.get("venue_name") or "Milano",
            "start": {"dateTime": _to_rfc3339(event.get("starts_at")), "timeZone": "Europe/Rome"},
            "end": {"dateTime": _to_rfc3339(event.get("ends_at") or event.get("starts_at")), "timeZone": "Europe/Rome"},
        }

        existing = self.connection.execute(
            "SELECT gcal_event_id FROM sync WHERE event_id = ?", (event["id"],)
        ).fetchone()

        if existing and existing["gcal_event_id"]:
            gcal_id = existing["gcal_event_id"]
            self.service().events().update(
                calendarId=calendar_id, eventId=gcal_id, body=body
            ).execute()
        else:
            created = self.service().events().insert(calendarId=calendar_id, body=body).execute()
            gcal_id = created["id"]

        self.connection.execute(
            "INSERT INTO sync (event_id, gcal_event_id, last_pushed_score) VALUES (?,?,?) "
            "ON CONFLICT(event_id) DO UPDATE SET gcal_event_id=excluded.gcal_event_id, "
            "last_pushed_score=excluded.last_pushed_score",
            (event["id"], gcal_id, score),
        )
        self.connection.execute(
            "INSERT INTO impressions (event_id, user_id, surface, score_at_show, shown_at) "
            "VALUES (?, 'owner', 'calendar', ?, ?)",
            (event["id"], score, _now_iso()),
        )
        self.connection.commit()
        return gcal_id


def _to_rfc3339(starts_at: str | None) -> str:
    """RA gives naive local (Europe/Rome) ISO; Google wants an RFC3339 datetime."""
    if not starts_at:
        return datetime.now().replace(microsecond=0).isoformat()
    try:
        return datetime.fromisoformat(starts_at).replace(microsecond=0).isoformat()
    except ValueError:
        return datetime.now().replace(microsecond=0).isoformat()
