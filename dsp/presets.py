"""Defines in-car radio acoustic presets (EQ curves, filters) for different vehicle/speed states.

Preset parameters are grounded in real automotive-audio and telephony research
rather than guessed values -- sources and reasoning are logged in
docs/DECISIONS.md. Bandwidth is kept wide for `driving` (full-range coloration,
not a narrow-band cut) since the source is high-quality Spotify audio, not a
cheap speaker -- only `paused_or_menu` uses a genuinely narrow band, modeled on
the actual telephone/radio voiceband standard.
"""

from __future__ import annotations

from dataclasses import dataclass

from pedalboard import (
    Compressor,
    Gain,
    HighpassFilter,
    HighShelfFilter,
    LowpassFilter,
    LowShelfFilter,
    Pedalboard,
    PeakFilter,
    Reverb,
)

# --- "driving": in-car cabin acoustics ---
# Full-bandwidth shaping, not a narrow bandpass -- a decent car stereo playing
# a high-quality source reproduces close to the full audible range. Values
# are based on published car-audio DSP tuning practice, not a cheap-speaker
# simulation. See docs/DECISIONS.md for sources.
DRIVING_SUBSONIC_HZ = 35.0  # below any car subwoofer's real f3 (~25-38Hz); removes inaudible/wasted energy only
DRIVING_CABIN_BOOM_HZ = 80.0  # sealed-cabin resonance region where physics naturally over-boosts bass
DRIVING_CABIN_BOOM_GAIN_DB = -3.0  # tuned-down, not left boomy -- real DSPs correct this rather than amplify it
DRIVING_CABIN_BOOM_Q = 0.7
DRIVING_MUD_HZ = 350.0  # road noise concentrates ~100-500Hz and muddies this band
DRIVING_MUD_Q = 1.0
DRIVING_PRESENCE_HZ = 2000.0  # boosted so music cuts through road-noise masking, matching real DSP practice
DRIVING_PRESENCE_Q = 0.8
DRIVING_TOP_END_HZ = 14000.0  # gentle softening only -- mild tweeter directivity/cabin absorption, not a cutoff
DRIVING_TOP_END_GAIN_DB = -2.0
DRIVING_TOP_END_Q = 0.7
DRIVING_GAIN_DB = -1.5  # cabin listening isn't quite as loud/direct as a studio reference, but close
DRIVING_COMPRESSOR_THRESHOLD_DB = -18.0
DRIVING_COMPRESSOR_RATIO = 2.5
DRIVING_COMPRESSOR_ATTACK_MS = 8.0
DRIVING_COMPRESSOR_RELEASE_MS = 100.0

# --- RPM-reactive modulation (Phase 4.5) ---
# A continuous modifier layered on the driving chain above, not a separate
# preset. rpm_norm = clamp(current_engine_rpm / engine_max_rpm, 0, 1). At low
# RPM the cabin is quieter, so the road-noise-driven mud-cut/presence-boost
# relax toward flat; at high RPM (louder cabin, more masking) they apply in
# full. See docs/DECISIONS.md for the reasoning and docs/OPEN_QUESTIONS.md for
# the tremolo/vibration idea deliberately left out of this pass.
DRIVING_MUD_GAIN_DB_RELAXED = -0.4  # near-flat at idle -- less masking to fight, no need to cut hard
DRIVING_MUD_GAIN_DB_FULL = -1.5  # same value as the original static preset, now the redline end of the range
DRIVING_PRESENCE_GAIN_DB_RELAXED = 0.8  # a light touch at idle
DRIVING_PRESENCE_GAIN_DB_FULL = 2.5  # same as the original static preset, now the redline end of the range
DRIVING_RPM_DUCK_DB_AT_IDLE = 0.0  # no extra ducking at low RPM
DRIVING_RPM_DUCK_DB_AT_REDLINE = -2.5  # music yields a bit to engine noise under load, within the -2..-3dB ask

# --- "paused_or_menu": event-speaker-from-outside-the-car echo ---
# Modeled on the ITU-T G.101 telephone/radio voiceband (300-3400Hz) -- the
# real industry standard behind "muffled/distant/through-a-wall" audio, not
# an arbitrary single lowpass. Reverb tuned down from an initial pass that
# was too wet; still large/wet enough to read as open-air echo.
PAUSED_HIGHPASS_HZ = 300.0
PAUSED_LOWPASS_HZ = 3400.0
PAUSED_REVERB_ROOM_SIZE = 0.5
PAUSED_REVERB_DAMPING = 0.6
PAUSED_REVERB_WET_LEVEL = 0.25
PAUSED_REVERB_DRY_LEVEL = 0.7
PAUSED_REVERB_WIDTH = 1.0


