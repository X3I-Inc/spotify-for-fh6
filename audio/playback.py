"""Plays back processed audio to the output device."""

from __future__ import annotations

import logging
import queue
from typing import TYPE_CHECKING, Optional, Union

import numpy as np
import sounddevice as sd

if TYPE_CHECKING:
    from audio.capture import AudioCapture

logger = logging.getLogger(__name__)

DEFAULT_SAMPLERATE = 44100
DEFAULT_CHANNELS = 2
DEFAULT_BLOCKSIZE = 1024
DEFAULT_QUEUE_BLOCKS = 20  # ~20 blocks of headroom before we start dropping


def _resolve_output_device(device: Optional[Union[int, str]]) -> int:
    devices = sd.query_devices()

    if isinstance(device, int):
        if devices[device]["max_output_channels"] < 1:
            raise ValueError(f"device {device} ({devices[device]['name']}) has no output channels")
        return device

    if isinstance(device, str):
        for idx, dev in enumerate(devices):
            if device.lower() in dev["name"].lower() and dev["max_output_channels"] > 0:
                return idx
        raise ValueError(f"no output device matching {device!r} found")

    default_idx = sd.default.device[1]
    if default_idx is None or default_idx < 0:
        raise ValueError("no default output device available")
    return default_idx


def _match_channels(block: np.ndarray, target_channels: int) -> np.ndarray:
    """Reshape a (frames, src_channels) block to (frames, target_channels).

    Downmixes to mono by averaging, upmixes mono by duplicating, and
    trims/pads for any other mismatch. Devices on the same machine aren't
    guaranteed to share a channel count (e.g. stereo loopback capture into
    mono Bluetooth headphones).
    """
    src_channels = block.shape[1]
    if src_channels == target_channels:
        return block
    if target_channels == 1:
        return block.mean(axis=1, keepdims=True).astype(block.dtype)
    if src_channels == 1:
        return np.repeat(block, target_channels, axis=1)
    if src_channels > target_channels:
        return block[:, :target_channels]
    pad = np.repeat(block[:, -1:], target_channels - src_channels, axis=1)
    return np.concatenate([block, pad], axis=1)


class AudioPlayback:
    """Plays audio blocks pushed via write(), on the output stream's own callback thread.

    Blocks are handed off through a bounded queue so the caller (typically an
    AudioCapture callback) never blocks on playback. If playback falls behind, the
    oldest queued block is dropped to make room (overrun); if it runs dry, silence
    is written for that block (underrun). Both are logged (rate-limited), never
    raised -- a glitch shouldn't take down the pipeline.
    """

    def __init__(
        self,
        device: Optional[Union[int, str]] = None,
        samplerate: int = DEFAULT_SAMPLERATE,
        channels: int = DEFAULT_CHANNELS,
        blocksize: int = DEFAULT_BLOCKSIZE,
        dtype: str = "float32",
        max_queued_blocks: int = DEFAULT_QUEUE_BLOCKS,
    ) -> None:
        self.device_index = _resolve_output_device(device)
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.dtype = dtype

        device_info = sd.query_devices(self.device_index)
        device_name = device_info["name"]
        self.channels = min(channels, device_info["max_output_channels"])
        if self.channels < channels:
            logger.warning(
                "Requested %d output channels but device %d (%s) only supports %d; using %d",
                channels, self.device_index, device_name, self.channels, self.channels,
            )
        logger.info("AudioPlayback selected output device %d: %s", self.device_index, device_name)
        print(f"[AudioPlayback] using output device {self.device_index}: {device_name}")

        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=max_queued_blocks)
        self._stream: Optional[sd.OutputStream] = None
        self.blocks_played = 0
        self.underrun_count = 0
        self.overrun_count = 0

    @property
    def latency(self) -> Optional[float]:
        """Host-reported output latency in seconds, once started."""
        return self._stream.latency if self._stream is not None else None

    def start(self) -> None:
        if self._stream is not None:
            raise RuntimeError("AudioPlayback already started")
        self._stream = sd.OutputStream(
            device=self.device_index,
            samplerate=self.samplerate,
            channels=self.channels,
            blocksize=self.blocksize,
            dtype=self.dtype,
            callback=self._on_output,
        )
        self._stream.start()

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def write(self, block: np.ndarray) -> None:
        """Queue a block for playback. Safe to call from the capture/DSP thread.

        Adapts the block's channel count to this device's if they differ (e.g. a
        stereo capture feeding a mono output) rather than failing -- capture and
        playback devices aren't guaranteed to agree on channel count.
        """
        block = _match_channels(block, self.channels)
        try:
            self._queue.put_nowait(block)
        except queue.Full:
            # Drop the oldest block to make room rather than blocking the caller,
            # which is typically a real-time-sensitive capture callback.
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self._queue.put_nowait(block)
            self.overrun_count += 1
            if self.overrun_count % 50 == 1:
                logger.warning("AudioPlayback overrun, dropped a block (count=%d)", self.overrun_count)

    def _on_output(self, outdata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags) -> None:
        if status.output_underflow:
            self.underrun_count += 1
            if self.underrun_count % 50 == 1:
                logger.warning("AudioPlayback underrun (count=%d)", self.underrun_count)

        try:
            block = self._queue.get_nowait()
        except queue.Empty:
            outdata.fill(0)
            return

        if block.shape[0] < frames:
            outdata[: block.shape[0]] = block
            outdata[block.shape[0] :] = 0
        else:
            outdata[:] = block[:frames]

        self.blocks_played += 1

    def __enter__(self) -> "AudioPlayback":
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.stop()


def pass_through(capture: "AudioCapture", playback: "AudioPlayback") -> None:
    """Wire capture directly to playback with no processing, and start both streams.

    For pipeline validation only -- DSP is inserted here in a later phase.
    """
    capture.callback = playback.write
    playback.start()
    capture.start()
