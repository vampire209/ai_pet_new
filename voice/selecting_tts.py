"""Choose the requested local provider and contain failures within speech."""

from dataclasses import replace
import logging
from threading import Event, Lock

from voice.neural_contracts import NeuralRuntimeConfig
from voice.qwen_backend import QwenCustomVoiceBackend
from voice.voice_models import VoiceCancelledError, VoiceSettings, VoiceUnavailableError
from voice.windows_tts import WindowsTTSBackend

LOGGER = logging.getLogger(__name__)


def _release(backend, method):
    action = getattr(backend, method, None)
    if callable(action):
        try:
            action()
        except Exception as exc:
            LOGGER.warning("[TTS] Provider cleanup failed (%s)", type(exc).__name__)


class SelectingTTSBackend:
    def __init__(self, settings: VoiceSettings, *, local_factory=None, system_factory=None):
        self.settings = settings
        self._local_factory = local_factory or (lambda: QwenCustomVoiceBackend(
            NeuralRuntimeConfig(python_executable=settings.voice_python_path,
                                model_path=settings.tts_model_path, device=settings.voice_device),
            voice_id=settings.voice_id, language=settings.voice_language))
        self._system_factory = system_factory or WindowsTTSBackend
        self._local = self._system = None
        self._lock = Lock()
        self._closed = False
        self._local_failure = ""

    def _check(self, cancelled):
        if self._closed or cancelled.is_set():
            raise VoiceCancelledError("朗读已取消。")

    def _provider(self, name, cancelled):
        with self._lock:
            self._check(cancelled)
            backend = getattr(self, name)
            if backend is None:
                backend = (self._local_factory if name == "_local" else self._system_factory)()
                setattr(self, name, backend)
            return backend

    def synthesize(self, text, *, reference_audio_path=None, speech_speed=1.0):
        return self.synthesize_cancellable(text, reference_audio_path=reference_audio_path,
                                          speech_speed=speech_speed, cancelled=Event())

    def synthesize_cancellable(self, text, *, reference_audio_path=None, speech_speed=1.0, cancelled):
        self._check(cancelled)
        if reference_audio_path is not None:
            raise VoiceUnavailableError("本阶段的本地音色和系统声音均不支持参考录音，请清空参考音频。")
        arguments = dict(reference_audio_path=None, speech_speed=speech_speed)
        choice = self.settings.tts_backend
        if choice != "system" and not self._local_failure:
            try:
                backend = self._provider("_local", cancelled)
                cancellable = getattr(backend, "synthesize_cancellable", None)
                audio = (cancellable(text, cancelled=cancelled, **arguments) if callable(cancellable)
                         else backend.synthesize(text, **arguments))
                self._check(cancelled)
                return replace(audio, backend_label="本地声音 · " + self.settings.voice_id)
            except VoiceCancelledError:
                raise
            except Exception as exc:
                self._check(cancelled)
                if choice == "local_neural":
                    raise VoiceUnavailableError(f"本地声音不可用：{str(exc)[:600]} 可选择“自动”回退到系统声音。") from exc
                self._local_failure = str(exc)[:600] or "本地模型不可用"
                with self._lock:
                    backend, self._local = self._local, None
                _release(backend, "close")
        self._check(cancelled)
        backend = self._provider("_system", cancelled)
        self._check(cancelled)
        audio = backend.synthesize(text, **arguments)
        self._check(cancelled)
        notice = "本地声音不可用，已回退：" + self._local_failure if self._local_failure else ""
        return replace(audio, backend_label="系统声音", notice=notice)

    def cancel_current(self):
        with self._lock:
            local, system = self._local, self._system
        for backend in (local, system):
            _release(backend, "cancel_current")

    def close(self):
        with self._lock:
            self._closed = True
            backends = self._local, self._system
            self._local = self._system = None
        for backend in backends:
            _release(backend, "close")
