"""Captures raw FH6 Data Out packets for a fixed duration and saves them to disk
(length-prefixed, same format as capture_raw_telemetry.py) for offline analysis.

Usage:
    python scripts/capture_timed.py [--seconds 15] [--out capture_timed.bin]
"""

from __future__ import annotations

import argparse
import socket
import time

HOST = "0.0.0.0"
PORT = 20127


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--out", default="capture_timed.bin")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((HOST, PORT))
    sock.settimeout(1.0)
    print(f"Listening on {HOST}:{PORT} for {args.seconds}s...")

    packets: list[bytes] = []
    start = time.time()
    while time.time() - start < args.seconds:
        try:
            data, _ = sock.recvfrom(4096)
        except socket.timeout:
            continue
        packets.append(data)

    sock.close()

    with open(args.out, "wb") as f:
        for p in packets:
            f.write(len(p).to_bytes(4, "little"))
            f.write(p)

    sizes = {len(p) for p in packets}
    print(f"Saved {len(packets)} packets to {args.out}. Sizes: {sorted(sizes)}")


if __name__ == "__main__":
    main()
