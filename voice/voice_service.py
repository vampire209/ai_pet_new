"""Coordinate optional voice inference without owning any chat business logic."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections import deque
import logging
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from voice.audio_player import AudioPlayer, UnavailableAudioPlayer
from voice.stt_service import STTService
from voice.streaming_text import SpeechSegmenter, split_for_speech
from voice.text import prepare_text_for_speech
from voice.tts_service import TTSService
from voice.voice_models import (
    SpeechAudio, VoiceSettings, VoiceState, VoiceUnavailableError,
)


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Request:
    request_id: int
    generation: int
    kind: str
    payload: str | Path
    settings: VoiceSettings


class _WorkerSignals(QObject):
    completed = Signal(int, int, str, object)
    failed = Signal(int, int, str, object)
    finished = Signal(int)


class _VoiceWorker(QRunnable):
    def __init__(self, request: _Request, stt: STTService, tts: TTSService):
        super().__init__()
        # Like ChatStreamWorker, keep the wrapper alive until finished is received.
        self.setAutoDelete(False)
        self.signals = _WorkerSignals()
        self.cancelled = Event()
        self.request = request
        self.stt = stt
        self.tts = tts

    def run(self) -> None:
        request = self.request
        try:
            if self.cancelled.is_set():
                return
            if request.kind == "stt":
                result = self.stt.transcribe(Path(request.payload), request.settings)
            else:
                result = self.tts.synthesize(str(request.payload), request.settings, cancelled=self.cancelled)
            if not self.cancelled.is_set():
                self.signals.completed.emit(
                    request.request_id, request.generation, request.kind, result,
                )
        except Exception as exc:
            if not self.cancelled.is_set():
                self.signals.failed.emit(
                    request.request_id, request.generation, request.kind, exc,
                )
        finally:
            self.signals.finished.emit(request.request_id)


class VoiceService(QObject):
    """UI-thread coordinator with lazy inference on a Qt worker thread.

    Standalone speech keeps only the latest replacement. Streamed replies queue
    sentences in order with one playing clip and at most one synthesized ahead.
    Neither this object nor its backends call ChatService, Memory or Todo.
    """

    text_recognized = Signal(str)
    state_changed = Signal(object)
    activity_changed = Signal()
    notice = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        *,
        settings: VoiceSettings | None = None,
        stt_service: STTService | None = None,
        tts_service: TTSService | None = None,
        audio_player: AudioPlayer | None = None,
        thread_pool: QThreadPool | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings or VoiceSettings()
        self._stt = stt_service or STTService()
        self._tts = tts_service or TTSService()
        self._player = audio_player or UnavailableAudioPlayer(self)
        self._pool = thread_pool or QThreadPool.globalInstance()
        self._state = VoiceState.IDLE
        self._generation = 0
        self._request_serial = 0
        self._worker: _VoiceWorker | None = None
        self._pending: _Request | None = None
        self._closed = False
        self._segmenter = SpeechSegmenter()
        self._stream_mode = False
        self._stream_open = False
        self._speech_queue: deque[_Request] = deque()
        self._queued_characters = 0
        self._ready_audio: tuple[_Request, SpeechAudio] | None = None
        self._playback_id: int | None = None
        self._playback_generation = -1
        self._player.finished.connect(self._on_playback_finished)
        self._player.error.connect(self._on_playback_error)

    @property
    def settings(self) -> VoiceSettings:
        return self._settings

    @property
    def state(self) -> VoiceState:
        return self._state

    @property
    def is_busy(self) -> bool:
        """Includes cancelled inference still unwinding in the background."""
        return bool(self._worker or self._pending or self._speech_queue or self._ready_audio)

    @property
    def is_speaking(self) -> bool:
        return self._stream_mode or self._state in (VoiceState.SYNTHESIZING, VoiceState.PLAYING)

    def configure(self, settings: VoiceSettings) -> None:
        if self._closed:
            return
        self.stop()
        identity = ("tts_backend", "tts_model_path", "voice_python_path", "voice_device", "voice_id", "voice_language")
        if any(getattr(settings, name) != getattr(self._settings, name) for name in identity):
            self._tts.reset_backend()
        self._settings = settings
        self.activity_changed.emit()

    def begin_reply(self) -> bool:
        """Start a sentence-stream session; callers feed only this reply's chunks."""
        if self._closed or not self._settings.enable_voice_reply:
            return False
        if not self.stop():
            return False
        self._stream_mode = self._stream_open = True
        self.activity_changed.emit()
        return True

    def append_reply(self, chunk: str) -> None:
        if self._closed or not self._stream_open:
            return
        try:
            self._enqueue_sentences(self._segmenter.feed(chunk))
        except Exception as exc:
            self._stream_text_failed(exc)

    def end_reply(self) -> None:
        if self._closed or not self._stream_open:
            return
        self._stream_open = False
        try:
            self._enqueue_sentences(self._segmenter.finish())
        except Exception as exc:
            self._stream_text_failed(exc)

    def _stream_text_failed(self, exc: Exception) -> None:
        LOGGER.warning("[Voice] Sentence preparation failed (%s)", type(exc).__name__)
        self.stop()
        self.error.emit("本轮语音文本解析失败，已停止朗读；文字聊天仍可继续。")

    def _enqueue_sentences(self, sentences: list[str]) -> None:
        for sentence in sentences:
            # Text may arrive much faster than speech. Never build an unlimited
            # multi-minute backlog or silently skip an arbitrary middle sentence.
            if len(self._speech_queue) >= 64 or self._queued_characters + len(sentence) > 12000:
                self.stop()
                self.notice.emit("本轮朗读积压过多，已停止语音；文字会继续显示。")
                return
            self._speech_queue.append(self._new_request("tts", sentence))
            self._queued_characters += len(sentence)
        self._pump_stream()

    def _pump_stream(self) -> None:
        if not self._stream_mode or self._closed:
            return
        if self._ready_audio is not None and self._playback_id is None:
            request, audio = self._ready_audio
            self._ready_audio = None
            self._play(request, audio)
            if not self._stream_mode:
                return
        # Pre-synthesize at most one sentence while the preceding one plays.
        if self._worker is None and self._ready_audio is None and self._speech_queue:
            request = self._speech_queue.popleft()
            self._queued_characters -= len(str(request.payload))
            if self._playback_id is None:
                self._set_state(VoiceState.SYNTHESIZING)
            self._start(request)
        if not self.is_busy and self._playback_id is None:
            self._set_state(VoiceState.IDLE)
            if not self._stream_open:
                self._stream_mode = False
                self.notice.emit("语音播放完成。")
        elif self._playback_id is None:
            self._set_state(VoiceState.SYNTHESIZING)
        self.activity_changed.emit()

    def transcribe(self, audio_path: str | Path) -> bool:
        if self._closed or not self._settings.enable_stt:
            return False
        self.stop()
        self._set_state(VoiceState.PROCESSING)
        self.notice.emit("正在识别……")
        LOGGER.info("[STT] Transcribing")
        self._schedule("stt", Path(audio_path))
        return True

    def speak(self, text: str) -> bool:
        """Replace previous speech with one complete visible reply."""
        if self._closed or not self._settings.enable_voice_reply:
            return False
        return self._speak_once(text)

    def preview(self, text: str = "你好呀，今天也一起加油吧。") -> bool:
        """Explicit local audition, even with auto-reply OFF; never calls chat."""
        if self._closed:
            return False
        return self._speak_once(text)

    def _speak_once(self, text: str) -> bool:
        sentences = split_for_speech(text)
        # Preserve the legacy short single-clip mode for injected consumers.
        # The desktop uses sentence mode; long text is always bounded.
        cleaned = prepare_text_for_speech(text)
        if not self._settings.stream_sentences and len(cleaned) <= 240:
            sentences = [cleaned] if cleaned else []
        if not self.stop():
            return False
        if not sentences:
            return False
        self._set_state(VoiceState.SYNTHESIZING)
        self.notice.emit("正在准备语音……")
        LOGGER.info("[TTS] Generating speech")
        if len(sentences) == 1:
            self._schedule("tts", sentences[0])
        else:
            self._stream_mode = True
            self._enqueue_sentences(sentences)
        return True

    def stop(self) -> bool:
        self._generation += 1
        self._pending = None
        self._stream_mode = self._stream_open = False
        self._segmenter.reset()
        self._speech_queue.clear()
        self._queued_characters = 0
        self._ready_audio = None
        self._playback_id = None
        if self._worker is not None:
            self._worker.cancelled.set()
            if self._worker.request.kind == "tts":
                self._tts.cancel_current()
        try:
            self._player.stop()
            if self._player.is_playing():
                raise RuntimeError("Player did not stop")
        except Exception as exc:
            # A broken device must not prevent the next text message being sent.
            # Disable further replies instead of risking overlapping playback.
            self._settings = replace(self._settings, enable_voice_reply=False)
            LOGGER.warning("[Audio] Stop failed (%s)", type(exc).__name__)
            self.error.emit("停止语音播放失败；文字聊天仍可继续。")
            self._set_state(VoiceState.CLOSED if self._closed else VoiceState.IDLE)
            self.activity_changed.emit()
            return False
        self._set_state(VoiceState.CLOSED if self._closed else VoiceState.IDLE)
        self.activity_changed.emit()
        return True

    def shutdown(self) -> None:
        """Idempotent and non-blocking; late worker/player signals are ignored."""
        if self._closed:
            return
        self._closed = True
        self.stop()
        # Real local backends may own a persistent helper. Their close contract
        # must be non-blocking and safe during an in-flight inference.
        self._tts.close()
        LOGGER.info("[Voice] Shutdown")

    def _set_state(self, state: VoiceState) -> None:
        if self._state != state:
            self._state = state
            self.state_changed.emit(state)

    def _new_request(self, kind: str, payload: str | Path) -> _Request:
        self._request_serial += 1
        return _Request(self._request_serial, self._generation, kind, payload, self._settings)

    def _schedule(self, kind: str, payload: str | Path) -> None:
        request = self._new_request(kind, payload)
        if self._worker is not None:
            self._pending = request
        else:
            self._start(request)

    def _start(self, request: _Request) -> None:
        worker = _VoiceWorker(request, self._stt, self._tts)
        self._worker = worker
        worker.signals.completed.connect(self._on_completed)
        worker.signals.failed.connect(self._on_failed)
        worker.signals.finished.connect(self._on_finished)
        try:
            self._pool.start(worker)
        except Exception as exc:
            self._worker = None
            self._on_failed(request.request_id, request.generation, request.kind, exc)

    def _is_current(self, generation: int) -> bool:
        return not self._closed and generation == self._generation

    @Slot(int, int, str, object)
    def _on_completed(self, request_id: int, generation: int, kind: str, result: object) -> None:
        if not self._is_current(generation):
            return
        if kind == "stt":
            self._set_state(VoiceState.IDLE)
            text = result.strip() if isinstance(result, str) else ""
            if text:
                LOGGER.info("[STT] Recognition completed (%d characters)", len(text))
                self.text_recognized.emit(text)
            else:
                self.notice.emit("没有听清，再说一次吧。")
            return
        try:
            if not isinstance(result, SpeechAudio):
                raise TypeError("TTS backend must return SpeechAudio")
            request = self._worker.request
            if self._stream_mode:
                self._ready_audio = (request, result)
                self._pump_stream()
            else:
                self._play(request, result)
        except Exception as exc:
            self._on_failed(request_id, generation, "audio", exc)

    def _play(self, request: _Request, audio: SpeechAudio) -> None:
        try:
            self._playback_id = request.request_id
            self._playback_generation = request.generation
            self._set_state(VoiceState.PLAYING)
            self._player.play(audio, playback_id=request.request_id, volume=request.settings.volume)
            if audio.backend_label:
                self.notice.emit(audio.backend_label + (" · " + audio.notice if audio.notice else " · 正在朗读"))
            LOGGER.info("[Audio] Playback started")
        except Exception as exc:
            self._on_failed(request.request_id, request.generation, "audio", exc)

    @Slot(int, int, str, object)
    def _on_failed(self, request_id: int, generation: int, kind: str, exc: Exception) -> None:
        if not self._is_current(generation):
            return
        if isinstance(exc, VoiceUnavailableError):
            field = "enable_stt" if kind == "stt" else "enable_voice_reply"
            self._settings = replace(self._settings, **{field: False})
            message = str(exc)
        elif isinstance(exc, MemoryError) or "out of memory" in str(exc).lower():
            message = "语音模型的内存或显存不足，请更换较小模型。"
        else:
            message = {
                "stt": "语音识别失败，请重试。",
                "tts": "语音合成失败，请重试。",
                "audio": "语音播放失败，请检查音频设备。",
            }.get(kind, "语音功能暂不可用。")
        LOGGER.warning("[Voice] %s failed (%s)", kind, type(exc).__name__)
        if kind == "audio" or self._stream_mode:
            self.stop()
        else:
            self._set_state(VoiceState.IDLE)
        self.error.emit(f"{message} 文字聊天仍可继续。")
        self.activity_changed.emit()

    @Slot(int)
    def _on_finished(self, request_id: int) -> None:
        if self._worker is not None and self._worker.request.request_id == request_id:
            self._worker = None
        pending, self._pending = self._pending, None
        if pending is not None and self._is_current(pending.generation):
            self._start(pending)
        self._pump_stream()

    @Slot(int)
    def _on_playback_finished(self, playback_id: int) -> None:
        if playback_id != self._playback_id or not self._is_current(self._playback_generation):
            return
        self._playback_id = None
        if self._stream_mode:
            self._pump_stream()
        else:
            self._set_state(VoiceState.IDLE)
            self.notice.emit("语音播放完成。")
        self.activity_changed.emit()

    @Slot(int, str)
    def _on_playback_error(self, playback_id: int, _message: str) -> None:
        if playback_id == self._playback_id:
            self._on_failed(playback_id, self._playback_generation, "audio", RuntimeError("Playback failed"))
