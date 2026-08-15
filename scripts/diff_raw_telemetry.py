"""Scans captured raw FH6 packets (from capture_raw_telemetry.py) for plausible
float32 field locations, to help re-derive the real offset layout now that we
know real packets are 324 bytes, not the assumed 311.

Usage:
    python scripts/diff_raw_telemetry.py scripts/capture.bin
"""

from __future__ import annotations

import struct
import sys


def load_packets(path: str) -> list[bytes]:
    packets = []
    with open(path, "rb") as f:
        while True:
            size_bytes = f.read(4)
            if len(size_bytes) < 4:
                break
            size = int.from_bytes(size_bytes, "little")
            packets.append(f.read(size))
    return packets


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "scripts/capture.bin"
    packets = load_packets(path)
    if not packets:
        print("No packets loaded.")
        return

    n = len(packets[0])
    print(f"Loaded {len(packets)} packets, {n} bytes each.\n")

    print(f"{'offset':>6} | {'i32':>12} | {'u32':>12} | {'f32':>14} | varies?")
    print("-" * 60)
    for off in range(0, n - 3, 4):
        i32_vals = [struct.unpack_from("<i", p, off)[0] for p in packets]
        u32_vals = [struct.unpack_from("<I", p, off)[0] for p in packets]
        f32_vals = [struct.unpack_from("<f", p, off)[0] for p in packets]

        varies = len(set(f32_vals)) > 1
        f_first = f32_vals[0]
        f_last = f32_vals[-1]

        marker = "  <-- varies" if varies else ""
        print(f"{off:>6} | {i32_vals[0]:>12} | {u32_vals[0]:>12} | {f_first:>14.4f} | {marker}")


if __name__ == "__main__":
    main()
