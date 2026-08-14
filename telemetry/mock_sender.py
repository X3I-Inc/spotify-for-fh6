"""Sends simulated FH6 telemetry packets over UDP for local testing without the game.

Cycles through a scripted sequence of gameplay states (driving, paused/menu) at the
real FH6 broadcast rate (~60Hz) so telemetry/parser.py's TelemetryListener can be
exercised end-to-end without the game running.

Standalone usage:
    python -m telemetry.mock_sender
"""

from __future__ import annotations

import math
import socket
import threading
import time
from typing import Optional

from telemetry.parser import DEFAULT_HOST, DEFAULT_PORT, FIELD_NAMES, SLED_DASH_STRUCT

HZ = 60
# (state_name, duration_seconds). Cycles indefinitely.
DEFAULT_CYCLE: list[tuple[str, float]] = [
    ("driving", 5.0),
    ("paused_or_menu", 3.0),
]

# RPM ramps in a triangle wave between idle and near-redline while "driving",
# repeatedly revving up and back down (like blipping the throttle) rather than
# holding a narrow constant band -- gives RPM-reactive DSP something with real
# range to react to, hitting both ends of rpm_norm (near 0 and near 1).
MOCK_ENGINE_IDLE_RPM = 900.0
MOCK_ENGINE_REDLINE_RPM = 6800.0  # below engine_max_rpm, leaving a little headroom
MOCK_RPM_CYCLE_SECONDS = 4.0


def _zeroed_fields() -> dict[str, float | int]:
    return {name: 0 for name in FIELD_NAMES}


def _triangle_wave(t: float, period: float) -> float:
    """0..1..0 triangle wave with the given period, continuous (no jump at wrap)."""
    phase = (t % period) / period
    return phase / 0.5 if phase < 0.5 else (1.0 - phase) / 0.5


def build_packet(state: str, t: float) -> bytes:
    """Build a fake Sled+Dash UDP payload for the given state at simulated time t."""
    values = _zeroed_fields()

    if state == "driving":
        # Oscillate speed between ~10 and ~40 m/s so DSP/state-dependent behavior
        # downstream has something to react to besides a flat number.
        speed = 25.0 + 15.0 * math.sin(t * 0.5)
        rpm_frac = _triangle_wave(t, MOCK_RPM_CYCLE_SECONDS)
        current_rpm = MOCK_ENGINE_IDLE_RPM + (MOCK_ENGINE_REDLINE_RPM - MOCK_ENGINE_IDLE_RPM) * rpm_frac

        values["is_race_on"] = 1
        values["speed"] = speed
        values["current_engine_rpm"] = current_rpm
        values["engine_max_rpm"] = 7000.0
        values["engine_idle_rpm"] = MOCK_ENGINE_IDLE_RPM
        values["power"] = 120000.0 + 40000.0 * math.sin(t * 0.5)
        values["torque"] = 300.0 + 50.0 * math.sin(t * 0.5)
        values["gear"] = 3
        values["accel"] = 200
        values["car_ordinal"] = 1
        values["car_class"] = 5
        values["num_cylinders"] = 6
    else:  # "paused_or_menu"
        values["is_race_on"] = 0
        values["speed"] = 0.0
        values["current_engine_rpm"] = 0.0
        values["car_ordinal"] = 1

    values["timestamp_ms"] = int(t * 1000) & 0xFFFFFFFF

    return SLED_DASH_STRUCT.pack(*(values[name] for name in FIELD_NAMES))


def send_loop(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    cycle: Optional[list[tuple[str, float]]] = None,
    stop_event: Optional[threading.Event] = None,
    hz: int = HZ,
) -> None:
    """Send simulated telemetry packets until stop_event is set (or forever).

    Runs in the calling thread — callers that want a background sender should run
    this in their own thread and use `stop_event` to end it.
    """
    cycle = cycle or DEFAULT_CYCLE
    stop_event = stop_event or threading.Event()
    interval = 1.0 / hz

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    start_time = time.monotonic()
    try:
        while not stop_event.is_set():
            for state_name, duration in cycle:
                state_start = time.monotonic()
                while time.monotonic() - state_start < duration:
                    if stop_event.is_set():
                        return
                    t = time.monotonic() - start_time
                    packet = build_packet(state_name, t)
                    sock.sendto(packet, (host, port))
                    time.sleep(interval)
    finally:
        sock.close()


def main() -> None:
    print(
        f"Mock FH6 telemetry sender -> {DEFAULT_HOST}:{DEFAULT_PORT} "
        f"(~{HZ}Hz, cycling {DEFAULT_CYCLE}). Ctrl+C to stop."
    )
    try:
        send_loop()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
