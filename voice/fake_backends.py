"""Deterministic, silent test doubles: no GPU, network, microphone or files."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject

from voice.audio_player import AudioPlayer
from voice.voice_models import SpeechAudio


class FakeSTTBackend:
    """``calls`` records caller-owned input Paths, including failed calls."""

    def __init__(self, text: str = "你好，桌宠。", *, error: Exception | None = None) -> None:
        self.text = text
        self.error = error
        self.calls: list[Path] = []

    def transcribe(self, audio_path: Path) -> str:
        self.calls.append(audio_path)
        if self.error is not None:
            raise self.error
        return self.text


class FakeTTSBackend:
    """``calls`` is a list of dicts with text/reference_audio_path/speech_speed."""

    def __init__(
        self, *, error: Exception | None = None, audio: SpeechAudio | None = None
    ) -> None:
        self.error = error
        self.audio = audio if audio is not None else SpeechAudio(b"\x00" * 480)
        self.calls: list[dict[str, object]] = []

    def synthesize(
        self, text: str, *, reference_audio_path: Path | None, speech_speed: float
    ) -> SpeechAudio:
        self.calls.append(
            {
                "text": text,
                "reference_audio_path": reference_audio_path,
                "speech_speed": speech_speed,
            }
        )
        if self.error is not None:
            raise self.error
        return self.audio


class FakeAudioPlayer(AudioPlayer):
    """Silent player which stays active until ``finish``/``fail``/``stop``.

    ``play_history`` contains SpeechAudio values; ``playback_ids`` and
    ``volumes`` are parallel lists. Failed play attempts are also recorded.
    ``stop_count`` counts every explicit stop call, including while idle.
    """

    def __init__(self, *, play_error: Exception | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.play_error = play_error
        self.play_history: list[SpeechAudio] = []
        self.playback_ids: list[int] = []
        self.volumes: list[float] = []
        self.stop_count = 0
        self._playing = False
        self._playback_id: int | None = None

    def play(self, audio: SpeechAudio, *, playback_id: int, volume: float = 1.0) -> None:
        self.play_history.append(audio)
        self.playback_ids.append(playback_id)
        self.volumes.append(volume)
        self._playing = False
        self._playback_id = None
        if self.play_error is not None:
            raise self.play_error
        self._playback_id = playback_id
        self._playing = True

    def stop(self) -> None:
        self.stop_count += 1
        self._playing = False
        self._playback_id = None

    def is_playing(self) -> bool:
        return self._playing

    def finish(self) -> None:
        playback_id = self._playback_id
        if playback_id is None:
            return
        self._playing = False
        self._playback_id = None
        self.finished.emit(playback_id)

    def fail(self, message: str = "模拟音频播放失败。") -> None:
        playback_id = self._playback_id
        if playback_id is None:
            return
        self._playing = False
        self._playback_id = None
        self.error.emit(playback_id, message)
