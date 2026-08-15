"""Background loading of playlists (+ cover art) and playback requests, kept
off the audio/telemetry/GUI threads since these are all network calls.

Same thread-safety pattern as overlay/now_playing.py: work happens on a
background thread, results are pushed to the GUI thread via a Qt signal
(Qt automatically marshals this as a queued connection whenever emitter and
receiver live on different threads).
"""

from __future__ import annotations

import logging
import threading

import requests
from PySide6.QtCore import QObject, Signal

from spotify.client import SpotifyClient

logger = logging.getLogger(__name__)

VOLUME_POLL_INTERVAL_SECONDS = 15.0  # deliberately conservative -- see docs/DECISIONS.md
VOLUME_STEP = 5
DEFAULT_VOLUME_GUESS = 50  # only used if PageUp/Down is pressed before the first successful poll


class SpotifyBridge(QObject):
    playlists_loaded = Signal(list)  # list[PlaylistSummary]
    playlist_art_loaded = Signal(str, bytes)  # playlist_id, image bytes
    playback_error = Signal(str)
    volume_changed = Signal(int)  # 0-100


class SpotifyLoader:
    """Owns a SpotifyClient and runs its network calls on background threads."""

    def __init__(self, client: SpotifyClient, bridge: SpotifyBridge) -> None:
        self.client = client
        self.bridge = bridge
        self._last_volume = DEFAULT_VOLUME_GUESS
        self._volume_poll_stop: threading.Event | None = None
        self._volume_poll_thread: threading.Thread | None = None

    def load_playlists_async(self) -> None:
        threading.Thread(target=self._load_playlists, daemon=True).start()

    def _load_playlists(self) -> None:
        try:
            playlists = self.client.get_playlists()
        except Exception as exc:
            logger.exception("Failed to load Spotify playlists")
            self.bridge.playback_error.emit(f"Couldn't load playlists: {exc}")
            return

        self.bridge.playlists_loaded.emit(playlists)

        for playlist in playlists:
            if playlist.image_url is None:
                continue
            try:
                resp = requests.get(playlist.image_url, timeout=15)
                resp.raise_for_status()
                self.bridge.playlist_art_loaded.emit(playlist.id, resp.content)
            except Exception:
                logger.warning("Failed to load cover art for playlist %s", playlist.name)

    def play_playlist_async(self, uri: str) -> None:
        threading.Thread(target=self._play_playlist, args=(uri,), daemon=True).start()

    def _play_playlist(self, uri: str) -> None:
        try:
            self.client.start_playback(uri)
        except Exception as exc:
            logger.warning("Failed to start playlist playback: %s", exc)
            self.bridge.playback_error.emit(str(exc))

    def toggle_playback_async(self, is_playing: bool) -> None:
        threading.Thread(target=self._toggle_playback, args=(is_playing,), daemon=True).start()

    def _toggle_playback(self, is_playing: bool) -> None:
        try:
            if is_playing:
                self.client.pause()
            else:
                self.client.resume()
        except Exception as exc:
            logger.warning("Failed to toggle playback: %s", exc)
            self.bridge.playback_error.emit(str(exc))

    def next_track_async(self) -> None:
        threading.Thread(target=self._run_transport, args=(self.client.next_track,), daemon=True).start()

    def previous_track_async(self) -> None:
        threading.Thread(target=self._run_transport, args=(self.client.previous_track,), daemon=True).start()

    def _run_transport(self, fn) -> None:
        try:
            fn()
        except Exception as exc:
            logger.warning("Failed to run transport control: %s", exc)
            self.bridge.playback_error.emit(str(exc))

    # --- volume -----------------------------------------------------------

    def start_volume_polling(self) -> None:
        """Periodically refreshes the displayed volume so external changes
        (adjusted from a phone, Spotify itself, another device) show up here
        too -- not just changes made through this app. Deliberately slow
        (VOLUME_POLL_INTERVAL_SECONDS): a previous version re-fetched
        playlists on every tab open and tripped Spotify's extended rate-limit
        penalty (~24h block) -- see docs/DECISIONS.md. One request per
        interval is trivially far under any real limit.
        """
        if self._volume_poll_thread is not None:
            return
        self._volume_poll_stop = threading.Event()
        self._volume_poll_thread = threading.Thread(
            target=self._volume_poll_loop, args=(self._volume_poll_stop,), daemon=True
        )
        self._volume_poll_thread.start()

    def stop_volume_polling(self) -> None:
        if self._volume_poll_stop is not None:
            self._volume_poll_stop.set()
        if self._volume_poll_thread is not None:
            self._volume_poll_thread.join(timeout=2.0)
        self._volume_poll_thread = None
        self._volume_poll_stop = None

    def _volume_poll_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            try:
                percent = self.client.get_volume_percent()
                if percent is not None:
                    self._last_volume = percent
                    self.bridge.volume_changed.emit(percent)
            except Exception as exc:
                logger.warning("Failed to poll Spotify volume: %s", exc)
            stop_event.wait(VOLUME_POLL_INTERVAL_SECONDS)

    def adjust_volume_async(self, delta: int) -> None:
        threading.Thread(target=self._adjust_volume, args=(delta,), daemon=True).start()

    def _adjust_volume(self, delta: int) -> None:
        new_volume = max(0, min(100, self._last_volume + delta))
        self._last_volume = new_volume
        # Optimistic update: reflect the change immediately rather than
        # waiting on the network call, then correct on the next poll if the
        # API call actually failed.
        self.bridge.volume_changed.emit(new_volume)
        try:
            self.client.set_volume(new_volume)
        except Exception as exc:
            logger.warning("Failed to set Spotify volume: %s", exc)
            self.bridge.playback_error.emit(str(exc))
