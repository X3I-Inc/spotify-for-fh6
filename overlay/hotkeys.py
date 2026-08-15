"""Global hotkeys that work regardless of window focus -- needed because the
overlay intentionally never accepts keyboard focus (Qt.WindowDoesNotAcceptFocus,
see overlay/window.py) so that clicking it doesn't pause FH6. That means a
normal Qt keyPressEvent on the overlay would never fire; pynput listens
system-wide instead, independent of which window is focused.

  - Ctrl+Alt+L (LockToggleHotkey): locks/unlocks the overlay's placement, so
    it can be repositioned or resized without alt-tabbing out of FH6. Locking
    freezes position and size only -- taps on the overlay's controls keep
    working in both states.
  - Page Up / Page Down (VolumeHotkeys): raises/lowers Spotify's volume.

Each runs on pynput's own background listener thread; the resulting action is
marshalled to the GUI thread via a Qt signal (thread-safe, same pattern as the
telemetry/now-playing bridges).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal
from pynput import keyboard

DEFAULT_HOTKEY = "<ctrl>+<alt>+l"


class LockToggleBridge(QObject):
    toggle_lock = Signal()


class LockToggleHotkey:
    """Wraps pynput's GlobalHotKeys so it can be start()/stop() alongside the
    rest of the app's background threads."""

    def __init__(self, bridge: LockToggleBridge, hotkey: str = DEFAULT_HOTKEY) -> None:
        self.bridge = bridge
        self.hotkey = hotkey
        self._listener: Optional[keyboard.GlobalHotKeys] = None

    def start(self) -> None:
        self._listener = keyboard.GlobalHotKeys({self.hotkey: self.bridge.toggle_lock.emit})
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None


class VolumeHotkeyBridge(QObject):
    volume_up = Signal()
    volume_down = Signal()


class VolumeHotkeys:
    """Global Page Up / Page Down -> Spotify volume up/down.

    Uses a plain pynput Listener rather than GlobalHotKeys, since these are
    single un-modified keys rather than a modifier combo. Non-suppressing (the
    default): the keypress still reaches FH6 normally in case it's bound to
    anything there (e.g. a camera/view change) -- this only adds a reaction
    to it, it doesn't take the key away from the game.
    """

    def __init__(self, bridge: VolumeHotkeyBridge) -> None:
        self.bridge = bridge
        self._listener: Optional[keyboard.Listener] = None

    def _on_press(self, key) -> None:
        if key == keyboard.Key.page_up:
            self.bridge.volume_up.emit()
        elif key == keyboard.Key.page_down:
            self.bridge.volume_down.emit()

    def start(self) -> None:
        self._listener = keyboard.Listener(on_press=self._on_press)
        self._listener.start()

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
