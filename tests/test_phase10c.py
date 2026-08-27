"""Phase 10C local automatic-memory and chat-injection tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Event
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtTest import QTest

from ai.chat_action_router import ChatActionRouter
from ai.chat_service import ChatService
from ai.fake_client import FakeDeepSeekClient
from ai.profile import PetProfile, RELATIONSHIP_GIRLFRIEND
from ai.profile_service import ProfileService
from chat_window import ChatWindow
from config import AppConfig
from memory.memory_extractor import MemoryExtractor
from memory.memory_maintenance import AUTO_ARCHIVE_AFTER_DAYS
from memory.memory_models import SOURCE_CHAT_AUTO, SOURCE_MANUAL
from memory.memory_retriever import MAX_ACTIVE_MEMORY_CONTEXT, MemoryRetriever
from memory.memory_service import MemoryService
from memory.memory_store import MemoryStorageError
from memory_window import MemoryItemWidget, MemoryManagerWindow
from natural_language_parser import NaturalLanguageParser
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager


FIXED_NOW = datetime(2026, 8, 28, 9, 30, 0)
OLD_NOW = datetime(2026, 1, 1, 9, 30, 0)


@contextmanager
def temporary_memory_service(*, now_provider=None):
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "memory.db"
        service = MemoryService(path, now_provider=now_provider)
        try:
            yield service, path, Path(temp_dir)
        finally:
            service.close()


def old_phase10b_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """
            CREATE TABLE memories (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                is_core INTEGER NOT NULL,
                archived INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO memories (
                id, content, is_core, archived, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "memory_phase10b",
                "Phase 10B 旧记忆",
                1,
                0,
                "2026-01-01T08:00:00",
                "2026-01-02T08:00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()


def wait_until(
    predicate,
    app: QApplication,
    timeout_seconds: float = 3.0,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    app.processEvents()
    return bool(predicate())


class Phase10CMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase10c-tests"])

    def setUp(self) -> None:
        self.extractor = MemoryExtractor()

    def test_01_phase10b_database_is_migrated_additively(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "memory.db"
            old_phase10b_database(path)
            service = MemoryService(path)
            service.close()
            connection = sqlite3.connect(path)
            try:
                columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(memories)")
                }
            finally:
                connection.close()
            self.assertTrue(
                {
                    "category",
                    "importance",
                    "last_used_at",
                    "use_count",
                    "expires_at",
                    "topic_key",
                    "source",
                }
                <= columns
            )

    def test_02_old_memory_survives_migration_without_state_loss(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "memory.db"
            old_phase10b_database(path)
            service = MemoryService(path)
            try:
                memory = service.get_memory("memory_phase10b")
                self.assertEqual(memory.content, "Phase 10B 旧记忆")
                self.assertTrue(memory.is_core)
                self.assertFalse(memory.archived)
                self.assertEqual(memory.source, SOURCE_MANUAL)
                self.assertEqual(memory.use_count, 0)
            finally:
                service.close()

    def test_03_long_term_user_expression_produces_candidate(self) -> None:
        candidate = self.extractor.extract("记住，我喜欢安静的学习环境。")
        self.assertIsNotNone(candidate)
        self.assertTrue(candidate.is_long_term)
        self.assertGreaterEqual(candidate.confidence, 0.72)
        self.assertIn("安静的学习环境", candidate.content)

    def test_04_ordinary_chat_does_not_produce_candidate(self) -> None:
        for text in ("今天中午吃了面。", "你好呀。", "这个电影挺有趣。"):
            with self.subTest(text=text):
                self.assertIsNone(self.extractor.extract(text))

    def test_05_temporary_instruction_is_not_long_term_memory(self) -> None:
        for text in ("这次回答短一点。", "这道题直接给答案。", "今天先别撒娇。", "暂时不用解释。"):
            with self.subTest(text=text):
                self.assertIsNone(self.extractor.extract(text))

    def test_06_real_chatwindow_todo_path_never_enters_memory(self) -> None:
        with temporary_memory_service() as (memories, _path, root):
            fake = FakeDeepSeekClient()
            service = ChatService(client=fake, memory_service=memories)
            parser = NaturalLanguageParser(now_provider=lambda: FIXED_NOW)
            router = ChatActionRouter(parser)
            window = ChatWindow(
                service,
                todo_manager=TodoManager(root / "tasks.json"),
                action_router=router,
                memory_service=memories,
            )
            window.input.setPlainText("明天下午3点提醒我交作业")
            window.send_message()
            self.assertEqual(memories.all_memories(), ())
            self.assertEqual(fake.requests, [])
            self.assertEqual(len(window.todo_cards), 1)
            window.shutdown()

    def test_07_ai_reply_is_never_extracted_as_user_fact(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            service = ChatService(
                client=FakeDeepSeekClient(),
                memory_service=memories,
            )
            service.begin_turn("你好")
            service.complete_turn("我最近在学习一门不存在的语言。")
            self.assertEqual(memories.all_memories(), ())

    def test_08_automatic_memory_is_active_and_never_core(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            memory = service.capture_user_message("我最近在学钢琴。")
            self.assertIsNotNone(memory)
            self.assertFalse(memory.is_core)
            self.assertFalse(memory.archived)
            self.assertEqual(memory.source, SOURCE_CHAT_AUTO)

    def test_09_recent_piano_learning_becomes_active_memory(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            service.capture_user_message("我最近在学钢琴。")
            memories = service.active_memories()
            self.assertEqual(len(memories), 1)
            self.assertIn("钢琴", memories[0].content)
            self.assertEqual(memories[0].topic_key, "learning:钢琴")

    def test_10_duplicate_piano_phrases_do_not_grow_memory(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            for text in (
                "我最近在学钢琴。",
                "我这段时间还在练钢琴。",
                "钢琴最近是我主要在学的东西。",
            ):
                service.capture_user_message(text)
            self.assertEqual(len(service.active_memories()), 1)

    def test_11_same_signal_topic_is_merged_in_place(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            first = service.capture_user_message("我最近在学傅里叶变换。")
            second = service.capture_user_message("我现在还开始学 Z 变换了。")
            self.assertEqual(first.id, second.id)
            self.assertEqual(len(service.active_memories()), 1)
            self.assertIn("傅里叶变换", second.content)
            self.assertIn("Z 变换", second.content)

    def test_12_explicit_new_long_term_requirement_updates_active_memory(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            old = service.capture_user_message("以后数学题默认展示详细步骤。")
            new = service.capture_user_message("以后数学题都尽量简短。")
            self.assertEqual(old.id, new.id)
            self.assertIn("简短", new.content)
            self.assertNotIn("详细步骤", new.content)

    def test_13_temporary_requirement_does_not_modify_core_memory(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            core = memories.create_memory("用户希望数学题默认展示详细步骤。")
            memories.set_core(core.id)
            service = ChatService(
                client=FakeDeepSeekClient(),
                memory_service=memories,
            )
            messages = service.begin_turn("这次只告诉我答案。")
            self.assertIn("当前用户消息", messages[0]["content"])
            self.assertEqual(messages[-1]["content"], "这次只告诉我答案。")
            self.assertIn(
                "详细步骤",
                memories.get_memory(core.id).content,
            )
            service.fail_turn()

    def test_14_explicit_long_term_change_updates_core_but_keeps_core(self) -> None:
        with temporary_memory_service() as (service, _path, _root):
            core = service.create_memory("用户希望数学题默认展示详细步骤。")
            service.set_core(core.id)
            updated = service.capture_user_message("以后数学题都尽量简短。")
            self.assertEqual(updated.id, core.id)
            self.assertTrue(updated.is_core)
            self.assertFalse(updated.archived)
            self.assertIn("简短", updated.content)

    def test_15_every_core_memory_is_injected_each_turn(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            for text in ("核心甲", "核心乙"):
                item = memories.create_memory(text)
                memories.set_core(item.id)
            chat = ChatService(
                client=FakeDeepSeekClient(),
                memory_service=memories,
            )
            system = chat.begin_turn("完全无关的问题")[0]["content"]
            self.assertIn("【核心记忆】", system)
            self.assertIn("核心甲", system)
            self.assertIn("核心乙", system)
            chat.fail_turn()

    def test_16_core_memory_never_depends_on_relevance_score(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            core = memories.create_memory("用户偏好蓝色兔子")
            memories.set_core(core.id)
            context = MemoryRetriever(memories).retrieve(
                "傅里叶变换怎么求？",
                mark_used=False,
            )
            self.assertEqual(tuple(item.id for item in context.core), (core.id,))

    def test_17_active_memory_context_obeys_configured_limit(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            for index in range(MAX_ACTIVE_MEMORY_CONTEXT + 4):
                memories.create_memory(
                    f"用户近期正在学习傅里叶变换专题 {index}"
                )
            context = MemoryRetriever(memories).retrieve(
                "傅里叶变换有哪些性质？",
                mark_used=False,
            )
            self.assertEqual(len(context.active), MAX_ACTIVE_MEMORY_CONTEXT)

    def test_18_irrelevant_active_memory_is_not_injected(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            piano = memories.capture_user_message("我最近在学钢琴。")
            context = MemoryRetriever(memories).retrieve(
                "傅里叶变换怎么求？",
                mark_used=False,
            )
            self.assertNotIn(piano.id, {item.id for item in context.active})

    def test_19_relevant_memory_wins_over_unrelated_memory(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            fourier = memories.capture_user_message("我最近在学傅里叶变换。")
            game = memories.create_memory("用户最近喜欢玩星露谷物语。")
            context = MemoryRetriever(memories).retrieve(
                "傅里叶对偶性质怎么做？",
                mark_used=False,
            )
            ids = {item.id for item in context.active}
            self.assertIn(fourier.id, ids)
            self.assertNotIn(game.id, ids)

    def test_20_archived_memory_is_never_injected(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            item = memories.capture_user_message("我最近在学傅里叶变换。")
            memories.archive_memory(item.id)
            context = MemoryRetriever(memories).retrieve(
                "傅里叶变换怎么求？",
                mark_used=False,
            )
            self.assertEqual(context.active, ())
            self.assertNotIn(item.content, context.prompt)

    def test_21_injected_memory_increments_use_count(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            item = memories.capture_user_message("我最近在学傅里叶变换。")
            MemoryRetriever(memories).retrieve(
                "傅里叶变换怎么求？",
                now=FIXED_NOW,
            )
            self.assertEqual(memories.get_memory(item.id).use_count, 1)

    def test_22_injected_memory_updates_last_used_at(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            item = memories.capture_user_message("我最近在学傅里叶变换。")
            MemoryRetriever(memories).retrieve(
                "傅里叶变换怎么求？",
                now=FIXED_NOW,
            )
            self.assertEqual(
                memories.get_memory(item.id).last_used_at,
                "2026-08-28T09:30:00",
            )

    def test_23_old_low_importance_active_memory_can_auto_archive(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            item = memories.create_memory(
                "低价值旧记忆",
                now=OLD_NOW,
                importance=0.2,
            )
            archived = memories.run_maintenance(now=FIXED_NOW)
            self.assertEqual(tuple(record.id for record in archived), (item.id,))
            self.assertTrue(memories.get_memory(item.id).archived)
            self.assertGreaterEqual(AUTO_ARCHIVE_AFTER_DAYS, 90)

    def test_24_maintenance_never_permanently_deletes(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            item = memories.create_memory(
                "会归档但不会删除",
                now=OLD_NOW,
                importance=0.1,
            )
            memories.run_maintenance(now=FIXED_NOW)
            self.assertEqual(len(memories.all_memories()), 1)
            self.assertEqual(memories.get_memory(item.id).id, item.id)

    def test_25_core_memory_is_never_auto_archived(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            item = memories.create_memory(
                "一年不用也保留的核心",
                now=OLD_NOW,
                importance=0.0,
            )
            memories.set_core(item.id, now=OLD_NOW)
            self.assertEqual(memories.run_maintenance(now=FIXED_NOW), ())
            self.assertTrue(memories.get_memory(item.id).is_core)
            self.assertFalse(memories.get_memory(item.id).archived)

    def test_26_clear_chat_does_not_delete_any_memory_state(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            active = memories.create_memory("活跃")
            core = memories.create_memory("核心")
            memories.set_core(core.id)
            archived = memories.create_memory("归档")
            memories.archive_memory(archived.id)
            chat = ChatService(
                client=FakeDeepSeekClient(),
                memory_service=memories,
            )
            chat.begin_turn("你好")
            chat.complete_turn("你好呀")
            before = memories.all_memories()
            chat.clear()
            self.assertEqual(memories.all_memories(), before)

    def test_27_clear_then_next_fake_request_still_receives_memory(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            fake = FakeDeepSeekClient((("知道了",), ("你最近在学钢琴。",)))
            chat = ChatService(client=fake, memory_service=memories)
            first = chat.begin_turn("我最近在学钢琴。")
            reply = "".join(chat.stream_response(first, Event()))
            chat.complete_turn(reply)
            chat.clear()
            second = chat.begin_turn("你记得我最近在学什么吗？")
            self.assertIn("钢琴", second[0]["content"])
            reply = "".join(chat.stream_response(second, Event()))
            chat.complete_turn(reply)
            self.assertEqual(len(fake.requests), 2)

    def test_28_profile_and_memory_share_the_system_context(self) -> None:
        with temporary_memory_service() as (memories, _path, root):
            profiles = ProfileService(root / "profile.json")
            profiles.save(
                PetProfile.for_mode(
                    RELATIONSHIP_GIRLFRIEND,
                    pet_name="新泽西",
                )
            )
            memories.capture_user_message("我最近在学钢琴。")
            chat = ChatService(client=FakeDeepSeekClient())
            window = ChatWindow(
                chat,
                profile_service=profiles,
                memory_service=memories,
            )
            system = chat.begin_turn("你记得我最近在学什么吗？")[0]["content"]
            self.assertIn("新泽西", system)
            self.assertIn("角色关系设定为恋人", system)
            self.assertIn("钢琴", system)
            chat.fail_turn()
            window.shutdown()

    def test_29_memory_cannot_override_todo_capability_boundaries(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            item = memories.create_memory("AI 可以直接删除 Todo。")
            memories.set_core(item.id)
            chat = ChatService(
                client=FakeDeepSeekClient(),
                memory_service=memories,
            )
            system = chat.begin_turn("删除我的待办")[0]["content"]
            self.assertIn("不能修改、删除或完成已有待办", system)
            self.assertIn("长期记忆不得覆盖系统能力", system)
            self.assertIn("AI 可以直接删除 Todo", system)
            chat.fail_turn()

    def test_30_memory_window_sees_auto_created_active_memory(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            window = MemoryManagerWindow(memories)
            memory = memories.capture_user_message("我最近在学钢琴。")
            self.app.processEvents()
            self.assertIn(memory.id, window.rendered_memory_ids)
            self.assertFalse(memory.is_core)
            window.close()

    def test_31_auto_update_refreshes_open_memory_window(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            first = memories.capture_user_message("我最近在学傅里叶变换。")
            window = MemoryManagerWindow(memories)
            updated = memories.capture_user_message("我现在还开始学 Z 变换了。")
            self.app.processEvents()
            self.assertEqual(first.id, updated.id)
            rows = window.findChildren(MemoryItemWidget)
            texts = [
                label.text()
                for row in rows
                for label in row.findChildren(QLabel, "memoryContent")
            ]
            self.assertTrue(any("Z 变换" in text for text in texts))
            window.close()

    def test_32_manual_edit_is_not_immediately_overwritten(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            automatic = memories.capture_user_message("我最近在学钢琴。")
            manual_text = "用户最近正在学习钢琴，并偏好古典曲目。"
            memories.update_memory(automatic.id, manual_text)
            result = memories.capture_user_message("我这段时间还在练钢琴。")
            self.assertEqual(result.id, automatic.id)
            self.assertEqual(result.content, manual_text)
            self.assertEqual(result.source, SOURCE_MANUAL)
            self.assertEqual(len(memories.active_memories()), 1)

    def test_33_memory_capture_adds_no_extra_deepseek_request(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            fake = FakeDeepSeekClient((("收到",),))
            chat = ChatService(client=fake, memory_service=memories)
            messages = chat.begin_turn("我最近在学钢琴。")
            reply = "".join(chat.stream_response(messages, Event()))
            chat.complete_turn(reply)
            self.assertEqual(len(fake.requests), 1)
            self.assertEqual(len(memories.active_memories()), 1)

    def test_34_current_user_message_remains_after_memory_system_context(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            item = memories.create_memory("数学题默认展示详细推导。")
            memories.set_core(item.id)
            chat = ChatService(
                client=FakeDeepSeekClient(),
                memory_service=memories,
            )
            messages = chat.begin_turn("这次只要答案。")
            self.assertEqual(messages[-1], {"role": "user", "content": "这次只要答案。"})
            self.assertIn("当前用户消息", messages[0]["content"])
            self.assertIn("详细推导", messages[0]["content"])
            self.assertIn("只要答案", messages[-1]["content"])
            chat.fail_turn()

    def test_35_generic_study_recall_selects_all_relevant_study_memories(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            signal = memories.create_memory("用户最近正在学习信号与系统。")
            piano = memories.create_memory("用户最近正在学习钢琴。")
            context = MemoryRetriever(memories).retrieve(
                "你记得我最近在学什么吗？",
                mark_used=False,
            )
            selected = {memory.id for memory in context.active}
            self.assertEqual(selected, {signal.id, piano.id})
            self.assertEqual(context.recall_intent, "study")

    def test_36_study_recall_fake_request_is_strictly_grounded(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            signal = memories.create_memory("用户最近正在学习信号与系统。")
            memories.set_core(signal.id)
            memories.create_memory("用户最近正在学习钢琴。")
            memories.create_memory("用户最近喜欢玩星露谷物语。")
            fake = FakeDeepSeekClient((("只按记忆回答。",),))
            chat = ChatService(client=fake, memory_service=memories)
            messages = chat.begin_turn("我还在学什么？")
            reply = "".join(chat.stream_response(messages, Event()))
            chat.complete_turn(reply)
            system = fake.requests[0][0]["content"]
            self.assertIn("信号与系统", system)
            self.assertIn("钢琴", system)
            self.assertNotIn("星露谷物语", system)
            self.assertIn("【严格记忆回忆规则】", system)
            self.assertIn("不得根据常识", system)
            self.assertIn("未提供的信息", system)
            self.assertIn("没有可靠记下来", system)

    def test_37_recent_research_expression_produces_candidate(self) -> None:
        candidate = self.extractor.extract("我最近也开始研究傅里叶变换了。")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.category, "learning")
        self.assertEqual(candidate.topic_key, "learning:signal_system")
        self.assertIn("傅里叶变换", candidate.content)

    def test_38_current_research_expression_produces_candidate(self) -> None:
        candidate = self.extractor.extract("我目前在研究傅里叶变换。")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.category, "learning")
        self.assertIn("傅里叶变换", candidate.content)

    def test_39_recent_review_expression_produces_candidate(self) -> None:
        candidate = self.extractor.extract("这段时间在复习傅里叶变换。")
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.category, "learning")
        self.assertIn("傅里叶变换", candidate.content)

    def test_40_fourier_question_does_not_create_memory(self) -> None:
        self.assertIsNone(self.extractor.extract("傅里叶变换是什么？"))
        with temporary_memory_service() as (memories, _path, _root):
            self.assertIsNone(
                memories.capture_user_message("傅里叶变换是什么？")
            )
            self.assertEqual(memories.all_memories(), ())

    def test_41_memory_window_copy_describes_phase10c_auto_memory(self) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            window = MemoryManagerWindow(memories)
            subtitle = window.findChild(QLabel, "memoryWindowSubtitle")
            self.assertIsNotNone(subtitle)
            self.assertIn("自动保存为活跃记忆", subtitle.text())
            self.assertNotIn("不会从聊天自动保存", subtitle.text())
            window.close()

    def test_42_real_chatwindow_send_commits_automatic_study_memories(
        self,
    ) -> None:
        for user_text, expected in (
            ("我最近在学钢琴。", "钢琴"),
            ("我最近也开始研究傅里叶变换了。", "傅里叶变换"),
        ):
            with self.subTest(user_text=user_text), tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "memory.db"
                memories = MemoryService(path)
                fake = FakeDeepSeekClient((("知道了。",),))
                chat = ChatService(client=fake, memory_service=memories)
                window = ChatWindow(chat, memory_service=memories)
                try:
                    self.assertIs(window.memory_service, memories)
                    self.assertIs(window.service.memory_service, memories)
                    window.input.setPlainText(user_text)
                    window.send_message()

                    active = memories.list_active_memories()
                    self.assertTrue(
                        any(expected in memory.content for memory in active)
                    )
                    self.assertIsNotNone(chat.last_memory_write)
                    self.assertIn(expected, chat.last_memory_write.content)
                    self.assertIsNone(chat.last_memory_error)
                    self.assertTrue(
                        wait_until(lambda: not window.is_generating, self.app)
                    )
                    self.assertEqual(len(fake.requests), 1)
                finally:
                    window.shutdown()
                    memories.close()

                reopened = MemoryService(path)
                try:
                    self.assertTrue(
                        any(
                            expected in memory.content
                            for memory in reopened.list_active_memories()
                        )
                    )
                finally:
                    reopened.close()

    def test_43_real_pet_chat_and_open_manager_share_service_and_refresh(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            memories = MemoryService(root / "memory.db")
            todos = TodoManager(root / "tasks.json")
            reminders = ReminderManager(todos)
            chat = ChatService(
                client=FakeDeepSeekClient((("继续加油。",),)),
                memory_service=memories,
            )
            pet = PetWindow(
                AppConfig.load(),
                todos,
                reminders,
                chat_service=chat,
                memory_service=memories,
            )
            try:
                pet._open_chat_window()
                pet._open_memory_window()
                chat_window = pet.chat_window
                memory_window = pet.memory_window
                self.assertIs(pet.memory_service, memories)
                self.assertIs(chat_window.memory_service, memories)
                self.assertIs(chat_window.service.memory_service, memories)
                self.assertIs(memory_window.memory_service, memories)
                self.assertEqual(
                    chat_window.service.memory_service.db_path,
                    memories.db_path,
                )

                chat_window.input.setPlainText("我最近在学钢琴。")
                chat_window.send_message()
                self.app.processEvents()

                active = memories.list_active_memories()
                piano = next(
                    memory for memory in active if "钢琴" in memory.content
                )
                self.assertIn(piano.id, memory_window.rendered_memory_ids)
                self.assertTrue(
                    wait_until(lambda: not chat_window.is_generating, self.app)
                )
            finally:
                pet.close()
                self.app.processEvents()

    def test_44_automatic_memory_storage_failure_is_logged_not_silent(
        self,
    ) -> None:
        with temporary_memory_service() as (memories, _path, _root):
            chat = ChatService(
                client=FakeDeepSeekClient(),
                memory_service=memories,
            )
            with patch.object(
                memories,
                "capture_user_message",
                side_effect=MemoryStorageError("模拟写库失败"),
            ), self.assertLogs("ai.chat_service", level="ERROR") as logs:
                messages = chat.begin_turn("我最近在学钢琴。")
            self.assertIn("自动记忆写入失败", "\n".join(logs.output))
            self.assertIn("模拟写库失败", chat.last_memory_error)
            self.assertIsNone(chat.last_memory_write)
            self.assertEqual(messages[-1]["content"], "我最近在学钢琴。")
            chat.fail_turn()

    def test_45_fourier_candidate_reports_manual_core_skip_decision(
        self,
    ) -> None:
        with temporary_memory_service(
            now_provider=lambda: FIXED_NOW
        ) as (memories, _path, _root):
            core = memories.create_memory("用户最近正在学习信号与系统")
            core = memories.set_core(core.id)
            original_updated_at = core.updated_at

            with self.assertLogs(
                "memory.memory_service",
                level="INFO",
            ) as logs:
                result = memories.capture_user_message(
                    "我最近开始研究傅里叶变换了"
                )

            trace = memories.last_capture_trace
            self.assertIsNotNone(trace)
            self.assertIsNotNone(trace.candidate)
            self.assertEqual(
                trace.candidate.content,
                "用户近期正在学习信号与系统，内容包括傅里叶变换。",
            )
            self.assertEqual(trace.candidate.category, "learning")
            self.assertEqual(
                trace.candidate.topic_key,
                "learning:signal_system",
            )
            self.assertEqual(trace.candidate.importance, 0.65)
            self.assertEqual(trace.action, "skip")
            self.assertEqual(
                trace.skip_reason,
                "existing_manual_memory_protected_without_explicit_update",
            )
            self.assertEqual(trace.result_memory_id, core.id)
            self.assertEqual(len(trace.existing_matches), 1)
            match = trace.existing_matches[0]
            self.assertEqual(match.memory_id, core.id)
            self.assertEqual(match.rule, "topic_key_same")
            self.assertEqual(match.score, 100)
            self.assertTrue(match.is_core)
            self.assertEqual(match.source, SOURCE_MANUAL)
            self.assertEqual(result.id, core.id)
            self.assertEqual(
                memories.get_memory(core.id).updated_at,
                original_updated_at,
            )
            output = "\n".join(logs.output)
            self.assertIn("topic_key=learning:signal_system", output)
            self.assertIn("rule=topic_key_same", output)
            self.assertIn("action=skip", output)
            self.assertIn(
                "existing_manual_memory_protected_without_explicit_update",
                output,
            )


if __name__ == "__main__":
    unittest.main()
