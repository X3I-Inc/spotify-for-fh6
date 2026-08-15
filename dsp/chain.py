"""Builds and applies the real-time audio effects chain driven by live telemetry."""

from __future__ import annotations

import logging
import math
import threading
from typing import Optional

import numpy as np
from pedalboard import Pedalboard

from dsp.presets import (
    DEFAULT_STATE,
    DRIVING_GAIN_DB,
    DRIVING_MUD_GAIN_DB_FULL,
    DRIVING_MUD_GAIN_DB_RELAXED,
    DRIVING_PRESENCE_GAIN_DB_FULL,
    DRIVING_PRESENCE_GAIN_DB_RELAXED,
    DRIVING_RPM_DUCK_DB_AT_IDLE,
    DRIVING_RPM_DUCK_DB_AT_REDLINE,
    PRESET_FACTORIES,
    make_driving_preset_with_handles,
)

logger = logging.getLogger(__name__)

DEFAULT_CROSSFADE_MS = 200.0

# Time constant for smoothing RPM-driven parameter changes. Telemetry arrives
# in discrete ticks (~60Hz from FH6/mock_sender), but snapping EQ gain/filter
# values instantly on each tick can "zipper" (audible stepping) if RPM is
# changing quickly. Smoothing at the audio block rate instead spreads each
# change over a few blocks.
RPM_SMOOTHING_MS = 150.0


