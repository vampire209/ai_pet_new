"""Compose the Windows desktop's optional voice perimeter without loading it."""

from pathlib import Path
import sys

from PySide6.QtCore import QObject

from voice.tts_service import TTSService
from voice.voice_models import VoiceSettings
from voice.voice_service import VoiceService
from voice.windows_audio_player import WindowsAudioPlayer
from voice.selecting_tts import SelectingTTSBackend


def create_desktop_voice_service(parent: QObject | None = None) -> VoiceService:
    # No process/device/model is opened until explicit preview or enabled reply.
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    return VoiceService(
        settings=VoiceSettings(stream_sentences=True, tts_backend="auto",
                               voice_python_path=str(root / "runtime/voice_env/Scripts/python.exe"),
                               tts_model_path=str(root / "models/qwen3_tts/0.6b_custom_voice")),
        tts_service=TTSService(SelectingTTSBackend, backend_validates_paths=True),
        audio_player=WindowsAudioPlayer(parent),
        parent=parent,
    )
