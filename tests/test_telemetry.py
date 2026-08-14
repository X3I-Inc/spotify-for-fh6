"""Validates TelemetryListener against mock_sender's scripted state cycle.

Run with pytest, or standalone from the audio-bridge/ directory:
    python -m tests.test_telemetry
"""

from __future__ import annotations

import threading
import time

from telemetry.mock_sender import send_loop
from telemetry.parser import TelemetryListener

TEST_DURATION_SECONDS = 10.0


def test_state_transitions() -> list[str]:
    received_states: list[str] = []
    lock = threading.Lock()

    def on_packet(packet) -> None:
        with lock:
            if not received_states or received_states[-1] != packet.state:
                received_states.append(packet.state)
                print(f"[t={time.monotonic():.1f}s] state -> {packet.state}")

    listener = TelemetryListener(callback=on_packet)
    listener.start()

    sender_stop = threading.Event()
    sender_thread = threading.Thread(
        target=send_loop, kwargs={"stop_event": sender_stop}, daemon=True
    )
    sender_thread.start()

    time.sleep(TEST_DURATION_SECONDS)

    sender_stop.set()
    sender_thread.join(timeout=2.0)
    listener.stop()

    print("Observed state sequence:", received_states)

    assert "driving" in received_states, "never observed a 'driving' state"
    assert "paused_or_menu" in received_states, "never observed a 'paused_or_menu' state"
    assert len(received_states) >= 2, "expected at least one state transition"

    return received_states


if __name__ == "__main__":
    test_state_transitions()
    print("OK")
