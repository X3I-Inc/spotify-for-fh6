# Open Questions

- **FH6 free-look behavior** — is free-look hold-to-look/spring-back, or persistent
  until re-centered? Confirm once back at the FH6 machine; determines whether
  drift-correction is needed for the dashboard-lock feature.

- **Per-app WASAPI loopback isolation** — **resolved for the "any output device"
  problem, still open for true per-app isolation.** Stereo Mix only mirrors the
  Realtek analog path and misses Bluetooth entirely; `sounddevice` has no real
  WASAPI loopback support (checked installed + latest 0.5.5, no `loopback` param
  on `WasapiSettings`). Decision: require VB-Cable as a setup step. Confirmed
  working end-to-end on real hardware — Spotify output set to `CABLE Input`,
  capture from `CABLE Output`, played back through Bluetooth headphones, live
  level readout showed real signal (not the ~0.0006 noise floor from before) and
  it was audibly correct. This still only isolates "whatever's routed to VB-Cable"
  — if the game were also routed there, its SFX would be captured too. True
  per-app isolation (so game audio and VB-Cable-routed music can coexist without
  the game's audio needing separate handling) is still unvalidated and doesn't
  need solving until FH6 is actually in the picture (Phase 9).

- ~~Real FH6 packet layout validation~~ — **done (2026-08-14).** Captured real
  packets from the FH6 machine: actual size is 324 bytes (FH6 inserts a 12-byte
  Horizon-only block after Sled that FM7 doesn't have), not the 311 originally
  assumed. `telemetry/parser.py` corrected and validated against live driving
  (gear/speed/position/accel all track real gameplay correctly). Full writeup
  in docs/DECISIONS.md. Sled fields (RPM, acceleration, velocity, suspension,
  tire slip, wheel speed, car ordinal/class/PI/drivetrain/cylinders) were
  already correct pre-fix and needed no changes.

- ~~Human listening test still needed for audio pass-through~~ — **done.** User
  confirmed the VB-Cable → capture → playback pipeline sounds correct with real
  Spotify audio playing.

- **WASAPI vs MME/DirectSound for lower latency** — the smoke test's output device
  landed on MME and measured ~186ms of that device's ~256ms total pipeline
  latency. Should try forcing WASAPI-hosted devices in `_resolve_output_device`
  (and possibly exclusive mode) to see how much that latency can be cut before
  it's noticeable during real driving.

- **Per-app isolation validation** — confirmed only that a *system-wide* loopback
  device (Stereo Mix) works end-to-end; still need to validate on the FH6 machine
  whether per-app WASAPI loopback or a virtual audio cable (VB-Cable) is required
  to keep game SFX out of the captured stream, per the item above.

- **DSP preset quality — needs a real listening pass, and ideally a real in-car
  comparison later.** `dsp/presets.py`'s `driving` and `paused_or_menu` values
  are now grounded in real automotive-audio DSP tuning practice and the ITU-T
  G.101 telephone voiceband standard (logged with sources in docs/DECISIONS.md)
  rather than guessed — but "grounded in research" and "sounds right by ear"
  are still two different bars, and only the second one actually matters here.
  `tests/test_dsp_chain.py` is built for exactly this (live 1/2/3 preset
  switching against real music via the VB-Cable path) — the `paused_or_menu`
  reverb got one round of by-ear tuning already (too wet → toned down), but the
  rebuilt `driving` chain (5 EQ stages + compressor) hasn't been listened to at
  all yet. Beyond "does it sound plausible," the real target is "does it sound
  like an actual car cabin / actual event-speaker-from-outside," which can only
  really be judged next to real FH6 gameplay audio — flagging for a comparison
  pass once back at the FH6 machine (Phase 9).

- **Crossfade duration (200ms default) is unvalidated by ear** — chosen as the
  midpoint of the 150–300ms range from the original plan, not because 200ms was
  confirmed to sound right. Worth trying the edges of that range during the
  manual `test_dsp_chain.py` session to see if snappier or slower reads better,
  particularly for the `paused_or_menu` transition where the reverb tail from
  `driving` needs time to settle before it's fully replaced.

- **RPM-reactive modulation (Phase 4.5) — needs a listening pass.** Widened
  2026-08-14 per live listening feedback that the original range was too
  subtle: ducking is now 0 → −5.0dB at redline (was −2.5), mud cut 0 → −3.0dB
  (was −0.4 → −1.5), presence boost 0 → +5.0dB (was 0.8 → 2.5) — see
  docs/DECISIONS.md. Structurally verified end-to-end (real telemetry driving
  real DSP parameters live, zero audio drops); the widened values themselves
  still need a fresh by-ear pass to confirm they read as "more noticeable" and
  not overdone. Things to listen for: does the wider ducking start to sound
  like the music is dropping out rather than yielding; does the 150ms
  smoothing time constant still feel responsive enough now that the swings are
  bigger, or does it start to sound like audible zipper/stepping.

- **Tremolo/vibration-wobble modulation — deliberately not built this pass.**
  Explicitly called out as the riskiest/easiest-to-overdo of the three RPM
  modulation options (ducking, filter relaxation, tremolo), so it was left out
  until the simpler two are confirmed to sound good by ear. The idea: a subtle
  amplitude or pitch wobble synced to RPM (like engine vibration bleeding into
  the cabin/stereo), which could easily read as seasick/broken if overdone
  rather than as an in-car detail. Revisit only after ducking + filter
  modulation get a real listening pass.
