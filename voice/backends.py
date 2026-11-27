"""Replaceable local-first STT/TTS contracts with no model SDK imports."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from voice.voice_models import SpeechAudio


class STTBackend(Protocol):
    """Transcribe a caller-owned file; implementations must not delete it."""

    def transcribe(self, audio_path: Path) -> str: ...


class TTSBackend(Protocol):
    """Read the supplied text only; this backend does not generate chat content."""

    def synthesize(
        self,
        text: str,
        *,
        reference_audio_path: Path | None,
        speech_speed: float,
    ) -> SpeechAudio: ...
