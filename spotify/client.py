"""Thin wrapper around the Spotify Web API endpoints the CarPlay-style "home"
grid needs: listing the user's playlists and starting playback of one.

Currently-playing info (title/artist/art/position) still comes from Windows
SMTC (overlay/now_playing.py) -- this module is only for playlist
browsing/selection, which SMTC doesn't expose.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

import requests

from spotify.auth import get_access_token

logger = logging.getLogger(__name__)

API_BASE = "https://api.spotify.com/v1"


class SpotifyApiError(RuntimeError):
    pass


@dataclass
class PlaylistSummary:
    id: str
    name: str
    owner: str
    image_url: Optional[str]
    uri: str


class SpotifyClient:
    def __init__(self, client_id: str) -> None:
        self.client_id = client_id
        self._access_token: Optional[str] = None

    def _headers(self) -> dict:
        if self._access_token is None:
            self._access_token = get_access_token(self.client_id)
        return {"Authorization": f"Bearer {self._access_token}"}

    def _get(self, path: str, params: Optional[dict] = None) -> dict:
        resp = requests.get(f"{API_BASE}{path}", headers=self._headers(), params=params, timeout=15)
        if resp.status_code == 401:
            # Access token expired mid-session; force a refresh and retry once.
            self._access_token = None
            resp = requests.get(f"{API_BASE}{path}", headers=self._headers(), params=params, timeout=15)
        if resp.status_code == 429:
            # Spotify's rate limit -- honor Retry-After if it's short, rather
            # than immediately failing (easy to hit this by re-opening the
            # Browse tab repeatedly, since each open used to re-fetch).
            retry_after = int(resp.headers.get("Retry-After", "0") or "0")
            if retry_after and retry_after <= 10:
                time.sleep(retry_after)
                resp = requests.get(f"{API_BASE}{path}", headers=self._headers(), params=params, timeout=15)
            else:
                raise SpotifyApiError(
                    f"Rate limited by Spotify -- try again in {retry_after or 'a few'}s."
                )
        resp.raise_for_status()
        return resp.json()

    def get_playlists(self, limit: int = 12) -> list[PlaylistSummary]:
        data = self._get("/me/playlists", params={"limit": limit})
        playlists = []
        for item in data.get("items", []):
            images = item.get("images") or []
            owner = (item.get("owner") or {}).get("display_name") or ""
            playlists.append(
                PlaylistSummary(
                    id=item["id"],
                    name=item["name"],
                    owner=owner,
                    image_url=images[0]["url"] if images else None,
                    uri=item["uri"],
                )
            )
        return playlists

    def start_playback(self, context_uri: str, device_id: Optional[str] = None) -> None:
        """Starts playback of a playlist on the active Spotify Connect device.

        Requires Spotify to already be open with an active device (desktop app,
        web player, etc.) -- the Web API can't launch the app itself.
        """
        params = {"device_id": device_id} if device_id else None
        resp = requests.put(
            f"{API_BASE}/me/player/play",
            headers=self._headers(),
            params=params,
            json={"context_uri": context_uri},
            timeout=15,
        )
        if resp.status_code == 404:
            raise SpotifyApiError(
                "No active Spotify device found -- open Spotify and play anything once first."
            )
        if resp.status_code == 403:
            raise SpotifyApiError("Playback control requires Spotify Premium.")
        resp.raise_for_status()

    def _transport(self, method: str, path: str) -> None:
        """Shared plumbing for the no-body transport controls (pause/resume/
        next/previous) below -- same error handling as start_playback."""
        resp = requests.request(method, f"{API_BASE}{path}", headers=self._headers(), timeout=15)
        if resp.status_code == 404:
            raise SpotifyApiError(
                "No active Spotify device found -- open Spotify and play anything once first."
            )
        if resp.status_code == 403:
            raise SpotifyApiError("Playback control requires Spotify Premium.")
        if resp.status_code not in (200, 202, 204):
            resp.raise_for_status()

    def pause(self) -> None:
        self._transport("PUT", "/me/player/pause")

    def resume(self) -> None:
        self._transport("PUT", "/me/player/play")

    def next_track(self) -> None:
        self._transport("POST", "/me/player/next")

    def previous_track(self) -> None:
        self._transport("POST", "/me/player/previous")

    def get_volume_percent(self) -> Optional[int]:
        """Returns the active device's current volume (0-100), or None if
        there's no active device. Written directly against `requests` rather
        than through `_get()` since this endpoint returns an empty 204 body
        (no active device/session) as a normal, common case -- `_get()`
        assumes a JSON body is always present.
        """
        resp = requests.get(f"{API_BASE}/me/player", headers=self._headers(), timeout=15)
        if resp.status_code == 401:
            self._access_token = None
            resp = requests.get(f"{API_BASE}/me/player", headers=self._headers(), timeout=15)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "0") or "0")
            raise SpotifyApiError(f"Rate limited by Spotify -- try again in {retry_after or 'a few'}s.")
        if resp.status_code == 204 or not resp.content:
            return None
        resp.raise_for_status()
        device = (resp.json() or {}).get("device") or {}
        return device.get("volume_percent")

    def set_volume(self, percent: int) -> None:
        percent = max(0, min(100, int(percent)))
        resp = requests.put(
            f"{API_BASE}/me/player/volume",
            headers=self._headers(),
            params={"volume_percent": percent},
            timeout=15,
        )
        if resp.status_code == 404:
            raise SpotifyApiError(
                "No active Spotify device found -- open Spotify and play anything once first."
            )
        if resp.status_code == 403:
            raise SpotifyApiError("Volume control requires Spotify Premium.")
        if resp.status_code not in (200, 202, 204):
            resp.raise_for_status()
