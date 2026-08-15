"""Quick check: can we read title/artist/album-art/position from Spotify via
Windows' System Media Transport Controls (SMTC), the same API the OS uses for
media keys and the volume-flyout "now playing" widget?

Usage:
    python scripts/test_now_playing.py
"""

import asyncio

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as MediaManager,
)
from winsdk.windows.storage.streams import Buffer, InputStreamOptions


async def main() -> None:
    manager = await MediaManager.request_async()
    session = manager.get_current_session()
    if session is None:
        print("No active media session found. Is Spotify playing?")
        return

    print("App:", session.source_app_user_model_id)

    props = await session.try_get_media_properties_async()
    print("Title:", props.title)
    print("Artist:", props.artist)
    print("Album:", props.album_title)

    timeline = session.get_timeline_properties()
    print("Position:", timeline.position, "/ End:", timeline.end_time)

    playback_info = session.get_playback_info()
    print("Playback status:", playback_info.playback_status)

    thumb_ref = props.thumbnail
    if thumb_ref is not None:
        stream = await thumb_ref.open_read_async()
        size = stream.size
        buf = Buffer(size)
        await stream.read_async(buf, size, InputStreamOptions.READ_AHEAD)
        raw = bytes(buf)
        with open("scripts/thumb.jpg", "wb") as f:
            f.write(raw)
        print(f"Thumbnail: {size} bytes, saved to scripts/thumb.jpg")
    else:
        print("No thumbnail available.")


if __name__ == "__main__":
    asyncio.run(main())
