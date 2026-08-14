# Architecture

## Module Breakdown

- **`telemetry/`** — Listens for and parses the game's UDP telemetry broadcast into
  structured packets. Also includes a mock sender for generating fake telemetry
  during development, so the rest of the pipeline can be built and tested without
  the game running.
- **`audio/`** — Handles capture of the background music stream (isolated from game
  SFX) and playback of the processed audio to the output device.
- **`dsp/`** — Owns the acoustic presets (menu/pause echo, in-car dampening, dynamic
  balance) and the effects chain that applies them to the captured audio in real
  time based on current game state.
- **`overlay/`** — Renders the on-screen overlay window, including the
  dashboard-anchored visual and (eventually) the look-angle estimation used to keep
  it feeling mounted to the car.
- **`tests/`** — Test suite for the above modules.

## Data Flow

```
UDP telemetry → TelemetryPacket → shared state object → DSP preset switch → audio chain → playback
                                          ↑
                                     overlay reads
                                     the same state
```

1. The game broadcasts UDP telemetry.
2. `telemetry/parser.py` decodes each packet into a structured `TelemetryPacket`.
3. Relevant fields are written into a shared state object representing "what's
   happening in the game right now" (driving, paused, in menu, speed, etc.).
4. The DSP layer watches the shared state and switches presets / adjusts the audio
   chain accordingly.
5. The audio chain processes the captured music stream and sends it to playback.
6. The overlay independently reads the same shared state (game status, look-angle
   estimate) to update what's drawn on screen.

## Shared State as the Integration Point

The shared state object is the single point where telemetry, DSP, and overlay meet.
Telemetry is the only writer; DSP and overlay are both readers. This keeps the three
subsystems decoupled from each other — neither DSP nor overlay needs to know
anything about UDP packets or parsing, and telemetry doesn't need to know anything
about audio processing or rendering. Each module only needs to agree on the shape of
the shared state.
