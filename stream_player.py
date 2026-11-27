"""Bounded 24fps streaming WebP/GIF decoder with perf_counter clock and loop cache."""

from __future__ import annotations

from collections import OrderedDict
import math
from pathlib import Path
import queue
import threading
import time
import zlib

from PySide6.QtGui import QImage, QPixmap

try:
    from PIL import Image
except ImportError:  # pragma: no cover - optional fallback when Pillow is absent
    Image = None  # type: ignore[assignment]


_DEFAULT_FRAME_SECONDS = 1.0 / 24.0
_MIN_FRAME_SECONDS = 0.015
_MAX_QUEUE_FRAMES = 6


class DecodedLoopCache:
    """Memory-bounded zlib-compressed frame cache for frequently looped clips."""

    def __init__(
        self,
        *,
        max_bytes: int = 24 * 1024 * 1024,
        entry_limit: int = 8 * 1024 * 1024,
    ) -> None:
        self._lock = threading.Lock()
        self._entries: OrderedDict[tuple, tuple[list[tuple[bytes, int, int, float]], int]] = OrderedDict()
        self._total_bytes = 0
        self.max_bytes = max_bytes
        self.entry_limit = entry_limit

    def get(self, key: tuple) -> list[tuple[bytes, int, int, float]] | None:
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            self._entries.move_to_end(key)
            return item[0]

    def put(
        self,
        key: tuple,
        frames: list[tuple[bytes, int, int, float]],
        byte_size: int,
    ) -> None:
        if byte_size <= 0 or byte_size > self.entry_limit:
            return
        with self._lock:
            existing = self._entries.pop(key, None)
            if existing is not None:
                self._total_bytes -= existing[1]
            while self._entries and self._total_bytes + byte_size > self.max_bytes:
                _, (_, old_size) = self._entries.popitem(last=False)
                self._total_bytes -= old_size
            self._entries[key] = (frames, byte_size)
            self._total_bytes += byte_size


def _rgba_to_pixmap(raw: bytes, width: int, height: int, dpr: float) -> QPixmap:
    image = QImage(raw, width, height, width * 4, QImage.Format.Format_RGBA8888)
    pixmap = QPixmap.fromImage(image.copy())
    pixmap.setDevicePixelRatio(max(1.0, float(dpr)))
    return pixmap


