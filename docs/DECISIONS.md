# Decisions

Running log of technical decisions and their rationale.

- **Overlay: Python + PySide6.** Chosen over Dear ImGui — PySide6 supports
  translucent, click-through, always-on-top windows and is faster to style well for
  a polished, game-like overlay look.

- **Overlay content: a Spotify/Apple-Music-style "now playing" card, not a
  telemetry stats HUD.** Explicit ask: the overlay should be about immersion
  (album art, track title/artist, progress bar) rather than displaying numbers
  like speed/RPM/gain. The driving/paused/menu state still shows up, but only
  as a small accent-color dot and the progress bar's fill color, not text.
  `overlay/window.py`'s `OverlayWindow` renders the card;
  `overlay/now_playing.py`'s `NowPlayingPoller` feeds it.

- **Overlay renders in a fixed base coordinate space with one uniform
  scale.** All layout constants are authored against a 640x340 design space
  (`BASE_WIDTH`/`BASE_HEIGHT`); `paintEvent` applies a single
  `painter.scale()` and mouse positions are divided by the same factor before
  hit-testing. Dragging the bottom-right grip resizes with the aspect ratio
  locked, so proportions are identical at every size, and size+position are
  persisted together. Chosen over per-element responsive layout because the
  panel should look like one fixed piece of hardware at any scale, not reflow.

- **Overlay icons: pre-rendered pixmaps from the Windows system icon font,
  not `drawText` and not hand-drawn paths.** Icons come from `Segoe Fluent
  Icons` (falling back to `Segoe MDL2 Assets`). Two bugs forced the current
  approach, both found by rendering the widget offscreen and looking at it
  (`scripts/preview_overlay.py`):
  - Drawing icon-font text under an active `painter.scale()` silently renders
    *nothing* -- every glyph vanished at any scale other than exactly 1.0,
    while ordinary text kept rendering fine. Fix: rasterize each glyph once
    into a cached 4x-supersampled `QPixmap` (`_glyph_pixmap`) and draw that,
    since pixmaps transform reliably and stay crisp when scaled up.
  - The glyph constants are Unicode Private Use Area characters, and pasting
    them literally into the source got them silently stripped to empty
    strings. They're written as `\u` escapes for that reason -- don't
    "clean up" the escapes back into literal characters.

- **Overlay never takes focus, so the game doesn't pause on tap.**
  `Qt.WindowDoesNotAcceptFocus` (Win32 `WS_EX_NOACTIVATE`) plus
  `WA_ShowWithoutActivating` means clicking the overlay doesn't activate its
  window and FH6 keeps focus. This only holds if the game runs
  windowed/borderless -- exclusive fullscreen minimizes on any focus change
  regardless.

- **In-Car / Regular sound toggle on the overlay, not just automatic
  telemetry-driven switching.** `DSPChain.set_sound_mode("in_car"|"regular")`
  (dsp/chain.py) lets the user override whether the "driving" preset's cabin
  coloration actually applies while driving, or audio stays untouched
  ("regular"). Deliberately scoped to *only* the driving state: the
  `paused_or_menu` echo effect always applies regardless of this toggle,
  since that's tied to actual game state (paused/in a menu), not a listening
  taste preference the user should be able to override. Rendered as a small
  pill switch (car / headphones icons) on the Now Playing tab, next to the
  transport controls -- calls `chain.set_sound_mode()` directly from the GUI
  thread on tap, safe because that method takes the same lock `process()`
  uses on the audio thread.

