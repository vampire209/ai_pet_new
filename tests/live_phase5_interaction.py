"""Visible Phase 5 interaction probe using a controllable local test clock."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from config import AppConfig
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager


class LivePhase5Probe:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.failed = False
        self.finished = False
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "tasks.json"
        self.base = datetime.now().replace(microsecond=0)
        self.event = self.base + timedelta(minutes=5)
        self.clock = [self.base]
        self.manager = TodoManager(self.path)
        self.task = self.manager.add_task(
            "Phase 5 实际交互",
            self.event.isoformat(timespec="seconds"),
            now=self.base,
        )
        self.scanner = ReminderManager(
            self.manager,
            now_provider=lambda: self.clock[0],
        )
        self.pet = PetWindow(AppConfig.load(), self.manager, self.scanner)

    def start(self) -> None:
        self.pet.show()
        self.app.processEvents()
        if self.pet.animation_state != "wait":
            self.fail("未来任务没有进入 wait")
            return
        print(
            f"LIVE_WAIT logical={self.pet.animation_state} "
            f"rendered={self.pet.current_animation}",
            flush=True,
        )
        QTimer.singleShot(700, self.trigger_final)
        QTimer.singleShot(10_000, lambda: self.fail("实际交互测试超时"))

    def trigger_final(self) -> None:
        self.clock[0] = self.event
        self.scanner.check_now()
        self.app.processEvents()
        if len(self.pet.reminder_bubbles) != 1:
            self.fail("final 没有生成唯一 bubble")
            return
        bubble = self.pet.reminder_bubbles[0]
        type_text = bubble.findChild(QLabel, "reminderTypeLabel").text()
        if type_text != "到点提醒" or self.pet.animation_state != "remind":
            self.fail("final bubble 或 remind 状态错误")
            return
        self.initial_frame = self.pet.current_frame_index
        print(f"LIVE_FINAL_BUBBLE type={type_text}", flush=True)
        QTimer.singleShot(
            self.pet._config.remind_interval_ms * 2 + 80,
            self.snooze_current,
        )

    def snooze_current(self) -> None:
        if self.pet.current_frame_index == self.initial_frame:
            self.fail("remind 动画没有向后播放")
            return
        bubble = self.pet.reminder_bubbles[0]
        bubble.findChild(QPushButton, "snooze5Button").click()
        self.app.processEvents()
        updated = self.manager.get_task(self.task.id)
        snoozes = [item for item in updated.reminders if item.type == "snooze"]
        if len(snoozes) != 1 or self.pet.animation_state != "wait":
            self.fail("稍后 5 分钟未保存或未恢复 wait")
            return
        self.snooze_id = snoozes[0].id
        print(
            f"LIVE_SNOOZE saved={snoozes[0].trigger_at} "
            f"state={self.pet.animation_state}",
            flush=True,
        )
        QTimer.singleShot(500, self.restart_for_snooze)

    def restart_for_snooze(self) -> None:
        self.scanner.stop()
        self.pet.close()
        self.clock[0] = self.event + timedelta(minutes=1)
        self.manager = TodoManager(self.path)
        loaded = self.manager.get_task(self.task.id)
        if loaded is None or loaded.get_reminder(self.snooze_id) is None:
            self.fail("重启后 snooze 丢失")
            return
        self.scanner = ReminderManager(
            self.manager,
            now_provider=lambda: self.clock[0],
        )
        self.pet = PetWindow(AppConfig.load(), self.manager, self.scanner)
        self.pet.show()
        self.app.processEvents()
        if self.pet.animation_state != "wait":
            self.fail("重启后没有从 snooze 恢复 wait")
            return
        print("LIVE_RESTART snooze_restored wait_restored", flush=True)
        QTimer.singleShot(600, self.trigger_snooze)

    def trigger_snooze(self) -> None:
        self.clock[0] = self.event + timedelta(minutes=5)
        self.scanner.check_now()
        self.app.processEvents()
        if len(self.pet.reminder_bubbles) != 1:
            self.fail("snooze 到点没有生成 bubble")
            return
        bubble = self.pet.reminder_bubbles[0]
        type_text = bubble.findChild(QLabel, "reminderTypeLabel").text()
        if type_text != "稍后提醒" or self.pet.animation_state != "remind":
            self.fail("snooze bubble 类型或 remind 状态错误")
            return
        print(f"LIVE_SNOOZE_BUBBLE type={type_text}", flush=True)
        QTimer.singleShot(500, self.complete_from_bubble)

    def complete_from_bubble(self) -> None:
        self.pet.reminder_bubbles[0].findChild(
            QPushButton,
            "completeTaskButton",
        ).click()
        self.app.processEvents()
        saved = TodoManager(self.path).get_task(self.task.id)
        if (
            saved is None
            or not saved.completed
            or self.pet.reminder_bubbles
            or self.pet.animation_state != "idle"
        ):
            self.fail("bubble 完成操作或 idle 恢复错误")
            return
        print(
            "LIVE_PASS wait remind bubble snooze restart complete idle",
            flush=True,
        )
        self.finished = True
        self.shutdown()

    def fail(self, message: str) -> None:
        if self.failed or self.finished:
            return
        self.failed = True
        print(f"LIVE_FAIL {message}", flush=True)
        self.shutdown()

    def shutdown(self) -> None:
        self.scanner.stop()
        self.pet.close()
        QTimer.singleShot(0, self.app.quit)

    def cleanup(self) -> None:
        self.temp_dir.cleanup()


def main() -> int:
    app = QApplication(["phase5-live-interaction"])
    app.setQuitOnLastWindowClosed(False)
    probe = LivePhase5Probe(app)
    probe.start()
    app.exec()
    failed = probe.failed or not probe.finished
    probe.cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
