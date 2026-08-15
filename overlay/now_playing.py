"""Polls Windows' System Media Transport Controls (SMTC) for the currently
playing track (title, artist, album art, position/duration).

This is the same OS-level API Spotify (and most other players) already feed
for hardware media keys and the volume-flyout "now playing" widget, so no
Spotify API keys/OAuth are needed -- just reading what the OS already knows.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Optional

from PySide6.QtCore import QObject, Signal
from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
)
from winsdk.windows.storage.streams import Buffer, InputStreamOptions

POLL_INTERVAL_SECONDS = 1.0
PLAYBACK_STATUS_PLAYING = 4  # winsdk's GlobalSystemMediaTransportControlsSessionPlaybackStatus.PLAYING


class NowPlayingBridge(QObject):
    """Thread-safe entry point: the poller's background asyncio thread emits
    this, Qt marshals it to the GUI thread automatically (queued connection,
    used whenever emitter and receiver live on different threads)."""

    track_changed = Signal(dict)


async def _read_thumbnail(thumb_ref) -> Optional[bytes]:
    if thumb_ref is None:
        return None
    try:
        stream = await thumb_ref.open_read_async()
        size = stream.size
        if size == 0:
            return None
        buf = Buffer(size)
        await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
        return bytes(buf)
    except Exception:
        return None


class NowPlayingPoller:
    """Polls the active media session on a background asyncio loop and emits
    updates via `bridge.track_changed`. Only re-reads album art when the track
    actually changes, since that's a real I/O read each time."""

    def __init__(self, bridge: NowPlayingBridge, interval: float = POLL_INTERVAL_SECONDS) -> None:
        self.bridge = bridge
        self.interval = interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_track_key: Optional[tuple] = None
        self._last_thumbnail: Optional[bytes] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _run(self) -> None:
        asyncio.run(self._poll_loop())

    async def _poll_loop(self) -> None:
        manager = await MediaManager.request_async()
        while not self._stop_event.is_set():
            await self._poll_once(manager)
            await asyncio.sleep(self.interval)

    async def _poll_once(self, manager) -> None:
        session = manager.get_current_session()
        if session is None:
            self._last_track_key = None
            self.bridge.track_changed.emit({"active": False})
            return

        try:
            props = await session.try_get_media_properties_async()
            timeline = session.get_timeline_properties()
            playback_info = session.get_playback_info()
        except Exception:
            return

        track_key = (props.title, props.artist)
        thumbnail = self._last_thumbnail
        if track_key != self._last_track_key:
            thumbnail = await _read_thumbnail(props.thumbnail)
            self._last_thumbnail = thumbnail
            self._last_track_key = track_key

        is_playing = int(playback_info.playback_status) == PLAYBACK_STATUS_PLAYING

        self.bridge.track_changed.emit(
            {
                "active": True,
                "title": props.title or "",
                "artist": props.artist or "",
                "position_s": timeline.position.total_seconds(),
                "duration_s": timeline.end_time.total_seconds(),
                "is_playing": is_playing,
                "thumbnail": thumbnail,
            }
        )