- **Spotify volume shown/changed via the Web API (`/me/player` device
  volume), controlled by global Page Up/Down, not the Windows per-app
  mixer.** "Current Spotify volume" means Spotify's own volume (what its
  slider shows / what Spotify Connect reports), not the OS volume-mixer level
  for the Spotify process -- those are different things, and only the former
  is meaningful across Spotify Connect devices. `overlay/hotkeys.py`'s
  `VolumeHotkeys` uses a plain (non-suppressing) pynput `Listener` for
  Page Up/Page Down rather than `GlobalHotKeys`, since these are single
  un-modified keys, not a modifier combo -- and non-suppressing so the
  keypress still reaches FH6 normally in case it's bound to anything there.
  UI updates optimistically on keypress (`SpotifyLoader._adjust_volume`
  emits the new value immediately, before the network call resolves) so it
  feels responsive; a slow background poll (`VOLUME_POLL_INTERVAL_SECONDS =
  15`) catches volume changes made elsewhere (phone, Spotify itself) and
  reconciles any failed set. 15s was chosen deliberately conservative after
  an earlier version's playlist-refetch-per-tab-open tripped Spotify's
  extended rate-limit penalty (~24h block, see the per-app-open playlist
  fetch note above) -- one request per 15s is trivially far under any real
  limit, there's no reason to poll faster for a value that mostly changes
  because the user just pressed a key (which already updates optimistically
  without waiting on a poll).
  `SpotifyClient.get_volume_percent()` is written directly against
  `requests` rather than through the shared `_get()` helper, because
  `/me/player` returns an empty `204` body when there's no active device --
  a normal, common case here (not an error) -- and `_get()` assumes a JSON
  body is always present. Indicator only renders on the "now_playing" tab
  (bottom-right, above the resize grip) since the "home" tab already uses
  that corner for the search/voice FABs; hidden entirely
  (`self._volume_percent is None`) until the first successful poll rather
  than showing a fake starting value.

- **Overlay lock = placement lock, not input lock.** The Ctrl+Alt+L hotkey
  freezes position/size only; taps on tabs, transport controls and playlist
  tiles keep working in both states. An earlier version made locking mean
  click-through (`WA_TransparentForMouseEvents`), which was reverted per
  explicit feedback -- the overlay is meant to be usable at all times, and
  locking is only about not nudging it out of place mid-race.

- **Now-playing data source: Windows SMTC (`winsdk`), not the Spotify Web
  API.** `GlobalSystemMediaTransportControlsSessionManager` reads the same
  OS-level "now playing" session Spotify already publishes for hardware media
  keys and the volume-flyout widget — title, artist, album art (as raw
  image bytes, confirmed working via a real Spotify session), position/
  duration, and play/pause state, all with zero authentication and works with
  any player, not just Spotify. Polled every 1s on a background asyncio
  thread; album art is only re-read (real file I/O) when the track actually
  changes, not every poll. Rejected the Spotify Web API for this — needs
  OAuth app registration/token refresh for a feature the OS already exposes
  for free.

- **Audio capture: WASAPI loopback via `sounddevice`.** Capture is scoped to the
  media player process specifically, so game SFX (engine, tires, environment) stays
  untouched.

- **Device auto-selection: keyword match on "loopback-like" device names.**
  `audio/capture.py` scans `sounddevice.query_devices()` for names containing
  `stereo mix`, `loopback`, `what u hear`, or `wave out mix` and picks the first
  match when no device is explicitly given, rather than defaulting to the system
  input (usually a real mic). On the dev machine this correctly found `Stereo Mix
  (Realtek HD Audio Stereo input)` (WDM-KS, device index 22) among 34 enumerated
  devices. This is a system-wide loopback, not per-app — see the open question
  below and in docs/OPEN_QUESTIONS.md.

- **Pass-through smoke test findings (dev machine, no game/FH6 involved yet).**
  Ran `audio/capture.py` → `audio/playback.py` pass-through for 5s using device 22
  (Stereo Mix, WDM-KS) as input and device 4 (Speakers, Realtek, MME) as output,
  both at the code's default 44100 Hz/1024-frame blocksize:
  - 215 blocks captured (~215 expected for 5s @ 44100Hz/1024), 213 played — no
    input overflow, output underrun, or output overrun logged.
  - PortAudio-reported latency: ~47 ms input + ~186 ms output ≈ 256 ms total
    pipeline latency. That's higher than ideal for a "feels live" overlay effect;
    the output device landed on the MME host API rather than WASAPI, which
    typically buffers more aggressively. Worth explicitly preferring WASAPI-hosted
    devices (or exclusive-mode WASAPI) in device auto-selection once this is
    tuned for real use — not done yet since this phase's goal was just proving
    the pipeline doesn't drop audio.
  - No audible listening test was performed by the agent (no music source was
    playing during the automated run, and there's no way for the agent to hear
    the output) — this still needs a human pass. See docs/OPEN_QUESTIONS.md.

