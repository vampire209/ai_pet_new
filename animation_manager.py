"""Dual-format (PNG sequence + 24fps streaming WebP) high-DPI animation playback."""

from __future__ import annotations

from pathlib import Path
import random
import re
from typing import Final

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QPixmap

from character_pack import (
    ALL_ACTION_CATEGORIES,
    AnimationClip,
    CharacterPack,
    builtin_default_pack,
)
from config import AppConfig
from stream_player import DecodedLoopCache, StreamPlayer


class AnimationManager(QObject):
    """Play PNG sequences or 24fps streaming WebP clips with high-DPI scaling."""

    SUPPORTED_ANIMATIONS: Final[tuple[str, ...]] = (
        "idle",
        "move",
        "wait",
        "remind",
    )
    ALL_ANIMATIONS: Final[tuple[str, ...]] = ALL_ACTION_CATEGORIES
    _FRAME_NAME = re.compile(r"^frame_(\d+)\.png$", re.IGNORECASE)

    frame_changed = Signal(QPixmap)
    clip_ended = Signal(str)

    def __init__(
        self,
        config: AppConfig,
        device_pixel_ratio: float = 1.0,
        parent: QObject | None = None,
        *,
        character_pack: CharacterPack | None = None,
        rng: random.Random | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._device_pixel_ratio = max(1.0, float(device_pixel_ratio))
        self._rng = rng or random.Random()
        self._pack: CharacterPack = character_pack or builtin_default_pack(
            config.animations_dir
        )
        self._loop_cache = DecodedLoopCache()
        self._stream_player = StreamPlayer(loop_cache=self._loop_cache)

        # Sequence frames indexed by AnimationClip
        self._source_sequences: dict[Path, list[QPixmap]] = {}
        self._scaled_sequences: dict[Path, list[QPixmap]] = {}

        self._current_name: str | None = None
        self._current_clip: AnimationClip | None = None
        self._current_index = 0
        self._playback_speed = 1.0

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance_frame)

        self._preload_pack_sequences()
        self._rebuild_scaled_sequences()

    @property
    def character_pack(self) -> CharacterPack:
        return self._pack

    @property
    def target_logical_height(self) -> int:
        return self._pack.default_height or self._config.pet_height

    @property
    def target_pixel_height(self) -> int:
        return max(1, round(self.target_logical_height * self._device_pixel_ratio))

    def load_character_pack(self, pack: CharacterPack) -> bool:
        """Hot-switch to another CharacterPack cleanly."""
        previous_action = self._current_name or "idle"
        self.stop()
        self._pack = pack
        self._source_sequences.clear()
        self._scaled_sequences.clear()
        self._preload_pack_sequences()
        self._rebuild_scaled_sequences()
        return self.play_animation(previous_action)

    def _preload_pack_sequences(self) -> None:
        """Load PNG sequence folders for the active pack once."""
        for clips in self._pack.actions.values():
            for clip in clips:
                if clip.kind != "sequence" or clip.path in self._source_sequences:
                    continue
                frames: list[QPixmap] = []
                for path in self._frame_paths_in(clip.path):
                    pixmap = QPixmap(str(path))
                    if not pixmap.isNull():
                        frames.append(pixmap)
                self._source_sequences[clip.path] = frames

    @classmethod
    def _frame_paths_in(cls, directory: Path) -> list[Path]:
        if not directory.is_dir():
            return []
        numbered: list[tuple[int, Path]] = []
        for path in directory.iterdir():
            if not path.is_file():
                continue
            match = cls._FRAME_NAME.fullmatch(path.name)
            if match:
                numbered.append((int(match.group(1)), path))
        return [path for _, path in sorted(numbered, key=lambda item: item[0])]

    def _rebuild_scaled_sequences(self) -> None:
        pixel_height = self.target_pixel_height
        rebuilt: dict[Path, list[QPixmap]] = {}
        for folder, sources in self._source_sequences.items():
            scaled_frames: list[QPixmap] = []
            for source in sources:
                scaled = source.scaledToHeight(
                    pixel_height,
                    Qt.TransformationMode.SmoothTransformation,
                )
                scaled.setDevicePixelRatio(self._device_pixel_ratio)
                scaled_frames.append(scaled)
            rebuilt[folder] = scaled_frames
        self._scaled_sequences = rebuilt

    def _choose_clip(self, action: str) -> AnimationClip | None:
        clips = self._pack.clips_for(action)
        if not clips:
            return None
        if len(clips) == 1:
            return clips[0]
        return self._rng.choice(clips)

    def _sequence_interval_ms(self, action: str, clip: AnimationClip) -> int:
        if self._pack.pack_id == "default" and action in self.SUPPORTED_ANIMATIONS:
            return self._config.interval_for(action)
        return max(16, int(round(clip.frame_interval_ms / max(0.25, self._playback_speed))))

    def play_animation(self, name: str, *, force_restart: bool = False) -> bool:
        """Play ``name`` or safely fall back via CharacterPack rules.

        Returns True when ``name`` exists in the active pack, False when it
        fell back to another action (such as idle). Requesting the already-active
        resolved action never resets its frame index unless force_restart=True.
        """
        resolved, matched_exact = self._pack.resolve_action(name)
        if resolved is None:
            self.stop()
            return False

        if self._current_name == resolved and self._current_clip is not None and not force_restart:
            if not self._timer.isActive():
                self._schedule_next_tick()
            return matched_exact

        clip = self._choose_clip(resolved)
        if clip is None:
            self.stop()
            return False

        self._timer.stop()
        self._stream_player.close()

        if clip.kind == "animated":
            try:
                first_frame = self._stream_player.start(
                    clip.path,
                    target_pixel_height=self.target_pixel_height,
                    dpr=self._device_pixel_ratio,
                    fps=clip.fps,
                    loop=clip.loop,
                    cache_loop=(resolved == "idle"),
                )
            except Exception:
                # Fall back gracefully to idle if a clip cannot be decoded
                if resolved != "idle":
                    self.play_animation("idle", force_restart=True)
                else:
                    self.stop()
                return False
            self._current_name = resolved
            self._current_clip = clip
            self._current_index = 0
            self.frame_changed.emit(first_frame)
            self._schedule_next_tick()
            return matched_exact

        frames = self._scaled_sequences.get(clip.path, [])
        if not frames:
            if resolved != "idle":
                self.play_animation("idle", force_restart=True)
            else:
                self.stop()
            return False

        self._current_name = resolved
        self._current_clip = clip
        self._current_index = 0
        self.frame_changed.emit(frames[0])
        self._schedule_next_tick()
        return matched_exact

    def _schedule_next_tick(self) -> None:
        clip = self._current_clip
        if clip is None or self._current_name is None:
            return
        if clip.kind == "animated":
            if self._stream_player.ended:
                return
            delay = self._stream_player.next_delay_ms(speed=self._playback_speed)
            self._timer.start(delay)
        else:
            delay = self._sequence_interval_ms(self._current_name, clip)
            self._timer.start(delay)

    def _advance_frame(self) -> None:
        clip = self._current_clip
        action = self._current_name
        if clip is None or action is None:
            return

        if clip.kind == "animated":
            frame, ended = self._stream_player.advance(speed=self._playback_speed)
            if frame is not None:
                self._current_index = self._stream_player.frame_index
                self.frame_changed.emit(frame)
            if ended:
                self.clip_ended.emit(action)
                return
            self._schedule_next_tick()
            return

        frames = self._scaled_sequences.get(clip.path, [])
        if not frames:
            return
        next_index = self._current_index + 1
        if next_index >= len(frames):
            if not clip.loop:
                self.clip_ended.emit(action)
                return
            next_index = 0
        self._current_index = next_index
        self.frame_changed.emit(frames[self._current_index])
        self._schedule_next_tick()

    def set_device_pixel_ratio(self, ratio: float) -> None:
        """Rescale cached sequences or restart active stream at new DPR."""
        normalized = max(1.0, float(ratio))
        if abs(normalized - self._device_pixel_ratio) < 0.01:
            return
        self._device_pixel_ratio = normalized
        self._rebuild_scaled_sequences()

        clip = self._current_clip
        action = self._current_name
        if clip is None or action is None:
            return
        if clip.kind == "animated":
            self.play_animation(action, force_restart=True)
            return
        frames = self._scaled_sequences.get(clip.path, [])
        if frames:
            self._current_index %= len(frames)
            self.frame_changed.emit(frames[self._current_index])

    def stop(self) -> None:
        self._timer.stop()
        self._stream_player.close()
        self._current_name = None
        self._current_clip = None
        self._current_index = 0

    @property
    def is_streaming_high_fps(self) -> bool:
        """True when playing a multi-frame animated WebP/GIF that has native motion."""
        return (
            self._current_clip is not None
            and self._current_clip.kind == "animated"
            and self._stream_player.frame_count > 1
        )

    @property
    def current_animation(self) -> str | None:
        return self._current_name

    @property
    def current_frame_index(self) -> int:
        return self._current_index

    @property
    def available_animations(self) -> tuple[str, ...]:
        return tuple(
            name for name in self.ALL_ANIMATIONS if self._pack.clips_for(name)
        )

    def frame_count(self, name: str) -> int:
        clips = self._pack.clips_for(name)
        if not clips:
            return 0
        first = clips[0]
        if first.kind == "sequence":
            return len(self._scaled_sequences.get(first.path, []))
        if self._current_clip == first and self._stream_player.frame_count > 0:
            return self._stream_player.frame_count
        return 1
