"""Renders the overlay to a PNG with fake data, so layout can be checked
without launching the whole audio/telemetry pipeline or the game.

Usage:
    python scripts/preview_overlay.py [--tab home|now_playing] [--out preview.png]
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from overlay.window import OverlayBridge, OverlayWindow  # noqa: E402


@dataclass
class FakePlaylist:
    id: str
    name: str
    owner: str
    uri: str


def _swatch(color: QColor, size: int = 300) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(color)
    return pm


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tab", default="now_playing", choices=["home", "now_playing"])
    parser.add_argument("--out", default="scripts/preview.png")
    parser.add_argument("--width", type=int, help="render at this panel width (checks uniform scaling)")
    parser.add_argument("--locked", action="store_true", help="render with placement locked")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    bridge = OverlayBridge()
    window = OverlayWindow(bridge)

    window._track = {
        "active": True,
        "title": "Stereo Love x One More Time",
        "artist": "Luke Muzzic",
        "position_s": 137.0,
        "duration_s": 256.0,
        "is_playing": True,
    }
    window._art_pixmap = _swatch(QColor(120, 80, 200))

    names = [
        ("House", "Emil"), ("$an$ money peace", "Emil"), ("Pooke", "Spotify"),
        ("Vibes", "Emil"), ("Inna BMW", "Emil"), ("CLS 63s AMG", "Emil"),
        ("INNA 2000s", "Emil"), ("Daily Mix 1", "Spotify"), ("Discover Weekly", "Spotify"),
        ("Late Night", "Emil"),
    ]
    colors = [
        QColor(200, 60, 60), QColor(210, 160, 40), QColor(60, 160, 210), QColor(150, 60, 190),
        QColor(60, 190, 120), QColor(190, 90, 140), QColor(90, 110, 220), QColor(220, 120, 60),
        QColor(70, 200, 190), QColor(160, 170, 70),
    ]
    window._playlists = [FakePlaylist(f"id{i}", n, o, f"spotify:playlist:{i}") for i, (n, o) in enumerate(names)]
    window._playlist_pixmaps = {f"id{i}": _swatch(colors[i]) for i in range(len(names))}
    window._state = "driving"
    window._tab = args.tab
    window._locked = args.locked
    if args.width:
        window._apply_width(args.width)

    # Checkerboard behind the card so the rounded translucent edges are visible.
    canvas = QPixmap(window.width() + 40, window.height() + 40)
    canvas.fill(QColor(70, 90, 110))
    painter = QPainter(canvas)
    for y in range(0, canvas.height(), 20):
        for x in range(0, canvas.width(), 20):
            if (x // 20 + y // 20) % 2 == 0:
                painter.fillRect(x, y, 20, 20, QColor(90, 110, 130))

    window.show()
    app.processEvents()
    painter.drawPixmap(20, 20, window.grab())
    painter.end()

    canvas.save(args.out)
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
