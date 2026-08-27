"""Phase 4 independent multi-reminder acceptance tests."""

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

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton

from config import AppConfig
from pet_window import PetWindow
from reminder_dialog import ReminderDialog, format_reminder_message
from reminder_manager import ReminderManager
from todo_manager import TodoManager
from todo_window import TaskEditDialog, TodoWindow


def local_iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat(timespec="seconds")


def reminder_of(task, reminder_type: str, offset: int = 0):
    return next(
        reminder
        for reminder in task.reminders
        if reminder.type == reminder_type and reminder.offset_minutes == offset
    )


class Phase4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase4-tests"])
        cls.config = AppConfig.load()

    def test_01_five_minute_advance_and_final_are_generated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            now = datetime.now().replace(microsecond=0)
            event = now + timedelta(minutes=10)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task("测试考试", local_iso(event), [5], now=now)
            self.assertEqual(len(task.reminders), 2)
            advance = reminder_of(task, "advance", 5)
            final = reminder_of(task, "final")
            self.assertEqual(advance.trigger_at, local_iso(now + timedelta(minutes=5)))
            self.assertEqual(final.trigger_at, local_iso(event))

    def test_02_acknowledging_advance_does_not_complete_or_change_final(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            now = datetime.now().replace(microsecond=0)
            event = now + timedelta(minutes=10)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task("独立确认", local_iso(event), [5], now=now)
            due: list[tuple] = []
            scanner = ReminderManager(
                manager,
                now_provider=lambda: now + timedelta(minutes=5),
            )
            scanner.reminder_ready.connect(lambda item, reminder: due.append((item, reminder)))
            scanner.check_now()
            self.assertEqual(len(due), 1)
            advance = due[0][1]
            manager.acknowledge_reminder(task.id, advance.id)
            persisted = TodoManager(manager.data_path).get_task(task.id)
            self.assertTrue(persisted.get_reminder(advance.id).acknowledged)
            self.assertFalse(persisted.completed)
            self.assertFalse(reminder_of(persisted, "final").triggered)

    def test_03_final_triggers_after_advance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            event = base + timedelta(minutes=10)
            clock = [base + timedelta(minutes=5)]
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task("两次提醒", local_iso(event), [5], now=base)
            due: list[tuple] = []
            scanner = ReminderManager(manager, now_provider=lambda: clock[0])
            scanner.reminder_ready.connect(lambda item, reminder: due.append((item, reminder)))
            scanner.check_now()
            advance = due[-1][1]
            manager.acknowledge_reminder(task.id, advance.id)
            scanner.reminder_finished(advance.id)
            self.app.processEvents()
            clock[0] = event
            scanner.check_now()
            self.assertEqual([item[1].type for item in due], ["advance", "final"])

    def test_04_three_offsets_generate_three_advance_and_one_final(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            now = datetime.now().replace(microsecond=0)
            event = now + timedelta(hours=2)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task(
                "多提醒",
                local_iso(event),
                [5, 30, 60],
                now=now,
            )
            advances = [item for item in task.reminders if item.type == "advance"]
            self.assertEqual({item.offset_minutes for item in advances}, {5, 30, 60})
            self.assertEqual(len(advances), 3)
            self.assertEqual(len([item for item in task.reminders if item.type == "final"]), 1)
            short_event = now + timedelta(minutes=10)
            skipped = manager.add_task(
                "跳过已错过提前提醒",
                local_iso(short_event),
                [30, 60],
                now=now,
            )
            self.assertEqual([item.type for item in skipped.reminders], ["final"])

    def test_05_edit_event_recalculates_every_untriggered_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            now = datetime.now().replace(microsecond=0)
            old_event = now + timedelta(days=2)
            new_event = now + timedelta(days=3, hours=4)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task("改时间", local_iso(old_event), [60, 1440], now=now)
            old_ids = {item.id for item in task.reminders}
            updated = manager.update_task(
                task.id,
                task.text,
                local_iso(new_event),
                [60, 1440],
                now=now,
            )
            self.assertTrue(old_ids.isdisjoint({item.id for item in updated.reminders}))
            self.assertEqual(
                reminder_of(updated, "advance", 60).trigger_at,
                local_iso(new_event - timedelta(hours=1)),
            )
            self.assertEqual(
                reminder_of(updated, "advance", 1440).trigger_at,
                local_iso(new_event - timedelta(days=1)),
            )
            self.assertEqual(reminder_of(updated, "final").trigger_at, local_iso(new_event))

    def test_06_edit_offsets_syncs_without_repeating_triggered_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            event = base + timedelta(hours=2)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task("改选项", local_iso(event), [60], now=base)
            due: list[tuple] = []
            scanner = ReminderManager(
                manager,
                now_provider=lambda: event - timedelta(hours=1),
            )
            scanner.reminder_ready.connect(lambda item, reminder: due.append((item, reminder)))
            scanner.check_now()
            first = due[0][1]
            manager.acknowledge_reminder(task.id, first.id)
            scanner.reminder_finished(first.id)
            self.app.processEvents()

            updated = manager.update_task(
                task.id,
                task.text,
                task.event_at,
                [5, 60],
                now=event - timedelta(hours=1),
            )
            self.assertEqual(updated.reminder_offsets, (5, 60))
            self.assertEqual(len([item for item in updated.reminders if item.type == "advance"]), 2)
            removed = manager.update_task(
                task.id,
                task.text,
                task.event_at,
                [5],
                now=event - timedelta(hours=1),
            )
            self.assertEqual(removed.reminder_offsets, (5,))
            restored = manager.update_task(
                task.id,
                task.text,
                task.event_at,
                [5, 60],
                now=event - timedelta(hours=1),
            )
            sixty = [
                item
                for item in restored.reminders
                if item.type == "advance" and item.offset_minutes == 60
            ]
            self.assertEqual(len(sixty), 1)
            self.assertTrue(sixty[0].triggered)
            self.assertTrue(sixty[0].acknowledged)

    def test_07_restart_keeps_acknowledged_and_future_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            event = base + timedelta(minutes=10)
            path = Path(temp_dir) / "tasks.json"
            manager = TodoManager(path)
            task = manager.add_task("重启保留", local_iso(event), [5], now=base)
            first_due: list[tuple] = []
            scanner = ReminderManager(
                manager,
                now_provider=lambda: base + timedelta(minutes=5),
            )
            scanner.reminder_ready.connect(
                lambda item, reminder: first_due.append((item, reminder))
            )
            scanner.check_now()
            advance = first_due[0][1]
            manager.acknowledge_reminder(task.id, advance.id)

            restarted = TodoManager(path)
            loaded = restarted.get_task(task.id)
            self.assertTrue(loaded.get_reminder(advance.id).acknowledged)
            self.assertFalse(reminder_of(loaded, "final").triggered)
            later_due: list[tuple] = []
            clock = [base + timedelta(minutes=6)]
            restarted_scanner = ReminderManager(restarted, now_provider=lambda: clock[0])
            restarted_scanner.reminder_ready.connect(
                lambda item, reminder: later_due.append((item, reminder))
            )
            restarted_scanner.check_now()
            self.assertEqual(later_due, [])
            clock[0] = event
            restarted_scanner.check_now()
            self.assertEqual([item[1].type for item in later_due], ["final"])

    def test_08_phase3_json_migrates_to_final_without_manual_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            event = datetime.now().replace(microsecond=0) + timedelta(hours=2)
            path = Path(temp_dir) / "tasks.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "legacy_pending",
                            "text": "旧待办",
                            "completed": False,
                            "event_at": local_iso(event),
                            "reminder_triggered": False,
                            "reminder_acknowledged": False,
                        },
                        {
                            "id": "legacy_done",
                            "text": "旧已确认提醒",
                            "completed": False,
                            "event_at": local_iso(event),
                            "reminder_triggered": True,
                            "reminder_acknowledged": True,
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manager = TodoManager(path)
            pending = manager.get_task("legacy_pending")
            acknowledged = manager.get_task("legacy_done")
            self.assertEqual(len(pending.reminders), 1)
            self.assertEqual(pending.reminders[0].type, "final")
            self.assertFalse(pending.reminders[0].triggered)
            self.assertTrue(acknowledged.reminders[0].triggered)
            self.assertTrue(acknowledged.reminders[0].acknowledged)

    def test_09_completed_task_suppresses_all_future_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            event = base + timedelta(hours=2)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task("提前完成", local_iso(event), [5, 60], now=base)
            manager.toggle_complete(task.id)
            due: list[tuple] = []
            scanner = ReminderManager(
                manager,
                now_provider=lambda: event + timedelta(minutes=1),
            )
            scanner.reminder_ready.connect(lambda item, reminder: due.append((item, reminder)))
            scanner.check_now()
            self.assertEqual(due, [])
            self.assertTrue(all(not item.triggered for item in manager.get_task(task.id).reminders))

    def test_10_ui_multiselect_summary_and_message_are_clear(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            now = datetime.now().replace(microsecond=0)
            event = now + timedelta(days=2)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task("界面考试", local_iso(event), [60, 1440], now=now)
            window = TodoWindow(manager)
            summaries = window.findChildren(QLabel, "reminderSummaryLabel")
            self.assertEqual(len(summaries), 1)
            self.assertIn("提前1小时", summaries[0].text())
            self.assertIn("提前1天", summaries[0].text())

            dialog = TaskEditDialog(task)
            self.assertTrue(dialog.findChild(QCheckBox, "editReminder60").isChecked())
            self.assertTrue(dialog.findChild(QCheckBox, "editReminder1440").isChecked())
            advance = reminder_of(task, "advance", 1440)
            self.assertEqual(format_reminder_message(advance), "还有：1天")
            reminder_dialog = ReminderDialog(task, advance)
            self.assertEqual(
                reminder_dialog.findChild(QLabel, "reminderMessage").text(),
                "还有：1天",
            )
            reminder_dialog.deleteLater()
            dialog.deleteLater()
            window.hide()
            window.deleteLater()

    def test_11_queue_and_remind_animation_advance_without_scan_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            now = datetime.now().replace(microsecond=0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            first = manager.add_task("队列一", local_iso(now - timedelta(seconds=2)))
            second = manager.add_task("队列二", local_iso(now - timedelta(seconds=1)))
            scanner = ReminderManager(manager, now_provider=lambda: now)
            pet = PetWindow(self.config, manager, scanner)
            pet.show()
            scanner.check_now()
            self.app.processEvents()

            self.assertEqual(len(pet.reminder_dialogs), 1)
            self.assertEqual(len(scanner.pending_reminders), 1)
            self.assertEqual(pet.animation_state, "remind")
            self.assertEqual(pet.current_animation, "remind")
            self.assertEqual(pet._animation_manager.frame_count("remind"), 8)

            QTest.qWait(self.config.remind_interval_ms + 70)
            advanced_index = pet.current_frame_index
            self.assertNotEqual(advanced_index, 0)
            scanner.check_now()
            scanner.check_now()
            scanner.check_now()
            self.assertEqual(pet.current_frame_index, advanced_index)
            QTest.qWait(self.config.remind_interval_ms + 40)
            self.assertNotEqual(pet.current_frame_index, advanced_index)

            first_button = pet.reminder_dialogs[0].findChild(
                QPushButton,
                "acknowledgeButton",
            )
            first_button.click()
            QTest.qWait(30)
            self.assertEqual(len(pet.reminder_dialogs), 1)
            self.assertEqual(pet.current_animation, "remind")
            second_button = pet.reminder_dialogs[0].findChild(
                QPushButton,
                "acknowledgeButton",
            )
            second_button.click()
            QTest.qWait(30)
            self.assertEqual(len(pet.reminder_dialogs), 0)
            self.assertEqual(pet.animation_state, "idle")
            self.assertEqual(pet.current_animation, "idle")
            self.assertFalse(manager.get_task(first.id).completed)
            self.assertFalse(manager.get_task(second.id).completed)
            pet.close()
