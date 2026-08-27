"""Phase 10B SQLite manual long-term memory tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication, QMessageBox

from ai.chat_service import ChatService
from ai.fake_client import FakeDeepSeekClient
from ai.profile import PetProfile
from ai.profile_service import ProfileService
from chat_window import ChatWindow
from config import AppConfig, PROJECT_ROOT as CONFIG_PROJECT_ROOT
from memory.memory_models import MEMORY_CONTENT_MAX_LENGTH, MemoryDataError
from memory.memory_service import (
    MemoryNotFoundError,
    MemoryService,
    MemoryStateError,
)
from memory.memory_store import default_memory_db_path
from memory_window import MemoryManagerWindow
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager


FIXED_NOW = datetime(2026, 8, 28, 9, 30, 0)


@contextmanager
def temporary_memory_service():
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "memory.db"
        service = MemoryService(path)
        try:
            yield service, path, Path(temp_dir)
        finally:
            service.close()


class Phase10BMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase10b-tests"])

    def test_01_first_run_creates_database_and_expected_schema(self) -> None:
        with temporary_memory_service() as (_service, path, _root):
            self.assertTrue(path.is_file())
            connection = sqlite3.connect(path)
            try:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(memories)")
                }
            finally:
                connection.close()
            self.assertTrue(
                {"id", "content", "is_core", "archived", "created_at", "updated_at"}
                <= columns
            )

    def test_02_create_memory_is_active_by_default(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            memory = service.create_memory("学习信号与系统", now=FIXED_NOW)
            self.assertFalse(memory.is_core)
            self.assertFalse(memory.archived)
            self.assertEqual(service.active_memories(), (memory,))
            self.assertEqual(memory.created_at, "2026-08-28T09:30:00")

    def test_03_empty_content_cannot_be_created(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            for content in ("", "   "):
                with self.subTest(content=content):
                    with self.assertRaises(MemoryDataError):
                        service.create_memory(content)

    def test_04_chinese_content_is_trimmed_and_preserved(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            memory = service.create_memory("  用户希望数学题展示关键步骤  ")
            self.assertEqual(memory.content, "用户希望数学题展示关键步骤")

    def test_05_emoji_content_is_supported(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            memory = service.create_memory("用户喜欢蓝色兔子🐰✨")
            self.assertEqual(service.get_memory(memory.id).content, "用户喜欢蓝色兔子🐰✨")

    def test_06_edit_memory_updates_content_and_timestamp(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            memory = service.create_memory("旧内容", now=FIXED_NOW)
            edited = service.update_memory(
                memory.id,
                "新内容",
                now=datetime(2026, 8, 28, 10, 0, 0),
            )
            self.assertEqual(edited.content, "新内容")
            self.assertEqual(edited.updated_at, "2026-08-28T10:00:00")
            self.assertEqual(service.get_memory(memory.id), edited)

    def test_07_delete_memory_is_permanent(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            memory = service.create_memory("将被删除")
            service.delete_memory(memory.id)
            self.assertEqual(service.all_memories(), ())
            with self.assertRaises(MemoryNotFoundError):
                service.get_memory(memory.id)

    def test_08_active_memory_can_become_core(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            memory = service.create_memory("核心候选")
            core = service.set_core(memory.id)
            self.assertTrue(core.is_core)
            self.assertEqual(service.core_memories(), (core,))
            self.assertEqual(service.active_memories(), ())

    def test_09_core_memory_can_return_to_active(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            memory = service.create_memory("核心记忆")
            service.set_core(memory.id)
            active = service.set_core(memory.id, False)
            self.assertFalse(active.is_core)
            self.assertEqual(service.active_memories(), (active,))

    def test_10_active_memory_can_be_archived(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            memory = service.create_memory("待归档")
            archived = service.archive_memory(memory.id)
            self.assertTrue(archived.archived)
            self.assertFalse(archived.is_core)
            self.assertEqual(service.active_memories(), ())

    def test_11_archiving_core_memory_clears_core_consistently(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            memory = service.create_memory("核心后归档")
            service.set_core(memory.id)
            archived = service.archive_memory(memory.id)
            self.assertTrue(archived.archived)
            self.assertFalse(archived.is_core)
            self.assertEqual(service.core_memories(), ())

    def test_12_archived_memory_restores_as_active(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            memory = service.create_memory("恢复我")
            service.archive_memory(memory.id)
            restored = service.restore_memory(memory.id)
            self.assertFalse(restored.archived)
            self.assertFalse(restored.is_core)
            self.assertEqual(service.active_memories(), (restored,))

    def test_13_search_supports_chinese_and_english_contains(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            chinese = service.create_memory("用户希望数学题展示关键步骤")
            english = service.create_memory("User prefers concise Python examples")
            service.create_memory("喜欢蓝色")
            self.assertEqual(service.search_memories("数学"), (chinese,))
            self.assertEqual(service.search_memories("PYTHON"), (english,))

    def test_14_core_and_active_queries_are_separate(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            core = service.create_memory("核心")
            active = service.create_memory("活跃")
            core = service.set_core(core.id)
            self.assertEqual(service.core_memories(), (core,))
            self.assertEqual(service.active_memories(), (active,))

    def test_15_archived_query_excludes_main_memories(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            archived = service.create_memory("归档")
            active = service.create_memory("活跃")
            archived = service.archive_memory(archived.id)
            self.assertEqual(service.archived_memories(), (archived,))
            self.assertEqual(service.search_memories("", archived=False), (active,))

    def test_16_restart_preserves_content_and_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "memory.db"
            first = MemoryService(path)
            memory = first.create_memory("初始内容")
            first.update_memory(memory.id, "编辑后的内容")
            first.close()
            second = MemoryService(path)
            try:
                self.assertEqual(second.get_memory(memory.id).content, "编辑后的内容")
            finally:
                second.close()

    def test_17_restart_preserves_core_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "memory.db"
            first = MemoryService(path)
            memory = first.create_memory("核心持久化")
            first.set_core(memory.id)
            first.close()
            second = MemoryService(path)
            try:
                self.assertTrue(second.get_memory(memory.id).is_core)
            finally:
                second.close()

    def test_18_restart_preserves_archived_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "memory.db"
            first = MemoryService(path)
            memory = first.create_memory("归档持久化")
            first.archive_memory(memory.id)
            first.close()
            second = MemoryService(path)
            try:
                self.assertTrue(second.get_memory(memory.id).archived)
            finally:
                second.close()

    def test_19_multiple_memories_remain_independent(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            created = [service.create_memory(f"记忆 {number}") for number in range(5)]
            service.set_core(created[0].id)
            service.archive_memory(created[1].id)
            self.assertEqual(len(service.all_memories()), 5)
            self.assertEqual(len(service.core_memories()), 1)
            self.assertEqual(len(service.active_memories()), 3)
            self.assertEqual(len(service.archived_memories()), 1)

    def test_20_memory_and_profile_are_fully_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memories = MemoryService(root / "memory.db")
            profiles = ProfileService(root / "profile.json")
            try:
                memory = memories.create_memory("用户偏好关键步骤")
                profiles.save(PetProfile(pet_name="新泽西"))
                self.assertEqual(memories.get_memory(memory.id).content, "用户偏好关键步骤")
                memories.delete_memory(memory.id)
                self.assertEqual(profiles.profile.pet_name, "新泽西")
            finally:
                memories.close()

    def test_21_memory_and_todo_are_fully_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memories = MemoryService(root / "memory.db")
            manager = TodoManager(root / "tasks.json")
            try:
                memory = memories.create_memory("用户喜欢逐步推导")
                task = manager.add_task("完成作业")
                memories.delete_memory(memory.id)
                self.assertIsNotNone(manager.get_task(task.id))
                self.assertNotIn("逐步推导", (root / "tasks.json").read_text(encoding="utf-8"))
            finally:
                memories.close()

    def test_22_clear_chat_preserves_every_memory_state(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            core = memories.create_memory("核心")
            active = memories.create_memory("活跃")
            archived = memories.create_memory("归档")
            memories.set_core(core.id)
            memories.archive_memory(archived.id)
            before = memories.all_memories()
            service = ChatService(client=FakeDeepSeekClient())
            window = ChatWindow(service)
            service.begin_turn("你好")
            service.complete_turn("你好呀")
            window.clear_chat()
            self.assertEqual(service.history, ())
            self.assertEqual(memories.all_memories(), before)
            self.assertEqual(len(memories.core_memories()), 1)
            self.assertEqual(len(memories.active_memories()), 1)
            self.assertEqual(len(memories.archived_memories()), 1)
            window.shutdown()

    def test_23_tests_use_temp_database_not_real_database(self) -> None:
        default_path = default_memory_db_path()
        self.assertEqual(default_path.name, "memory.db")
        self.assertEqual(default_path.parent.name, "ai_pet_new")
        self.assertFalse(default_path.is_relative_to(CONFIG_PROJECT_ROOT))
        with temporary_memory_service() as (service, path, _root):
            self.assertEqual(service.db_path, path)
            self.assertNotEqual(service.db_path, default_path)

    def test_24_memory_manager_window_opens_closes_and_is_single_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memories = MemoryService(root / "memory.db")
            manager = TodoManager(root / "tasks.json")
            scanner = ReminderManager(manager)
            pet = PetWindow(
                AppConfig.load(),
                manager,
                scanner,
                memory_service=memories,
            )
            pet._open_memory_window()
            first = pet.memory_window
            self.assertIs(first.memory_service, memories)
            self.assertTrue(first.isVisible())
            first.close()
            self.app.processEvents()
            self.assertFalse(first.isVisible())
            pet._open_memory_window()
            self.assertIs(pet.memory_window, first)
            pet.close()

    def test_25_service_mutations_refresh_open_window_immediately(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            window = MemoryManagerWindow(service)
            window.show()
            memory = service.create_memory("立即刷新")
            self.app.processEvents()
            self.assertIn(memory.id, window.rendered_memory_ids)
            service.archive_memory(memory.id)
            self.app.processEvents()
            self.assertNotIn(memory.id, window.rendered_memory_ids)
            window.toggle_archive_view()
            self.assertIn(memory.id, window.rendered_memory_ids)
            service.restore_memory(memory.id)
            self.app.processEvents()
            self.assertNotIn(memory.id, window.rendered_memory_ids)
            window.toggle_archive_view()
            self.assertIn(memory.id, window.rendered_memory_ids)
            window.close()

    def test_26_menu_uses_injected_memory_service(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memories = MemoryService(root / "memory.db")
            manager = TodoManager(root / "tasks.json")
            scanner = ReminderManager(manager)
            pet = PetWindow(
                AppConfig.load(),
                manager,
                scanner,
                memory_service=memories,
            )
            menu = pet._create_context_menu()
            self.assertEqual(
                [action.text() for action in menu.actions()],
                ["打开待办", "AI聊天", "桌宠设置", "记忆管理", "", "退出"],
            )
            menu.actions()[3].trigger()
            self.app.processEvents()
            self.assertIs(pet.memory_window.memory_service, memories)
            pet.close()

    def test_27_archived_memory_cannot_be_set_core_directly(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            memory = service.create_memory("已归档")
            service.archive_memory(memory.id)
            with self.assertRaises(MemoryStateError):
                service.set_core(memory.id)

    def test_28_search_can_be_scoped_to_archive(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            active = service.create_memory("数学 active")
            archived = service.create_memory("数学 archive")
            service.archive_memory(archived.id)
            self.assertEqual(service.search_memories("数学", archived=False), (active,))
            self.assertEqual(service.search_memories("数学", archived=True), (service.get_memory(archived.id),))

    def test_29_content_has_a_reasonable_maximum_length(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            service.create_memory("蓝" * MEMORY_CONTENT_MAX_LENGTH)
            with self.assertRaises(MemoryDataError):
                service.create_memory("蓝" * (MEMORY_CONTENT_MAX_LENGTH + 1))

    def test_30_ui_actions_delegate_and_refresh_without_direct_sql(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            window = MemoryManagerWindow(service)
            with patch(
                "memory_window.QInputDialog.getMultiLineText",
                return_value=("用户希望数学题展示关键步骤", True),
            ):
                window._prompt_new_memory()
            created = service.active_memories()[0]
            self.assertIn(created.id, window.rendered_memory_ids)

            with patch(
                "memory_window.QInputDialog.getMultiLineText",
                return_value=("用户希望数学题展示完整关键步骤", True),
            ):
                window._prompt_edit_memory(created.id)
            self.assertIn("完整", service.get_memory(created.id).content)
            window._set_core(created.id, True)
            self.assertTrue(service.get_memory(created.id).is_core)
            window._set_core(created.id, False)
            window._archive_memory(created.id)
            self.assertTrue(service.get_memory(created.id).archived)
            window._restore_memory(created.id)
            self.assertFalse(service.get_memory(created.id).archived)
            with patch(
                "memory_window.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ):
                window._confirm_delete_memory(created.id)
            self.assertEqual(service.all_memories(), ())
            window.close()

    def test_31_memory_is_not_added_to_deepseek_system_or_history(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            memories.create_memory("绝不能自动发送给模型的记忆")
            chat = ChatService(client=FakeDeepSeekClient())
            messages = chat.begin_turn("你好")
            self.assertNotIn("绝不能自动发送给模型的记忆", repr(messages))
            chat.complete_turn("你好呀")
            self.assertNotIn("绝不能自动发送给模型的记忆", repr(chat.history))


if __name__ == "__main__":
    unittest.main()
