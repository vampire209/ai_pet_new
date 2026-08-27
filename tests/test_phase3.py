"""Phase 3 final-event reminder acceptance tests."""

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

from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDateEdit,
    QDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTimeEdit,
)

from config import AppConfig
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager
from todo_window import TaskEditDialog, TodoWindow, format_event_time


def local_iso(value: datetime) -> str:
    return value.replace(microsecond=0).isoformat(timespec="seconds")


class Phase3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase3-tests"])
        cls.config = AppConfig.load()

    def test_no_time_and_legacy_event_values_are_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_path = Path(temp_dir) / "tasks.json"
            records = [
                {"id": "missing", "text": "没有新字段", "completed": False},
                {
                    "id": "empty",
                    "text": "空时间",
                    "event_at": "",
                    "reminder_triggered": True,
                    "reminder_acknowledged": True,
                },
                {
                    "id": "invalid",
                    "text": "非法时间",
                    "event_at": "not-an-iso-time",
                },
            ]
            data_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
            manager = TodoManager(data_path)
            self.assertEqual(len(manager.get_tasks()), 3)
            for task in manager.get_tasks():
                self.assertIsNone(task.event_at)
                self.assertFalse(task.reminder_triggered)
                self.assertFalse(task.reminder_acknowledged)

            normal = manager.add_task("普通任务")
            self.assertIsNone(normal.event_at)
            reloaded = TodoManager(data_path).get_task(normal.id)
            self.assertIsNone(reloaded.event_at)

    def test_optional_time_ui_adds_future_and_rejects_past(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window = TodoWindow(manager)
            task_input = window.findChild(QLineEdit, "taskInput")
            time_enabled = window.findChild(QCheckBox, "addTimeEnabled")
            date_edit = window.findChild(QDateEdit, "addEventDate")
            time_edit = window.findChild(QTimeEdit, "addEventTime")
            self.assertFalse(time_enabled.isChecked())
            self.assertTrue(date_edit.calendarPopup())
            self.assertEqual(time_edit.displayFormat(), "HH:mm")
            self.assertFalse(time_edit.isReadOnly())

            future = (datetime.now() + timedelta(days=1)).replace(second=0, microsecond=0)
            task_input.setText("测试提醒")
            time_enabled.setChecked(True)
            date_edit.setDate(QDate(future.year, future.month, future.day))
            time_edit.setTime(QTime(future.hour, future.minute))
            window._add_task()
            created = manager.get_tasks()[0]
            self.assertEqual(created.event_at, local_iso(future))
            self.assertFalse(created.reminder_triggered)
            self.assertFalse(created.reminder_acknowledged)

            past = (datetime.now() - timedelta(minutes=2)).replace(second=0, microsecond=0)
            task_input.setText("过期任务")
            time_enabled.setChecked(True)
            date_edit.setDate(QDate(past.year, past.month, past.day))
            time_edit.setTime(QTime(past.hour, past.minute))
            with patch.object(QMessageBox, "warning") as warning:
                window._add_task()
            warning.assert_called_once_with(
                window,
                "时间无效",
                "提醒时间必须晚于当前时间。",
            )
            self.assertEqual(len(manager.get_tasks()), 1)
            window.hide()
            window.deleteLater()

    def test_editing_event_resets_reminder_and_old_time_cannot_fire(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_path = Path(temp_dir) / "tasks.json"
            base = datetime.now().replace(microsecond=0)
            old_event = base + timedelta(hours=1)
            new_event = base + timedelta(hours=3)
            manager = TodoManager(data_path)
            task = manager.add_task("考试", local_iso(old_event))
            manager.mark_reminder_triggered(task.id)
            manager.acknowledge_reminder(task.id)

            updated = manager.update_task(task.id, "高等数学考试", local_iso(new_event))
            self.assertEqual(updated.event_at, local_iso(new_event))
            self.assertFalse(updated.reminder_triggered)
            self.assertFalse(updated.reminder_acknowledged)
            manager.acknowledge_reminder(task.id)
            self.assertFalse(manager.get_task(task.id).reminder_acknowledged)

            reloaded = TodoManager(data_path).get_task(task.id)
            self.assertEqual(reloaded.event_at, local_iso(new_event))
            self.assertEqual(format_event_time(reloaded.event_at), format_event_time(local_iso(new_event)))

            due: list = []
            reminder_manager = ReminderManager(
                manager,
                now_provider=lambda: old_event + timedelta(minutes=1),
            )
            reminder_manager.reminder_due.connect(due.append)
            reminder_manager.check_now()
            self.assertEqual(due, [])

    def test_due_scan_persists_once_and_ack_does_not_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_path = Path(temp_dir) / "tasks.json"
            now = datetime.now().replace(microsecond=0)
            manager = TodoManager(data_path)
            task = manager.add_task("最终提醒", local_iso(now - timedelta(seconds=1)))
            due: list = []
            reminder_manager = ReminderManager(manager, now_provider=lambda: now)
            reminder_manager.reminder_due.connect(due.append)

            reminder_manager.check_now()
            reminder_manager.check_now()
            reminder_manager.check_now()
            self.assertEqual([item.id for item in due], [task.id])
            persisted = TodoManager(data_path).get_task(task.id)
            self.assertTrue(persisted.reminder_triggered)
            self.assertFalse(persisted.reminder_acknowledged)

            manager.acknowledge_reminder(task.id)
            acknowledged = TodoManager(data_path).get_task(task.id)
            self.assertTrue(acknowledged.reminder_acknowledged)
            self.assertFalse(acknowledged.completed)
            self.assertIsNone(acknowledged.completed_at)

    def test_restart_after_missed_time_triggers_and_preserves_local_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_path = Path(temp_dir) / "tasks.json"
            base = datetime.now().replace(microsecond=0)
            event = base + timedelta(minutes=2)
            first_manager = TodoManager(data_path)
            task = first_manager.add_task("重启补触发", local_iso(event))

            restarted_manager = TodoManager(data_path)
            reloaded = restarted_manager.get_task(task.id)
            self.assertEqual(reloaded.event_at, local_iso(event))
            self.assertIsNone(datetime.fromisoformat(reloaded.event_at).tzinfo)

            due: list = []
            reminder_manager = ReminderManager(
                restarted_manager,
                now_provider=lambda: event + timedelta(minutes=10),
            )
            reminder_manager.reminder_due.connect(due.append)
            reminder_manager.check_now()
            self.assertEqual([item.id for item in due], [task.id])

    def test_pet_reminder_ack_and_view_restore_idle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_path = Path(temp_dir) / "tasks.json"
            now = datetime.now().replace(microsecond=0)
            manager = TodoManager(data_path)
            first = manager.add_task("点击知道了", local_iso(now - timedelta(minutes=1)))
            reminder_manager = ReminderManager(manager, now_provider=lambda: now)
            pet = PetWindow(self.config, manager, reminder_manager)
            pet.show()
            reminder_manager.check_now()
            self.app.processEvents()

            self.assertEqual(len(pet.reminder_dialogs), 1)
            self.assertEqual(pet.animation_state, "remind")
            self.assertEqual(pet.current_animation, "remind")
            acknowledge = pet.reminder_dialogs[0].findChild(
                QPushButton,
                "acknowledgeButton",
            )
            acknowledge.click()
            self.app.processEvents()
            acknowledged = manager.get_task(first.id)
            self.assertTrue(acknowledged.reminder_acknowledged)
            self.assertFalse(acknowledged.completed)
            self.assertEqual(pet.animation_state, "idle")
            self.assertEqual(len(pet.reminder_dialogs), 0)

            second = manager.add_task("点击查看待办", local_iso(now - timedelta(seconds=1)))
            reminder_manager.check_now()
            self.app.processEvents()
            view_button = pet.reminder_dialogs[0].findChild(
                QPushButton,
                "viewTodoButton",
            )
            view_button.click()
            self.app.processEvents()
            self.assertIsNotNone(pet.todo_window)
            self.assertTrue(pet.todo_window.isVisible())
            self.assertEqual(pet.todo_window.task_count, 2)
            self.assertEqual(pet.animation_state, "idle")
            self.assertFalse(manager.get_task(second.id).reminder_acknowledged)

            pet.todo_window.hide()
            pet.todo_window.deleteLater()
            pet.close()

    def test_edit_dialog_rejects_new_past_time_and_list_uses_task_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            future = (datetime.now() + timedelta(days=1)).replace(second=0, microsecond=0)
            task = manager.add_task("时间显示", local_iso(future))
            window = TodoWindow(manager)
            labels = window.findChildren(QLabel, "eventTimeLabel")
            self.assertEqual(len(labels), 1)
            self.assertEqual(labels[0].text(), format_event_time(task.event_at))

            no_time_task = manager.add_task("编辑校验")
            dialog = TaskEditDialog(no_time_task)
            dialog.time_checkbox.setChecked(True)
            past = (datetime.now() - timedelta(minutes=2)).replace(second=0, microsecond=0)
            dialog.date_edit.setDate(QDate(past.year, past.month, past.day))
            dialog.time_edit.setTime(QTime(past.hour, past.minute))
            with patch.object(QMessageBox, "warning") as warning:
                dialog._accept_if_valid()
            warning.assert_called_once_with(
                dialog,
                "时间无效",
                "提醒时间必须晚于当前时间。",
            )
            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
            dialog.deleteLater()
            window.hide()
            window.deleteLater()


if __name__ == "__main__":
    unittest.main()