@dataclass
class DrivingChainHandles:
    """The driving preset's Pedalboard, plus references to the specific live
    plugin instances RPM modulation needs to retune -- so DSPChain can mutate
    their parameters in place each block instead of rebuilding the chain."""

    board: Pedalboard
    mud_filter: PeakFilter
    presence_filter: PeakFilter
    output_gain: Gain


def make_driving_preset_with_handles() -> DrivingChainHandles:
    """In-car cabin acoustics: full-bandwidth coloration, not a narrow-band cut.

    Returns handles to the mud/presence/gain plugins so RPM modulation
    (dsp/chain.py) can adjust them live; use make_driving_preset() instead if
    you just want the plain chain.
    """
    mud_filter = PeakFilter(
        cutoff_frequency_hz=DRIVING_MUD_HZ,
        gain_db=DRIVING_MUD_GAIN_DB_FULL,
        q=DRIVING_MUD_Q,
    )
    presence_filter = PeakFilter(
        cutoff_frequency_hz=DRIVING_PRESENCE_HZ,
        gain_db=DRIVING_PRESENCE_GAIN_DB_FULL,
        q=DRIVING_PRESENCE_Q,
    )
    output_gain = Gain(gain_db=DRIVING_GAIN_DB)

    board = Pedalboard(
        [
            HighpassFilter(cutoff_frequency_hz=DRIVING_SUBSONIC_HZ),
            LowShelfFilter(
                cutoff_frequency_hz=DRIVING_CABIN_BOOM_HZ,
                gain_db=DRIVING_CABIN_BOOM_GAIN_DB,
                q=DRIVING_CABIN_BOOM_Q,
            ),
            mud_filter,
            presence_filter,
            HighShelfFilter(
                cutoff_frequency_hz=DRIVING_TOP_END_HZ,
                gain_db=DRIVING_TOP_END_GAIN_DB,
                q=DRIVING_TOP_END_Q,
            ),
            output_gain,
            Compressor(
                threshold_db=DRIVING_COMPRESSOR_THRESHOLD_DB,
                ratio=DRIVING_COMPRESSOR_RATIO,
                attack_ms=DRIVING_COMPRESSOR_ATTACK_MS,
                release_ms=DRIVING_COMPRESSOR_RELEASE_MS,
            ),
        ]
    )
    return DrivingChainHandles(
        board=board,
        mud_filter=mud_filter,
        presence_filter=presence_filter,
        output_gain=output_gain,
    )


def make_driving_preset() -> Pedalboard:
    """In-car cabin acoustics chain alone, without modulation handles."""
    return make_driving_preset_with_handles().board


def compute_rpm_norm(current_engine_rpm: float, engine_max_rpm: float) -> float:
    """Normalize engine RPM to 0..1, clamped. 0 if engine_max_rpm is non-positive."""
    if engine_max_rpm <= 0:
        return 0.0
    return max(0.0, min(1.0, current_engine_rpm / engine_max_rpm))


def make_paused_or_menu_preset() -> Pedalboard:
    """Event-speaker-from-outside-the-car echo: telephone-band voiceband + a big reverb."""
    return Pedalboard(
        [
            HighpassFilter(cutoff_frequency_hz=PAUSED_HIGHPASS_HZ),
            LowpassFilter(cutoff_frequency_hz=PAUSED_LOWPASS_HZ),
            Reverb(
                room_size=PAUSED_REVERB_ROOM_SIZE,
                damping=PAUSED_REVERB_DAMPING,
                wet_level=PAUSED_REVERB_WET_LEVEL,
                dry_level=PAUSED_REVERB_DRY_LEVEL,
                width=PAUSED_REVERB_WIDTH,
            ),
        ]
    )


def make_neutral_preset() -> Pedalboard:
    """Unprocessed pass-through -- startup default before any telemetry state is known."""
    return Pedalboard()


DEFAULT_STATE = "neutral"

# Maps telemetry-facing state names to their preset factory. DSPChain builds
# one live instance per entry at construction and keeps reusing it, so each
# preset's internal effect state (reverb tail, compressor envelope) persists
# across blocks while it's actively selected.
PRESET_FACTORIES = {
    "driving": make_driving_preset,
    "paused_or_menu": make_paused_or_menu_preset,
    "neutral": make_neutral_preset,
}
