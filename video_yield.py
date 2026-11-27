"""Detect fullscreen video/games on Windows so the pet yields topmost cleanly."""

from __future__ import annotations

import os
import sys
from typing import Callable

from PySide6.QtCore import QObject, QRect, QTimer, Signal


_SHELL_CLASSES = frozenset({
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "Windows.UI.Core.CoreWindow",
})


def is_rect_fullscreen(
    window_rect: tuple[int, int, int, int],
    monitor_rect: tuple[int, int, int, int],
    *,
    tolerance_px: int = 4,
) -> bool:
    """Return True when window_rect covers the monitor_rect within tolerance."""
    wl, wt, wr, wb = window_rect
    ml, mt, mr, mb = monitor_rect
    if wr <= wl or wb <= wt or mr <= ml or mb <= mt:
        return False
    return (
        wl <= ml + tolerance_px
        and wt <= mt + tolerance_px
        and wr >= mr - tolerance_px
        and wb >= mb - tolerance_px
    )


def detect_windows_fullscreen(pet_hwnd: int | None = None) -> bool:
    """Check whether another process owns a fullscreen foreground window on Windows."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        fg = user32.GetForegroundWindow()
        if not fg or (pet_hwnd is not None and int(fg) == int(pet_hwnd)):
            return False

        # Ignore our own process windows (chat, todo, settings, etc.).
        fg_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
        if fg_pid.value == os.getpid():
            return False

        class_buf = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(fg, class_buf, 128)
        if class_buf.value in _SHELL_CLASSES:
            return False

        rect = wintypes.RECT()
        if not user32.GetWindowRect(fg, ctypes.byref(rect)):
            return False

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
            ]

        monitor = user32.MonitorFromWindow(fg, 2)  # MONITOR_DEFAULTTONEAREST
        if not monitor:
            return False
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False

        mon = info.rcMonitor
        return is_rect_fullscreen(
            (rect.left, rect.top, rect.right, rect.bottom),
            (mon.left, mon.top, mon.right, mon.bottom),
        )
    except Exception:
        return False


class FullscreenYieldWatcher(QObject):
    """Low-frequency watcher that notifies PetWindow when fullscreen apps appear."""

    fullscreen_changed = Signal(bool)

    def __init__(
        self,
        *,
        hwnd_provider: Callable[[], int | None],
        detector: Callable[[int | None], bool] | None = None,
        interval_ms: int = 1000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._hwnd_provider = hwnd_provider
        self._detector = detector or detect_windows_fullscreen
        self._enabled = True
        self._is_fullscreen = False
        self._timer = QTimer(self)
        self._timer.setInterval(max(200, int(interval_ms)))
        self._timer.timeout.connect(self.check_now)

    @property
    def is_fullscreen(self) -> bool:
        return self._is_fullscreen

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled and self._is_fullscreen:
            self._is_fullscreen = False
            self.fullscreen_changed.emit(False)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def check_now(self) -> bool:
        detected = bool(self._detector(self._hwnd_provider())) if self._enabled else False
        if detected != self._is_fullscreen:
            self._is_fullscreen = detected
            self.fullscreen_changed.emit(detected)
        return self._is_fullscreen