class StreamPlayer:
    """Stream animated WebP/GIF frames on a worker thread with zero drift."""

    def __init__(self, *, loop_cache: DecodedLoopCache | None = None) -> None:
        self._loop_cache = loop_cache or DecodedLoopCache()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._queue: queue.Queue = queue.Queue(maxsize=_MAX_QUEUE_FRAMES)
        self._path: Path | None = None
        self._target_pixel_height: int = 270
        self._dpr: float = 1.0
        self._loop: bool = True
        self._cache_loop: bool = False
        self._default_fps: float = 24.0
        self._frame_index: int = 0
        self._frame_count: int = 0
        self._playhead: float = 0.0
        self._next_time: float = 0.0
        self._last_clock: float = 0.0
        self._ended: bool = False
        self._error: str | None = None

    @property
    def is_available(self) -> bool:
        return Image is not None

    @property
    def frame_index(self) -> int:
        return self._frame_index

    @property
    def frame_count(self) -> int:
        return self._frame_count

    @property
    def ended(self) -> bool:
        return self._ended

    @property
    def error(self) -> str | None:
        return self._error

    def start(
        self,
        path: Path,
        *,
        target_pixel_height: int,
        dpr: float = 1.0,
        fps: float = 24.0,
        loop: bool = True,
        cache_loop: bool = False,
    ) -> QPixmap:
        """Open clip, decode frame 0 synchronously for instant display, stream rest."""
        if Image is None:
            raise RuntimeError("缺少 Pillow 依赖，无法解码动态 WebP。")
        self.close()
        self._stop = threading.Event()
        self._queue = queue.Queue(maxsize=_MAX_QUEUE_FRAMES)
        self._path = Path(path)
        self._target_pixel_height = max(1, int(target_pixel_height))
        self._dpr = max(1.0, float(dpr))
        self._default_fps = max(1.0, float(fps))
        self._loop = bool(loop)
        self._cache_loop = bool(cache_loop)
        self._ended = False
        self._error = None
        self._frame_index = 0

        mtime_ns = self._path.stat().st_mtime_ns
        cache_key = (str(self._path), self._target_pixel_height, mtime_ns)
        cached = self._loop_cache.get(cache_key) if self._cache_loop else None

        if cached:
            packed, width, height, delay = cached[0]
            first_raw = zlib.decompress(packed)
            self._frame_count = len(cached)
            first_delay = delay
            self._start_cached_worker(cached, self._stop, self._queue)
        else:
            first_raw, width, height, first_delay, n_frames = self._decode_first_frame(
                self._path,
                self._target_pixel_height,
                self._default_fps,
            )
            self._frame_count = n_frames
            if n_frames > 1:
                self._start_file_worker(
                    self._path,
                    self._target_pixel_height,
                    self._default_fps,
                    cache_key if self._cache_loop else None,
                    self._stop,
                    self._queue,
                )
            elif not self._loop:
                self._queue.put_nowait((None, 0, 0, 0.0, None))

        now = time.perf_counter()
        self._playhead = 0.0
        self._next_time = first_delay
        self._last_clock = now
        return _rgba_to_pixmap(first_raw, width, height, self._dpr)

    @staticmethod
    def _resize_frame(image, target_pixel_height: int) -> tuple[bytes, int, int]:
        rgba = image.convert("RGBA")
        w, h = rgba.size
        if h != target_pixel_height and h > 0:
            new_w = max(1, int(round(w * (target_pixel_height / float(h)))))
            rgba = rgba.resize((new_w, target_pixel_height), Image.Resampling.LANCZOS)
        return rgba.tobytes(), rgba.size[0], rgba.size[1]

    @classmethod
    def _frame_delay_seconds(cls, image, default_fps: float) -> float:
        raw_ms = image.info.get("duration")
        if isinstance(raw_ms, (int, float)) and raw_ms > 0:
            return max(_MIN_FRAME_SECONDS, float(raw_ms) / 1000.0)
        return max(_MIN_FRAME_SECONDS, 1.0 / max(1.0, default_fps))

    def _decode_first_frame(
        self,
        path: Path,
        target_pixel_height: int,
        default_fps: float,
    ) -> tuple[bytes, int, int, float, int]:
        with Image.open(path) as img:
            n_frames = max(1, int(getattr(img, "n_frames", 1)))
            img.seek(0)
            img.load()
            delay = self._frame_delay_seconds(img, default_fps)
            raw, width, height = self._resize_frame(img, target_pixel_height)
            return raw, width, height, delay, n_frames

    def _put_frame(
        self,
        target_queue: queue.Queue,
        stop: threading.Event,
        item: object,
    ) -> bool:
        while not stop.is_set():
            try:
                target_queue.put(item, timeout=0.02)
                return True
            except queue.Full:
                continue
        return False

    def _start_cached_worker(
        self,
        cached: list[tuple[bytes, int, int, float]],
        stop: threading.Event,
        target_queue: queue.Queue,
    ) -> None:
        loop = self._loop

        def run() -> None:
            start_idx = 1
            while not stop.is_set():
                for idx in range(start_idx, len(cached)):
                    if stop.is_set():
                        return
                    packed, width, height, delay = cached[idx]
                    raw = zlib.decompress(packed)
                    if not self._put_frame(target_queue, stop, (raw, width, height, delay, idx)):
                        return
                if not loop:
                    self._put_frame(target_queue, stop, (None, 0, 0, 0.0, None))
                    return
                start_idx = 0

        worker = threading.Thread(target=run, name="pet-stream-cache", daemon=True)
        worker.start()
        self._thread = worker

    def _start_file_worker(
        self,
        path: Path,
        target_pixel_height: int,
        default_fps: float,
        cache_key: tuple | None,
        stop: threading.Event,
        target_queue: queue.Queue,
    ) -> None:
        loop = self._loop

        def run() -> None:
            try:
                packed_frames: list[tuple[bytes, int, int, float]] = []
                packed_bytes = 0
                collect = cache_key is not None
                first_pass = True

                while not stop.is_set():
                    with Image.open(path) as img:
                        n_frames = max(1, int(getattr(img, "n_frames", 1)))
                        for idx in range(n_frames):
                            if stop.is_set():
                                return
                            img.seek(idx)
                            img.load()
                            delay = self._frame_delay_seconds(img, default_fps)
                            raw, width, height = self._resize_frame(img, target_pixel_height)
                            if collect and first_pass:
                                compressed = zlib.compress(raw, level=1)
                                packed_bytes += len(compressed)
                                if packed_bytes > self._loop_cache.entry_limit:
                                    collect = False
                                    packed_frames.clear()
                                else:
                                    packed_frames.append((compressed, width, height, delay))
                            if first_pass and idx == 0:
                                continue
                            if not self._put_frame(
                                target_queue, stop, (raw, width, height, delay, idx)
                            ):
                                return
                    if collect and first_pass and cache_key is not None and packed_frames:
                        self._loop_cache.put(cache_key, packed_frames, packed_bytes)
                    first_pass = False
                    if not loop:
                        self._put_frame(target_queue, stop, (None, 0, 0, 0.0, None))
                        return
                    if packed_frames:
                        # Switch seamlessly to in-memory cached loop for subsequent cycles.
                        while not stop.is_set():
                            for idx, (compressed, width, height, delay) in enumerate(packed_frames):
                                if stop.is_set():
                                    return
                                raw = zlib.decompress(compressed)
                                if not self._put_frame(
                                    target_queue, stop, (raw, width, height, delay, idx)
                                ):
                                    return
                        return
            except Exception as exc:
                if not stop.is_set():
                    self._put_frame(target_queue, stop, (None, 0, 0, 0.0, type(exc).__name__))

        worker = threading.Thread(target=run, name="pet-stream-decode", daemon=True)
        worker.start()
        self._thread = worker

    def advance(self, *, speed: float = 1.0, now: float | None = None) -> tuple[QPixmap | None, bool]:
        """Advance cumulative presentation clock and return latest due frame."""
        if self._ended or self._path is None:
            return None, False
        current = time.perf_counter() if now is None else now
        clamped_speed = max(0.25, min(3.0, float(speed)))
        self._playhead += max(0.0, current - self._last_clock) * clamped_speed
        self._last_clock = current

        latest: tuple[bytes, int, int] | None = None
        ended = False
        while self._playhead + 1e-8 >= self._next_time:
            try:
                raw, width, height, delay, idx = self._queue.get_nowait()
            except queue.Empty:
                break
            if raw is None:
                self._ended = True
                self._error = idx if isinstance(idx, str) else None
                ended = True
                break
            self._next_time += delay
            self._frame_index = int(idx)
            latest = (raw, width, height)

        pixmap = (
            _rgba_to_pixmap(latest[0], latest[1], latest[2], self._dpr)
            if latest is not None
            else None
        )
        return pixmap, ended

    def next_delay_ms(self, *, speed: float = 1.0, now: float | None = None) -> int:
        if self._path is None or self._ended:
            return 33
        current = time.perf_counter() if now is None else now
        clamped_speed = max(0.25, min(3.0, float(speed)))
        remaining = (
            (self._next_time - self._playhead) / clamped_speed
            - max(0.0, current - self._last_clock)
        )
        if remaining <= 0:
            return 16 if self._queue.empty() else 1
        return max(1, min(100, math.ceil(remaining * 1000.0)))

    def close(self) -> None:
        self._stop.set()
        self._thread = None
        self._path = None
        self._ended = True
