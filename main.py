"""AI desktop pet application entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from ai.ai_settings_service import AiSettingsService
from ai.chat_history_store import ChatHistoryStore
from ai.profile_service import ProfileService
from config import AppConfig
from memory.memory_service import MemoryService
from memory.memory_store import MemoryStorageError
from pet_state import PetStateStore
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager, TodoStorageError
from ui_resources import load_application_icon
from voice.settings_store import VoiceSettingsStore


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv if argv is None else argv)
    smoke_test = "--smoke-test" in args
    qt_args = [arg for arg in args if arg != "--smoke-test"]

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(qt_args)
    app.setApplicationName("AIPetNew")
    app.setApplicationDisplayName("AI 桌宠")
    app.setWindowIcon(load_application_icon())
    app.setQuitOnLastWindowClosed(False)

    try:
        todo_manager = TodoManager()
    except TodoStorageError as exc:
        print(f"[启动失败] {exc}", file=sys.stderr)
        return 1

    reminder_manager = ReminderManager(todo_manager)
    profile_service = ProfileService()
    ai_settings_service = AiSettingsService()
    chat_history_store = None if smoke_test else ChatHistoryStore()
    voice_settings_store = None if smoke_test else VoiceSettingsStore()
    pet_state_store = None if smoke_test else PetStateStore()
    try:
        memory_service = MemoryService(":memory:" if smoke_test else None)
    except MemoryStorageError as exc:
        print(f"[启动失败] {exc}", file=sys.stderr)
        return 1

    pet = PetWindow(
        AppConfig.load(),
        todo_manager,
        reminder_manager,
        profile_service=profile_service,
        memory_service=memory_service,
        ai_settings_service=ai_settings_service,
        chat_history_store=chat_history_store,
        voice_settings_store=voice_settings_store,
        state_store=pet_state_store,
    )
    pet.show()
    reminder_manager.start(immediate=True)
    app.aboutToQuit.connect(pet.close)

    if smoke_test:
        QTimer.singleShot(1200, app.quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
