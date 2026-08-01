"""Spotify Web API client: Authorization Code OAuth, token storage/refresh in
the DB, and the two reads we need — top artists (all 3 time ranges) and followed
artists. Scopes: user-top-read user-follow-read (spec 4.1).

related-artists is intentionally NOT used: Spotify closed it to new apps in late
2024. Taste expansion happens via Last.fm instead (next step).
"""
from __future__ import annotations

import base64
import logging
import sqlite3
import threading
import urllib.parse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"
SCOPES = "user-top-read user-follow-read"


class SpotifyAuthError(RuntimeError):
    pass


class SpotifyClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        connection: sqlite3.Connection,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.connection = connection

    # -- authorization ----------------------------------------------------

    def authorize_url(self, state: str) -> str:
        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "state": state,
            "show_dialog": "false",
        }
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    def exchange_code(self, code: str) -> None:
        response = requests.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
            headers=self._basic_auth_header(),
            timeout=30,
        )
        if response.status_code != 200:
            raise SpotifyAuthError(f"token exchange failed: {response.status_code} {response.text}")
        self._store_tokens(response.json())

    def has_token(self) -> bool:
        return self._load_row() is not None

    # -- reads ------------------------------------------------------------

    def me(self) -> dict:
        return self._get("/me")

    def top_artists(self, time_range: str, limit: int = 50) -> list[dict]:
        """time_range in {'short_term','medium_term','long_term'}. Ranked list."""
        data = self._get("/me/top/artists", params={"time_range": time_range, "limit": limit})
        return data.get("items", [])

    def followed_artists(self, page_limit: int = 50) -> list[dict]:
        artists: list[dict] = []
        after = None
        while True:
            params = {"type": "artist", "limit": page_limit}
            if after is not None:
                params["after"] = after
            data = self._get("/me/following", params=params)
            block = data.get("artists", {})
            items = block.get("items", [])
            artists.extend(items)
            after = (block.get("cursors") or {}).get("after")
            if not after or not items:
                break
        return artists

    # -- token plumbing ---------------------------------------------------

    def access_token(self) -> str:
        row = self._load_row()
        if row is None:
            raise SpotifyAuthError("Not authorized yet — run `event-radar spotify-login`.")
        expires_at = datetime.fromisoformat(row["expires_at"])
        # Refresh a minute early to avoid races on expiry.
        if datetime.now(timezone.utc) >= expires_at - timedelta(seconds=60):
            self._refresh(row["refresh_token"])
            row = self._load_row()
        return row["access_token"]

    def _refresh(self, refresh_token: str) -> None:
        response = requests.post(
            TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": refresh_token},
            headers=self._basic_auth_header(),
            timeout=30,
        )
        if response.status_code != 200:
            raise SpotifyAuthError(f"token refresh failed: {response.status_code} {response.text}")
        payload = response.json()
        # Spotify may omit refresh_token on refresh — keep the existing one.
        payload.setdefault("refresh_token", refresh_token)
        self._store_tokens(payload)

    def _get(self, path: str, params: dict | None = None) -> dict:
        token = self.access_token()
        response = requests.get(
            f"{API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        if response.status_code == 401:
            # Force a refresh once, then retry.
            row = self._load_row()
            if row is not None:
                self._refresh(row["refresh_token"])
                token = self.access_token()
                response = requests.get(
                    f"{API_BASE}{path}",
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=30,
                )
        if response.status_code != 200:
            raise SpotifyAuthError(f"GET {path} -> {response.status_code} {response.text}")
        return response.json()

    def _basic_auth_header(self) -> dict:
        raw = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        return {"Authorization": f"Basic {base64.b64encode(raw).decode('ascii')}"}

    def _store_tokens(self, payload: dict) -> None:
        expires_in = payload.get("expires_in", 3600)
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).isoformat()
        self.connection.execute(
            "INSERT INTO oauth_tokens (provider, access_token, refresh_token, expires_at, scope, updated_at) "
            "VALUES ('spotify', ?, ?, ?, ?, ?) "
            "ON CONFLICT(provider) DO UPDATE SET "
            "access_token=excluded.access_token, refresh_token=excluded.refresh_token, "
            "expires_at=excluded.expires_at, scope=excluded.scope, updated_at=excluded.updated_at",
            (
                payload.get("access_token"),
                payload.get("refresh_token"),
                expires_at,
                payload.get("scope", SCOPES),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()

    def _load_row(self) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM oauth_tokens WHERE provider = 'spotify'"
        ).fetchone()


# -- interactive login helper --------------------------------------------


class _CallbackHandler(BaseHTTPRequestHandler):
    captured: dict = {}

    def do_GET(self):  # noqa: N802 (http.server API)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        query = urllib.parse.parse_qs(parsed.query)
        _CallbackHandler.captured = {key: values[0] for key, values in query.items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h2>Event Radar: authorized. You can close this tab.</h2>")

    def log_message(self, *args):  # silence the default stderr logging
        return


def run_login(client: SpotifyClient, host: str = "127.0.0.1", port: int = 8080, timeout_seconds: int = 300) -> dict:
    """Print nothing; return the OAuth callback params once the user authorizes.

    Starts a one-shot local server on the redirect URI, waits for the browser
    redirect, then exchanges the code. Caller is responsible for showing the
    authorize URL to the user.
    """
    _CallbackHandler.captured = {}
    server = HTTPServer((host, port), _CallbackHandler)
    server.timeout = timeout_seconds

    def serve_until_callback():
        while not _CallbackHandler.captured:
            server.handle_request()

    thread = threading.Thread(target=serve_until_callback, daemon=True)
    thread.start()
    thread.join(timeout=timeout_seconds)
    server.server_close()

    captured = _CallbackHandler.captured
    if "error" in captured:
        raise SpotifyAuthError(f"authorization denied: {captured['error']}")
    if "code" not in captured:
        raise SpotifyAuthError("timed out waiting for the Spotify authorization callback")
    client.exchange_code(captured["code"])
    return captured
