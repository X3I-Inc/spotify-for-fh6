"""Phase 7 integration: wires telemetry -> DSP -> audio capture/playback end to end.

Listens for real (or mock) FH6 telemetry, drives the DSP chain's state and
RPM-reactive modulation from it live, and applies that chain to captured
loopback audio (e.g. Spotify routed through VB-Cable) before playing it back.

Usage:
    python main.py --input "CABLE Output" --output "Headphone (Realtek"

    # Test without the game running, using the mock telemetry sender:
    python main.py --input "CABLE Output" --output "Headphone (Realtek" --mock
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from typing import Optional, Union

from audio.capture import AudioCapture, list_devices
from audio.playback import AudioPlayback
from dsp.chain import DSPChain
from dsp.presets import compute_rpm_norm
from overlay.hotkeys import DEFAULT_HOTKEY, LockToggleBridge, LockToggleHotkey, VolumeHotkeyBridge, VolumeHotkeys
from overlay.now_playing import NowPlayingBridge, NowPlayingPoller
from overlay.window import create_overlay
from spotify.client import SpotifyClient
from spotify.loader import VOLUME_STEP, SpotifyBridge, SpotifyLoader
from telemetry.mock_sender import send_loop
from telemetry.parser import DEFAULT_HOST, DEFAULT_PORT, TelemetryListener, TelemetryPacket

MS_TO_MPH = 2.23694

logger = logging.getLogger(__name__)


def _parse_device_arg(value: Optional[str]) -> Optional[Union[int, str]]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="capture device index or name substring (e.g. 'CABLE Output')")
    parser.add_argument("--output", help="playback device index or name substring (e.g. your headphones)")
    parser.add_argument("--telemetry-host", default=DEFAULT_HOST)
    parser.add_argument("--telemetry-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--crossfade-ms", type=float, default=200.0)
    parser.add_argument("--mock", action="store_true", help="use the mock telemetry sender instead of real FH6 UDP")
    parser.add_argument("--list-devices", action="store_true", help="print audio devices and exit")
    parser.add_argument("--no-overlay", action="store_true", help="skip the on-screen HUD overlay")
    parser.add_argument(
        "--overlay-corner",
        default="bottom-left",
        choices=["top-left", "top-right", "bottom-left", "bottom-right"],
    )
    parser.add_argument(
        "--spotify-client-id",
        default=os.environ.get("SPOTIFY_CLIENT_ID"),
        help="Spotify Developer app Client ID (or set SPOTIFY_CLIENT_ID) -- enables the playlist "
        "grid screen on the overlay. Requires http://127.0.0.1:8888/callback registered as a "
        "redirect URI on that app.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.list_devices:
        list_devices()
        return

    input_device = _parse_device_arg(args.input)
    output_device = _parse_device_arg(args.output)

    capture = AudioCapture(device=input_device)
    playback = AudioPlayback(device=output_device)
    chain = DSPChain(samplerate=capture.samplerate, crossfade_ms=args.crossfade_ms)

    app = window = bridge = now_playing_poller = lock_hotkey = spotify_loader = volume_hotkeys = None
    if not args.no_overlay:
        now_playing_bridge = NowPlayingBridge()
        lock_bridge = LockToggleBridge()

        spotify_bridge = None
        if args.spotify_client_id:
            spotify_client = SpotifyClient(args.spotify_client_id)
            spotify_bridge = SpotifyBridge()
            spotify_loader = SpotifyLoader(spotify_client, spotify_bridge)
        else:
            print(
                "No --spotify-client-id / SPOTIFY_CLIENT_ID set -- the overlay's playlist "
                "grid screen will be unavailable."
            )

        app, window, bridge = create_overlay(
            now_playing_bridge=now_playing_bridge,
            lock_bridge=lock_bridge,
            spotify_bridge=spotify_bridge,
            on_playlist_selected=(spotify_loader.play_playlist_async if spotify_loader else None),
            on_home_opened=(spotify_loader.load_playlists_async if spotify_loader else None),
            on_play_pause=(spotify_loader.toggle_playback_async if spotify_loader else None),
            on_next=(spotify_loader.next_track_async if spotify_loader else None),
            on_previous=(spotify_loader.previous_track_async if spotify_loader else None),
            on_sound_mode_changed=chain.set_sound_mode,
            corner=args.overlay_corner,
        )
        now_playing_poller = NowPlayingPoller(now_playing_bridge)
        now_playing_poller.start()
        lock_hotkey = LockToggleHotkey(lock_bridge)
        lock_hotkey.start()

        if spotify_loader is not None:
            spotify_loader.start_volume_polling()
            volume_hotkey_bridge = VolumeHotkeyBridge()
            volume_hotkey_bridge.volume_up.connect(lambda: spotify_loader.adjust_volume_async(VOLUME_STEP))
            volume_hotkey_bridge.volume_down.connect(lambda: spotify_loader.adjust_volume_async(-VOLUME_STEP))
            volume_hotkeys = VolumeHotkeys(volume_hotkey_bridge)
            volume_hotkeys.start()

        print(
            f"Overlay ready -- tap it to control playback, drag to move, drag the bottom-right "
            f"corner to resize. Press {DEFAULT_HOTKEY} to lock/unlock its position and size "
            "(taps keep working either way). Page Up/Down adjusts Spotify's volume."
        )

    def on_audio_block(block) -> None:
        playback.write(chain.process(block))

    capture.callback = on_audio_block

    last_status_print = 0.0

    def on_telemetry(packet: TelemetryPacket) -> None:
        nonlocal last_status_print
        rpm_norm = compute_rpm_norm(packet.current_engine_rpm, packet.engine_max_rpm)
        chain.update_telemetry(packet.state, rpm_norm)
        mod = chain.get_driving_modulation_state()

        if bridge is not None:
            bridge.status_changed.emit(
                {
                    "state": packet.state,
                    "speed_mph": packet.speed * MS_TO_MPH,
                    "rpm": packet.current_engine_rpm,
                    "gear": packet.gear,
                    "gain_db": mod["gain_db"],
                    "mud_gain_db": mod["mud_gain_db"],
                    "presence_gain_db": mod["presence_gain_db"],
                }
            )

        now = time.monotonic()
        if now - last_status_print > 1.0:
            last_status_print = now
            print(
                f"[telemetry] state={packet.state:<14} speed={packet.speed:5.1f} m/s  "
                f"rpm={packet.current_engine_rpm:6.0f}  gear={packet.gear}  "
                f"rpm_norm={mod['rpm_norm']:.2f}  gain={mod['gain_db']:.2f}dB"
            )

    playback.start()
    capture.start()

    mock_stop_event: Optional[threading.Event] = None
    mock_thread: Optional[threading.Thread] = None
    listener: Optional[TelemetryListener] = None

    if args.mock:
        print("Using mock telemetry sender (no real FH6 connection).")
        mock_stop_event = threading.Event()
        mock_thread = threading.Thread(
            target=send_loop,
            kwargs={"host": args.telemetry_host, "port": args.telemetry_port, "stop_event": mock_stop_event},
            daemon=True,
        )
        mock_thread.start()

    listener = TelemetryListener(host=args.telemetry_host, port=args.telemetry_port, callback=on_telemetry)
    listener.start()

    print(
        f"\nRunning. Telemetry on {args.telemetry_host}:{args.telemetry_port}"
        f"{' (mock)' if args.mock else ''}. Ctrl+C to stop.\n"
    )

    try:
        if app is not None:
            # Qt owns the main thread's event loop when the overlay is shown.
            # A plain `except KeyboardInterrupt` around app.exec() doesn't
            # work: Ctrl+C's SIGINT can land while Python is executing inside
            # an arbitrary Qt slot callback (e.g. _on_status_changed), and Qt
            # catches/prints exceptions raised there instead of letting them
            # propagate out of app.exec() -- hence the traceback-but-no-exit
            # behavior. Installing an explicit SIGINT handler that calls
            # app.quit() sidesteps that entirely: it always runs the same way
            # regardless of which slot Python happened to be in. The QTimer
            # is still needed so Python's interpreter gets control back
            # often enough to notice the signal at all (Qt's C++ event loop
            # otherwise never hands control back to Python).
            import signal

            from PySide6.QtCore import QTimer

            def _handle_sigint(*_args) -> None:
                print("\nStopping...")
                app.quit()

            signal.signal(signal.SIGINT, _handle_sigint)

            keepalive = QTimer()
            keepalive.timeout.connect(lambda: None)
            keepalive.start(200)
            app.exec()
        else:
            while True:
                time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        listener.stop()
        if now_playing_poller is not None:
            now_playing_poller.stop()
        if lock_hotkey is not None:
            lock_hotkey.stop()
        if volume_hotkeys is not None:
            volume_hotkeys.stop()
        if spotify_loader is not None:
            spotify_loader.stop_volume_polling()
        if mock_stop_event is not None:
            mock_stop_event.set()
        if mock_thread is not None:
            mock_thread.join(timeout=2.0)
        capture.stop()
        playback.stop()


if __name__ == "__main__":
    main()
