"""Lazy, serial TTS ownership with cancellable external provider lifecycles."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
from threading import Event, Lock, RLock

from voice.backends import TTSBackend
from voice.voice_models import SpeechAudio, VoiceCancelledError, VoiceError, VoiceSettings, VoiceUnavailableError


LOGGER = logging.getLogger(__name__)


class TTSService:
    def __init__(self, factory: Callable[[VoiceSettings], TTSBackend] | None = None, *, backend_validates_paths: bool = False) -> None:
        self._factory = factory
        self._backend: TTSBackend | None = None
        self._model_key: tuple[str, ...] | None = None
        self._lock = RLock()
        self._lifecycle_lock = Lock()
        self._closed = False
        self._backend_validates_paths = backend_validates_paths
        self._active_cancel: Event | None = None

    def cancel_current(self) -> None:
        with self._lifecycle_lock:
            if self._active_cancel is not None:
                self._active_cancel.set()
            backend = self._backend
        cancel = getattr(backend, "cancel_current", None)
        if callable(cancel):
            try:
                cancel()
            except Exception as exc:
                LOGGER.warning("[TTS] Backend cancellation failed (%s)", type(exc).__name__)

    def reset_backend(self) -> None:
        """Release an idle model and reset Auto's failure latch after selection."""
        self.cancel_current()
        with self._lifecycle_lock:
            backend, self._backend = self._backend, None
            self._model_key = None
        self._close_backend(backend)

    def close(self) -> None:
        """Reject future work and close the current backend without taking its inference lock."""
        with self._lifecycle_lock:
            self._closed = True
            if self._active_cancel is not None:
                self._active_cancel.set()
            backend, self._backend = self._backend, None
        self._close_backend(backend)

    @staticmethod
    def _close_backend(backend: TTSBackend | None) -> None:
        close = getattr(backend, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:
                LOGGER.warning("[TTS] Backend cleanup failed (%s)", type(exc).__name__)

    def synthesize(self, text: str, settings: VoiceSettings, *, cancelled: Event | None = None) -> SpeechAudio:
        """Synthesize prepared text; reference/speed changes do not reload a model."""
        if not isinstance(text, str) or not text.strip():
            raise VoiceError("没有可朗读的文字。")
        model_path = settings.tts_model_path.strip()
        if not self._backend_validates_paths and model_path and not Path(model_path).expanduser().exists():
            raise VoiceUnavailableError("语音合成模型路径不存在，请检查 TTS 模型设置。")
        reference = settings.reference_audio_path.strip()
        reference_path = Path(reference).expanduser() if reference else None
        if not self._backend_validates_paths and reference_path is not None and not reference_path.is_file():
            raise VoiceUnavailableError("角色参考音频不存在或不是文件，请检查参考音频设置。")
        if self._factory is None:
            raise VoiceUnavailableError("尚未配置语音合成后端；文字聊天仍可正常使用。")
        with self._lock:
            cancelled = cancelled if cancelled is not None else Event()
            with self._lifecycle_lock:
                self._active_cancel = cancelled
            try:
                if cancelled.is_set():
                    raise VoiceCancelledError("朗读已取消。")
                if self._closed:
                    raise VoiceUnavailableError("语音合成服务已关闭。")
                model_key = (model_path, settings.tts_backend, settings.voice_python_path,
                             settings.voice_device, settings.voice_id, settings.voice_language)
                if self._backend is None or self._model_key != model_key:
                    LOGGER.info("[TTS] Loading backend lazily")
                    backend = self._factory(settings)
                    if not callable(getattr(backend, "synthesize", None)):
                        self._close_backend(backend)
                        raise VoiceError("语音合成后端不支持 synthesize。")
                    with self._lifecycle_lock:
                        closed = self._closed or cancelled.is_set()
                        previous = self._backend
                        if not closed:
                            self._backend = backend
                            self._model_key = model_key
                    if closed:
                        self._close_backend(backend)
                        raise VoiceCancelledError("语音合成已取消或关闭。")
                    if previous is not backend:
                        self._close_backend(previous)
                backend = self._backend
                if backend is None or cancelled.is_set():
                    raise VoiceCancelledError("朗读已取消。")
                LOGGER.info("[TTS] Generating speech...")
                arguments = dict(reference_audio_path=reference_path, speech_speed=settings.speech_speed)
                cancellable = getattr(backend, "synthesize_cancellable", None)
                audio = (cancellable(text, cancelled=cancelled, **arguments) if callable(cancellable)
                         else backend.synthesize(text, **arguments))
            finally:
                with self._lifecycle_lock:
                    if self._active_cancel is cancelled:
                        self._active_cancel = None
            if cancelled.is_set():
                raise VoiceCancelledError("朗读已取消。")
            if not isinstance(audio, SpeechAudio):
                raise VoiceError("语音合成后端返回了无效的 PCM 音频。")
            return audio
