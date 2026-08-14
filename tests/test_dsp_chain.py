"""Manual/interactive DSP chain test: capture -> DSPChain -> playback.

Reuses the VB-Cable-based capture/playback pipeline already confirmed working
in Phase 3, with dsp.chain.DSPChain inserted in between. Defaults to fully
manual control: pick the state with 1/2/3 and dial RPM up/down by hand with
-/=, so specific combinations (e.g. "driving" pinned at redline) can be
listened to on demand rather than only whatever the mock sender's ramp
happens to be doing. A mock FH6 telemetry sender + TelemetryListener still run
in the background and can be switched to with 't', for judging the live
telemetry-driven ramp the same way Phase 7's real integration eventually will.

Interactive:
    python -m tests.test_dsp_chain

Non-interactive device selection:
    python -m tests.test_dsp_chain --input "CABLE Output" --output "Buds4 Pro"

While running:
    1 = driving (in-car cabin)      -- manual mode
    2 = paused_or_menu (event echo) -- manual mode
    3 = neutral (pass-through)      -- manual mode
    -/= = decrease/increase RPM (only audible while in "driving")
    t = toggle telemetry-follow on/off (mock sender drives state + RPM instead)
    q = quit
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from typing import Optional, Union

from audio.capture import AudioCapture, list_devices
from audio.playback import AudioPlayback
from dsp.chain import DEFAULT_CROSSFADE_MS, DSPChain
from dsp.presets import DEFAULT_STATE, compute_rpm_norm
from telemetry.mock_sender import send_loop
from telemetry.parser import TelemetryListener, TelemetryPacket

STATE_KEYS = {"1": "driving", "2": "paused_or_menu", "3": "neutral"}
RPM_STEP = 0.1


class SharedMode:
    """Manual state/RPM + follow-telemetry flag, shared between the key-read and telemetry threads."""

    def __init__(self) -> None:
        self.follow_telemetry = False
        self.manual_state = DEFAULT_STATE
        self.manual_rpm_norm = 0.0


def _parse_device_arg(value: Optional[str]) -> Optional[Union[int, str]]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _handle_key(key: str, chain: DSPChain, mode: SharedMode, stop_event: threading.Event) -> None:
    if key == "q":
        print("Quitting...")
        stop_event.set()
        return
    if key == "t":
        mode.follow_telemetry = not mode.follow_telemetry
        print(f"-> telemetry-follow {'ON' if mode.follow_telemetry else 'OFF (manual control)'}")
        return

    state = STATE_KEYS.get(key)
    if state is not None:
        mode.follow_telemetry = False
        mode.manual_state = state
        print(f"-> manual: state={state!r}  rpm_norm={mode.manual_rpm_norm:.2f}")
        chain.update_telemetry(state, mode.manual_rpm_norm)
        return

    if key in ("-", "_"):
        mode.follow_telemetry = False
        mode.manual_rpm_norm = max(0.0, mode.manual_rpm_norm - RPM_STEP)
        print(f"-> manual: state={mode.manual_state!r}  rpm_norm={mode.manual_rpm_norm:.2f}")
        chain.update_telemetry(mode.manual_state, mode.manual_rpm_norm)
        return

    if key in ("=", "+"):
        mode.follow_telemetry = False
        mode.manual_rpm_norm = min(1.0, mode.manual_rpm_norm + RPM_STEP)
        print(f"-> manual: state={mode.manual_state!r}  rpm_norm={mode.manual_rpm_norm:.2f}")
        chain.update_telemetry(mode.manual_state, mode.manual_rpm_norm)
        return


def _read_keys(chain: DSPChain, mode: SharedMode, stop_event: threading.Event) -> None:
    """Blocking key-read loop, run on its own thread so audio keeps flowing."""
    try:
        import msvcrt
    except ImportError:
        print("msvcrt not available (non-Windows) -- type 1/2/3/-/=/t/q + Enter instead.")
        while not stop_event.is_set():
            line = sys.stdin.readline()
            if line:
                _handle_key(line.strip()[:1], chain, mode, stop_event)
        return

    while not stop_event.is_set():
        if msvcrt.kbhit():
            key = msvcrt.getch().decode(errors="ignore")
            _handle_key(key, chain, mode, stop_event)
        else:
            time.sleep(0.02)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", help="input device index or name substring")
    parser.add_argument("--output", help="output device index or name substring")
    parser.add_argument("--crossfade-ms", type=float, default=DEFAULT_CROSSFADE_MS)
    args = parser.parse_args()

    print("Available audio devices:\n")
    list_devices()

    input_device = _parse_device_arg(args.input)
    output_device = _parse_device_arg(args.output)

    if args.input is None:
        raw = input("\nInput device (index or name substring, blank = auto-pick loopback): ").strip()
        input_device = _parse_device_arg(raw)

    if args.output is None:
        raw = input("Output device (index or name substring, blank = system default): ").strip()
        output_device = _parse_device_arg(raw)

    capture = AudioCapture(device=input_device)
    playback = AudioPlayback(device=output_device)
    chain = DSPChain(samplerate=capture.samplerate, crossfade_ms=args.crossfade_ms)
    mode = SharedMode()

    def on_block(block):
        playback.write(chain.process(block))

    capture.callback = on_block

    last_telemetry_state = {"value": None}

    def on_packet(packet: TelemetryPacket) -> None:
        if not mode.follow_telemetry:
            return
        rpm_norm = compute_rpm_norm(packet.current_engine_rpm, packet.engine_max_rpm)
        chain.update_telemetry(packet.state, rpm_norm)
        if packet.state != last_telemetry_state["value"]:
            print(f"[telemetry] state -> {packet.state!r}")
            last_telemetry_state["value"] = packet.state

    print(
        "\n1 = driving   2 = paused/menu   3 = neutral   -/= = RPM down/up   "
        "t = toggle telemetry-follow   q = quit\n"
        f"Crossfade: {args.crossfade_ms:.0f}ms. Manual control (state={chain.current_state!r}, "
        f"rpm_norm={mode.manual_rpm_norm:.2f}). Play some music now.\n"
    )

    playback.start()
    capture.start()

    telemetry_stop = threading.Event()
    sender_thread = threading.Thread(target=send_loop, kwargs={"stop_event": telemetry_stop}, daemon=True)
    sender_thread.start()
    listener = TelemetryListener(callback=on_packet)
    listener.start()

    stop_event = threading.Event()
    key_thread = threading.Thread(target=_read_keys, args=(chain, mode, stop_event), daemon=True)
    key_thread.start()

    try:
        while not stop_event.is_set():
            time.sleep(1.0)
            mod = chain.get_driving_modulation_state()
            follow = "telemetry" if mode.follow_telemetry else "manual"
            print(
                f"  [{follow}] state={chain.current_state!r}  rpm_norm={mod['rpm_norm']:.2f}  "
                f"gain={mod['gain_db']:+.2f}dB  mud={mod['mud_gain_db']:+.2f}dB  "
                f"presence={mod['presence_gain_db']:+.2f}dB"
            )
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        telemetry_stop.set()
        listener.stop()
        capture.stop()
        playback.stop()


if __name__ == "__main__":
    main()
