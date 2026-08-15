# Contributing

This started as a personal build for one game (Forza Horizon 6) and one media source (Spotify via Windows' media session), so contributions that generalize either of those are especially welcome — e.g. other Forza titles (the telemetry format is shared with FH5/Motorsport), other loopback-capturable media players, or other platforms.

## Before you start

- Check [docs/OPEN_QUESTIONS.md](docs/OPEN_QUESTIONS.md) and [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) — they track known gaps and in-progress work, so you don't duplicate effort.
- Read [docs/DECISIONS.md](docs/DECISIONS.md) before changing DSP tuning, telemetry offsets, or the Spotify integration — it has the reasoning (and sourcing, for the DSP presets) behind choices that might otherwise look arbitrary, plus real footguns already hit once (e.g. Spotify's rate-limit behavior).

## Development setup

```bash
pip install -r requirements.txt
python main.py --input "CABLE Output" --output "<your device>" --mock
```

`--mock` runs the full pipeline against a simulated telemetry sender, so you can develop without Forza Horizon 6 running.

Useful dev tooling in `scripts/`:
- `scripts/preview_overlay.py` — renders the overlay to a PNG with fake data, for checking layout changes without launching the game (`--tab`, `--width`, `--locked` flags).
- `scripts/capture_raw_telemetry.py` / `scripts/capture_timed.py` — dump raw UDP telemetry packets to disk for offline analysis, if you're touching `telemetry/parser.py`.

## Pull requests

- Keep changes scoped — a DSP tuning tweak and a UI redesign should be separate PRs.
- If you change telemetry offsets or DSP parameters, note what you validated it against (mock sender vs. real gameplay) in the PR description.
- No test suite gate currently exists beyond `tests/` — run what's there (`pytest`) and describe any manual verification (e.g. "smoke-tested with `--mock`", "verified against real FH6 telemetry").
