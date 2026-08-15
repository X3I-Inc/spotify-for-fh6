"""Scans a raw capture (from capture_timed.py) for the byte offset that looks
like a gear indicator: a small integer (0-10ish) that changes in a stepwise,
mostly-monotonic pattern correlated with RPM sawtooth (RPM drops sharply =
upshift, byte should increment around the same time).

Usage:
    python scripts/find_gear.py scripts/capture_timed.bin
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
    path = sys.argv[1] if len(sys.argv) > 1 else "scripts/capture_timed.bin"
    packets = load_packets(path)
    if not packets:
        print("No packets loaded.")
        return

    n = len(packets[0])
    rpm = [struct.unpack_from("<f", p, 16)[0] for p in packets]

    # find indices where RPM drops sharply frame-to-frame (candidate upshift moments)
    shift_moments = []
    for i in range(1, len(rpm)):
        if rpm[i] < rpm[i - 1] - 800:  # sharp RPM drop
            shift_moments.append(i)

    print(f"Loaded {len(packets)} packets. Detected {len(shift_moments)} sharp RPM-drop moments (candidate shifts).\n")

    print("Scanning every byte offset for low-cardinality values that change near shift moments...\n")
    best = []
    for off in range(n):
        vals = [p[off] for p in packets]
        distinct_vals = set(vals)
        distinct = len(distinct_vals)
        if distinct < 2 or distinct > 12:
            continue
        if max(vals) > 20:
            continue
        # count how many shift moments have a value change within +/- 3 packets
        hits = 0
        for s in shift_moments:
            lo, hi = max(0, s - 3), min(len(vals), s + 3)
            if len(set(vals[lo:hi])) > 1:
                hits += 1
        score = hits / len(shift_moments) if shift_moments else 0
        best.append((score, off, distinct, vals[0], vals[-1], sorted(distinct_vals)))

    best.sort(reverse=True)
    print(f"{'offset':>6} | {'score':>6} | {'distinct':>8} | {'first':>5} | {'last':>5} | values")
    for score, off, distinct, first, last, dv in best[:30]:
        print(f"{off:>6} | {score:>6.2f} | {distinct:>8} | {first:>5} | {last:>5} | {dv}")


if __name__ == "__main__":
    main()
