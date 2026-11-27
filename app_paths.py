"""Unified user-writable data and configuration paths for AI Pet."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QStandardPaths


APP_DATA_DIR_NAME = "ai_pet_new"


def user_data_dir() -> Path:
    """Return one consistent per-user config/data directory for source & packaged runs."""

    root = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericConfigLocation
    )
    base = Path(root) if root else Path.home() / ".config"
    return base / APP_DATA_DIR_NAME


def tasks_data_path() -> Path:
    return user_data_dir() / "tasks.json"


def profile_config_path() -> Path:
    return user_data_dir() / "profile.json"


def chat_ui_config_path() -> Path:
    return user_data_dir() / "chat_ui.json"


def chat_history_path() -> Path:
    return user_data_dir() / "chat_history.json"


def voice_settings_path() -> Path:
    return user_data_dir() / "voice_settings.json"


def ai_settings_path() -> Path:
    return user_data_dir() / "ai_settings.json"


def pet_state_path() -> Path:
    return user_data_dir() / "pet_state.json"


def memory_db_path() -> Path:
    return user_data_dir() / "memory.db"


def user_app_config_path() -> Path:
    return user_data_dir() / "config.json"
