"""Non-blocking playback boundary; concrete audio-device support is Phase 11C."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from voice.voice_models import SpeechAudio, VoiceUnavailableError


class AudioPlayer(QObject):
    """Implement on the VoiceService/UI thread without blocking it.

    ``finished`` and ``error`` must carry the playback ID passed to ``play``.
    This lets the coordinator ignore callbacks from an already-stopped clip.
    Device backends must replace/stop previous playback, never mix AI voices.
    """

    finished = Signal(int)
    error = Signal(int, str)

    def play(self, audio: SpeechAudio, *, playback_id: int, volume: float = 1.0) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def is_playing(self) -> bool:
        raise NotImplementedError


class UnavailableAudioPlayer(AudioPlayer):
    """Safe default: importing/starting the app never opens an audio device."""

    def play(self, audio: SpeechAudio, *, playback_id: int, volume: float = 1.0) -> None:
        raise VoiceUnavailableError("尚未配置语音播放器；文字聊天仍可正常使用。")

    def stop(self) -> None:
        pass

    def is_playing(self) -> bool:
        return False
