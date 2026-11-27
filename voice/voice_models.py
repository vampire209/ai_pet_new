"""Small, model-independent data contracts for the optional voice feature."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math


class VoiceError(Exception):
    """A voice-only failure which must not interrupt text chat."""


class VoiceUnavailableError(VoiceError):
    """An optional backend, model or reference resource is unavailable."""


class VoiceCancelledError(VoiceError):
    """Cancellation is never a reason to start a fallback synthesis."""


class VoiceState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"  # Reserved for Phase 11B; no microphone is opened yet.
    PROCESSING = "processing"
    SYNTHESIZING = "synthesizing"
    PLAYING = "playing"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    """Immutable per-operation settings; disk persistence belongs to Phase 11E."""

    enable_stt: bool = False
    enable_voice_reply: bool = False
    stream_sentences: bool = False
    stt_model_path: str = ""
    tts_model_path: str = ""
    reference_audio_path: str = ""
    speech_speed: float = 1.0
    volume: float = 1.0
    tts_backend: str = "system"
    voice_python_path: str = ""
    voice_device: str = "cuda:0"
    voice_id: str = "Serena"
    voice_language: str = "Chinese"

    def __post_init__(self) -> None:
        for name in ("enable_stt", "enable_voice_reply", "stream_sentences"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} 必须是布尔值。")
        for name in ("stt_model_path", "tts_model_path", "reference_audio_path"):
            if not isinstance(getattr(self, name), str):
                raise ValueError(f"{name} 必须是路径字符串。")
        if self.tts_backend not in ("auto", "local_neural", "system"):
            raise ValueError("未知的语音后端。")
        for name in ("voice_python_path", "voice_device", "voice_id", "voice_language"):
            value = getattr(self, name)
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError(f"{name} 必须是字符串且不含 NUL。")
        for name, minimum, maximum in (("speech_speed", 0.5, 2.0), ("volume", 0.0, 1.0)):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not minimum <= value <= maximum
            ):
                raise ValueError(f"{name} 必须在 {minimum} 到 {maximum} 之间。")


@dataclass(frozen=True, slots=True)
class SpeechAudio:
    """Interleaved PCM bytes, not an owned file; no temporary audio is written."""

    data: bytes
    sample_rate: int = 24000
    channels: int = 1
    sample_width: int = 2
    backend_label: str = ""
    notice: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            raise ValueError("语音音频必须是 PCM bytes。")
        for name in ("sample_rate", "channels", "sample_width"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} 必须是正整数。")
        if self.sample_width not in (1, 2, 3, 4):
            raise ValueError("PCM 采样宽度必须为 1、2、3 或 4 字节。")
        if not self.data or len(self.data) % (self.channels * self.sample_width):
            raise ValueError("PCM 音频不能为空且必须包含完整的采样帧。")
