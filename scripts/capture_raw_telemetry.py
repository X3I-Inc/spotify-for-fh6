"""Dumps raw FH6 Data Out UDP packets to disk, unparsed, for validating the
assumed Sled+Dash packet layout (docs/DECISIONS.md) against real game output.

Usage:
    python scripts/capture_raw_telemetry.py [--host 0.0.0.0] [--port 20127] [--count 20] [--out capture.bin]
"""

from __future__ import annotations

import argparse
import socket
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0", help="Address to bind (default: all interfaces)")
    parser.add_argument("--port", type=int, default=20127)
    parser.add_argument("--count", type=int, default=20, help="Number of packets to capture")
    parser.add_argument("--out", default="capture.bin", help="Output file for raw packet bytes")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    print(f"Listening on {args.host}:{args.port} — waiting for {args.count} packets from FH6...")

    packets: list[bytes] = []
    sizes: set[int] = set()

    try:
        while len(packets) < args.count:
            data, addr = sock.recvfrom(4096)
            packets.append(data)
            sizes.add(len(data))
            print(f"  [{len(packets)}/{args.count}] {len(data)} bytes from {addr[0]}:{addr[1]}")
    except KeyboardInterrupt:
        print("\nStopped early.")
    finally:
        sock.close()

    if not packets:
        print("No packets captured.")
        sys.exit(1)

    with open(args.out, "wb") as f:
        for p in packets:
            f.write(len(p).to_bytes(4, "little"))
            f.write(p)

    print(f"\nSaved {len(packets)} packets to {args.out}")
    print(f"Observed packet sizes: {sorted(sizes)} (expected 311 for Sled+Dash)")
    if sizes != {311}:
        print("!! Packet size does not match the assumed 311-byte Sled+Dash layout — offsets need rechecking.")


if __name__ == "__main__":
    main()
