"""Spotify Web API OAuth via Authorization Code + PKCE.

PKCE is used instead of the plain Authorization Code flow specifically so no
client secret has to be stored on this machine at all -- only a public
Client ID (from a Spotify Developer app you create yourself) plus a one-time
browser login. After that, a cached refresh token makes future runs silent.

Requires a Spotify Developer app with this exact redirect URI registered:
    http://127.0.0.1:8888/callback
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import json
import logging
import secrets
import threading
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
REDIRECT_URI = "http://127.0.0.1:8888/callback"
REDIRECT_PORT = 8888
SCOPES = "playlist-read-private user-read-currently-playing user-read-playback-state user-modify-playback-state"

TOKEN_CACHE_PATH = Path(__file__).parent / ".spotify_token_cache.json"


class SpotifyAuthError(RuntimeError):
    pass


def _generate_pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    # Set per-flow on the class before the server is started; a fresh flow
    # always fully replaces these before spinning up the server.
    auth_code: Optional[str] = None
    auth_error: Optional[str] = None
    state_expected: Optional[str] = None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if params.get("state", [None])[0] != _CallbackHandler.state_expected:
            _CallbackHandler.auth_error = "state_mismatch"
            self._respond("Spotify login failed (state mismatch). You can close this tab.")
            return

        code = params.get("code", [None])[0]
        if code:
            _CallbackHandler.auth_code = code
            self._respond("Spotify connected. You can close this tab and go back to the app.")
        else:
            _CallbackHandler.auth_error = params.get("error", ["unknown_error"])[0]
            self._respond(f"Spotify login failed: {_CallbackHandler.auth_error}. You can close this tab.")

    def _respond(self, message: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(f"<html><body style='font-family:sans-serif'><h2>{message}</h2></body></html>".encode())

    def log_message(self, format, *args) -> None:  # silence default HTTP server access logging
        pass


def _run_authorization_flow(client_id: str) -> dict:
    verifier, challenge = _generate_pkce_pair()
    state = secrets.token_urlsafe(16)
    _CallbackHandler.state_expected = state
    _CallbackHandler.auth_code = None
    _CallbackHandler.auth_error = None

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
        "scope": SCOPES,
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    server = http.server.HTTPServer(("127.0.0.1", REDIRECT_PORT), _CallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print(f"\nOpening your browser for Spotify login...\nIf it doesn't open, visit:\n{url}\n")
    webbrowser.open(url)
    server_thread.join(timeout=180)

    if _CallbackHandler.auth_code is None:
        reason = _CallbackHandler.auth_error or "timed out waiting for login"
        raise SpotifyAuthError(f"Spotify authorization did not complete: {reason}")

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": _CallbackHandler.auth_code,
            "redirect_uri": REDIRECT_URI,
            "client_id": client_id,
            "code_verifier": verifier,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _refresh_token(client_id: str, refresh_token: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def get_access_token(client_id: str) -> str:
    """Returns a valid access token. Only opens the browser login flow if no
    cached (working) refresh token exists yet."""
    cached = None
    if TOKEN_CACHE_PATH.exists():
        try:
            cached = json.loads(TOKEN_CACHE_PATH.read_text())
        except Exception:
            cached = None

    if cached and cached.get("refresh_token"):
        try:
            token_data = _refresh_token(client_id, cached["refresh_token"])
            token_data.setdefault("refresh_token", cached["refresh_token"])
            TOKEN_CACHE_PATH.write_text(json.dumps(token_data))
            return token_data["access_token"]
        except Exception:
            logger.warning("Cached Spotify refresh token no longer works; re-authorizing")

    token_data = _run_authorization_flow(client_id)
    TOKEN_CACHE_PATH.write_text(json.dumps(token_data))
    return token_data["access_token"]
