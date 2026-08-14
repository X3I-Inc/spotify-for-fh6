"""Manual/interactive pass-through pipeline test: capture -> playback, no DSP.

Lists devices, lets you pick input/output (by index or name substring), runs
pass-through for a few seconds, and prints diagnostics (blocks processed,
under/overrun counts, approximate latency, and measured signal level). This is
a listening test -- play some music into the chosen input device and confirm
you hear it out the chosen output device with minimal added delay and no
dropouts. Block counts and zero under/overruns only prove the pipeline didn't
error -- they'd look identical for dead silence -- so this also tracks the
actual peak/RMS amplitude of what was captured, printed live once per second
and summarized at the end, as concrete evidence real audio (not silence)
passed through.

Interactive:
    python -m tests.test_audio_passthrough

Non-interactive:
    python -m tests.test_audio_passthrough --input "Stereo Mix" --output "Speakers" --duration 10
"""

from __future__ import annotations

import argparse
import time
from typing import Optional, Union

import numpy as np

from audio.capture import AudioCapture, list_devices
from audio.playback import AudioPlayback

DEFAULT_DURATION = 10.0
SILENCE_THRESHOLD = 0.001  # peak amplitude below this is treated as "no real signal"


def _parse_device_arg(value: Optional[str]) -> Optional[Union[int, str]]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="input device index or name substring")
    parser.add_argument("--output", help="output device index or name substring")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION)
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

    # Track actual signal level alongside the pass-through, so "it ran with no
    # errors" and "real audio actually flowed through" are visibly different
    # things -- both would otherwise report identical block counts.
    stats = {"peak": 0.0, "interval_peak": 0.0, "sum_sq": 0.0, "samples": 0}

    def on_block(block: np.ndarray) -> None:
        if block.size:
            level = float(np.abs(block).max())
            stats["peak"] = max(stats["peak"], level)
            stats["interval_peak"] = max(stats["interval_peak"], level)
            stats["sum_sq"] += float(np.sum(block.astype(np.float64) ** 2))
            stats["samples"] += block.size
        playback.write(block)

    capture.callback = on_block

    print(
        f"\nStarting pass-through for {args.duration:.0f}s -- "
        "play some music into the input device now.\n"
    )
    playback.start()
    capture.start()

    cap_latency = capture.latency
    pb_latency = playback.latency

    try:
        elapsed = 0.0
        step = 1.0
        while elapsed < args.duration:
            time.sleep(min(step, args.duration - elapsed))
            elapsed += step
            print(f"  t={elapsed:4.1f}s  input level (last 1s peak): {stats['interval_peak']:.4f}")
            stats["interval_peak"] = 0.0
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        capture.stop()
        playback.stop()

    expected_blocks = args.duration * capture.samplerate / capture.blocksize
    queue_latency = capture.blocksize / capture.samplerate  # one block of queuing headroom
    total_latency = (cap_latency or 0.0) + (pb_latency or 0.0) + queue_latency
    rms = (stats["sum_sq"] / stats["samples"]) ** 0.5 if stats["samples"] else 0.0

    print("\n--- Diagnostics ---")
    print(f"Blocks captured:        {capture.blocks_processed} (~{expected_blocks:.0f} expected)")
    print(f"Blocks played:          {playback.blocks_played}")
    print(f"Input overflow count:   {capture.overflow_count}")
    print(f"Output underrun count:  {playback.underrun_count}")
    print(f"Output overrun count:   {playback.overrun_count}")
    print(f"Reported input latency:  {(cap_latency or 0.0) * 1000:.1f} ms")
    print(f"Reported output latency: {(pb_latency or 0.0) * 1000:.1f} ms")
    print(f"Approx. total pipeline latency: {total_latency * 1000:.1f} ms")
    print(f"Peak input level (whole run): {stats['peak']:.4f}  (0.0-1.0 scale; silence ~= 0.0000)")
    print(f"RMS input level (whole run):  {rms:.4f}")

    if capture.overflow_count or playback.underrun_count or playback.overrun_count:
        print("\nSome drops/glitches were logged above -- check if they were audible.")
    else:
        print("No drops detected.")

    if stats["peak"] < SILENCE_THRESHOLD:
        print(
            "\nWARNING: captured audio looks like silence (peak "
            f"{stats['peak']:.4f} < {SILENCE_THRESHOLD}). Block counts alone don't "
            "prove real audio passed through -- check the input device and that "
            "something was actually playing."
        )
    else:
        print(
            "\nNon-zero signal was captured and pushed through playback -- this is "
            "concrete evidence real audio (not silence) flowed through the pipeline."
        )


if __name__ == "__main__":
    main()
