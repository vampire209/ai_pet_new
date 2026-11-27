"""Lazy backend ownership for STT; call from a worker, never a UI callback."""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
from threading import RLock

from voice.backends import STTBackend
from voice.voice_models import VoiceError, VoiceSettings, VoiceUnavailableError


LOGGER = logging.getLogger(__name__)


class STTService:
    def __init__(self, factory: Callable[[VoiceSettings], STTBackend] | None = None) -> None:
        self._factory = factory
        self._backend: STTBackend | None = None
        self._model_key: str | None = None
        self._lock = RLock()

    def transcribe(self, audio_path: str | Path, settings: VoiceSettings) -> str:
        """Load on first use and reuse until the STT model path changes.

        The lock also protects model instances if callers share this wrapper
        across workers. Feature-enable gating belongs to VoiceService.
        """
        source = Path(audio_path).expanduser()
        if not source.is_file():
            raise VoiceError("录音文件不存在或不是文件，请重新录音。")
        model_path = settings.stt_model_path.strip()
        if model_path and not Path(model_path).expanduser().exists():
            raise VoiceUnavailableError("语音识别模型路径不存在，请检查 STT 模型设置。")
        if self._factory is None:
            raise VoiceUnavailableError("尚未配置语音识别后端；文字聊天仍可正常使用。")
        with self._lock:
            if self._backend is None or self._model_key != model_path:
                LOGGER.info("[STT] Loading backend lazily")
                backend = self._factory(settings)
                if not callable(getattr(backend, "transcribe", None)):
                    raise VoiceError("语音识别后端不支持 transcribe。")
                self._backend = backend
                self._model_key = model_path
            LOGGER.info("[STT] Transcribing...")
            result = self._backend.transcribe(source)
            if not isinstance(result, str):
                raise VoiceError("语音识别后端返回了无效的文字结果。")
            # Avoid logging private speech content.
            LOGGER.info("[STT] Recognition completed (%d characters)", len(result))
            return result
