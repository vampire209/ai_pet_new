"""Atomic JSON persistence for user voice reply preferences."""

from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path
import sys
from typing import Any

from app_paths import voice_settings_path
from voice.voice_models import VoiceSettings


_VALID_BACKENDS = frozenset({"auto", "local_neural", "system"})


def default_voice_settings_path() -> Path:
    return voice_settings_path()


class VoiceSettingsStore:
    """Persist lightweight user-chosen voice toggles without overriding runtime paths."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        self.config_path = (
            Path(config_path)
            if config_path is not None
            else default_voice_settings_path()
        )

    def apply_to(self, base: VoiceSettings) -> VoiceSettings:
        if not self.config_path.is_file():
            return base
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[语音设置] 读取失败，使用默认值：{exc}", file=sys.stderr)
            return base
        if not isinstance(raw, dict):
            return base

        updates: dict[str, Any] = {}
        enabled = raw.get("enable_voice_reply")
        if isinstance(enabled, bool):
            updates["enable_voice_reply"] = enabled

        backend = raw.get("tts_backend")
        if isinstance(backend, str) and backend in _VALID_BACKENDS:
            updates["tts_backend"] = backend

        speed = raw.get("speech_speed")
        if (
            isinstance(speed, (int, float))
            and not isinstance(speed, bool)
            and math.isfinite(speed)
            and 0.5 <= float(speed) <= 2.0
        ):
            updates["speech_speed"] = float(speed)

        volume = raw.get("volume")
        if (
            isinstance(volume, (int, float))
            and not isinstance(volume, bool)
            and math.isfinite(volume)
            and 0.0 <= float(volume) <= 1.0
        ):
            updates["volume"] = float(volume)

        if not updates:
            return base
        return replace(base, **updates)

    def save_from(self, settings: VoiceSettings) -> bool:
        payload = {
            "version": 1,
            "enable_voice_reply": bool(settings.enable_voice_reply),
            "tts_backend": settings.tts_backend,
            "speech_speed": float(settings.speech_speed),
            "volume": float(settings.volume),
        }
        temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.config_path)
            return True
        except OSError as exc:
            print(f"[语音设置] 保存失败：{exc}", file=sys.stderr)
            return False
