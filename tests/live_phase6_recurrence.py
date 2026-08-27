"""Visible Phase 6 recurrence probe using a controllable local clock."""

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
from models.recurrence import REPEAT_DAILY, REPEAT_WEEKLY
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager


def local_iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat(timespec="seconds")


class LivePhase6Probe:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.failed = False
        self.finished = False
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "tasks.json"
        self.base = datetime.now().replace(microsecond=0)
        self.clock = [self.base]
        self.manager = TodoManager(self.path)
        self.scanner = ReminderManager(
            self.manager,
            now_provider=lambda: self.clock[0],
        )
        self.pet = PetWindow(AppConfig.load(), self.manager, self.scanner)

    def start(self) -> None:
        self.daily_event = self.base + timedelta(minutes=1)
        self.daily = self.manager.add_task(
            "Phase 6 每日任务",
            local_iso(self.daily_event),
            [],
            REPEAT_DAILY,
            now=self.base,
        )
        self.pet.show()
        self.app.processEvents()
        if self.pet.animation_state != "wait":
            self.fail("场景 A：未来每日任务没有进入 wait")
            return
        print("LIVE_A wait", flush=True)
        QTimer.singleShot(500, self.scenario_a_trigger)
        QTimer.singleShot(15_000, lambda: self.fail("Phase 6 live 测试超时"))

    def scenario_a_trigger(self) -> None:
        self.clock[0] = self.daily_event
        self.scanner.check_now()
        self.app.processEvents()
        if len(self.pet.reminder_bubbles) != 1 or self.pet.animation_state != "remind":
            self.fail("场景 A：final 没有进入 remind 并显示唯一 bubble")
            return
        print("LIVE_A remind bubble", flush=True)
        QTimer.singleShot(500, self.scenario_a_complete)

    def scenario_a_complete(self) -> None:
        self.pet.reminder_bubbles[0].findChild(
            QPushButton,
            "completeTaskButton",
        ).click()
        self.app.processEvents()
        updated = self.manager.get_task(self.daily.id)
        expected = self.daily_event + timedelta(days=1)
        if (
            updated.completed
            or updated.event_at != local_iso(expected)
            or self.pet.animation_state != "wait"
        ):
            self.fail("场景 A：完成后没有生成下一天并恢复 wait")
            return
        print(f"LIVE_A complete next={updated.event_at} wait", flush=True)
        self.next_daily_event = expected
        QTimer.singleShot(500, self.scenario_b_trigger)

    def scenario_b_trigger(self) -> None:
        self.clock[0] = self.next_daily_event
        self.scanner.check_now()
        self.app.processEvents()
        if len(self.pet.reminder_bubbles) != 1:
            self.fail("场景 B：下一周期 final 没有显示 bubble")
            return
        self.pet.reminder_bubbles[0].findChild(
            QPushButton,
            "snooze5Button",
        ).click()
        self.app.processEvents()
        updated = self.manager.get_task(self.daily.id)
        snoozes = [
            item
            for item in updated.reminders
            if item.type == "snooze" and not item.triggered
        ]
        if (
            len(snoozes) != 1
            or updated.event_at != local_iso(self.next_daily_event)
            or self.pet.animation_state != "wait"
        ):
            self.fail("场景 B：稍后 5 分钟没有保留每日基础时间")
            return
        print(f"LIVE_B snooze={snoozes[0].trigger_at} wait", flush=True)
        QTimer.singleShot(500, self.scenario_b_snooze_due)

    def scenario_b_snooze_due(self) -> None:
        self.clock[0] = self.next_daily_event + timedelta(minutes=5)
        self.scanner.check_now()
        self.app.processEvents()
        if len(self.pet.reminder_bubbles) != 1 or self.pet.animation_state != "remind":
            self.fail("场景 B：snooze 到点没有重新 remind")
            return
        print("LIVE_B snooze_remind", flush=True)
        self.pet.reminder_bubbles[0].findChild(
            QPushButton,
            "completeTaskButton",
        ).click()
        self.app.processEvents()
        updated = self.manager.get_task(self.daily.id)
        expected = self.next_daily_event + timedelta(days=1)
        if updated.event_at != local_iso(expected) or self.pet.animation_state != "wait":
            self.fail("场景 B：snooze 完成后下一次没有回到原始时刻")
            return
        print(f"LIVE_B complete next={updated.event_at} original_time", flush=True)
        self.manager.delete_task(self.daily.id)
        QTimer.singleShot(500, self.scenario_c_prepare)

    def scenario_c_prepare(self) -> None:
        current = self.clock[0]
        days_until_friday = (5 - current.isoweekday()) % 7
        friday = (current + timedelta(days=days_until_friday)).replace(
            hour=18,
            minute=0,
            second=0,
            microsecond=0,
        )
        if friday <= current:
            friday += timedelta(days=7)
        self.friday = friday
        self.weekly = self.manager.add_task(
            "Phase 6 每周任务",
            local_iso(friday),
            [30],
            REPEAT_WEEKLY,
            [1, 5],
            now=current,
        )
        self.clock[0] = friday
        self.scanner.check_now()
        self.app.processEvents()
        if len(self.pet.reminder_bubbles) != 1:
            self.fail("场景 C：周五 final 没有显示 bubble")
            return
        self.pet.reminder_bubbles[0].findChild(
            QPushButton,
            "completeTaskButton",
        ).click()
        self.app.processEvents()
        updated = self.manager.get_task(self.weekly.id)
        expected = friday + timedelta(days=3)
        if updated.event_at != local_iso(expected) or expected.isoweekday() != 1:
            self.fail("场景 C：周五完成后没有推进到下周一")
            return
        print(f"LIVE_C friday_to_monday next={updated.event_at}", flush=True)
        QTimer.singleShot(500, self.scenario_d_restart)

    def scenario_d_restart(self) -> None:
        before = self.manager.get_task(self.weekly.id)
        expected_ids = [item.id for item in before.reminders]
        expected_event = before.event_at
        self.scanner.stop()
        self.pet.close()

        self.manager = TodoManager(self.path)
        loaded = self.manager.get_task(self.weekly.id)
        self.scanner = ReminderManager(
            self.manager,
            now_provider=lambda: self.clock[0],
        )
        self.pet = PetWindow(AppConfig.load(), self.manager, self.scanner)
        self.pet.show()
        self.app.processEvents()
        if (
            loaded is None
            or loaded.event_at != expected_event
            or [item.id for item in loaded.reminders] != expected_ids
            or self.pet.animation_state != "wait"
            or self.pet.reminder_bubbles
        ):
            self.fail("场景 D：重启恢复或 reminder 去重失败")
            return
        print("LIVE_D restart_restored no_duplicate wait", flush=True)
        print(
            "LIVE_PASS A_daily B_snooze C_weekly D_restart",
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
    app = QApplication(["phase6-live-recurrence"])
    app.setQuitOnLastWindowClosed(False)
    probe = LivePhase6Probe(app)
    probe.start()
    app.exec()
    failed = probe.failed or not probe.finished
    probe.cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
