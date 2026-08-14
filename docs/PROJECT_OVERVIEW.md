# Project Overview

## What This Is

Immersive In-Game Audio Bridge is an open-source tool designed to enhance gameplay
immersion by dynamically transforming background music so that it sounds like a
realistic, in-car radio. Instead of plain background audio playing on top of the
game — disconnected from whatever is actually happening on screen — the tool reads
live information from the game and automatically adjusts the acoustics of the music
in real time. The result is a seamless audio experience where songs muffle when the
player pauses, adjust based on driving perspective, and echo as if coming directly
from the surrounding game environment.

The core idea is simple: your music shouldn't sound the same whether you're tearing
down a highway at 150 mph, idling in a pause menu, or browsing a garage screen. A real
car radio doesn't work that way, and neither should the soundtrack to your driving
game. This tool closes that gap without touching a single game file.

## How It Works

The system is built around a four-step real-time pipeline. Each stage has a single,
well-defined responsibility, and together they form a continuous loop that reacts to
the game as it's being played.

### 1. Game State Tracking

The system continuously listens to live telemetry data broadcast by the game over the
network. It does not read game memory, inject into the game process, or modify any
game files in any way — it simply listens to data the game is already choosing to
send out. From this telemetry stream, the tool tracks key gameplay moments: whether
the player is actively driving on the road, sitting in a menu, or navigating pause
screens. This state is the foundation for everything downstream — it's the signal
that tells the rest of the pipeline what the audio *should* sound like right now.

### 2. Isolated Music Capture

The tool captures audio from the player's preferred media app — whatever they happen
to be using for music, whether that's a streaming app, a local player, or anything
else — while keeping that capture completely separate from the game's own sound
effects. This isolation is important: it ensures the game's engine, tire, and
environmental sounds remain crisp and untouched, while only the music stream is
picked up, processed, and modified. The player's in-game audio experience for
everything except music is left exactly as the game designed it.

### 3. Real-Time Acoustic Filtering

As the in-game status changes, the tool instantly applies mathematical sound
adjustments to the captured music stream. Different gameplay states call for
different acoustic treatments:

- **Menu & Pause States** — the system cuts high frequencies and adds depth, making
  the music sound like it's echoing from event speakers somewhere outside the car,
  rather than playing directly in the player's ears. This mirrors the way you'd
  actually experience music if you stepped out of a car at an event and the stereo
  kept playing behind you.
- **In-Car Acoustics** — while actively driving, the tool dampens specific frequency
  ranges to simulate the acoustics of a stereo playing inside an enclosed car cabin.
  This is the "default" driving sound: slightly boxier, warmer, and less bright than
  a raw studio mix, the way music actually sounds through a car's speakers with the
  windows up.
- **Dynamic Adjustments** — balance and volume shift on the fly so the music
  naturally complements vehicle noise rather than fighting it. As engine noise,
  speed, or ambient sound intensity change, the music adapts alongside it instead of
  staying static.

### 4. Seamless Playback

The modified audio is sent to the player's headphones or speakers with virtually no
noticeable delay. The goal is a genuinely reactive environment — one where the
player's own music library feels like it was designed as an official part of the game
world, not something bolted on top of it.

## Key Advantages

- **100% Non-Intrusive** — the tool works entirely external to the game. It reads
  publicly broadcast telemetry and captures audio at the OS level; it never opens,
  patches, or injects into the game process. This means zero risk of game file
  corruption and zero risk of anti-cheat flags, since nothing about the game itself
  is ever touched.
- **Universal Compatibility** — because the tool captures audio at the system level
  rather than integrating with a specific service, it works with any background
  music or media player the player already uses. There's no dependency on a
  particular streaming platform or library format.
- **Fully Automated** — sound profiles change dynamically based on gameplay with no
  manual adjustment needed. The player doesn't flip settings or presets mid-race —
  the tool listens to the game and reacts on its own.

## Overlay UI (Design Intent)

The tool includes a changeable overlay — it can be rendered solid or transparent —
intended to visually anchor to the car's dashboard, reinforcing the illusion that the
radio is physically part of the vehicle.

A true 3D camera-lock, where the overlay tracks the in-game camera perfectly as the
player looks around, isn't achievable without either reading game memory (which would
break the non-intrusive, anti-cheat-safe guarantee that's central to the project) or
using computer vision to infer camera orientation from the rendered frame (which is
fragile and heavyweight). Instead, the current design infers a look-angle estimate
from the player's own free-look input — the raw mouse or controller deltas the player
generates when looking around — calibrated via a center-set hotkey that lets the
player mark "this is dead ahead." This approach gives the overlay a "mounted to the
dash" feel without ever touching the game process itself, trading perfect precision
for a solution that stays entirely within the non-intrusive design philosophy.

## Target Game

The initial target is **Forza Horizon 6**, via its UDP telemetry broadcast feature
(the "Data Out" option in the game's settings). This is the same packet family used
by Forza Horizon 5 and Forza Motorsport, which means the underlying telemetry parsing
approach should carry over to those titles with minimal changes, even though FH6 is
the primary focus for this build.
