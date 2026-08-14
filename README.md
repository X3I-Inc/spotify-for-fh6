# Immersive In-Game Audio Bridge

A non-intrusive, telemetry-driven audio DSP overlay for Forza Horizon 6. It reads live game
telemetry over UDP broadcast and dynamically applies audio effects to your background music
(captured via audio loopback) to simulate in-car radio acoustics — muffled highs at low speed,
cabin resonance, road noise bleed, etc. No game files are modified.

## Modules

- `telemetry/parser.py` — parses raw FH6 UDP telemetry packets into structured data.
- `telemetry/mock_sender.py` — sends simulated telemetry packets over UDP for local testing without the game.
- `audio/capture.py` — captures background music audio via system loopback for DSP processing.
- `audio/playback.py` — plays back processed audio to the output device.
- `dsp/presets.py` — defines in-car radio acoustic presets (EQ curves, filters) for different vehicle/speed states.
- `dsp/chain.py` — builds and applies the real-time audio effects chain driven by live telemetry.
- `overlay/window.py` — renders the non-intrusive overlay UI showing active audio effects and telemetry state.
