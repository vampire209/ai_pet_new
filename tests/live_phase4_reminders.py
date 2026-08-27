"""Real wall-clock Phase 4 probe: +5 minute advance and +10 minute final."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton

from config import AppConfig
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager


def local_stamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


class LivePhase4Probe:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.failed = False
        self.finished = False
        self.received_types: list[str] = []
        self.animation_advanced: dict[str, bool] = {}
        self.temp_dir = tempfile.TemporaryDirectory()
        self.start_at = datetime.now().replace(microsecond=0)
        self.event_at = self.start_at + timedelta(minutes=10)
        self.advance_at = self.start_at + timedelta(minutes=5)
        data_path = Path(self.temp_dir.name) / "tasks.json"
        self.manager = TodoManager(data_path)
        self.task = self.manager.add_task(
            "Phase 4 真实计时测试",
            local_stamp(self.event_at),
            [5],
            now=self.start_at,
        )
        self.reminder_manager = ReminderManager(self.manager)
        self.pet = PetWindow(
            AppConfig.load(),
            self.manager,
            self.reminder_manager,
        )
        self.reminder_manager.reminder_ready.connect(self._on_due)

    def start(self) -> None:
        print(f"LIVE_START {local_stamp(self.start_at)}", flush=True)
        print(f"ADVANCE_TARGET {local_stamp(self.advance_at)}", flush=True)
        print(f"FINAL_TARGET {local_stamp(self.event_at)}", flush=True)
        print(f"TEMP_DATA {self.manager.data_path}", flush=True)
        self.pet.show()
        self.reminder_manager.start(immediate=True)
        QTimer.singleShot(12 * 60 * 1000, self._timeout)

    def _on_due(self, task, reminder) -> None:
        actual = datetime.now().replace(microsecond=0)
        print(
            f"LIVE_DUE type={reminder.type} target={reminder.trigger_at} "
            f"actual={local_stamp(actual)}",
            flush=True,
        )
        expected = "advance" if not self.received_types else "final"
        if reminder.type != expected:
            self._fail(f"提醒顺序错误：预期 {expected}，实际 {reminder.type}")
            return
        self.received_types.append(reminder.type)
        if task.completed:
            self._fail("提醒触发时任务被错误标记为完成")
            return
        if len(self.pet.reminder_dialogs) != 1:
            self._fail("提醒触发时没有且仅有一个弹窗")
            return
        if self.pet.current_animation != "remind":
            self._fail("提醒触发时未播放 remind 动画")
            return

        initial_frame = self.pet.current_frame_index
        QTimer.singleShot(
            self.pet._config.remind_interval_ms * 2 + 80,
            lambda kind=reminder.type, frame=initial_frame: self._check_animation(
                kind,
                frame,
            ),
        )
        QTimer.singleShot(
            1500,
            lambda reminder_id=reminder.id: self._ack_current(reminder_id),
        )
        QTimer.singleShot(
            2300,
            lambda kind=reminder.type, reminder_id=reminder.id: self._verify_closed(
                kind,
                reminder_id,
            ),
        )

    def _check_animation(self, kind: str, initial_frame: int) -> None:
        if self.failed:
            return
        advanced = (
            self.pet.current_animation == "remind"
            and self.pet.current_frame_index != initial_frame
        )
        self.animation_advanced[kind] = advanced
        print(
            f"ANIMATION type={kind} advanced={advanced} "
            f"frame={self.pet.current_frame_index}",
            flush=True,
        )
        if not advanced:
            self._fail(f"{kind} 的 remind 动画未继续播放")

    def _ack_current(self, reminder_id: str) -> None:
        if self.failed:
            return
        persisted = self.manager.get_task(self.task.id).get_reminder(reminder_id)
        if persisted is not None and persisted.acknowledged:
            return
        dialogs = self.pet.reminder_dialogs
        if len(dialogs) != 1 or dialogs[0].reminder_id != reminder_id:
            self._fail("自动确认时当前提醒弹窗不匹配")
            return
        button = dialogs[0].findChild(QPushButton, "acknowledgeButton")
        if button is None:
            self._fail("提醒窗口缺少知道了按钮")
            return
        button.click()

    def _verify_closed(self, kind: str, reminder_id: str) -> None:
        if self.failed:
            return
        task = TodoManager(self.manager.data_path).get_task(self.task.id)
        reminder = task.get_reminder(reminder_id) if task is not None else None
        if task is None or reminder is None:
            self._fail("确认后无法从 JSON 重新读取提醒")
            return
        if not reminder.triggered or not reminder.acknowledged:
            self._fail(f"{kind} 状态没有正确持久化")
            return
        if task.completed:
            self._fail(f"确认 {kind} 后任务被错误完成")
            return
        if self.pet.reminder_dialogs:
            self._fail(f"确认 {kind} 后提醒窗口没有关闭")
            return
        if self.pet.current_animation != "idle":
            self._fail(f"确认 {kind} 后没有恢复 idle")
            return
        if kind == "advance":
            final = next(item for item in task.reminders if item.type == "final")
            if final.triggered:
                self._fail("advance 确认错误影响了 final")
                return
            print(
                "ADVANCE_PASS acknowledged task_incomplete final_pending idle_restored",
                flush=True,
            )
            return
        self._complete()

    def _complete(self) -> None:
        task = TodoManager(self.manager.data_path).get_task(self.task.id)
        states = [
            (item.type, item.triggered, item.acknowledged)
            for item in task.reminders
        ]
        print(f"LIVE_STATE reminders={states} completed={task.completed}", flush=True)
        print(
            "LIVE_PASS advance_then_final persisted_independently "
            "remind_animated idle_restored local_time_unchanged",
            flush=True,
        )
        self.finished = True
        self._shutdown()

    def _timeout(self) -> None:
        if not self.finished:
            self._fail("12 分钟内未完成两次真实提醒")

    def _fail(self, message: str) -> None:
        if self.failed:
            return
        self.failed = True
        print(f"LIVE_FAIL {message}", flush=True)
        self._shutdown()

    def _shutdown(self) -> None:
        self.reminder_manager.stop()
        self.pet.close()
        QTimer.singleShot(0, self.app.quit)

    def cleanup(self) -> None:
        self.temp_dir.cleanup()


def main() -> int:
    app = QApplication(["phase4-live-reminders"])
    app.setQuitOnLastWindowClosed(False)
    probe = LivePhase4Probe(app)
    probe.start()
    app.exec()
    failed = probe.failed or not probe.finished
    probe.cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
