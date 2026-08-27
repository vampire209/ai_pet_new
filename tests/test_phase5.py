"""Phase 5 wait, actionable bubble, and persistent snooze acceptance tests."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from config import AppConfig
from pet_window import PetWindow
from reminder_bubble import ReminderBubble
from reminder_manager import ReminderManager
from todo_manager import TodoManager
from todo_window import TodoWindow, format_snooze_status


def local_iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat(timespec="seconds")


def advance_due_task(manager: TodoManager, now: datetime, text: str = "提前任务"):
    return manager.add_task(
        text,
        local_iso(now + timedelta(minutes=5)),
        [5],
        now=now - timedelta(seconds=1),
    )


def final_due_task(manager: TodoManager, now: datetime, text: str = "到点任务"):
    return manager.add_task(
        text,
        local_iso(now - timedelta(seconds=1)),
        now=now - timedelta(minutes=1),
    )


class Phase5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase5-tests"])
        cls.config = AppConfig.load()

    def test_01_phase4_json_defaults_cancelled_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            now = datetime.now().replace(microsecond=0)
            event = now + timedelta(hours=1)
            path = Path(temp_dir) / "tasks.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "phase4_task",
                            "text": "Phase 4 数据",
                            "completed": False,
                            "event_at": local_iso(event),
                            "reminder_offsets": [],
                            "reminders": [
                                {
                                    "id": "phase4_final",
                                    "type": "final",
                                    "offset_minutes": 0,
                                    "trigger_at": local_iso(event),
                                    "triggered": False,
                                    "acknowledged": False,
                                }
                            ],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manager = TodoManager(path)
            task = manager.get_task("phase4_task")
            self.assertFalse(task.reminders[0].cancelled)
            manager.update_task(task.id, "迁移后保存")
            saved = json.loads(path.read_text(encoding="utf-8"))[0]
            self.assertFalse(saved["reminders"][0]["cancelled"])

    def test_02_all_four_snooze_durations_persist_and_trigger(self) -> None:
        for minutes in (5, 10, 30, 60):
            with self.subTest(minutes=minutes), tempfile.TemporaryDirectory() as temp_dir:
                base = datetime.now().replace(microsecond=0)
                path = Path(temp_dir) / "tasks.json"
                manager = TodoManager(path)
                task = final_due_task(manager, base, f"稍后{minutes}分钟")
                due: list[tuple] = []
                scanner = ReminderManager(manager, now_provider=lambda: base)
                scanner.reminder_ready.connect(
                    lambda item, reminder: due.append((item, reminder))
                )
                scanner.check_now()
                original = due[0][1]
                updated = manager.snooze_reminder(
                    task.id,
                    original.id,
                    minutes,
                    now=base,
                )
                snoozes = [item for item in updated.reminders if item.type == "snooze"]
                self.assertEqual(len(snoozes), 1)
                snooze = snoozes[0]
                self.assertEqual(
                    snooze.trigger_at,
                    local_iso(base + timedelta(minutes=minutes)),
                )
                self.assertTrue(updated.get_reminder(original.id).acknowledged)
                self.assertFalse(updated.completed)
                scanner.reminder_finished(original.id)

                restarted = TodoManager(path)
                loaded_snooze = restarted.get_task(task.id).get_reminder(snooze.id)
                self.assertIsNotNone(loaded_snooze)
                self.assertFalse(loaded_snooze.triggered)
                restarted_due: list[tuple] = []
                restarted_scanner = ReminderManager(
                    restarted,
                    now_provider=lambda: base + timedelta(minutes=minutes),
                )
                restarted_scanner.reminder_ready.connect(
                    lambda item, reminder: restarted_due.append((item, reminder))
                )
                restarted_scanner.check_now()
                self.assertEqual([item[1].type for item in restarted_due], ["snooze"])

    def test_03_complete_cancels_future_reminders_and_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            event = base + timedelta(hours=2)
            path = Path(temp_dir) / "tasks.json"
            manager = TodoManager(path)
            task = manager.add_task("完成并取消", local_iso(event), [5, 60], now=base)
            completed = manager.complete_task(task.id, now=base + timedelta(minutes=1))
            self.assertTrue(completed.completed)
            self.assertTrue(all(item.cancelled for item in completed.reminders))
            restarted = TodoManager(path)
            loaded = restarted.get_task(task.id)
            self.assertTrue(loaded.completed)
            self.assertTrue(all(item.cancelled for item in loaded.reminders))
            due: list = []
            scanner = ReminderManager(
                restarted,
                now_provider=lambda: event + timedelta(minutes=1),
            )
            scanner.reminder_ready.connect(lambda task, reminder: due.append(reminder))
            scanner.check_now()
            self.assertEqual(due, [])

    def test_04_future_reminder_drives_wait_and_completion_or_delete_restores_idle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            scanner = ReminderManager(manager, now_provider=lambda: base)
            pet = PetWindow(self.config, manager, scanner)
            pet.show()
            self.assertEqual(pet.animation_state, "idle")

            first = manager.add_task(
                "未来任务",
                local_iso(base + timedelta(hours=1)),
                now=base,
            )
            self.app.processEvents()
            self.assertEqual(pet.animation_state, "wait")
            expected_animation = (
                "wait"
                if "wait" in pet._animation_manager.available_animations
                else "idle"
            )
            self.assertEqual(pet.current_animation, expected_animation)

            manager.complete_task(first.id, now=base)
            self.app.processEvents()
            self.assertEqual(pet.animation_state, "idle")
            second = manager.add_task(
                "待删除未来任务",
                local_iso(base + timedelta(hours=2)),
                now=base,
            )
            self.assertEqual(pet.animation_state, "wait")
            manager.delete_task(second.id)
            self.app.processEvents()
            self.assertEqual(pet.animation_state, "idle")
            pet.close()

    def test_05_move_preempts_wait_and_release_restores_wait(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            manager.add_task("等待拖动", local_iso(base + timedelta(hours=1)), now=base)
            scanner = ReminderManager(manager, now_provider=lambda: base)
            pet = PetWindow(self.config, manager, scanner)
            pet.show()
            self.assertEqual(pet.animation_state, "wait")

            origin = QPointF(500, 500)
            press = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(100, 100),
                origin,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            pet.mousePressEvent(press)
            threshold = QApplication.styleHints().startDragDistance()
            moved = QPointF(500 + threshold + 5, 500)
            move = QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(100 + threshold + 5, 100),
                moved,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            pet.mouseMoveEvent(move)
            self.assertEqual(pet.animation_state, "move")
            release = QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                QPointF(100 + threshold + 5, 100),
                moved,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            pet.mouseReleaseEvent(release)
            self.assertEqual(pet.animation_state, "wait")
            pet.close()

    def test_06_advance_bubble_preempts_wait_deduplicates_and_restores_wait(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = advance_due_task(manager, base, "复习高数")
            scanner = ReminderManager(manager, now_provider=lambda: base)
            pet = PetWindow(self.config, manager, scanner)
            pet.show()
            self.assertEqual(pet.animation_state, "wait")

            scanner.check_now()
            scanner.check_now()
            scanner.check_now()
            self.app.processEvents()
            self.assertEqual(len(pet.reminder_bubbles), 1)
            bubble = pet.reminder_bubbles[0]
            self.assertEqual(pet.animation_state, "remind")
            self.assertEqual(pet.current_animation, "remind")
            self.assertTrue(bubble.windowFlags() & Qt.WindowType.FramelessWindowHint)
            self.assertTrue(bubble.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            self.assertTrue(bubble.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
            self.assertEqual(
                bubble.findChild(QLabel, "reminderTypeLabel").text(),
                "提前提醒",
            )
            self.assertIn("还有 5分钟", bubble.findChild(QLabel, "reminderMessage").text())

            screen = QApplication.screenAt(pet.geometry().center()) or QApplication.primaryScreen()
            area = screen.availableGeometry()
            geometry = bubble.frameGeometry()
            self.assertGreaterEqual(geometry.left(), area.left())
            self.assertLessEqual(geometry.right(), area.right())
            self.assertGreaterEqual(geometry.top(), area.top())
            self.assertLessEqual(geometry.bottom(), area.bottom())

            close_button = bubble.findChild(QPushButton, "acknowledgeButton")
            close_button.click()
            QTest.qWait(20)
            updated = manager.get_task(task.id)
            advance = next(item for item in updated.reminders if item.type == "advance")
            final = next(item for item in updated.reminders if item.type == "final")
            self.assertTrue(advance.acknowledged)
            self.assertFalse(updated.completed)
            self.assertFalse(final.triggered)
            self.assertEqual(len(pet.reminder_bubbles), 0)
            self.assertEqual(pet.animation_state, "wait")
            pet.close()

    def test_07_final_bubble_restores_idle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = final_due_task(manager, base, "提交报告")
            scanner = ReminderManager(manager, now_provider=lambda: base)
            pet = PetWindow(self.config, manager, scanner)
            pet.show()
            self.assertEqual(pet.animation_state, "idle")
            scanner.check_now()
            self.app.processEvents()
            bubble = pet.reminder_bubbles[0]
            self.assertEqual(
                bubble.findChild(QLabel, "reminderTypeLabel").text(),
                "到点提醒",
            )
            self.assertIn("时间到了", bubble.findChild(QLabel, "reminderMessage").text())
            bubble.findChild(QPushButton, "acknowledgeButton").click()
            QTest.qWait(20)
            self.assertTrue(manager.get_task(task.id).reminder_acknowledged)
            self.assertEqual(pet.animation_state, "idle")
            self.assertEqual(pet.current_animation, "idle")
            pet.close()

    def test_08_bubble_complete_finishes_task_and_stops_later_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = advance_due_task(manager, base, "完成操作")
            scanner = ReminderManager(manager, now_provider=lambda: base)
            pet = PetWindow(self.config, manager, scanner)
            pet.show()
            scanner.check_now()
            self.app.processEvents()
            pet.reminder_bubbles[0].findChild(
                QPushButton,
                "completeTaskButton",
            ).click()
            QTest.qWait(20)
            completed = manager.get_task(task.id)
            self.assertTrue(completed.completed)
            self.assertTrue(all(item.triggered or item.cancelled for item in completed.reminders))
            self.assertEqual(len(pet.reminder_bubbles), 0)
            self.assertEqual(pet.animation_state, "idle")
            scanner.check_now()
            self.assertEqual(len(pet.reminder_bubbles), 0)
            pet.close()

    def test_09_bubble_snooze_persists_shows_status_and_retriggers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            path = Path(temp_dir) / "tasks.json"
            manager = TodoManager(path)
            task = final_due_task(manager, base, "稍后再做")
            scanner = ReminderManager(manager, now_provider=lambda: base)
            pet = PetWindow(self.config, manager, scanner)
            pet.show()
            scanner.check_now()
            self.app.processEvents()
            pet.reminder_bubbles[0].findChild(QPushButton, "snooze10Button").click()
            QTest.qWait(20)
            updated = manager.get_task(task.id)
            snooze = next(item for item in updated.reminders if item.type == "snooze")
            self.assertEqual(snooze.trigger_at, local_iso(base + timedelta(minutes=10)))
            self.assertFalse(updated.completed)
            self.assertEqual(pet.animation_state, "wait")
            self.assertEqual(format_snooze_status(updated, now=base), f"稍后至 {snooze.trigger_datetime:%H:%M}")

            todo = TodoWindow(manager)
            labels = todo.findChildren(QLabel, "snoozeStatusLabel")
            self.assertEqual(len(labels), 1)
            self.assertIn("稍后至", labels[0].text())
            todo.hide()
            todo.deleteLater()
            pet.close()

            restarted = TodoManager(path)
            due: list[tuple] = []
            restarted_scanner = ReminderManager(
                restarted,
                now_provider=lambda: base + timedelta(minutes=10),
            )
            restarted_scanner.reminder_ready.connect(
                lambda item, reminder: due.append((item, reminder))
            )
            restarted_scanner.check_now()
            self.assertEqual([item[1].type for item in due], ["snooze"])

    def test_10_edit_invalidates_snooze_and_delete_closes_active_bubble(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = final_due_task(manager, base, "编辑失效")
            scanner = ReminderManager(manager, now_provider=lambda: base)
            ready: list[tuple] = []
            scanner.reminder_ready.connect(lambda item, reminder: ready.append((item, reminder)))
            scanner.check_now()
            current = ready[0][1]
            snoozed = manager.snooze_reminder(task.id, current.id, 30, now=base)
            self.assertTrue(any(item.type == "snooze" for item in snoozed.reminders))
            scanner.reminder_finished(current.id)
            new_event = base + timedelta(hours=2)
            edited = manager.update_task(
                task.id,
                task.text,
                local_iso(new_event),
                [],
                now=base,
            )
            self.assertFalse(any(item.type == "snooze" for item in edited.reminders))

            second = final_due_task(manager, base, "删除活动提醒")
            pet = PetWindow(self.config, manager, scanner)
            pet.show()
            scanner.check_now()
            self.app.processEvents()
            self.assertEqual(len(pet.reminder_bubbles), 1)
            manager.delete_task(second.id)
            QTest.qWait(20)
            self.assertEqual(len(pet.reminder_bubbles), 0)
            self.assertIsNone(scanner.active_reminder_id)
            scanner.check_now()
            self.assertEqual(len(pet.reminder_bubbles), 0)
            pet.close()
