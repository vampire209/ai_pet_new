"""User-directory persistence for DeepSeek API connection settings."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

from PySide6.QtCore import QObject, Signal

from ai.ai_config import (
    ALTERNATE_MODEL,
    API_KEY_ENVIRONMENT_VARIABLE,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DeepSeekConfig,
    MODEL_ENVIRONMENT_VARIABLE,
)
from app_paths import ai_settings_path


PRESET_MODELS: tuple[str, ...] = (
    DEFAULT_MODEL,
    ALTERNATE_MODEL,
    "deepseek-chat",
    "deepseek-reasoner",
)


class AiSettingsValidationError(ValueError):
    """Raised when user-entered AI connection settings are invalid."""


class AiSettingsStorageError(RuntimeError):
    """Raised when AI connection settings cannot be written to disk."""


def default_ai_settings_path() -> Path:
    return ai_settings_path()


@dataclass(frozen=True, slots=True)
class AiConnectionSettings:
    """Validated user connection preferences (empty api_key falls back to env)."""

    api_key: str = ""
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL

    def __post_init__(self) -> None:
        if not isinstance(self.api_key, str):
            raise AiSettingsValidationError("API Key 必须是文本。")
        normalized_key = self.api_key.strip()
        if any(ch in normalized_key for ch in ("\r", "\n")):
            raise AiSettingsValidationError("API Key 不能包含换行符。")

        if not isinstance(self.base_url, str):
            raise AiSettingsValidationError("API 地址必须是文本。")
        normalized_url = self.base_url.strip() or DEFAULT_BASE_URL
        if not (
            normalized_url.startswith("https://")
            or normalized_url.startswith("http://")
        ):
            raise AiSettingsValidationError("API 地址必须以 http:// 或 https:// 开头。")

        if not isinstance(self.model, str):
            raise AiSettingsValidationError("模型名称必须是文本。")
        normalized_model = self.model.strip()
        if not normalized_model:
            raise AiSettingsValidationError("模型名称不能为空。")

        object.__setattr__(self, "api_key", normalized_key)
        object.__setattr__(self, "base_url", normalized_url)
        object.__setattr__(self, "model", normalized_model)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "AiConnectionSettings":
        api_key = raw.get("api_key", "")
        base_url = raw.get("base_url", DEFAULT_BASE_URL)
        model = raw.get("model", DEFAULT_MODEL)
        try:
            return cls(
                api_key=api_key if isinstance(api_key, str) else "",
                base_url=base_url if isinstance(base_url, str) else DEFAULT_BASE_URL,
                model=model if isinstance(model, str) else DEFAULT_MODEL,
            )
        except AiSettingsValidationError:
            return cls()

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "model": self.model,
        }

    def resolve_api_key(self) -> str:
        """Prefer explicit GUI-saved key; fall back to DEEPSEEK_API_KEY env var."""
        if self.api_key:
            return self.api_key
        return os.environ.get(API_KEY_ENVIRONMENT_VARIABLE, "").strip()

    def resolve_config(self) -> DeepSeekConfig:
        env_model = os.environ.get(MODEL_ENVIRONMENT_VARIABLE, "").strip()
        effective_model = (
            self.model
            if self.model != DEFAULT_MODEL or not env_model
            else env_model
        )
        return DeepSeekConfig(
            base_url=self.base_url,
            model=effective_model,
        )


class AiSettingsService(QObject):
    """Own saved AI API preferences and notify open UI/clients immediately."""

    settings_changed = Signal(object)

    def __init__(
        self,
        config_path: Path | str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_path = (
            Path(config_path) if config_path is not None else default_ai_settings_path()
        )
        self._settings = self._load()

    @property
    def settings(self) -> AiConnectionSettings:
        return self._settings

    def reload(self) -> AiConnectionSettings:
        self._settings = self._load()
        return self._settings

    def save(self, settings: AiConnectionSettings) -> AiConnectionSettings:
        if not isinstance(settings, AiConnectionSettings):
            raise TypeError("settings 必须是 AiConnectionSettings。")
        temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.config_path)
        except OSError as exc:
            raise AiSettingsStorageError(f"无法保存 AI 接口设置：{exc}") from exc
        self._settings = settings
        self.settings_changed.emit(settings)
        return settings

    def _load(self) -> AiConnectionSettings:
        if not self.config_path.is_file():
            return AiConnectionSettings()
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[AI设置] 配置读取失败，使用默认值：{exc}", file=sys.stderr)
            return AiConnectionSettings()
        if not isinstance(raw, dict):
            return AiConnectionSettings()
        return AiConnectionSettings.from_dict(raw)
