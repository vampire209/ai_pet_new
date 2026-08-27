"""Phase 2 persistence, tolerance, UI, and desktop-pet entry tests."""

from __future__ import annotations

from contextlib import redirect_stderr
from datetime import datetime
from io import StringIO
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

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QMessageBox

from config import AppConfig
from pet_window import PetWindow
from todo_manager import TodoManager
from todo_window import TaskEditDialog, TodoWindow


class Phase2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase2-tests"])
        cls.config = AppConfig.load()

    def test_crud_and_all_states_survive_manager_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_path = Path(temp_dir) / "nested" / "tasks.json"
            manager = TodoManager(data_path)
            self.assertTrue(data_path.exists())
            self.assertEqual(json.loads(data_path.read_text(encoding="utf-8")), [])

            task = manager.add_task("  完成高数作业  ")
            self.assertEqual(task.text, "完成高数作业")
            self.assertFalse(task.completed)
            self.assertIsNone(task.completed_at)
            created = datetime.fromisoformat(task.created_at or "")
            self.assertIsNone(created.tzinfo)

            manager = TodoManager(data_path)
            self.assertEqual(manager.get_task(task.id).text, "完成高数作业")

            manager.update_task(task.id, "完成高数第3章习题")
            manager = TodoManager(data_path)
            self.assertEqual(manager.get_task(task.id).text, "完成高数第3章习题")

            completed = manager.toggle_complete(task.id)
            self.assertTrue(completed.completed)
            self.assertIsNotNone(completed.completed_at)
            completed_time = datetime.fromisoformat(completed.completed_at or "")
            self.assertIsNone(completed_time.tzinfo)

            manager = TodoManager(data_path)
            self.assertTrue(manager.get_task(task.id).completed)
            manager.toggle_complete(task.id)
            manager = TodoManager(data_path)
            restored = manager.get_task(task.id)
            self.assertFalse(restored.completed)
            self.assertIsNone(restored.completed_at)

            self.assertTrue(manager.delete_task(task.id))
            manager = TodoManager(data_path)
            self.assertEqual(manager.get_tasks(), ())

    def test_bad_and_legacy_json_never_crashes_startup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_path = Path(temp_dir) / "tasks.json"
            data_path.write_text("", encoding="utf-8")
            manager = TodoManager(data_path)
            self.assertEqual(manager.get_tasks(), ())
            self.assertEqual(json.loads(data_path.read_text(encoding="utf-8")), [])

            data_path.write_text("{ damaged", encoding="utf-8")
            warning_output = StringIO()
            with redirect_stderr(warning_output):
                manager = TodoManager(data_path)
            self.assertEqual(manager.get_tasks(), ())
            self.assertIn("JSON 损坏", warning_output.getvalue())

            records = [
                {
                    "text": "缺少字段但可恢复",
                    "completed": True,
                    "created_at": "",
                    "completed_at": "",
                },
                {"id": "duplicate", "text": "有效任务", "created_at": "invalid"},
                {"id": "duplicate", "text": "重复 ID 任务"},
                {"id": "bad", "text": "   "},
                "not an object",
            ]
            data_path.write_text(
                json.dumps(records, ensure_ascii=False),
                encoding="utf-8",
            )
            warning_output = StringIO()
            with redirect_stderr(warning_output):
                manager = TodoManager(data_path)
            tasks = manager.get_tasks()
            self.assertEqual(len(tasks), 3)
            self.assertEqual(len({task.id for task in tasks}), 3)
            self.assertTrue(tasks[0].completed)
            self.assertIsNone(tasks[0].created_at)
            self.assertIsNone(tasks[0].completed_at)
            self.assertIsNone(tasks[1].created_at)
            self.assertIn("跳过", warning_output.getvalue())
            self.assertIn("ID 重复", warning_output.getvalue())

    def test_todo_window_add_enter_edit_toggle_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window = TodoWindow(manager)
            task_input = window.findChild(QLineEdit, "taskInput")
            self.assertIsNotNone(task_input)
            self.assertGreaterEqual(task_input.minimumHeight(), 36)

            task_input.setText("   ")
            task_input.returnPressed.emit()
            self.assertEqual(window.task_count, 0)

            task_input.setText("完成高数作业")
            task_input.returnPressed.emit()
            self.assertEqual(window.task_count, 1)
            self.assertEqual(task_input.text(), "")
            task_id = manager.get_tasks()[0].id

            with (
                patch.object(
                    TaskEditDialog,
                    "exec",
                    return_value=QDialog.DialogCode.Accepted,
                ),
                patch.object(TaskEditDialog, "get_values", return_value=("   ", None)),
                patch.object(window, "_show_error") as warning,
            ):
                window._edit_task(task_id)
            warning.assert_called_once()
            self.assertEqual(manager.get_task(task_id).text, "完成高数作业")

            with (
                patch.object(
                    TaskEditDialog,
                    "exec",
                    return_value=QDialog.DialogCode.Accepted,
                ),
                patch.object(
                    TaskEditDialog,
                    "get_values",
                    return_value=("完成高数第3章习题", None),
                ),
            ):
                window._edit_task(task_id)
            self.assertEqual(manager.get_task(task_id).text, "完成高数第3章习题")

            window._toggle_task(task_id)
            self.assertTrue(manager.get_task(task_id).completed)
            window._toggle_task(task_id)
            self.assertFalse(manager.get_task(task_id).completed)
            self.assertIsNone(manager.get_task(task_id).completed_at)

            with patch.object(
                QMessageBox,
                "question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                window._delete_task(task_id)
            self.assertEqual(window.task_count, 0)
            self.assertEqual(TodoManager(manager.data_path).get_tasks(), ())
            window.hide()
            window.deleteLater()

    def test_pet_entry_single_instance_drag_isolation_and_live_idle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            pet = PetWindow(self.config, manager)
            pet.show()
            self.app.processEvents()
            self.assertIsNone(pet.todo_window)

            origin = QPointF(500, 500)
            press = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(100, 100),
                origin,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            release = QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                QPointF(100, 100),
                origin,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            pet.mousePressEvent(press)
            pet.mouseReleaseEvent(release)
            self.assertIsNone(pet.todo_window)

            threshold = QApplication.styleHints().startDragDistance()
            pet.mousePressEvent(press)
            drag_global = QPointF(500 + threshold + 5, 500)
            move = QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(100 + threshold + 5, 100),
                drag_global,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            pet.mouseMoveEvent(move)
            drag_release = QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                QPointF(100 + threshold + 5, 100),
                drag_global,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            pet.mouseReleaseEvent(drag_release)
            self.assertIsNone(pet.todo_window)

            double_click = QMouseEvent(
                QEvent.Type.MouseButtonDblClick,
                QPointF(100, 100),
                origin,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            pet.mouseDoubleClickEvent(double_click)
            self.app.processEvents()
            todo = pet.todo_window
            self.assertIsNotNone(todo)
            self.assertEqual(todo.windowTitle(), "我的待办")
            self.assertTrue(todo.isVisible())

            task_input = todo.findChild(QLineEdit, "taskInput")
            task_input.setText("关闭后仍存在")
            task_input.returnPressed.emit()
            self.assertEqual(todo.task_count, 1)

            todo.close()
            self.app.processEvents()
            self.assertFalse(todo.isVisible())
            pet.mouseDoubleClickEvent(double_click)
            self.app.processEvents()
            self.assertIs(pet.todo_window, todo)
            self.assertTrue(todo.isVisible())
            self.assertEqual(todo.task_count, 1)

            menu_texts = [action.text() for action in pet._create_context_menu().actions()]
            self.assertEqual(menu_texts, ["打开待办", "AI聊天", "", "退出"])
            menu = pet._create_context_menu()
            menu.actions()[0].trigger()
            self.app.processEvents()
            self.assertIs(pet.todo_window, todo)
            self.assertTrue(todo.isVisible())

            self.assertEqual(pet.current_animation, "idle")
            frame_before = pet.current_frame_index
            QTest.qWait(self.config.idle_interval_ms + 60)
            self.assertNotEqual(pet.current_frame_index, frame_before)
            self.assertEqual(pet.current_animation, "idle")

            todo.hide()
            todo.deleteLater()
            pet.close()


if __name__ == "__main__":
    unittest.main()
