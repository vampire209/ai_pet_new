"""Preloaded, high-DPI PNG sequence animation playback."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Final

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QPixmap

from config import AppConfig


class AnimationManager(QObject):
    """Load each PNG once and use a QTimer to cycle through cached frames."""

    SUPPORTED_ANIMATIONS: Final[tuple[str, ...]] = (
        "idle",
        "move",
        "wait",
        "remind",
    )
    _FRAME_NAME = re.compile(r"^frame_(\d+)\.png$", re.IGNORECASE)

    frame_changed = Signal(QPixmap)

    def __init__(
        self,
        config: AppConfig,
        device_pixel_ratio: float = 1.0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._device_pixel_ratio = max(1.0, float(device_pixel_ratio))
        self._source_frames: dict[str, list[QPixmap]] = {}
        self._frames: dict[str, list[QPixmap]] = {}
        self._current_name: str | None = None
        self._current_index = 0

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance_frame)

        self._preload_sources()
        self._rebuild_scaled_frames()

    def _preload_sources(self) -> None:
        """Read every available PNG into memory exactly once at startup."""
        for name in self.SUPPORTED_ANIMATIONS:
            frames: list[QPixmap] = []
            for path in self._frame_paths(name):
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    frames.append(pixmap)
            self._source_frames[name] = frames

    def _frame_paths(self, name: str) -> list[Path]:
        animation_dir = self._config.animations_dir / name
        if not animation_dir.is_dir():
            return []

        numbered_paths: list[tuple[int, Path]] = []
        for path in animation_dir.iterdir():
            if not path.is_file():
                continue
            match = self._FRAME_NAME.fullmatch(path.name)
            if match:
                numbered_paths.append((int(match.group(1)), path))
        return [path for _, path in sorted(numbered_paths, key=lambda item: item[0])]

    def _rebuild_scaled_frames(self) -> None:
        """Build DPR-aware display pixmaps from the cached original pixmaps."""
        target_pixel_height = max(
            1,
            round(self._config.pet_height * self._device_pixel_ratio),
        )
        rebuilt: dict[str, list[QPixmap]] = {}
        for name, sources in self._source_frames.items():
            scaled_frames: list[QPixmap] = []
            for source in sources:
                scaled = source.scaledToHeight(
                    target_pixel_height,
                    Qt.TransformationMode.SmoothTransformation,
                )
                scaled.setDevicePixelRatio(self._device_pixel_ratio)
                scaled_frames.append(scaled)
            rebuilt[name] = scaled_frames
        self._frames = rebuilt

    def play_animation(self, name: str) -> bool:
        """Play ``name`` or safely fall back to idle when it is unavailable.

        Returning ``False`` means the requested animation was missing, even if
        the idle fallback continues successfully. Requesting the active
        animation never resets its frame index.
        """
        requested_available = name in self.SUPPORTED_ANIMATIONS and bool(
            self._frames.get(name)
        )
        target = name if requested_available else "idle"
        frames = self._frames.get(target, [])
        if not frames:
            self.stop()
            return False

        if self._current_name == target:
            if not self._timer.isActive():
                self._timer.start(self._config.interval_for(target))
            return requested_available

        self._timer.stop()
        self._current_name = target
        self._current_index = 0
        self._timer.start(self._config.interval_for(target))
        self.frame_changed.emit(frames[0])
        return requested_available

    def _advance_frame(self) -> None:
        frames = self._frames.get(self._current_name or "", [])
        if not frames:
            return
        self._current_index = (self._current_index + 1) % len(frames)
        self.frame_changed.emit(frames[self._current_index])

    def set_device_pixel_ratio(self, ratio: float) -> None:
        """Rescale from in-memory originals after moving to another screen."""
        normalized = max(1.0, float(ratio))
        if abs(normalized - self._device_pixel_ratio) < 0.01:
            return
        self._device_pixel_ratio = normalized
        self._rebuild_scaled_frames()

        frames = self._frames.get(self._current_name or "", [])
        if frames:
            self._current_index %= len(frames)
            self.frame_changed.emit(frames[self._current_index])

    def stop(self) -> None:
        self._timer.stop()
        self._current_name = None
        self._current_index = 0

    @property
    def current_animation(self) -> str | None:
        return self._current_name

    @property
    def current_frame_index(self) -> int:
        return self._current_index

    @property
    def available_animations(self) -> tuple[str, ...]:
        return tuple(
            name for name in self.SUPPORTED_ANIMATIONS if self._frames.get(name)
        )

    def frame_count(self, name: str) -> int:
        return len(self._frames.get(name, []))