- **Bug found & fixed: hardcoded stereo output crashed on mono devices.** On the
  user's real machine, running pass-through into a Bluetooth headset
  (`Headphones (2- Emil's Buds4 Pro`, 1 output channel over that host API) raised
  `PortAudioError: Invalid number of channels`, because both `AudioCapture` and
  `AudioPlayback` always requested `DEFAULT_CHANNELS = 2` regardless of what the
  chosen device actually supports. Fixed by:
  - Clamping `self.channels` to `min(requested, device's max channels)` at init
    time in both classes, with a logged warning when clamping occurs.
  - Adding `_match_channels()` in `audio/playback.py`, applied in `write()`, to
    reshape any incoming block to the playback device's actual channel count
    (mono downmix via averaging, mono upmix via duplication, trim/pad otherwise)
    — needed because capture and playback devices aren't guaranteed to agree on
    channel count (stereo loopback → mono Bluetooth headset, in this case).
  - Re-verified on the same hardware: Stereo Mix → Buds4 Pro mono output now
    runs the full duration with 215 blocks captured / 204 played, zero
    over/underruns, instead of crashing.

- **Correction: "WASAPI loopback via `sounddevice`" (earlier decision, above) is
  not actually achievable and has been walked back.** Real-machine testing with
  music playing through Bluetooth earbuds showed Stereo Mix capturing silence
  (peak ~0.0006) despite audible playback — Stereo Mix only mirrors the Realtek
  analog path, never Bluetooth. Attempted a real fix using WASAPI loopback
  (opening the Bluetooth output device directly via
  `sd.WasapiSettings(loopback=True)`), which correctly resolved the right device
  (confirmed via `hostapis[wasapi_idx]["default_output_device"]`, not
  `sd.default.device` which maps to the wrong hostapi) but failed at stream-open
  time: `PortAudioError: Invalid number of channels`. Root cause: `sounddevice`
  has no `loopback` parameter on `WasapiSettings` in either the installed version
  or the latest release (0.5.5) — checked both. `audio/capture.py` has been
  reverted to the Stereo-Mix-keyword approach (proven working, just limited to
  analog output). See docs/OPEN_QUESTIONS.md for the real options going forward
  (`soundcard` library, `pyaudiowpatch`, or VB-Cable) — none chosen yet.

