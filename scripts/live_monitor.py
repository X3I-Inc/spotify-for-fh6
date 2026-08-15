"""Live-decodes candidate RPM/speed/gear offsets from real FH6 Data Out packets
so they can be eyeballed against the in-game HUD while driving, to pin down the
real Dash-extension offsets now that we know packets are 324 bytes (not 311).

Usage:
    python scripts/live_monitor.py [--seconds 10]
"""

from __future__ import annotations

import argparse
import socket
import struct
import time

HOST = "0.0.0.0"
PORT = 20127


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))
    sock.settimeout(1.0)
    print(f"Listening on {HOST}:{PORT} for {args.seconds}s — drive now (accelerate from a stop if possible)...")

    start = time.time()
    n = 0
    while time.time() - start < args.seconds:
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        n += 1
        if n % 6 != 0:  # print ~10x/sec instead of every packet (~60Hz)
            continue

        rpm = struct.unpack_from("<f", data, 16)[0]
        vel_z = struct.unpack_from("<f", data, 40)[0]
        gear = data[307] if len(data) > 307 else None

        # candidates for speed near the Dash extension, given the 13-byte shift
        speed_244 = struct.unpack_from("<f", data, 244)[0]
        speed_256 = struct.unpack_from("<f", data, 256)[0]

        ms_to_mph = 2.23694
        print(
            f"t={time.time()-start:5.1f}s | rpm={rpm:7.1f} | vel_z={vel_z:6.2f} m/s "
            f"({vel_z*ms_to_mph:5.1f} mph) | speed@244={speed_244:8.2f} | "
            f"speed@256={speed_256:6.2f} ({speed_256*ms_to_mph:5.1f} mph) | gear_byte@307={gear}"
        )

    sock.close()
    print(f"\nDone. Captured {n} packets.")


if __name__ == "__main__":
    main()