class DSPChain:
    """Applies a telemetry-state-selected pedalboard chain to audio blocks.

    Presets are built once (via dsp.presets factories) and reused for the
    lifetime of the chain, so each preset's internal effect state (reverb
    tail, compressor envelope) persists across blocks while it's actively in
    use, rather than being rebuilt from scratch on every call.

    Switching states crossfades between the outgoing and incoming preset's
    output over `crossfade_ms`, using an equal-power curve computed
    per-sample (not per-block) so the fade is smooth regardless of block
    size. Only one crossfade is tracked at a time: calling set_state() again
    mid-fade collapses the in-progress fade and starts a fresh one from
    whatever preset is currently selected -- acceptable for a manually-driven
    listening test where rapid double-switches aren't the normal case; a
    telemetry-driven Phase 7 integration would want to reconsider this if
    state flips rapidly in practice.

    RPM-reactive modulation (Phase 4.5) is layered on top of the "driving"
    preset only -- it's a continuous parameter update (gain ducking + mud/
    presence filter relaxation, see dsp/presets.py), not a crossfaded switch,
    and has no effect in any other state. update_telemetry() is the combined
    entry point (state + rpm_norm) meant for live telemetry; set_state() is
    still available on its own for manual/keyboard-driven testing.
    """

    def __init__(
        self,
        samplerate: int,
        crossfade_ms: float = DEFAULT_CROSSFADE_MS,
        initial_state: str = DEFAULT_STATE,
    ) -> None:
        if initial_state not in PRESET_FACTORIES:
            raise ValueError(f"unknown initial state {initial_state!r}; expected one of {sorted(PRESET_FACTORIES)}")

        self.samplerate = samplerate
        self.crossfade_frames = max(1, int(samplerate * crossfade_ms / 1000.0))
        self._rpm_smoothing_frames = max(1, int(samplerate * RPM_SMOOTHING_MS / 1000.0))

        # Build "driving" via the handles-returning factory so RPM modulation
        # can mutate its mud/presence/gain plugins in place; other presets
        # don't need modulation so the plain factory is fine for them.
        self._driving_handles = make_driving_preset_with_handles()
        self._presets: dict[str, Pedalboard] = {"driving": self._driving_handles.board}
        for name, factory in PRESET_FACTORIES.items():
            if name != "driving":
                self._presets[name] = factory()

        # Each preset needs reset=True on process() the first time it's used
        # after being (re)activated -- both on first-ever use and after being
        # idle -- so stale filter/reverb state doesn't bleed into unrelated
        # audio once it's selected again.
        self._needs_reset = {name: True for name in self._presets}

        self.current_state = initial_state
        self._fade_from_state: Optional[str] = None
        self._fade_elapsed = 0

        self._rpm_norm_target = 0.0
        self._rpm_norm_smoothed = 0.0

        # User-facing override (overlay toggle) for whether "driving" should
        # actually apply the in-car cabin coloration, or leave audio
        # untouched (regular/normal sound) even while actively driving.
        # Doesn't affect "paused_or_menu" -- that echo effect always applies
        # regardless of this toggle, since it's driven by game state, not a
        # taste preference.
        self._sound_mode = "in_car"

        self._lock = threading.Lock()

    def set_sound_mode(self, mode: str) -> None:
        """Switch between "in_car" (apply the driving cabin-acoustics preset)
        and "regular" (leave audio untouched even while driving).

        Safe to call from a different thread than process() (e.g. the GUI
        thread reacting to an overlay tap).
        """
        if mode not in ("in_car", "regular"):
            raise ValueError(f"unknown sound mode {mode!r}; expected 'in_car' or 'regular'")
        with self._lock:
            self._sound_mode = mode
        logger.info("DSPChain sound mode -> %r", mode)

    def set_state(self, state: str) -> None:
        """Switch to a new preset, crossfading from whatever's currently active.

        Safe to call from a different thread than process() (e.g. a keyboard
        listener thread while process() runs on the audio callback thread).
        """
        if state not in self._presets:
            raise ValueError(f"unknown state {state!r}; expected one of {sorted(self._presets)}")

        with self._lock:
            if state == self.current_state:
                return  # already there, or already fading toward it

            self._needs_reset[self.current_state] = True  # about to go idle
            self._fade_from_state = self.current_state
            self._fade_elapsed = 0
            self.current_state = state
            logger.info("DSPChain crossfading %r -> %r over %d frames", self._fade_from_state, state, self.crossfade_frames)

    def update_telemetry(self, state: str, rpm_norm: float) -> None:
        """Apply a live telemetry tick: switch state (if needed) and set the RPM
        modulation target. RPM modulation only has an audible effect while
        `state` (post any crossfade) is "driving" -- it's harmless to call this
        every tick regardless of state, since the modulated plugins simply
        aren't in the signal path otherwise.

        Safe to call from a different thread than process() (e.g. the
        telemetry listener thread).
        """
        with self._lock:
            sound_mode = self._sound_mode
        effective_state = "neutral" if (state == "driving" and sound_mode == "regular") else state

        self.set_state(effective_state)
        with self._lock:
            self._rpm_norm_target = max(0.0, min(1.0, rpm_norm))

    def _run(self, name: str, block: np.ndarray) -> np.ndarray:
        preset = self._presets[name]
        reset = self._needs_reset[name]
        out = preset.process(block, sample_rate=self.samplerate, reset=reset)
        self._needs_reset[name] = False
        return out

    def _update_rpm_modulation(self, frames: int) -> float:
        """Advance the smoothed RPM value by one block and retune the driving
        preset's mud/presence/gain plugins to match. Returns the smoothed
        rpm_norm used, for diagnostics/display."""
        with self._lock:
            target = self._rpm_norm_target
            smoothed = self._rpm_norm_smoothed

        # One-pole exponential smoothing towards the target, scaled to this
        # block's duration so the time constant is independent of blocksize.
        alpha = 1.0 - math.exp(-frames / self._rpm_smoothing_frames)
        smoothed += (target - smoothed) * alpha

        with self._lock:
            self._rpm_norm_smoothed = smoothed

        mud_gain = DRIVING_MUD_GAIN_DB_RELAXED + (DRIVING_MUD_GAIN_DB_FULL - DRIVING_MUD_GAIN_DB_RELAXED) * smoothed
        presence_gain = (
            DRIVING_PRESENCE_GAIN_DB_RELAXED
            + (DRIVING_PRESENCE_GAIN_DB_FULL - DRIVING_PRESENCE_GAIN_DB_RELAXED) * smoothed
        )
        duck_db = DRIVING_RPM_DUCK_DB_AT_IDLE + (DRIVING_RPM_DUCK_DB_AT_REDLINE - DRIVING_RPM_DUCK_DB_AT_IDLE) * smoothed

        self._driving_handles.mud_filter.gain_db = mud_gain
        self._driving_handles.presence_filter.gain_db = presence_gain
        self._driving_handles.output_gain.gain_db = DRIVING_GAIN_DB + duck_db

        return smoothed

    def get_driving_modulation_state(self) -> dict:
        """Current RPM-modulated parameter values, for display/diagnostics."""
        return {
            "rpm_norm": self._rpm_norm_smoothed,
            "gain_db": self._driving_handles.output_gain.gain_db,
            "mud_gain_db": self._driving_handles.mud_filter.gain_db,
            "presence_gain_db": self._driving_handles.presence_filter.gain_db,
        }

    def process(self, block: np.ndarray) -> np.ndarray:
        """Process one audio block (frames, channels) through the active preset(s)."""
        self._update_rpm_modulation(block.shape[0])

        with self._lock:
            current_state = self.current_state
            fade_from_state = self._fade_from_state
            fade_elapsed = self._fade_elapsed

        if fade_from_state is None:
            return self._run(current_state, block)

        frames = block.shape[0]
        old_out = self._run(fade_from_state, block)
        new_out = self._run(current_state, block)

        progress = (fade_elapsed + np.arange(frames, dtype=np.float64)) / self.crossfade_frames
        progress = np.clip(progress, 0.0, 1.0)
        # Equal-power curve: constant perceived loudness through the blend,
        # unlike a plain linear crossfade which dips in the middle.
        weight_new = np.sin(progress * (math.pi / 2.0)).astype(np.float32)
        weight_old = np.cos(progress * (math.pi / 2.0)).astype(np.float32)
        if block.ndim > 1:
            weight_new = weight_new[:, None]
            weight_old = weight_old[:, None]

        blended = (old_out * weight_old + new_out * weight_new).astype(block.dtype)

        with self._lock:
            # Only advance/clear if this is still the fade we snapshotted --
            # a concurrent set_state() may have started a newer one already.
            if self._fade_from_state == fade_from_state:
                self._fade_elapsed = fade_elapsed + frames
                if self._fade_elapsed >= self.crossfade_frames:
                    self._fade_from_state = None
                    self._fade_elapsed = 0

        return blended
