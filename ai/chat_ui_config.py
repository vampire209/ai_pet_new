"""Persistent, non-business settings for the native chat UI."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys

from PySide6.QtCore import QStandardPaths


SUPPORTED_BACKGROUND_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class ChatUiSettingsError(RuntimeError):
    """Raised when a requested UI preference cannot be persisted."""


def default_chat_ui_config_path() -> Path:
    root = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericConfigLocation
    )
    base = Path(root) if root else Path.home() / ".config"
    return base / "ai_pet_new" / "chat_ui.json"


@dataclass(slots=True)
class ChatUiSettings:
    """Store only user-selected chat presentation preferences."""

    config_path: Path | None = None

    def __post_init__(self) -> None:
        if self.config_path is None:
            self.config_path = default_chat_ui_config_path()
        else:
            self.config_path = Path(self.config_path)

    def load_background_path(self) -> Path | None:
        path = self.config_path
        if path is None or not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[聊天界面] 配置读取失败，使用默认背景：{exc}", file=sys.stderr)
            return None
        if not isinstance(raw, dict):
            return None
        value = raw.get("background_path")
        if not isinstance(value, str) or not value.strip():
            return None
        background = Path(value).expanduser()
        if (
            background.suffix.lower() not in SUPPORTED_BACKGROUND_SUFFIXES
            or not background.is_file()
        ):
            return None
        return background

    def save_background_path(self, background_path: Path | None) -> None:
        path = self.config_path
        if path is None:
            raise ChatUiSettingsError("聊天界面配置路径不可用。")
        normalized: str | None = None
        if background_path is not None:
            candidate = Path(background_path).expanduser()
            if (
                candidate.suffix.lower() not in SUPPORTED_BACKGROUND_SUFFIXES
                or not candidate.is_file()
            ):
                raise ChatUiSettingsError("所选背景文件无效或已经不存在。")
            normalized = str(candidate.resolve())

        payload = {
            "version": 1,
            "background_path": normalized,
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as exc:
            raise ChatUiSettingsError(f"无法保存聊天界面配置：{exc}") from exc

    def restore_default_background(self) -> None:
        self.save_background_path(None)