- **Decided: VB-Cable for loopback capture, not `soundcard`/`pyaudiowpatch`.**
  Installed VB-Cable, set Spotify's output device to `CABLE Input (VB-Audio
  Virtual Cable)` (the plain pair, not the `CABLE In 16ch` variant VB-Cable also
  installs — that one has no matching `CABLE Output` capture side), and pointed
  `audio/capture.py`'s existing keyword match at `CABLE Output`. Confirmed
  end-to-end on real hardware: live per-second level readout showed real
  non-silent signal (previously flatlined at ~0.0006 with Stereo Mix + Bluetooth
  output), and the user confirmed it was audibly correct through Bluetooth
  headphones. No code changes were needed — the existing `_LOOPBACK_KEYWORDS`
  substring match already covers "CABLE Output" via the "loopback" keyword, and
  device selection by name substring (`--input "CABLE Output"`) already worked.
  Chose this over `soundcard`/`pyaudiowpatch` because it required zero code
  changes and is device-agnostic (works the same regardless of analog/USB/
  Bluetooth output) — the tradeoff is a one-time manual setup step per user
  (install VB-Cable, point the media player's output at `CABLE Input`).

- **DSP: `pedalboard`.** Provides ready-made filters (lowpass, highpass, reverb,
  gain) instead of hand-rolling DSP primitives.

- **DSP preset parameters (`dsp/presets.py`), rewritten to be research-grounded
  instead of guessed.** First pass was reasoned-from-scratch (documented below
  for history); researched real automotive-audio and telephony sources and
  rebuilt `driving` from that, per explicit ask to "make everything up to
  standards" while keeping bandwidth high for the Spotify source. Findings:
  - Road/tire noise concentrates in the **100–500Hz** range, masking exactly
    the band where music clarity lives; real car-audio DSP tuning compensates
    by cutting ~1–2dB of "muddiness" there and boosting ~1–3kHz "presence" so
    vocals/instruments cut through
    ([source](https://audiointensity.com/blogs/dsp/road-noise-compensation)).
  - Below a sealed cabin's resonance frequency (~25–38Hz for real car
    subwoofers), the cabin **physically boosts bass ~12dB/octave** ("cabin
    gain") — but tuned systems correct that boom back down rather than
    leaving it boomy, since it's mostly mud, not useful bass
    ([source](https://www.minidsp.com/applications/car-audio/4-car-audio-philosophy-challenges),
    [source](https://audiojudgement.com/resonant-frequency-car/)).
  - Standard car-audio practice includes a **subsonic filter around 20–40Hz**
    (below what any car speaker reproduces anyway) to remove inaudible/wasted
    energy — not a broad lowpass eating into the audible range.
  - `driving` was rebuilt around these findings, using `pedalboard`'s
    `LowShelfFilter`/`PeakFilter`/`HighShelfFilter` (not used in the first
    pass) instead of a blunt `HighpassFilter`+`LowpassFilter` pair that threw
    away most of the audible spectrum:
    `HighpassFilter(35Hz)` (subsonic only) → `LowShelfFilter(80Hz, -3dB)`
    (tames cabin-gain boom instead of leaving/amplifying it) →
    `PeakFilter(350Hz, -1.5dB)` (road-noise mud cut) →
    `PeakFilter(2000Hz, +2.5dB)` (presence boost, cuts through masking) →
    `HighShelfFilter(14000Hz, -2dB)` (gentle top-end softening only, not a
    cutoff) → `Gain(-1.5dB)` → light `Compressor` for loudness management.
    Full bandwidth is preserved (no hard lowpass below ~14kHz) per the
    explicit ask to keep quality high since the source is Spotify, not a
    cheap radio.
  - `paused_or_menu` switched from an arbitrary single `LowpassFilter(3500Hz)`
    to the **ITU-T G.101 telephone/radio voiceband, 300–3400Hz** — the actual
    industry-standard band behind virtually every "muffled/distant/through-a-
    wall" effect in games and film, not a guessed number. Reverb unchanged
    from the prior tuning pass (see below).
  - `neutral`: unchanged, empty `Pedalboard()`.
  - None of this is re-verified by ear yet — see docs/OPEN_QUESTIONS.md.

- **First-pass preset parameters (superseded by the research-grounded rewrite
  above, kept for history).** Original `driving` used
  `HighpassFilter(120Hz)` + `LowpassFilter(6000Hz)` + `Gain(-3dB)` +
  `Compressor(threshold=-18dB, ratio=3:1, attack=5ms, release=80ms)` — reasoned
  from "small speakers roll off bass/treble" without checking real car-audio
  tuning practice; the 6000Hz lowpass in particular was far too narrow and cut
  most of the audible spectrum, which is why it got reworked. `paused_or_menu`
  originally used a single `LowpassFilter(3500Hz)` before being swapped for
  the verified telephone-band range. Reverb was **tuned down after the user's
  first listening pass called it too wet**: `room_size` 0.8→0.5, `wet_level`
  0.5→0.25, `dry_level` 0.4→0.7, `damping` 0.5→0.6 (tighter tail) — that
  reverb tuning carried forward into the current preset unchanged:
  `Reverb(room_size=0.5, damping=0.6, wet_level=0.25, dry_level=0.7,
  width=1.0)`.

- **RPM-reactive modulation (Phase 4.5) — ranges and design.** A continuous
  modifier layered on the already-built `driving` preset, not a new discrete
  preset — `rpm_norm = clamp(current_engine_rpm / engine_max_rpm, 0, 1)` drives
  three live parameter changes on the existing chain's plugin instances:
  - **Ducking**: `Gain` goes from the base −1.5dB (unchanged from the static
    preset) at idle to −4.0dB total (−1.5 base + −2.5 RPM-driven) near
    redline. −2.5dB sits inside the −2 to −3dB range asked for — picked the
    middle since "how much should music yield to engine noise" is a listening
    call, not a research-derived number.
  - **Mud cut (350Hz `PeakFilter`)**: relaxes from −0.4dB at idle (near-flat —
    little masking to fight in a quiet cabin) up to the original static
    value, −1.5dB, at redline.
  - **Presence boost (2000Hz `PeakFilter`)**: relaxes from +0.8dB at idle up
    to the original static value, +2.5dB, at redline. Both filter ranges use
    the prior static preset value as their "full" (redline) end, so RPM
    modulation is additive to the already-tuned `driving` character rather
    than replacing it — at redline the preset sounds identical to before this
    phase.
  - **Widened 2026-08-14 per live listening feedback ("make the RPM effect
    more").** Original ranges (ducking 0→−2.5dB, mud −0.4→−1.5dB, presence
    0.8→2.5dB) read as too subtle during a live drive. Widened to: ducking
    0→−5.0dB, mud cut 0→−3.0dB (now flat at idle instead of near-flat),
    presence boost 0→+5.0dB (now flat at idle instead of a light touch). Not
    yet re-confirmed by ear at the new values — see docs/OPEN_QUESTIONS.md.
  - **Tremolo/vibration-wobble was deliberately left out of this pass** per
    the task's own risk call-out (easiest of the three to overdo) — tracked
    as a future enhancement in docs/OPEN_QUESTIONS.md, to be evaluated only
    after ducking + filter modulation are confirmed to sound good.
  - **Smoothing**: telemetry ticks arrive discretely (~60Hz), so RPM-driven
    parameter values aren't snapped to their target instantly — `DSPChain`
    keeps a smoothed `rpm_norm` that exponentially approaches the telemetry
    target with a 150ms time constant, recomputed once per audio block
    (`_update_rpm_modulation`), independent of blocksize. This is separate
    from and unrelated to the state-crossfade mechanism: RPM modulation never
    crossfades, it just continuously retunes the driving preset's live plugin
    parameters in place (confirmed mutable at runtime — verified pedalboard
    plugin attributes can be changed between `process()` calls and the output
    changes accordingly).
  - **Only affects `driving`**: modulation always updates the driving
    preset's plugin parameters every block regardless of current state (cheap,
    a few float ops), but since those plugins only sit in the `driving`
    Pedalboard chain, mutating them is inert whenever `paused_or_menu` or
    `neutral` is actually selected/audible — no separate gating needed.
  - **Mock sender updated**: RPM previously oscillated in a narrow sine band
    (1000–4000 of a 7000 max, never getting near idle or redline). Replaced
    with a continuous triangle wave between idle (900) and near-redline
    (6800) over a 4s period, so the modulation has real range to react to
    during manual/automated testing, and the wave is continuous at the
    wrap-around point (no discontinuity/click).
  - End-to-end smoke test (real telemetry → DSPChain → capture/playback, on
    real hardware) confirmed `rpm_norm` correctly tracks the mock sender's
    ramp live (0.48 → 0.91 → 0.62 → 0.19 → ...) and decays cleanly toward 0 on
    transition into `paused_or_menu`, with zero audio drops. Not yet
    confirmed by ear whether the ducking/filter modulation is audible or
    tasteful — see docs/OPEN_QUESTIONS.md.

- **Crossfade design (`dsp/chain.py`).** Equal-power curve (`sin`/`cos` of
  progress × π/2), not linear — linear crossfades dip in perceived loudness
  partway through since two uncorrelated signals summed at 0.5/0.5 amplitude
  are quieter than either alone; equal-power keeps total energy ~constant
  through the blend. Computed per-sample (via `np.arange` inside the block),
  not per-block, so fade smoothness doesn't depend on block size. Default
  200ms, inside the 150–300ms range called out in the original plan — picked
  the midpoint since neither edge (too snappy vs. too syrupy) was validated by
  ear yet.
  - Both the outgoing and incoming preset are kept "hot" (continuously
    `process()`-ed with `reset=False`) for as long as either might be heard,
    so a preset with its own internal state (the `paused_or_menu` reverb's
    tail, in particular) doesn't restart mid-fade and click. A preset that's
    gone idle is flagged for `reset=True` on its next use, clearing stale
    filter/reverb state before it's heard again.
  - Only one crossfade is tracked at a time. Calling `set_state()` again
    mid-fade collapses the in-progress fade into a fresh one starting from
    whatever's currently selected, rather than chaining three-way blends.
    Deliberate simplification for a hand-driven listening test; worth
    revisiting if Phase 7's telemetry-driven switching turns out to flip
    states rapidly enough for this to be audible.

- **Dashboard-lock approach.** Rejected reading game memory for camera angle (risks
  anti-cheat flags, breaks the non-intrusive guarantee). Rejected telemetry-based
  head tracking (FH6 doesn't broadcast free-look camera angle). Chosen instead:
  infer angle from the player's own free-look input deltas (mouse/controller),
  calibrated with a center-set hotkey.
  - **Open question:** whether FH6 free-look is hold-to-look/spring-back or
    persistent — determines whether drift-correction is needed.

- **Telemetry format: FH6 Sled-format UDP packet on port 20127.**

- **Real FH6 capture validated (2026-08-14): packet is 324 bytes, not 311 —
  parser corrected.** Captured raw Data Out packets from the actual FH6 machine
  (Data Out -> 192.168.1.202:20127) using `scripts/capture_raw_telemetry.py`
  and `scripts/capture_timed.py`, and found every packet is 324 bytes, not the
  311 assumed from the FM7-only Sled+Dash layout below. Confirmed against the
  [official FH6 Data Out documentation](https://support.forza.net/hc/en-us/articles/51744149102611-Forza-Horizon-6-Data-Out-Documentation)
  (via a mirrored copy, since the Forza support site blocks direct fetches)
  and cross-checked empirically against live gameplay (accelerating through
  gears, braking):
  - FH6/FH5/FH4 insert a **12-byte "Horizon" block** — `car_group` (i32),
    `smashable_vel_diff` (f32), `smashable_mass` (f32) — right after the
    232-byte Sled section and before the Dash tail, which Forza Motorsport's
    format doesn't have.
  - The 79-byte Dash tail (position, speed, power, torque, tire temps, lap
    info, inputs) is otherwise unchanged in content, just shifted 12 bytes
    later: `speed` moves from offset 244 -> 256, `power` 248 -> 260, etc.
  - Confirmed empirically: `speed` at the new offset 256 tracked measured
    forward velocity (`velocity_z`, offset 40) almost exactly through a real
    acceleration run. `gear` (now at offset 319) stepped 3->4 exactly when RPM
    redlined and speed climbed through a real upshift; `accel` (now at 315)
    dropped to 0 exactly on throttle lift. `position_x/y/z` (now 244-255)
    advanced smoothly frame-to-frame consistent with `speed`. `car_group`
    stayed constant (13) for the whole session and `smashable_vel_diff`/
    `smashable_mass` stayed 0 with no collisions — matches the documented
    field purpose exactly. This matches the official doc's explicit note that
    input bytes live at offsets 315/316/319 (accel/brake/gear).
  - There's also a 1-byte trailing field at offset 323 (always 0 in captures
    so far), added to `FIELD_SPEC` as `_trailing` to keep the struct format
    aligned to the real 324-byte size, not otherwise used.
  - `telemetry/parser.py`'s `FIELD_SPEC`/`TelemetryPacket` updated to match;
    `mock_sender.py` needed no changes since it builds packets dynamically
    from `FIELD_NAMES`.
  - Raw capture scripts used for this, kept for future re-validation: `scripts/capture_raw_telemetry.py`
    (fixed packet count), `scripts/capture_timed.py` (fixed duration),
    `scripts/diff_raw_telemetry.py` and `scripts/find_gear.py` (offset
    scanners), `scripts/live_monitor.py` (real-time offset sanity check
    against the HUD while driving).

- **Telemetry parsing target: Sled + Dash (311 bytes in the original FM7-only
  assumption; see the 324-byte correction above for what's actually implemented).**
  The base "Sled" struct alone doesn't carry `Speed`/`Power`/`Torque`, which the rest
  of the app depends on — those live in the "Dash" extension that FH6/FH5/Forza
  Motorsport append after the Sled fields. `telemetry/parser.py` implements the
  combined 311-byte layout. Struct format string is little-endian, no padding
  (`<...`), built field-by-field in `FIELD_SPEC`. Layout used (offset: field, type):

  **Sled (0–231, 58 fields)**
  ```
    0: is_race_on                 i32        4: timestamp_ms               u32
    8: engine_max_rpm             f32       12: engine_idle_rpm            f32
   16: current_engine_rpm         f32       20: acceleration_x/y/z         f32 x3 (20,24,28)
   32: velocity_x/y/z             f32 x3 (32,36,40)
   44: angular_velocity_x/y/z     f32 x3 (44,48,52)
   56: yaw                        f32       60: pitch                      f32
   64: roll                       f32
   68: norm_suspension_travel_fl/fr/rl/rr   f32 x4 (68,72,76,80)
   84: tire_slip_ratio_fl/fr/rl/rr          f32 x4 (84,88,92,96)
  100: wheel_rotation_speed_fl/fr/rl/rr     f32 x4 (100,104,108,112)
  116: wheel_on_rumble_strip_fl/fr/rl/rr    i32 x4 (116,120,124,128)
  132: wheel_in_puddle_depth_fl/fr/rl/rr    f32 x4 (132,136,140,144)
  148: surface_rumble_fl/fr/rl/rr           f32 x4 (148,152,156,160)
  164: tire_slip_angle_fl/fr/rl/rr          f32 x4 (164,168,172,176)
  180: tire_combined_slip_fl/fr/rl/rr       f32 x4 (180,184,188,192)
  196: suspension_travel_meters_fl/fr/rl/rr f32 x4 (196,200,204,208)
  212: car_ordinal                i32      216: car_class                 i32
  220: car_performance_index      i32      224: drivetrain_type           i32
  228: num_cylinders              i32
  ```

  **Dash extension (232–310, 27 fields)**
  ```
  232: position_x/y/z             f32 x3 (232,236,240)
  244: speed                      f32      248: power                     f32
  252: torque                     f32
  256: tire_temp_fl/fr/rl/rr      f32 x4 (256,260,264,268)
  272: boost                      f32      276: fuel                      f32
  280: distance_traveled          f32
  284: best_lap                   f32      288: last_lap                  f32
  292: current_lap                f32      296: current_race_time         f32
  300: lap_number                 u16
  302: race_position/accel/brake/clutch/hand_brake/gear   u8 x6 (302,303,304,305,306,307)
  308: steer/normalized_driving_line/normalized_ai_brake_difference   s8 x3 (308,309,310)
  ```
  Total: 311 bytes. This is the layout to diff against a real FH6 capture (see
  docs/OPEN_QUESTIONS.md) — field order/offsets are carried over from the
  documented FM7/FH5 format and are unverified against live FH6 output.
