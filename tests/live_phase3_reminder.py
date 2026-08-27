"""Manual-duration Phase 3 probe: a real local-time reminder two minutes ahead."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QPushButton

from config import AppConfig
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager


class LiveReminderProbe(QObject):
    def __init__(self, app: QApplication, delay_seconds: int = 120) -> None:
        super().__init__()
        self.app = app
        self.result = 1
        self.trigger_count = 0
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_path = Path(self.temp_dir.name) / "tasks.json"
        self.manager = TodoManager(self.data_path)
        self.target = (
            datetime.now() + timedelta(seconds=delay_seconds)
        ).replace(microsecond=0)
        self.task = self.manager.add_task(
            "Phase 3 真实两分钟提醒测试",
            self.target.isoformat(timespec="seconds"),
        )
        self.reminder_manager = ReminderManager(self.manager)
        self.pet = PetWindow(
            AppConfig.load(),
            self.manager,
            self.reminder_manager,
        )
        self.reminder_manager.reminder_due.connect(self._on_due)

    def start(self) -> None:
        print(
            "LIVE_TARGET "
            + self.target.isoformat(timespec="seconds")
            + " local",
            flush=True,
        )
        self.pet.show()
        self.reminder_manager.start(immediate=True)
        QTimer.singleShot(190_000, self._timeout)

    def _on_due(self, task) -> None:
        if task.id != self.task.id:
            return
        self.trigger_count += 1
        triggered_at = datetime.now().isoformat(timespec="seconds")
        print(
            f"LIVE_TRIGGER {triggered_at} count={self.trigger_count}",
            flush=True,
        )
        if self.trigger_count == 1:
            QTimer.singleShot(31_000, self._finish)

    def _finish(self) -> None:
        persisted = TodoManager(self.data_path).get_task(self.task.id)
        conditions = {
            "one_signal": self.trigger_count == 1,
            "persisted": persisted is not None,
            "time_unchanged": (
                persisted is not None and persisted.event_at == self.task.event_at
            ),
            "triggered": persisted is not None and persisted.reminder_triggered,
            "not_completed": persisted is not None and not persisted.completed,
        }
        print(f"LIVE_STATE {conditions}", flush=True)
        if not all(conditions.values()):
            self._fail("持久化状态、单次触发或提醒窗口检查失败")
            return

        if not persisted.reminder_acknowledged:
            if len(self.pet.reminder_dialogs) != 1:
                self._fail("提醒未确认，但提醒窗口已不存在")
                return
            acknowledge = self.pet.reminder_dialogs[0].findChild(
                QPushButton,
                "acknowledgeButton",
            )
            if acknowledge is None:
                self._fail("未找到“知道了”按钮")
                return
            acknowledge.click()
            self.app.processEvents()
        else:
            print("LIVE_EARLY_ACK user acknowledged before 31-second check", flush=True)
        acknowledged = TodoManager(self.data_path).get_task(self.task.id)
        if (
            acknowledged is None
            or not acknowledged.reminder_acknowledged
            or acknowledged.completed
            or self.pet.animation_state != "idle"
        ):
            self._fail("确认提醒或动画恢复检查失败")
            return

        print(
            "LIVE_PASS one_popup_after_30s acknowledged_without_completion "
            "remind_to_idle local_time_unchanged",
            flush=True,
        )
        self.result = 0
        self._shutdown()

    def _timeout(self) -> None:
        if self.result != 0:
            self._fail("190 秒内未完成真实提醒")

    def _fail(self, message: str) -> None:
        print(f"LIVE_FAIL {message}", file=sys.stderr, flush=True)
        self.result = 1
        self._shutdown()

    def _shutdown(self) -> None:
        self.reminder_manager.stop()
        self.pet.close()
        self.app.quit()

    def cleanup(self) -> None:
        self.temp_dir.cleanup()


def main() -> int:
    app = QApplication(["phase3-live-reminder"])
    app.setQuitOnLastWindowClosed(False)
    delay_seconds = 120
    if len(sys.argv) == 3 and sys.argv[1] == "--delay-seconds":
        delay_seconds = max(1, int(sys.argv[2]))
    probe = LiveReminderProbe(app, delay_seconds)
    QTimer.singleShot(0, probe.start)
    app.exec()
    probe.cleanup()
    return probe.result


if __name__ == "__main__":
    raise SystemExit(main())
