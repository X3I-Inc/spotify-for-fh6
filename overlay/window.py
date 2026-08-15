"""Renders the non-intrusive overlay UI: a landscape CarPlay-style Spotify
panel -- proportioned like a real in-dash car screen (wider than tall), so it
reads as a panel mounted in the car rather than a floating app window.

Icons come from `Segoe Fluent Icons`, the icon font Microsoft ships with
Windows (with `Segoe MDL2 Assets` as the fallback on older builds) -- real
system icons rather than hand-drawn shapes.

  - "home" tab: a scrollable grid of playlist tiles (cover art, name, owner),
    with floating search/voice buttons like the real CarPlay Spotify screen
    (currently decorative -- no search/voice backend). Mouse wheel scrolls;
    tapping a tile starts that playlist and switches to "now_playing".
  - "now_playing" tab: album art on the left, with title, artist, progress bar
    and transport controls (previous / play-pause / next) in a column on the
    right; the whole row is centered as one block.

DSP/telemetry state only shows up as a subtle accent-color dot (green while
driving, amber paused/menu), not numbers -- the goal is immersion, not a
stats readout.

Three behaviors matter for using this while actually driving in-game:

  - **It never takes focus.** `Qt.WindowDoesNotAcceptFocus` (which maps to
    Win32's `WS_EX_NOACTIVATE`) plus `WA_ShowWithoutActivating` means clicking
    the overlay does not activate its window, so FH6 keeps focus and doesn't
    auto-pause when you tap a control. Note this relies on the game running
    windowed/borderless -- exclusive-fullscreen still minimizes on any focus
    change, which no overlay can avoid.
  - **Lock only freezes placement, never interaction.** The global hotkey
    (overlay/hotkeys.py, default Ctrl+Alt+L) toggles whether the panel can be
    dragged and resized. Taps on tabs, transport controls and playlist tiles
    keep working in both states -- locking is there to stop you nudging the
    panel out of place mid-race, not to disable the UI.
  - **It scales as one piece.** All drawing and hit-testing happen in a fixed
    BASE_WIDTH x BASE_HEIGHT coordinate space, with a single uniform
    `painter.scale()` applied at paint time and the inverse applied to mouse
    positions. Dragging the bottom-right grip (when unlocked) resizes the
    panel with its aspect ratio locked, so every element keeps its exact
    proportions at any size. Size and position are both saved to disk and
    restored on the next run.

Dashboard-lock / look-angle-based positioning (Phase 8) isn't implemented
yet -- this only supports a fixed, user-placed position.

Audio capture, telemetry, and Spotify API calls all run on background
threads, but Qt widgets can only be touched from the thread that owns the
QApplication event loop. OverlayBridge/NowPlayingBridge/LockToggleBridge/
SpotifyBridge are the thread-safe entry points: background threads emit their
signal, and Qt automatically marshals that across to the GUI thread (a queued
connection is used automatically whenever emitter and receiver live on
different threads).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, QPoint, QRectF, Qt, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontDatabase,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QWidget

logger = logging.getLogger(__name__)

# Design coordinate space. Everything below is authored against these numbers;
# the window can be any size, and a single uniform scale maps between the two.
BASE_WIDTH = 640
BASE_HEIGHT = 290
ASPECT = BASE_WIDTH / BASE_HEIGHT
MIN_WIDTH = 360
MAX_WIDTH = 1280

HEADER_HEIGHT = 54
CONTENT_TOP = HEADER_HEIGHT
CONTENT_HEIGHT = BASE_HEIGHT - HEADER_HEIGHT

LOGO_SIZE = 32
LOGO_MARGIN = 14

OVERLAY_MARGIN = 24  # distance from screen edge for the default position
TAP_MOVEMENT_THRESHOLD_PX = 6  # click-vs-drag distinction
RESIZE_GRIP_SIZE = 26  # bottom-right corner hit zone, in base coords

# Icon box sizes (base coords). Fixed boxes rather than font metrics, since
# icons are drawn as pre-rendered pixmaps -- see _glyph_pixmap().
ICON_TAB = 15
ICON_LOGO = 15
ICON_TRANSPORT = 17
ICON_TRANSPORT_PLAY = 20
ICON_FAB = 16
ICON_LOCK = 12
ICON_GRIP = 11

# --- "now_playing" tab layout (horizontal: art left, info column right) ---
NP_ART_SIZE = 150
NP_ART_MARGIN = 26
NP_INFO_GAP = 26
NP_ICON_SIZE = 28
NP_ICON_GAP = 18

# --- "home" tab layout. Sized so two full rows (art + both text lines) fit
# inside the panel height without the second row's labels being clipped by
# the card's rounded bottom edge. ---
HOME_GRID_TOP = CONTENT_TOP + 6
HOME_GRID_COLS = 6
HOME_TILE_SIZE = 78
HOME_TILE_GAP = 10
HOME_TILE_TEXT_H = 28
HOME_TILE_MARGIN = 16
HOME_GRID_BOTTOM_PAD = 8
FAB_RADIUS = 17

# Segoe Fluent Icons / MDL2 glyph codepoints (each verified rendering on this
# machine before being used here). Written as \u escapes rather than literal
# characters: these live in Unicode's Private Use Area, and pasting them
# directly into source risks them being silently stripped by editors/tooling
# (which happened here -- every constant became an empty string).
GLYPH_HOME = ""
GLYPH_MUSIC = ""
GLYPH_MUSIC_SINGLE = ""
GLYPH_PLAY = ""
GLYPH_PAUSE = ""
GLYPH_PREV = ""
GLYPH_NEXT = ""
GLYPH_SEARCH = ""
GLYPH_MIC = ""
GLYPH_LOCKED = ""
GLYPH_UNLOCKED = ""
GLYPH_RESIZE = ""
GLYPH_CAR = ""
GLYPH_HEADPHONES = ""
GLYPH_VOLUME_MUTE = ""
GLYPH_VOLUME_LOW = ""
GLYPH_VOLUME_MED = ""
GLYPH_VOLUME_HIGH = ""

ICON_SOUND_MODE = 14
SOUND_TOGGLE_WIDTH = 84
SOUND_TOGGLE_GAP = 28  # gap from the transport controls

# Volume indicator: bottom-right of the "now_playing" tab only (the "home"
# tab uses that same corner for the search/voice FABs). Positioned clear of
# the resize grip's hit zone in the very corner.
ICON_VOLUME = 13
VOLUME_BAR_WIDTH = 42
VOLUME_BAR_HEIGHT = 4
VOLUME_ROW_HEIGHT = 14
VOLUME_ROW_BOTTOM_MARGIN = 16  # from BASE_HEIGHT, clear of the resize grip

_STATE_COLORS = {
    "driving": QColor(90, 220, 140),
    "paused_or_menu": QColor(230, 190, 80),
    "neutral": QColor(150, 150, 160),
}
_SPOTIFY_GREEN = QColor(30, 215, 96)
_CARD_BG = QColor(18, 18, 20, 238)

_STATE_FILE = Path(__file__).parent / "overlay_state.json"


def _icon_font_family() -> str:
    """The Windows system icon font, preferring the newer Fluent set and
    falling back to MDL2 on builds that predate it."""
    families = QFontDatabase.families()
    return "Segoe Fluent Icons" if "Segoe Fluent Icons" in families else "Segoe MDL2 Assets"


_GLYPH_CACHE: dict[tuple, QPixmap] = {}
GLYPH_SUPERSAMPLE = 4


def _glyph_pixmap(glyph: str, size: int, color: QColor) -> QPixmap:
    """Renders an icon-font glyph into a cached, supersampled pixmap.

    Drawing icon-font text directly under `painter.scale()` silently produces
    nothing -- verified on this machine: every glyph vanished at any scale
    other than exactly 1.0 while ordinary text kept rendering. Pixmaps
    transform reliably, so glyphs are rasterized once at 4x here (unscaled)
    and then drawn as images, which also keeps them crisp when the panel is
    scaled up.
    """
    key = (glyph, size, color.rgba())
    cached = _GLYPH_CACHE.get(key)
    if cached is not None:
        return cached

    px = max(8, size * GLYPH_SUPERSAMPLE)
    pixmap = QPixmap(px, px)
    pixmap.fill(Qt.transparent)

    font = QFont(_icon_font_family())
    font.setPixelSize(int(px * 0.82))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.TextAntialiasing)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, glyph)
    painter.end()

    _GLYPH_CACHE[key] = pixmap
    return pixmap


def _draw_glyph(painter: QPainter, glyph: str, center_x: float, center_y: float, size: int, color: QColor) -> None:
    """Draws an icon glyph centered on a point, in base coordinates."""
    pixmap = _glyph_pixmap(glyph, size, color)
    target = QRectF(center_x - size / 2, center_y - size / 2, size, size)
    painter.drawPixmap(target, pixmap, QRectF(pixmap.rect()))


def _load_saved_state() -> dict:
    try:
        return json.loads(_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(pos: QPoint, width: int) -> None:
    try:
        _STATE_FILE.write_text(json.dumps({"x": pos.x(), "y": pos.y(), "width": width}))
    except Exception:
        logger.exception("Failed to save overlay state")


def _format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class OverlayBridge(QObject):
    """Thread-safe entry point for pushing telemetry-driven state updates."""

    status_changed = Signal(dict)


class OverlayWindow(QWidget):
    def __init__(
        self,
        bridge: OverlayBridge,
        now_playing_bridge=None,
        lock_bridge=None,
        spotify_bridge=None,
        on_playlist_selected: Optional[Callable[[str], None]] = None,
        on_home_opened: Optional[Callable[[], None]] = None,
        on_play_pause: Optional[Callable[[bool], None]] = None,
        on_next: Optional[Callable[[], None]] = None,
        on_previous: Optional[Callable[[], None]] = None,
        on_sound_mode_changed: Optional[Callable[[str], None]] = None,
        corner: str = "bottom-left",
    ) -> None:
        super().__init__()
        # WindowDoesNotAcceptFocus -> WS_EX_NOACTIVATE on Windows: clicking the
        # overlay never activates it, so the game keeps focus and won't pause.
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setMouseTracking(True)  # so the grip can show a resize cursor on hover

        self._locked = False  # locked freezes placement only -- taps always work
        self._drag_offset: Optional[QPoint] = None
        self._press_pos: Optional[QPoint] = None
        self._resizing = False
        self._resize_start_global: Optional[QPoint] = None
        self._resize_start_width = BASE_WIDTH

        self._tab = "home"  # or "now_playing"
        self._browse_scroll = 0.0
        self._playlists_requested = False

        self._on_playlist_selected = on_playlist_selected
        self._on_home_opened = on_home_opened
        self._on_play_pause = on_play_pause
        self._on_next = on_next
        self._on_previous = on_previous
        self._on_sound_mode_changed = on_sound_mode_changed
        self._sound_mode = "in_car"  # or "regular" -- mirrors DSPChain's own default
        self._volume_percent: Optional[int] = None  # None until the first successful poll

        self._state = "neutral"
        self._track = {
            "active": False,
            "title": "",
            "artist": "",
            "position_s": 0.0,
            "duration_s": 0.0,
            "is_playing": False,
        }
        self._art_pixmap: Optional[QPixmap] = None
        self._last_thumbnail_bytes: Optional[bytes] = None

        self._playlists: list = []  # list[spotify.client.PlaylistSummary]
        self._playlist_pixmaps: dict[str, QPixmap] = {}
        self._playlist_error: Optional[str] = None

        bridge.status_changed.connect(self._on_status_changed)
        if now_playing_bridge is not None:
            now_playing_bridge.track_changed.connect(self._on_track_changed)
        if lock_bridge is not None:
            lock_bridge.toggle_lock.connect(self._toggle_lock)
        if spotify_bridge is not None:
            spotify_bridge.playlists_loaded.connect(self._on_playlists_loaded)
            spotify_bridge.playlist_art_loaded.connect(self._on_playlist_art_loaded)
            spotify_bridge.playback_error.connect(self._on_playback_error)
            spotify_bridge.volume_changed.connect(self._on_volume_changed)

        saved = _load_saved_state()
        width = int(saved.get("width", BASE_WIDTH))
        self._apply_width(width)
        if "x" in saved and "y" in saved:
            self.move(QPoint(int(saved["x"]), int(saved["y"])))
        else:
            self._position_default(corner)

        self._request_playlists_once()

    # --- scaling ---------------------------------------------------------

    @property
    def _scale(self) -> float:
        return self.width() / BASE_WIDTH

    def _to_base(self, pos: QPoint) -> QPoint:
        s = self._scale
        return QPoint(int(pos.x() / s), int(pos.y() / s))

    def _apply_width(self, width: int) -> None:
        width = max(MIN_WIDTH, min(MAX_WIDTH, int(width)))
        self.resize(width, round(width / ASPECT))

    # --- geometry / positioning ---------------------------------------

    def _position_default(self, corner: str) -> None:
        screen = QApplication.primaryScreen().availableGeometry()
        x, y = OVERLAY_MARGIN, OVERLAY_MARGIN
        if "right" in corner:
            x = screen.width() - self.width() - OVERLAY_MARGIN
        if "bottom" in corner:
            y = screen.height() - self.height() - OVERLAY_MARGIN
        self.move(screen.x() + x, screen.y() + y)

    def _set_tab(self, tab: str) -> None:
        self._tab = tab
        if tab == "home":
            self._request_playlists_once()
        self.update()

    def _set_sound_mode(self, mode: str) -> None:
        if mode == self._sound_mode:
            return
        self._sound_mode = mode
        if self._on_sound_mode_changed is not None:
            self._on_sound_mode_changed(mode)
        self.update()

    def _request_playlists_once(self) -> None:
        # Playlists don't change mid-session often enough to justify
        # re-fetching every time the Home tab is opened -- doing that hammered
        # Spotify's rate limit. Fetch once, lazily, on first need.
        if self._playlists_requested:
            return
        self._playlists_requested = True
        if self._on_home_opened is not None:
            self._on_home_opened()

    # --- lock / drag / resize / tap / scroll ------------------------------

    def _toggle_lock(self) -> None:
        self._locked = not self._locked
        logger.info(
            "Overlay placement %s (taps still work either way)",
            "locked" if self._locked else "unlocked -- drag to move, drag the corner grip to resize",
        )
        if self._locked:
            self.unsetCursor()
        self.update()

    def _resize_grip_rect(self) -> QRectF:
        return QRectF(BASE_WIDTH - RESIZE_GRIP_SIZE, BASE_HEIGHT - RESIZE_GRIP_SIZE, RESIZE_GRIP_SIZE, RESIZE_GRIP_SIZE)

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return
        base_pos = self._to_base(event.position().toPoint())
        if not self._locked and self._resize_grip_rect().contains(base_pos):
            self._resizing = True
            self._resize_start_global = event.globalPosition().toPoint()
            self._resize_start_width = self.width()
            return
        self._press_pos = event.globalPosition().toPoint()
        self._drag_offset = self._press_pos - self.pos()

    def mouseMoveEvent(self, event) -> None:
        if self._resizing and self._resize_start_global is not None:
            dx = (event.globalPosition().toPoint() - self._resize_start_global).x()
            self._apply_width(self._resize_start_width + dx)
            return

        if self._drag_offset is not None and not self._locked:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            return

        # Hover feedback: the corner grip is only actionable while unlocked.
        if not self._locked and self._resize_grip_rect().contains(self._to_base(event.position().toPoint())):
            self.setCursor(Qt.SizeFDiagCursor)
        else:
            self.unsetCursor()

    def mouseReleaseEvent(self, event) -> None:
        if self._resizing:
            self._resizing = False
            self._resize_start_global = None
            _save_state(self.pos(), self.width())
            return

        if self._press_pos is None:
            return

        moved = (event.globalPosition().toPoint() - self._press_pos).manhattanLength()
        self._drag_offset = None
        release_base_pos = self._to_base(event.position().toPoint())
        self._press_pos = None

        # Taps are honored whether or not placement is locked -- locking is
        # about not nudging the panel, not about disabling the UI.
        if moved <= TAP_MOVEMENT_THRESHOLD_PX:
            self._handle_tap(release_base_pos)
        elif not self._locked:
            _save_state(self.pos(), self.width())

    def wheelEvent(self, event) -> None:
        if self._tab != "home":
            return
        self._browse_scroll -= event.angleDelta().y() / 2.0
        self._browse_scroll = max(0.0, min(self._browse_scroll, self._browse_max_scroll()))
        self.update()

    def _browse_max_scroll(self) -> float:
        rows = max(1, (len(self._playlists) + HOME_GRID_COLS - 1) // HOME_GRID_COLS)
        row_h = HOME_TILE_SIZE + HOME_TILE_TEXT_H + HOME_TILE_GAP
        content_h = rows * row_h + HOME_GRID_BOTTOM_PAD
        visible_h = BASE_HEIGHT - HOME_GRID_TOP
        return max(0.0, content_h - visible_h)

    def _handle_tap(self, pos: QPoint) -> None:
        if pos.y() < HEADER_HEIGHT:
            for tab_id, rect in self._tab_rects():
                if rect.contains(pos):
                    self._set_tab(tab_id)
                    return
            return

        if self._tab == "now_playing":
            layout = self._now_playing_layout()
            if layout["prev_rect"].contains(pos) and self._on_previous is not None:
                self._on_previous()
            elif layout["play_pause_rect"].contains(pos) and self._on_play_pause is not None:
                self._on_play_pause(bool(self._track.get("is_playing")))
            elif layout["next_rect"].contains(pos) and self._on_next is not None:
                self._on_next()
            elif layout["sound_car_rect"].contains(pos):
                self._set_sound_mode("in_car")
            elif layout["sound_regular_rect"].contains(pos):
                self._set_sound_mode("regular")
            return

        if self._tab == "home":
            adjusted = QPoint(pos.x(), int(pos.y() + self._browse_scroll))
            for playlist, rect in self._playlist_tile_rects():
                if rect.contains(adjusted):
                    if self._on_playlist_selected is not None:
                        self._on_playlist_selected(playlist.uri)
                    self._set_tab("now_playing")
                    return

    # --- header tabs -----------------------------------------------------

    def _tab_specs(self):
        return [("home", GLYPH_HOME, "Home"), ("now_playing", GLYPH_MUSIC, "Now Playing")]

    def _tab_rects(self):
        """Pills sized from real text metrics (plus the icon box + padding) so
        a label never overflows its pill, laid out left to right after the
        logo."""
        text_metrics = QFontMetrics(QFont("Segoe UI", 10, QFont.DemiBold))
        pad_x, icon_gap = 18, 8
        height = HEADER_HEIGHT - 16

        x = LOGO_MARGIN + LOGO_SIZE + 18
        for tab_id, _glyph, label in self._tab_specs():
            width = pad_x * 2 + ICON_TAB + icon_gap + text_metrics.horizontalAdvance(label)
            yield tab_id, QRectF(x, (HEADER_HEIGHT - height) / 2, width, height)
            x += width + 8

    # --- now-playing layout ------------------------------------------------

    def _now_playing_layout(self) -> dict:
        """Single source of truth for the now-playing row geometry -- used by
        both painting and tap hit-testing so they can't drift apart."""
        title_h, artist_h = 26, 22
        gap_after_artist, progress_h, gap_after_progress = 16, 20, 18

        info_height = title_h + artist_h + gap_after_artist + progress_h + gap_after_progress + NP_ICON_SIZE
        row_height = max(NP_ART_SIZE, info_height)
        row_top = CONTENT_TOP + (CONTENT_HEIGHT - row_height) / 2

        art_rect = QRectF(NP_ART_MARGIN, row_top + (row_height - NP_ART_SIZE) / 2, NP_ART_SIZE, NP_ART_SIZE)

        info_x = art_rect.right() + NP_INFO_GAP
        info_width = BASE_WIDTH - info_x - NP_ART_MARGIN
        info_top = row_top + (row_height - info_height) / 2

        title_rect = QRectF(info_x, info_top, info_width, title_h)
        artist_rect = QRectF(info_x, title_rect.bottom(), info_width, artist_h)
        progress_y = artist_rect.bottom() + gap_after_artist
        controls_top = progress_y + progress_h + gap_after_progress

        prev_rect = QRectF(info_x, controls_top, NP_ICON_SIZE, NP_ICON_SIZE)
        play_rect = QRectF(prev_rect.right() + NP_ICON_GAP, controls_top, NP_ICON_SIZE, NP_ICON_SIZE)
        next_rect = QRectF(play_rect.right() + NP_ICON_GAP, controls_top, NP_ICON_SIZE, NP_ICON_SIZE)

        # In-Car / Regular sound toggle, sharing the transport row to the
        # right of Next. Note the resizable window scales this whole layout
        # uniformly rather than reflowing it (see the module docstring) -- the
        # base coordinate space here is fixed, so this always has the same
        # room relative to the transport controls regardless of panel size.
        toggle_x = next_rect.right() + SOUND_TOGGLE_GAP
        toggle_y = controls_top
        sound_car_rect = QRectF(toggle_x, toggle_y, SOUND_TOGGLE_WIDTH / 2, NP_ICON_SIZE)
        sound_regular_rect = QRectF(toggle_x + SOUND_TOGGLE_WIDTH / 2, toggle_y, SOUND_TOGGLE_WIDTH / 2, NP_ICON_SIZE)

        return {
            "art_rect": art_rect,
            "title_rect": title_rect,
            "artist_rect": artist_rect,
            "progress_x": info_x,
            "progress_y": progress_y,
            "progress_width": info_width,
            "prev_rect": prev_rect,
            "play_pause_rect": play_rect,
            "next_rect": next_rect,
            "sound_car_rect": sound_car_rect,
            "sound_regular_rect": sound_regular_rect,
        }

    def _playlist_tile_rects(self):
        row_h = HOME_TILE_SIZE + HOME_TILE_TEXT_H + HOME_TILE_GAP
        for i, playlist in enumerate(self._playlists):
            col, row = i % HOME_GRID_COLS, i // HOME_GRID_COLS
            x = HOME_TILE_MARGIN + col * (HOME_TILE_SIZE + HOME_TILE_GAP)
            y = HOME_GRID_TOP + row * row_h
            yield playlist, QRectF(x, y, HOME_TILE_SIZE, HOME_TILE_SIZE)

    def _fab_centers(self):
        return (
            (BASE_WIDTH - 34, BASE_HEIGHT - 84),  # search
            (BASE_WIDTH - 34, BASE_HEIGHT - 36),  # voice
        )

    # --- data updates ----------------------------------------------------

    def _on_status_changed(self, status: dict) -> None:
        self._state = status.get("state", self._state)
        self.update()

    def _on_track_changed(self, track: dict) -> None:
        self._track.update(track)

        thumb = track.get("thumbnail")
        if thumb is not None and thumb is not self._last_thumbnail_bytes:
            self._last_thumbnail_bytes = thumb
            pixmap = QPixmap()
            if pixmap.loadFromData(thumb):
                self._art_pixmap = pixmap
        elif "thumbnail" in track and thumb is None and not track.get("active", True):
            self._art_pixmap = None
            self._last_thumbnail_bytes = None

        self.update()

    def _on_playlists_loaded(self, playlists: list) -> None:
        self._playlists = playlists
        self._playlist_error = None
        self._browse_scroll = min(self._browse_scroll, self._browse_max_scroll())
        self.update()

    def _on_playlist_art_loaded(self, playlist_id: str, image_bytes: bytes) -> None:
        pixmap = QPixmap()
        if pixmap.loadFromData(image_bytes):
            self._playlist_pixmaps[playlist_id] = pixmap
            self.update()

    def _on_playback_error(self, message: str) -> None:
        self._playlist_error = message
        self.update()

    def _on_volume_changed(self, percent: int) -> None:
        self._volume_percent = percent
        self.update()

    # --- painting ----------------------------------------------------

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.TextAntialiasing)
        # Everything below is drawn in the fixed base coordinate space; this
        # single transform is what makes the panel scale as one piece.
        painter.scale(self._scale, self._scale)

        painter.setPen(Qt.NoPen)
        painter.setBrush(_CARD_BG)
        card_rect = QRectF(0, 0, BASE_WIDTH, BASE_HEIGHT)
        painter.drawRoundedRect(card_rect, 22, 22)

        painter.save()
        painter.setClipRect(QRectF(0, CONTENT_TOP, BASE_WIDTH, CONTENT_HEIGHT))
        if self._tab == "home":
            self._paint_home(painter)
        else:
            self._paint_now_playing(painter)
        painter.restore()

        self._paint_header(painter)
        if not self._locked:
            self._paint_resize_grip(painter)

    def _paint_header(self, painter: QPainter) -> None:
        logo_rect = QRectF(LOGO_MARGIN, (HEADER_HEIGHT - LOGO_SIZE) / 2, LOGO_SIZE, LOGO_SIZE)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_SPOTIFY_GREEN)
        painter.drawEllipse(logo_rect)
        _draw_glyph(
            painter, GLYPH_MUSIC_SINGLE, logo_rect.center().x(), logo_rect.center().y(), ICON_LOGO, QColor(12, 12, 14)
        )

        for (tab_id, glyph, label), (_, rect) in zip(self._tab_specs(), self._tab_rects()):
            self._draw_tab(painter, tab_id, glyph, label, rect)

        # Placement lock state, then the in-car-mode accent dot.
        _draw_glyph(
            painter,
            GLYPH_LOCKED if self._locked else GLYPH_UNLOCKED,
            BASE_WIDTH - 45,
            HEADER_HEIGHT / 2,
            ICON_LOCK,
            QColor(125, 125, 135) if self._locked else QColor(95, 95, 105),
        )

        color = _STATE_COLORS.get(self._state, _STATE_COLORS["neutral"])
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawEllipse(QRectF(BASE_WIDTH - 26, HEADER_HEIGHT / 2 - 4, 8, 8))

    def _draw_tab(self, painter: QPainter, tab_id: str, glyph: str, label: str, rect: QRectF) -> None:
        active = self._tab == tab_id

        if active:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(255, 255, 255))
            painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        fg = QColor(12, 12, 14) if active else QColor(165, 165, 172)

        text_font = QFont("Segoe UI", 10, QFont.DemiBold if active else QFont.Normal)
        text_w = QFontMetrics(text_font).horizontalAdvance(label)
        gap = 8
        start_x = rect.center().x() - (ICON_TAB + gap + text_w) / 2

        _draw_glyph(painter, glyph, start_x + ICON_TAB / 2, rect.center().y(), ICON_TAB, fg)
        painter.setPen(fg)
        painter.setFont(text_font)
        painter.drawText(
            QRectF(start_x + ICON_TAB + gap, rect.top(), text_w + 2, rect.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            label,
        )

    def _paint_resize_grip(self, painter: QPainter) -> None:
        rect = self._resize_grip_rect()
        _draw_glyph(
            painter, GLYPH_RESIZE, rect.center().x() - 2, rect.center().y() - 2, ICON_GRIP, QColor(125, 125, 138)
        )

    def _paint_now_playing(self, painter: QPainter) -> None:
        # Drawn regardless of whether a track is active -- volume is a
        # device-level property, not tied to what's currently playing.
        self._draw_volume_indicator(painter)

        if not self._track.get("active"):
            painter.setPen(QColor(200, 200, 208))
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(QRectF(0, CONTENT_TOP, BASE_WIDTH, CONTENT_HEIGHT), Qt.AlignCenter, "No music playing")
            return

        layout = self._now_playing_layout()
        self._draw_rounded_pixmap(painter, self._art_pixmap, layout["art_rect"], 14, QColor(48, 48, 54))

        title_font = QFont("Segoe UI", 16, QFont.Bold)
        title = self._elide(self._track["title"], title_font, int(layout["title_rect"].width()))
        painter.setPen(QColor(255, 255, 255))
        painter.setFont(title_font)
        painter.drawText(layout["title_rect"], Qt.AlignVCenter | Qt.AlignLeft, title)

        artist_font = QFont("Segoe UI", 11)
        artist = self._elide(self._track["artist"], artist_font, int(layout["artist_rect"].width()))
        painter.setPen(QColor(175, 175, 184))
        painter.setFont(artist_font)
        painter.drawText(layout["artist_rect"], Qt.AlignVCenter | Qt.AlignLeft, artist)

        self._draw_progress_bar(painter, layout["progress_x"], layout["progress_y"], layout["progress_width"])
        self._draw_transport(painter, layout)
        self._draw_sound_toggle(painter, layout)

    def _draw_progress_bar(self, painter: QPainter, x: float, y: float, width: float) -> None:
        duration = max(self._track.get("duration_s", 0.0), 0.01)
        position = min(self._track.get("position_s", 0.0), duration)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 48))
        painter.drawRoundedRect(QRectF(x, y, width, 4), 2, 2)
        painter.setBrush(_SPOTIFY_GREEN)
        painter.drawRoundedRect(QRectF(x, y, width * (position / duration), 4), 2, 2)

        painter.setPen(QColor(150, 150, 158))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(QRectF(x, y + 8, width, 16), Qt.AlignLeft, _format_time(position))
        painter.drawText(QRectF(x, y + 8, width, 16), Qt.AlignRight, f"-{_format_time(duration - position)}")

    def _draw_transport(self, painter: QPainter, layout: dict) -> None:
        white = QColor(240, 240, 245)
        prev_c, play_c, next_c = layout["prev_rect"].center(), layout["play_pause_rect"].center(), layout["next_rect"].center()
        _draw_glyph(painter, GLYPH_PREV, prev_c.x(), prev_c.y(), ICON_TRANSPORT, white)
        _draw_glyph(painter, GLYPH_NEXT, next_c.x(), next_c.y(), ICON_TRANSPORT, white)
        _draw_glyph(
            painter,
            GLYPH_PAUSE if self._track.get("is_playing") else GLYPH_PLAY,
            play_c.x(),
            play_c.y(),
            ICON_TRANSPORT_PLAY,
            white,
        )

    def _draw_sound_toggle(self, painter: QPainter, layout: dict) -> None:
        """In-Car / Regular sound toggle: whether the driving cabin-acoustics
        DSP preset applies while actually driving, or audio stays untouched.
        Doesn't affect the paused/menu echo effect -- that's game state, not
        a taste preference."""
        car_rect, regular_rect = layout["sound_car_rect"], layout["sound_regular_rect"]
        pill_rect = car_rect.united(regular_rect)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 20))
        painter.drawRoundedRect(pill_rect, pill_rect.height() / 2, pill_rect.height() / 2)

        active_rect = car_rect if self._sound_mode == "in_car" else regular_rect
        painter.setBrush(_SPOTIFY_GREEN)
        painter.drawRoundedRect(active_rect, active_rect.height() / 2, active_rect.height() / 2)

        car_color = QColor(12, 12, 14) if self._sound_mode == "in_car" else QColor(150, 150, 158)
        regular_color = QColor(12, 12, 14) if self._sound_mode == "regular" else QColor(150, 150, 158)
        _draw_glyph(painter, GLYPH_CAR, car_rect.center().x(), car_rect.center().y(), ICON_SOUND_MODE, car_color)
        _draw_glyph(
            painter, GLYPH_HEADPHONES, regular_rect.center().x(), regular_rect.center().y(), ICON_SOUND_MODE, regular_color
        )

    def _volume_icon_rect_bar_text(self):
        """Geometry for the volume indicator, right-aligned to the same
        margin as the album art, sitting just above the resize grip."""
        y = BASE_HEIGHT - VOLUME_ROW_BOTTOM_MARGIN - VOLUME_ROW_HEIGHT
        text_w = 32
        right = BASE_WIDTH - NP_ART_MARGIN
        text_rect = QRectF(right - text_w, y, text_w, VOLUME_ROW_HEIGHT)
        bar_rect = QRectF(text_rect.left() - 8 - VOLUME_BAR_WIDTH, y + (VOLUME_ROW_HEIGHT - VOLUME_BAR_HEIGHT) / 2, VOLUME_BAR_WIDTH, VOLUME_BAR_HEIGHT)
        icon_cx = bar_rect.left() - 8 - ICON_VOLUME / 2
        icon_cy = y + VOLUME_ROW_HEIGHT / 2
        return icon_cx, icon_cy, bar_rect, text_rect

    def _draw_volume_indicator(self, painter: QPainter) -> None:
        if self._volume_percent is None:
            return  # no successful poll yet (or Spotify not configured) -- nothing to show

        icon_cx, icon_cy, bar_rect, text_rect = self._volume_icon_rect_bar_text()
        percent = self._volume_percent

        if percent == 0:
            glyph = GLYPH_VOLUME_MUTE
        elif percent < 34:
            glyph = GLYPH_VOLUME_LOW
        elif percent < 67:
            glyph = GLYPH_VOLUME_MED
        else:
            glyph = GLYPH_VOLUME_HIGH
        _draw_glyph(painter, glyph, icon_cx, icon_cy, ICON_VOLUME, QColor(190, 190, 198))

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 40))
        painter.drawRoundedRect(bar_rect, 2, 2)
        painter.setBrush(QColor(190, 190, 198))
        painter.drawRoundedRect(QRectF(bar_rect.x(), bar_rect.y(), bar_rect.width() * percent / 100.0, bar_rect.height()), 2, 2)

        painter.setPen(QColor(190, 190, 198))
        painter.setFont(QFont("Segoe UI", 8))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, f"{percent}%")

    def _paint_home(self, painter: QPainter) -> None:
        if self._playlist_error:
            painter.setPen(QColor(230, 140, 140))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(QRectF(0, CONTENT_TOP, BASE_WIDTH, CONTENT_HEIGHT), Qt.AlignCenter, self._playlist_error)
        elif not self._playlists:
            painter.setPen(QColor(185, 185, 195))
            painter.setFont(QFont("Segoe UI", 10))
            painter.drawText(QRectF(0, CONTENT_TOP, BASE_WIDTH, CONTENT_HEIGHT), Qt.AlignCenter, "Loading playlists...")
        else:
            painter.save()
            painter.setClipRect(QRectF(0, CONTENT_TOP, BASE_WIDTH, CONTENT_HEIGHT))
            painter.translate(0, -self._browse_scroll)

            name_font = QFont("Segoe UI", 9, QFont.DemiBold)
            owner_font = QFont("Segoe UI", 8)
            for playlist, rect in self._playlist_tile_rects():
                self._draw_rounded_pixmap(
                    painter, self._playlist_pixmaps.get(playlist.id), rect, 8, QColor(44, 44, 50)
                )

                painter.setPen(QColor(238, 238, 242))
                painter.setFont(name_font)
                painter.drawText(
                    QRectF(rect.x(), rect.bottom() + 5, HOME_TILE_SIZE, 15),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    self._elide(playlist.name, name_font, HOME_TILE_SIZE),
                )

                if playlist.owner:
                    painter.setPen(QColor(158, 158, 166))
                    painter.setFont(owner_font)
                    painter.drawText(
                        QRectF(rect.x(), rect.bottom() + 19, HOME_TILE_SIZE, 14),
                        Qt.AlignLeft | Qt.AlignVCenter,
                        self._elide(playlist.owner, owner_font, HOME_TILE_SIZE),
                    )

            painter.restore()

        self._draw_floating_buttons(painter)

    def _draw_floating_buttons(self, painter: QPainter) -> None:
        (sx, sy), (mx, my) = self._fab_centers()

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(46, 46, 52, 240))
        painter.drawEllipse(QRectF(sx - FAB_RADIUS, sy - FAB_RADIUS, FAB_RADIUS * 2, FAB_RADIUS * 2))
        _draw_glyph(painter, GLYPH_SEARCH, sx, sy, ICON_FAB, QColor(225, 225, 232))

        painter.setBrush(_SPOTIFY_GREEN)
        painter.drawEllipse(QRectF(mx - FAB_RADIUS, my - FAB_RADIUS, FAB_RADIUS * 2, FAB_RADIUS * 2))
        _draw_glyph(painter, GLYPH_MIC, mx, my, ICON_FAB, QColor(12, 12, 14))

    def _draw_rounded_pixmap(
        self, painter: QPainter, pixmap: Optional[QPixmap], rect: QRectF, radius: float, fallback_color: QColor
    ) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(path)
        if pixmap is not None:
            scaled = pixmap.scaled(
                int(rect.width()), int(rect.height()), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            painter.drawPixmap(rect.topLeft(), scaled)
        else:
            painter.fillRect(rect, fallback_color)
        painter.restore()

    @staticmethod
    def _elide(text: str, font: QFont, max_width: int) -> str:
        return QFontMetrics(font).elidedText(text, Qt.ElideRight, max_width)


def create_overlay(
    now_playing_bridge=None,
    lock_bridge=None,
    spotify_bridge=None,
    on_playlist_selected: Optional[Callable[[str], None]] = None,
    on_home_opened: Optional[Callable[[], None]] = None,
    on_play_pause: Optional[Callable[[bool], None]] = None,
    on_next: Optional[Callable[[], None]] = None,
    on_previous: Optional[Callable[[], None]] = None,
    on_sound_mode_changed: Optional[Callable[[str], None]] = None,
    corner: str = "bottom-left",
):
    """Create (or reuse) the QApplication and show the overlay window.

    Caller owns the Qt event loop (call `app.exec()`); this only constructs and
    shows the window so it can be wired into other startup code first. Returns
    (app, window, bridge) -- bridge is for pushing telemetry-driven state via
    `bridge.status_changed.emit({"state": ...})`.
    """
    app = QApplication.instance() or QApplication([])
    bridge = OverlayBridge()
    window = OverlayWindow(
        bridge,
        now_playing_bridge=now_playing_bridge,
        lock_bridge=lock_bridge,
        spotify_bridge=spotify_bridge,
        on_playlist_selected=on_playlist_selected,
        on_home_opened=on_home_opened,
        on_play_pause=on_play_pause,
        on_next=on_next,
        on_previous=on_previous,
        on_sound_mode_changed=on_sound_mode_changed,
        corner=corner,
    )
    window.show()
    return app, window, bridge
