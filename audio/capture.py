"""Captures background music audio via system loopback for DSP processing.

Capture uses a legacy loopback-recording device -- "Stereo Mix" or similar --
found by name. This has a real, confirmed limitation: it only mirrors the
Realtek analog output path. If the system's default output is a Bluetooth
device (headphones, earbuds), Stereo Mix hears nothing, because Bluetooth
audio never touches that analog path.

True WASAPI loopback (opening an output device directly in a capture mode
that would follow the audio regardless of Bluetooth/USB/analog) was tried and
does NOT work with this project's `sounddevice` dependency: `sounddevice`
wraps stock PortAudio, and neither the installed version nor the latest
release (0.5.5) exposes a loopback flag on `WasapiSettings` -- attempting it
raises `PortAudioError: Invalid number of channels`. Genuine WASAPI loopback
needs a different stack (`pyaudiowpatch`, or the `soundcard` package) or an
OS-level virtual audio cable (e.g. VB-Cable) that appears as a normal
recording device sounddevice can already use. See docs/DECISIONS.md and
docs/OPEN_QUESTIONS.md before changing this approach.

At this stage there is no per-app isolation wired up -- capture grabs
everything going to the loopback device, not just the media player.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Union

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

DEFAULT_SAMPLERATE = 44100
DEFAULT_CHANNELS = 2
DEFAULT_BLOCKSIZE = 1024

# Keywords used to auto-pick a loopback-ish input device when none is specified.
# None of these give per-app isolation, and none of them capture Bluetooth
# output -- they only mirror whatever's going to the analog output path.
_LOOPBACK_KEYWORDS = ("stereo mix", "loopback", "what u hear", "wave out mix")


def _looks_like_loopback(name: str) -> bool:
    lowered = name.lower()
    return any(keyword in lowered for keyword in _LOOPBACK_KEYWORDS)


def list_devices() -> None:
    """Print all audio devices sounddevice can see, in a readable format."""
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    print(f"{'idx':>3}  {'in':>2}/{'out':>3}  {'rate':>7}  host api / name")
    print("-" * 70)
    for idx, dev in enumerate(devices):
        hostapi_name = hostapis[dev["hostapi"]]["name"]
        marker = "  *loopback-like*" if _looks_like_loopback(dev["name"]) else ""
        print(
            f"{idx:>3}  {dev['max_input_channels']:>2}/{dev['max_output_channels']:>3}  "
            f"{dev['default_samplerate']:>7.0f}  {hostapi_name} / {dev['name']}{marker}"
        )


def _resolve_input_device(device: Optional[Union[int, str]]) -> int:
    devices = sd.query_devices()

    if isinstance(device, int):
        if devices[device]["max_input_channels"] < 1:
            raise ValueError(f"device {device} ({devices[device]['name']}) has no input channels")
        return device

    if isinstance(device, str):
        for idx, dev in enumerate(devices):
            if device.lower() in dev["name"].lower() and dev["max_input_channels"] > 0:
                return idx
        raise ValueError(f"no input device matching {device!r} found")

    # No device specified: prefer a loopback-like device so "whatever is
    # playing on this machine" is captured by default. Only covers the analog
    # output path -- see module docstring for the Bluetooth caveat.
    for idx, dev in enumerate(devices):
        if dev["max_input_channels"] > 0 and _looks_like_loopback(dev["name"]):
            return idx

    default_idx = sd.default.device[0]
    if default_idx is None or default_idx < 0:
        raise ValueError("no loopback-like device found and no default input device available")
    logger.warning(
        "No loopback-like device found; falling back to default input device %d "
        "(likely a real microphone, not loopback)",
        default_idx,
    )
    return default_idx


class AudioCapture:
    """Captures audio blocks from an input (ideally loopback) device.

    Usage:
        def on_block(block: np.ndarray) -> None:
            ...  # block.shape == (frames, channels), dtype float32

        with AudioCapture(callback=on_block) as cap:
            time.sleep(10)
    """

    def __init__(
        self,
        device: Optional[Union[int, str]] = None,
        samplerate: int = DEFAULT_SAMPLERATE,
        channels: int = DEFAULT_CHANNELS,
        blocksize: int = DEFAULT_BLOCKSIZE,
        dtype: str = "float32",
        callback: Optional[Callable[[np.ndarray], None]] = None,
    ) -> None:
        self.device_index = _resolve_input_device(device)
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.dtype = dtype
        self.callback = callback

        device_info = sd.query_devices(self.device_index)
        device_name = device_info["name"]
        self.channels = min(channels, device_info["max_input_channels"])
        if self.channels < channels:
            logger.warning(
                "Requested %d input channels but device %d (%s) only supports %d; using %d",
                channels, self.device_index, device_name, self.channels, self.channels,
            )
        logger.info("AudioCapture selected input device %d: %s", self.device_index, device_name)
        print(f"[AudioCapture] using input device {self.device_index}: {device_name}")

        self._stream: Optional[sd.InputStream] = None
        self.blocks_processed = 0
        self.overflow_count = 0

    @property
    def latency(self) -> Optional[float]:
        """Host-reported input latency in seconds, once started."""
        return self._stream.latency if self._stream is not None else None

    def start(self) -> None:
        if self._stream is not None:
            raise RuntimeError("AudioCapture already started")
        self._stream = sd.InputStream(
            device=self.device_index,
            samplerate=self.samplerate,
            channels=self.channels,
            blocksize=self.blocksize,
            dtype=self.dtype,
            callback=self._on_audio,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def _on_audio(self, indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
        if status.input_overflow:
            self.overflow_count += 1
            if self.overflow_count % 50 == 1:
                logger.warning("AudioCapture input overflow (count=%d)", self.overflow_count)

        self.blocks_processed += 1
        if self.callback is not None:
            try:
                self.callback(indata.copy())
            except Exception:
                logger.exception("AudioCapture callback raised an exception")

    def __enter__(self) -> "AudioCapture":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()
