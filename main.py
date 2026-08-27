"""AI desktop pet Phase 5 entry point."""

from __future__ import annotations

import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from config import AppConfig
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager, TodoStorageError


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
    app.setQuitOnLastWindowClosed(False)

    try:
        todo_manager = TodoManager()
    except TodoStorageError as exc:
        print(f"[启动失败] {exc}", file=sys.stderr)
        return 1

    reminder_manager = ReminderManager(todo_manager)
    pet = PetWindow(AppConfig.load(), todo_manager, reminder_manager)
    pet.show()
    reminder_manager.start(immediate=True)
    app.aboutToQuit.connect(pet.close)

    if smoke_test:
        QTimer.singleShot(1200, app.quit)

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
