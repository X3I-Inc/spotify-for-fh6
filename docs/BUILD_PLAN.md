# Build Plan

| Phase | Description | Status |
|---|---|---|
| 1 | Scaffolding — project structure, stubs, requirements | Done |
| 2 | Telemetry listener — UDP socket, packet parsing, shared state | Done |
| 3 | Audio capture — isolated loopback capture of media player output | Done — capture/playback/pass-through built and validated end-to-end on real hardware via VB-Cable (Spotify → CABLE Input → capture → DSP-free playback), confirmed audibly correct by the user; per-app isolation (game SFX vs. music) still untested pending the FH6 machine |
| 4 | DSP chain — presets + real-time effects chain (pedalboard) | In progress — 3 presets + crossfading DSPChain built, structurally smoke-tested (all 3 states, zero drops) via real capture/playback; awaiting user's manual listening test (`tests/test_dsp_chain.py`) to judge preset character and crossfade smoothness against real music |
| 4.5 | RPM-reactive modulation on the `driving` preset — gain ducking + mud/presence filter relaxation vs. RPM, smoothed to avoid zipper noise | In progress — built and end-to-end smoke-tested live through the real capture/telemetry/playback pipeline (rpm_norm correctly tracks the mock sender's triangle-wave RPM ramp, decays cleanly on state exit, zero audio drops); awaiting user's listening pass via `tests/test_dsp_chain.py` (now telemetry-driven by default) to judge whether the ducking/filter modulation is audible and tasteful, not just structurally correct |
| 5 | Playback — low-latency output of processed audio | Not started |
| 6 | Basic overlay — window rendering, solid/transparent modes | Done — Spotify/Apple-Music-style "now playing" card (album art, title, artist, progress bar) via `overlay/window.py`, not a stats HUD; frameless/translucent/click-through/always-on-top; pinned to a fixed screen corner (Phase 8 handles dashboard-lock positioning). Track info pulled from Windows SMTC via `overlay/now_playing.py` (`winsdk`), no Spotify API keys needed |
| 7 | Integration — wire telemetry → DSP → audio → overlay end to end | Done — `main.py` wires TelemetryListener → DSPChain → AudioCapture/Playback and the overlay together; awaiting a full real-hardware run against live FH6 + the overlay together for final confirmation |
| 8 | Overlay angle / dashboard-lock feature — free-look-input-based angle estimation, center-set calibration | Not started |
| 9 | FH6 real-world validation — test against live game telemetry, fix packet layout / behavior assumptions | In progress — real packet layout captured and fixed (324 bytes, not 311; see docs/DECISIONS.md); per-app audio isolation and DSP/RPM-modulation listening pass against real gameplay still pending |
