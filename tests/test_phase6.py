"""Phase 6 daily and weekly recurrence acceptance tests."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel, QPushButton

from config import AppConfig
from models.recurrence import (
    REPEAT_DAILY,
    REPEAT_NONE,
    REPEAT_WEEKLY,
    next_occurrence,
)
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager
from todo_window import TaskEditDialog, TodoWindow, format_task_schedule


def local_iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat(timespec="seconds")


def reminders_of(task, reminder_type: str):
    return [item for item in task.reminders if item.type == reminder_type]


class Phase6Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase6-tests"])
        cls.config = AppConfig.load()

    def test_01_legacy_defaults_to_none_and_one_time_stays_one_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime(2026, 8, 27, 10, 0)
            event = base + timedelta(hours=2)
            path = Path(temp_dir) / "tasks.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "legacy",
                            "text": "旧任务",
                            "completed": False,
                            "event_at": local_iso(event),
                            "reminder_offsets": [],
                        },
                        {
                            "id": "broken_weekdays",
                            "text": "损坏星期仍可恢复",
                            "completed": False,
                            "event_at": local_iso(event),
                            "reminder_offsets": [],
                            "repeat_type": REPEAT_WEEKLY,
                            "repeat_weekdays": 123,
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manager = TodoManager(path)
            legacy = manager.get_task("legacy")
            self.assertEqual(legacy.repeat_type, REPEAT_NONE)
            self.assertEqual(legacy.repeat_weekdays, ())
            recovered = manager.get_task("broken_weekdays")
            self.assertEqual(recovered.repeat_type, REPEAT_WEEKLY)
            self.assertEqual(recovered.repeat_weekdays, (event.isoweekday(),))

            once = manager.add_task("一次性", local_iso(event), now=base)
            self.assertEqual(once.repeat_type, REPEAT_NONE)
            completed = manager.complete_task(once.id, now=base)
            self.assertTrue(completed.completed)
            self.assertIsNotNone(completed.completed_at)

    def test_02_create_daily_weekly_and_multiple_weekdays(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime(2026, 8, 27, 10, 0)
            path = Path(temp_dir) / "tasks.json"
            manager = TodoManager(path)
            daily = manager.add_task(
                "背单词",
                "2026-08-27T20:00:00",
                [30],
                REPEAT_DAILY,
                now=base,
            )
            weekly = manager.add_task(
                "健身",
                "2026-08-27T18:00:00",
                [30],
                REPEAT_WEEKLY,
                [1, 3, 5],
                now=base,
            )
            self.assertEqual(daily.repeat_type, REPEAT_DAILY)
            self.assertEqual(daily.repeat_weekdays, ())
            self.assertEqual(weekly.repeat_type, REPEAT_WEEKLY)
            self.assertEqual(weekly.repeat_weekdays, (1, 3, 5))
            self.assertEqual(weekly.event_at, "2026-08-28T18:00:00")

            restarted = TodoManager(path)
            self.assertEqual(restarted.get_task(daily.id).repeat_type, REPEAT_DAILY)
            self.assertEqual(
                restarted.get_task(weekly.id).repeat_weekdays,
                (1, 3, 5),
            )

    def test_03_daily_final_advance_and_multiple_advance_times(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime(2026, 8, 27, 17, 0)
            event = datetime(2026, 8, 27, 20, 0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task(
                "每日多提醒",
                local_iso(event),
                [5, 30, 60],
                REPEAT_DAILY,
                now=base,
            )
            advances = reminders_of(task, "advance")
            self.assertEqual(
                [item.trigger_at for item in advances],
                [
                    "2026-08-27T19:00:00",
                    "2026-08-27T19:30:00",
                    "2026-08-27T19:55:00",
                ],
            )
            self.assertEqual(reminders_of(task, "final")[0].trigger_at, local_iso(event))

    def test_04_weekly_final_and_advance_times_are_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime(2026, 8, 27, 10, 0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task(
                "每周提醒",
                "2026-08-27T18:00:00",
                [30],
                REPEAT_WEEKLY,
                [1, 5],
                now=base,
            )
            self.assertEqual(task.event_at, "2026-08-28T18:00:00")
            self.assertEqual(
                reminders_of(task, "advance")[0].trigger_at,
                "2026-08-28T17:30:00",
            )
            self.assertEqual(
                reminders_of(task, "final")[0].trigger_at,
                "2026-08-28T18:00:00",
            )

    def test_05_weekly_boundaries_cross_week_month_and_year(self) -> None:
        friday = datetime(2026, 1, 30, 18, 0)
        self.assertEqual(
            next_occurrence(friday, REPEAT_WEEKLY, [1, 5]),
            datetime(2026, 2, 2, 18, 0),
        )
        year_end = datetime(2027, 12, 31, 18, 0)
        self.assertEqual(year_end.isoweekday(), 5)
        self.assertEqual(
            next_occurrence(year_end, REPEAT_WEEKLY, [1, 5]),
            datetime(2028, 1, 3, 18, 0),
        )

    def test_06_daily_completion_advances_and_does_not_permanently_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime(2026, 8, 27, 19, 0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task(
                "每日完成",
                "2026-08-27T20:00:00",
                [30],
                REPEAT_DAILY,
                now=base,
            )
            completed = manager.complete_task(
                task.id,
                now=datetime(2026, 8, 27, 20, 5),
            )
            self.assertFalse(completed.completed)
            self.assertIsNone(completed.completed_at)
            self.assertEqual(completed.last_completed_at, "2026-08-27T20:05:00")
            self.assertEqual(completed.event_at, "2026-08-28T20:00:00")
            self.assertEqual(
                reminders_of(completed, "advance")[-1].trigger_at,
                "2026-08-28T19:30:00",
            )
            self.assertEqual(
                reminders_of(completed, "final")[-1].trigger_at,
                "2026-08-28T20:00:00",
            )

    def test_07_weekly_completion_advances_selected_days_and_friday_to_monday(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            monday = datetime(2026, 8, 24, 18, 0)
            task = manager.add_task(
                "周一三五",
                local_iso(monday),
                [],
                REPEAT_WEEKLY,
                [1, 3, 5],
                now=monday - timedelta(hours=1),
            )
            wednesday = manager.complete_task(task.id, now=monday)
            self.assertEqual(wednesday.event_at, "2026-08-26T18:00:00")
            friday = manager.complete_task(
                task.id,
                now=datetime(2026, 8, 26, 18, 0),
            )
            self.assertEqual(friday.event_at, "2026-08-28T18:00:00")
            next_monday = manager.complete_task(
                task.id,
                now=datetime(2026, 8, 28, 18, 0),
            )
            self.assertEqual(next_monday.event_at, "2026-08-31T18:00:00")

    def test_08_daily_snooze_keeps_base_time_and_completion_uses_base(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            event = datetime(2026, 8, 27, 20, 0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task(
                "每日稍后",
                local_iso(event),
                [],
                REPEAT_DAILY,
                now=event - timedelta(minutes=1),
            )
            final = reminders_of(task, "final")[0]
            manager.mark_reminder_triggered(task.id, final.id)
            snoozed = manager.snooze_reminder(task.id, final.id, 5, now=event)
            self.assertEqual(snoozed.event_at, local_iso(event))
            snooze = reminders_of(snoozed, "snooze")[0]
            self.assertEqual(snooze.trigger_at, "2026-08-27T20:05:00")
            manager.mark_reminder_triggered(task.id, snooze.id)
            advanced = manager.complete_task(
                task.id,
                snooze.id,
                now=datetime(2026, 8, 27, 20, 5),
            )
            self.assertEqual(advanced.event_at, "2026-08-28T20:00:00")
            self.assertFalse(advanced.completed)

    def test_09_weekly_snooze_keeps_base_time_and_next_selected_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            friday = datetime(2026, 8, 28, 18, 0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task(
                "每周稍后",
                local_iso(friday),
                [],
                REPEAT_WEEKLY,
                [1, 5],
                now=friday - timedelta(minutes=1),
            )
            final = reminders_of(task, "final")[0]
            manager.mark_reminder_triggered(task.id, final.id)
            snoozed = manager.snooze_reminder(task.id, final.id, 10, now=friday)
            self.assertEqual(snoozed.event_at, local_iso(friday))
            snooze = reminders_of(snoozed, "snooze")[0]
            manager.mark_reminder_triggered(task.id, snooze.id)
            advanced = manager.complete_task(
                task.id,
                snooze.id,
                now=friday + timedelta(minutes=10),
            )
            self.assertEqual(advanced.event_at, "2026-08-31T18:00:00")

    def test_10_restart_preserves_next_occurrence_without_duplicate_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.json"
            event = datetime(2026, 8, 27, 20, 0)
            manager = TodoManager(path)
            task = manager.add_task(
                "重启去重",
                local_iso(event),
                [5, 30],
                REPEAT_DAILY,
                now=event - timedelta(hours=1),
            )
            advanced = manager.complete_task(task.id, now=event)
            expected_event = advanced.event_at
            expected_records = [item.to_dict() for item in advanced.reminders]
            expected_last_completed = advanced.last_completed_at

            # Restart at the scenario's time, not weeks after its fixed date.
            with patch("models.task.datetime", wraps=datetime) as clock:
                clock.now.return_value = event
                restarted = TodoManager(path)
            loaded = restarted.get_task(task.id)
            self.assertEqual(loaded.event_at, expected_event)
            self.assertEqual(loaded.last_completed_at, expected_last_completed)
            self.assertEqual(
                [item.to_dict() for item in loaded.reminders],
                expected_records,
            )
            with patch("models.task.datetime", wraps=datetime) as clock:
                clock.now.return_value = event
                restarted_again = TodoManager(path)
            self.assertEqual(
                [item.id for item in restarted_again.get_task(task.id).reminders],
                [item.id for item in loaded.reminders],
            )

    def test_11_restart_before_final_waits_and_missed_advance_is_not_replayed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            real_now = datetime.now().replace(microsecond=0)
            event = real_now + timedelta(minutes=2)
            advance = event - timedelta(minutes=5)
            path = Path(temp_dir) / "tasks.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "id": "missed_advance",
                            "text": "错过提前提醒",
                            "completed": False,
                            "event_at": local_iso(event),
                            "reminder_offsets": [5],
                            "repeat_type": REPEAT_DAILY,
                            "repeat_weekdays": [],
                            "reminders": [
                                {
                                    "id": "old_advance",
                                    "type": "advance",
                                    "offset_minutes": 5,
                                    "trigger_at": local_iso(advance),
                                    "triggered": False,
                                    "acknowledged": False,
                                    "cancelled": False,
                                },
                                {
                                    "id": "current_final",
                                    "type": "final",
                                    "offset_minutes": 0,
                                    "trigger_at": local_iso(event),
                                    "triggered": False,
                                    "acknowledged": False,
                                    "cancelled": False,
                                },
                            ],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            manager = TodoManager(path)
            loaded = manager.get_task("missed_advance")
            self.assertEqual(reminders_of(loaded, "advance"), [])
            self.assertEqual(reminders_of(loaded, "final")[0].id, "current_final")

            before_due: list = []
            waiting_scanner = ReminderManager(
                manager,
                now_provider=lambda: event - timedelta(seconds=1),
            )
            waiting_scanner.reminder_ready.connect(
                lambda task, reminder: before_due.append(reminder)
            )
            waiting_scanner.check_now()
            self.assertEqual(before_due, [])
            self.assertTrue(waiting_scanner.waiting_state)

            due: list = []
            due_scanner = ReminderManager(manager, now_provider=lambda: event)
            due_scanner.reminder_ready.connect(
                lambda task, reminder: due.append(reminder)
            )
            due_scanner.check_now()
            self.assertEqual([item.type for item in due], ["final"])

    def test_12_delete_recurring_task_removes_snooze_and_future_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            event = datetime(2026, 8, 27, 20, 0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task(
                "删除循环",
                local_iso(event),
                [],
                REPEAT_DAILY,
                now=event - timedelta(minutes=1),
            )
            final = reminders_of(task, "final")[0]
            manager.mark_reminder_triggered(task.id, final.id)
            manager.snooze_reminder(task.id, final.id, 30, now=event)
            self.assertTrue(manager.delete_task(task.id))
            self.assertIsNone(manager.get_task(task.id))
            scanner = ReminderManager(
                manager,
                now_provider=lambda: event + timedelta(days=2),
            )
            due: list = []
            scanner.reminder_ready.connect(lambda task, reminder: due.append(reminder))
            scanner.check_now()
            self.assertEqual(due, [])

    def test_13_edit_time_weekdays_and_all_repeat_conversions_recalculate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime(2026, 8, 27, 10, 0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task(
                "转换任务",
                "2026-08-27T20:00:00",
                [30],
                now=base,
            )
            daily = manager.update_task(
                task.id,
                task.text,
                "2026-08-27T21:00:00",
                [5],
                REPEAT_DAILY,
                [],
                now=base,
            )
            self.assertEqual(daily.repeat_type, REPEAT_DAILY)
            self.assertEqual(reminders_of(daily, "advance")[-1].trigger_at, "2026-08-27T20:55:00")

            weekly = manager.update_task(
                task.id,
                task.text,
                daily.event_at,
                [30],
                REPEAT_WEEKLY,
                [1, 5],
                now=base,
            )
            self.assertEqual(weekly.repeat_type, REPEAT_WEEKLY)
            self.assertEqual(weekly.repeat_weekdays, (1, 5))
            self.assertEqual(weekly.event_at, "2026-08-28T21:00:00")
            first_weekly_ids = {item.id for item in weekly.reminders if not item.triggered}

            changed_days = manager.update_task(
                task.id,
                task.text,
                weekly.event_at,
                [30],
                REPEAT_WEEKLY,
                [2, 4],
                now=base,
            )
            self.assertEqual(changed_days.event_at, "2026-09-01T21:00:00")
            self.assertTrue(
                first_weekly_ids.isdisjoint(
                    {item.id for item in changed_days.reminders if not item.triggered}
                )
            )

            back_daily = manager.update_task(
                task.id,
                task.text,
                changed_days.event_at,
                [],
                REPEAT_DAILY,
                [],
                now=base,
            )
            self.assertEqual(back_daily.repeat_type, REPEAT_DAILY)
            back_none = manager.update_task(
                task.id,
                task.text,
                back_daily.event_at,
                [],
                REPEAT_NONE,
                [],
                now=base,
            )
            self.assertEqual(back_none.repeat_type, REPEAT_NONE)
            none_to_weekly = manager.update_task(
                task.id,
                task.text,
                back_none.event_at,
                [],
                REPEAT_WEEKLY,
                [3],
                now=base,
            )
            self.assertEqual(none_to_weekly.repeat_type, REPEAT_WEEKLY)
            self.assertEqual(none_to_weekly.repeat_weekdays, (3,))

    def test_14_ten_second_scans_never_advance_an_occurrence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            event = datetime(2026, 8, 27, 20, 0)
            clock = [event]
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task(
                "扫描只触发一次",
                local_iso(event),
                [],
                REPEAT_DAILY,
                now=event - timedelta(minutes=1),
            )
            scanner = ReminderManager(manager, now_provider=lambda: clock[0])
            due: list = []
            scanner.reminder_ready.connect(lambda item, reminder: due.append(reminder))
            scanner.check_now()
            scanner.check_now()
            scanner.check_now()
            self.assertEqual(len(due), 1)
            self.assertEqual(manager.get_task(task.id).event_at, local_iso(event))

            current = due[0]
            advanced = manager.complete_task(task.id, current.id, now=event)
            scanner.reminder_finished(current.id)
            expected_event = advanced.event_at
            expected_ids = [item.id for item in advanced.reminders]
            scanner.check_now()
            scanner.check_now()
            self.assertEqual(manager.get_task(task.id).event_at, expected_event)
            self.assertEqual(
                [item.id for item in manager.get_task(task.id).reminders],
                expected_ids,
            )

    def test_15_todo_ui_repeat_controls_schedule_text_and_json_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            path = Path(temp_dir) / "tasks.json"
            manager = TodoManager(path)
            daily = manager.add_task(
                "界面每日",
                local_iso(base + timedelta(days=1)),
                [5],
                REPEAT_DAILY,
                now=base,
            )
            weekly = manager.add_task(
                "界面每周",
                local_iso(base + timedelta(days=1)),
                [],
                REPEAT_WEEKLY,
                [1, 3, 5],
                now=base,
            )
            self.assertRegex(format_task_schedule(daily), r"^每天 · \d{2}:\d{2}$")
            self.assertRegex(
                format_task_schedule(weekly),
                r"^每周一、周三、周五 · \d{2}:\d{2}$",
            )

            window = TodoWindow(manager)
            add_combo = window.findChild(QComboBox, "addRepeatType")
            self.assertIsNotNone(add_combo)
            self.assertEqual(
                [add_combo.itemData(index) for index in range(add_combo.count())],
                [REPEAT_NONE, REPEAT_DAILY, REPEAT_WEEKLY],
            )
            add_combo.setCurrentIndex(add_combo.findData(REPEAT_WEEKLY))
            self.app.processEvents()
            weekday_checks = [
                window.findChild(QCheckBox, f"addWeekday{weekday}")
                for weekday in range(1, 8)
            ]
            self.assertTrue(all(item is not None for item in weekday_checks))
            self.assertFalse(weekday_checks[0].parentWidget().isHidden())

            dialog = TaskEditDialog(weekly, window)
            edit_combo = dialog.findChild(QComboBox, "editRepeatType")
            self.assertEqual(edit_combo.currentData(), REPEAT_WEEKLY)
            self.assertTrue(dialog.findChild(QCheckBox, "editWeekday1").isChecked())
            self.assertTrue(dialog.findChild(QCheckBox, "editWeekday3").isChecked())
            self.assertTrue(dialog.findChild(QCheckBox, "editWeekday5").isChecked())
            dialog.deleteLater()
            window.deleteLater()

            records = json.loads(path.read_text(encoding="utf-8"))
            saved = {record["id"]: record for record in records}
            self.assertEqual(saved[daily.id]["repeat_type"], REPEAT_DAILY)
            self.assertEqual(saved[weekly.id]["repeat_weekdays"], [1, 3, 5])
            self.assertIn("last_completed_at", saved[daily.id])

    def test_16_recurring_bubble_remind_then_completion_returns_to_wait(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime.now().replace(microsecond=0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            task = manager.add_task(
                "循环气泡",
                local_iso(base - timedelta(seconds=1)),
                [],
                REPEAT_DAILY,
                now=base - timedelta(minutes=1),
            )
            scanner = ReminderManager(manager, now_provider=lambda: base)
            pet = PetWindow(self.config, manager, scanner)
            pet.show()
            scanner.check_now()
            self.app.processEvents()
            self.assertEqual(pet.animation_state, "remind")
            self.assertEqual(len(pet.reminder_bubbles), 1)
            bubble = pet.reminder_bubbles[0]
            self.assertIn("时间到了", bubble.findChild(QLabel, "reminderMessage").text())
            bubble.findChild(QPushButton, "completeTaskButton").click()
            self.app.processEvents()
            updated = manager.get_task(task.id)
            self.assertFalse(updated.completed)
            self.assertEqual(
                updated.event_at,
                local_iso(base.replace(microsecond=0) + timedelta(days=1, seconds=-1)),
            )
            self.assertEqual(len(pet.reminder_bubbles), 0)
            self.assertEqual(pet.animation_state, "wait")
            pet.close()
