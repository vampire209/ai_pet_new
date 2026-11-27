"""Phase 9 local chat Todo actions and desktop-pet chat UI tests."""

from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel

from ai.ai_config import DeepSeekConfig
from ai.chat_action_router import ChatActionKind, ChatActionRouter, TaskDraft
from ai.chat_service import ChatService
from ai.chat_ui_config import (
    SUPPORTED_BACKGROUND_SUFFIXES,
    ChatUiSettings,
    default_chat_ui_config_path,
)
from ai.deepseek_client import DeepSeekClient
from ai.fake_client import FakeDeepSeekClient
from chat_window import ChatWindow, OriginalPixmapLabel, TodoConfirmationCard
from config import PROJECT_ROOT, AppConfig
from models.recurrence import REPEAT_DAILY, REPEAT_WEEKLY
from natural_language_parser import NaturalLanguageParser
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager, TodoStorageError
from ui_resources import (
    UI_ASSETS,
    application_icon_source_path,
    load_application_icon,
)


BASE = datetime(2026, 8, 27, 10, 0, 0)


def wait_until(predicate, app: QApplication, timeout_seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    app.processEvents()
    return bool(predicate())


def fixed_router() -> ChatActionRouter:
    return ChatActionRouter(NaturalLanguageParser(now_provider=lambda: BASE))


def make_window(
    manager: TodoManager | None = None,
    fake: FakeDeepSeekClient | None = None,
    ui_settings: ChatUiSettings | None = None,
) -> tuple[ChatWindow, FakeDeepSeekClient]:
    client = fake or FakeDeepSeekClient()
    window = ChatWindow(
        ChatService(client=client),
        todo_manager=manager,
        action_router=fixed_router(),
        ui_settings=ui_settings,
    )
    window.show()
    return window, client


def send(window: ChatWindow, text: str) -> None:
    window.input.setPlainText(text)
    window.send_message()


def write_test_image(path: Path, color: str) -> None:
    image = QImage(640, 360, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    if not image.save(str(path)):
        raise AssertionError(f"无法生成测试图片：{path}")


class Phase9Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase9-tests"])
        cls.config = AppConfig.load()

    def test_01_ordinary_chat_and_false_positive_phrases_stay_chat(self) -> None:
        router = fixed_router()
        for text in (
            "你好",
            "提醒是什么功能？",
            "你觉得我明天应该复习什么？",
            "明天适合学习什么？",
        ):
            with self.subTest(text=text):
                self.assertEqual(router.route(text).kind, ChatActionKind.CHAT)

    def test_02_explicit_creation_phrases_use_phase7_parser(self) -> None:
        router = fixed_router()
        samples = (
            "明天下午3点提醒我交作业",
            "明天下午3点帮我提醒交作业",
            "帮我设置提醒明天下午3点交作业",
            "给我设置一个提醒明天下午3点交作业",
            "设置一个提醒明天下午3点交作业",
            "帮我记一下明天下午3点交作业",
            "帮我建个待办明天下午3点交作业",
            "创建一个待办明天下午3点交作业",
            "加一个待办明天下午3点交作业",
            "别忘了提醒我明天下午3点交作业",
        )
        for text in samples:
            with self.subTest(text=text):
                result = router.route(text)
                self.assertEqual(result.kind, ChatActionKind.TODO_DRAFT)
                self.assertEqual(result.draft.title, "交作业")
                self.assertEqual(result.draft.time_text, "2026-08-28 15:00")

    def test_03_draft_fields_and_advance_are_structured_without_persistence(self) -> None:
        router = fixed_router()
        result = router.route("明天下午3点提醒我交作业，提前30分钟提醒")
        self.assertEqual(result.kind, ChatActionKind.TODO_DRAFT)
        self.assertEqual(result.draft.title, "交作业")
        self.assertEqual(result.draft.repeat_text, "不重复")
        self.assertEqual(result.draft.advance_minutes, (30,))
        self.assertEqual(result.draft.advance_text, "30分钟")

    def test_04_incomplete_and_past_commands_never_produce_drafts(self) -> None:
        router = fixed_router()
        incomplete = router.route("明天提醒我学习")
        self.assertEqual(incomplete.kind, ChatActionKind.TODO_INCOMPLETE)
        self.assertIsNone(incomplete.draft)
        self.assertIn("几点", incomplete.message)
        past = router.route("今天上午9点提醒我学习")
        self.assertEqual(past.kind, ChatActionKind.TODO_INCOMPLETE)
        self.assertIn("已经过去", past.message)

    def test_05_delete_modify_and_complete_are_explicitly_blocked(self) -> None:
        router = fixed_router()
        for text in (
            "把我明天的作业任务删掉",
            "删除明天的待办",
            "把提醒改到下午四点",
            "修改我的待办",
            "把作业任务标记完成",
        ):
            with self.subTest(text=text):
                result = router.route(text)
                self.assertEqual(
                    result.kind,
                    ChatActionKind.UNSUPPORTED_TODO_OPERATION,
                )
                self.assertIn("不能删除、修改或完成", result.message)

    def test_06_no_api_key_still_builds_local_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            os.environ,
            {},
            clear=True,
        ):
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            service = ChatService(client=DeepSeekClient(DeepSeekConfig()))
            window = ChatWindow(
                service,
                todo_manager=manager,
                action_router=fixed_router(),
            )
            send(window, "明天下午3点提醒我交作业")
            self.assertEqual(len(window.todo_cards), 1)
            self.assertEqual(manager.get_tasks(), ())
            self.assertIn("待办草案", window.status_label.text())
            window.shutdown()

    def test_07_network_failure_client_is_not_called_for_todo_draft(self) -> None:
        manager_path = None
        with tempfile.TemporaryDirectory() as temp_dir:
            manager_path = Path(temp_dir) / "tasks.json"
            manager = TodoManager(manager_path)
            fake = FakeDeepSeekClient((RuntimeError("network down"),))
            window, fake = make_window(manager, fake)
            send(window, "明天下午3点提醒我交作业")
            self.assertEqual(fake.requests, [])
            self.assertEqual(len(window.todo_cards), 1)
            window.shutdown()

    def test_08_draft_stage_does_not_write_json_or_generate_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.json"
            manager = TodoManager(path)
            before = path.read_text(encoding="utf-8")
            window, _fake = make_window(manager)
            send(window, "明天下午3点提醒我交作业，提前30分钟提醒")
            self.assertEqual(manager.get_tasks(), ())
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertEqual(json.loads(before), [])
            window.shutdown()

    def test_09_confirm_creates_task_reminders_json_and_read_only_card(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.json"
            manager = TodoManager(path)
            window, fake = make_window(manager)
            send(window, "明天下午3点提醒我交作业，提前30分钟提醒")
            card = window.todo_cards[0]
            card.confirm_button.click()
            self.app.processEvents()
            self.assertEqual(fake.requests, [])
            self.assertEqual(card.state, "created")
            self.assertEqual(card.heading_label.text(), "✓ 已创建")
            self.assertFalse(card.confirm_button.isEnabled())
            task = manager.get_tasks()[0]
            self.assertEqual(task.text, "交作业")
            self.assertEqual(task.reminder_offsets, (30,))
            self.assertEqual(
                {reminder.type for reminder in task.reminders},
                {"advance", "final"},
            )
            saved = json.loads(path.read_text(encoding="utf-8"))[0]
            self.assertEqual(saved["text"], "交作业")
            window.shutdown()

    def test_10_cancel_never_creates_task_or_changes_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.json"
            manager = TodoManager(path)
            before = path.read_text(encoding="utf-8")
            window, _fake = make_window(manager)
            send(window, "明天下午3点提醒我交作业")
            card = window.todo_cards[0]
            card.cancel_button.click()
            self.assertEqual(card.state, "cancelled")
            self.assertEqual(manager.get_tasks(), ())
            self.assertEqual(path.read_text(encoding="utf-8"), before)
            self.assertIn("先不创建", window.message_texts[-1][1])
            window.shutdown()

    def test_11_daily_confirmation_reuses_phase6_recurrence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window, _fake = make_window(manager)
            send(window, "每天晚上8点提醒我背单词")
            card = window.todo_cards[0]
            self.assertEqual(card.draft.repeat_type, REPEAT_DAILY)
            self.assertEqual(card.draft.repeat_text, "每天")
            card.confirm_button.click()
            task = manager.get_tasks()[0]
            self.assertEqual(task.repeat_type, REPEAT_DAILY)
            self.assertEqual(task.event_at, "2026-08-27T20:00:00")
            self.assertEqual({item.type for item in task.reminders}, {"final"})
            window.shutdown()

    def test_12_weekly_multiple_weekdays_reuse_phase6_recurrence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window, _fake = make_window(manager)
            send(window, "每周一三五下午6点提醒我健身")
            card = window.todo_cards[0]
            self.assertEqual(card.draft.repeat_type, REPEAT_WEEKLY)
            self.assertEqual(card.draft.repeat_weekdays, (1, 3, 5))
            self.assertIn("周一", card.draft.repeat_text)
            card.confirm_button.click()
            task = manager.get_tasks()[0]
            self.assertEqual(task.repeat_type, REPEAT_WEEKLY)
            self.assertEqual(task.repeat_weekdays, (1, 3, 5))
            self.assertEqual(task.event_at, "2026-08-28T18:00:00")
            window.shutdown()

    def test_13_creation_failure_never_claims_success(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window, _fake = make_window(manager)
            send(window, "明天下午3点提醒我交作业")
            card = window.todo_cards[0]
            with patch.object(
                manager,
                "add_task",
                side_effect=TodoStorageError("模拟保存失败"),
            ):
                card.confirm_button.click()
            self.assertEqual(card.state, "failed")
            self.assertNotIn("已创建", card.heading_label.text())
            self.assertNotIn("设置好了", window.message_texts[-1][1])
            self.assertEqual(manager.get_tasks(), ())
            self.assertEqual(window.service.history, ())
            window.shutdown()

    def test_14_clear_chat_keeps_already_created_todo_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.json"
            manager = TodoManager(path)
            window, _fake = make_window(manager)
            send(window, "明天下午3点提醒我交作业")
            window.todo_cards[0].confirm_button.click()
            task_id = manager.get_tasks()[0].id
            self.assertNotEqual(window.service.history, ())
            window.clear_chat()
            self.app.processEvents()
            self.assertEqual(window.message_texts, ())
            self.assertEqual(window.todo_cards, ())
            self.assertEqual(window.service.history, ())
            self.assertIsNotNone(manager.get_task(task_id))
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))), 1)
            window.shutdown()

    def test_15_clear_pending_card_invalidates_it_without_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window, _fake = make_window(manager)
            send(window, "明天下午3点提醒我交作业")
            card = window.todo_cards[0]
            window.clear_chat()
            self.assertEqual(card.state, "cancelled")
            card.confirm_button.click()
            self.assertEqual(manager.get_tasks(), ())
            window.shutdown()

    def test_16_normal_chat_uses_fake_deepseek_and_creates_two_bubbles(self) -> None:
        fake = FakeDeepSeekClient((("你", "好", "呀"),), chunk_delay_seconds=0.005)
        window, fake = make_window(fake=fake)
        send(window, "你好")
        self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(
            window.message_texts,
            (("user", "你好"), ("assistant", "你好呀")),
        )
        self.assertEqual(
            len(window.findChildren(QFrame, "userMessage")),
            1,
        )
        self.assertEqual(
            len(window.findChildren(QFrame, "assistantMessage")),
            1,
        )
        window.shutdown()

    def test_17_successful_local_result_enters_context_before_next_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            fake = FakeDeepSeekClient((("记", "得"),), chunk_delay_seconds=0.005)
            window, fake = make_window(manager, fake)
            command = "明天下午3点提醒我交作业"
            send(window, command)
            window.todo_cards[0].confirm_button.click()
            self.assertEqual(fake.requests, [])
            self.assertEqual(window.service.history[0]["content"], command)
            self.assertIn("已创建待办：交作业", window.service.history[1]["content"])
            send(window, "我刚才让你提醒什么？")
            self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
            request = fake.requests[0]
            contents = [message["content"] for message in request]
            self.assertIn(command, contents)
            self.assertTrue(any("已创建待办：交作业" in item for item in contents))
            window.shutdown()

    def test_18_incomplete_command_shows_prompt_and_no_card_or_api_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window, fake = make_window(manager)
            send(window, "明天提醒我学习")
            self.assertEqual(window.todo_cards, ())
            self.assertEqual(manager.get_tasks(), ())
            self.assertEqual(fake.requests, [])
            self.assertIn("几点", window.message_texts[-1][1])
            window.shutdown()

    def test_19_prohibited_operations_never_call_todo_mutations_or_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window, fake = make_window(manager)
            with (
                patch.object(manager, "delete_task") as delete,
                patch.object(manager, "update_task") as update,
                patch.object(manager, "complete_task") as complete,
            ):
                send(window, "把我明天的作业任务删掉")
                send(window, "把提醒改到下午四点")
                send(window, "把作业任务标记完成")
            delete.assert_not_called()
            update.assert_not_called()
            complete.assert_not_called()
            self.assertEqual(fake.requests, [])
            self.assertEqual(manager.get_tasks(), ())
            window.shutdown()

    def test_20_ui_theme_avatar_header_and_confirmation_card_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window, _fake = make_window(manager)
            self.assertEqual(window.objectName(), "chatWindow")
            self.assertIsNotNone(window.findChild(QFrame, "chatHeader"))
            avatar = window.findChild(QLabel, "chatAvatar")
            self.assertIsNotNone(avatar)
            self.assertIsNotNone(avatar.pixmap())
            self.assertFalse(avatar.pixmap().isNull())
            self.assertIn("DeepSeek", window.model_label.text())
            send(window, "明天下午3点提醒我交作业")
            card = window.findChild(TodoConfirmationCard, "todoConfirmationCard")
            self.assertIsNotNone(card)
            self.assertEqual(card.title_label.text(), "交作业")
            self.assertLessEqual(card.maximumWidth(), 520)
            window.shutdown()

    def test_21_stream_updates_one_widget_and_stop_clear_still_work(self) -> None:
        fake = FakeDeepSeekClient(
            (("流" for _ in range(100)),),
            chunk_delay_seconds=0.01,
        )
        window, _fake = make_window(fake=fake)
        send(window, "慢慢回答")
        self.assertEqual(window.message_texts[-1][1], "桌宠正在想……")
        self.assertTrue(
            wait_until(
                lambda: window.message_texts[-1][1] != "桌宠正在想……",
                self.app,
            )
        )
        assistant_count = len(window.findChildren(QFrame, "assistantMessage"))
        partial = window.message_texts[-1][1]
        window.stop_generation()
        self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
        self.assertEqual(window.message_texts[-1][1], partial)
        self.assertEqual(
            len(window.findChildren(QFrame, "assistantMessage")),
            assistant_count,
        )
        window.clear_chat()
        self.assertEqual(window.message_texts, ())
        window.shutdown()

    def test_22_enter_shift_enter_and_multiple_open_close_remain_stable(self) -> None:
        fake = FakeDeepSeekClient((("好",),))
        window, fake = make_window(fake=fake)
        window.input.setPlainText("第一行")
        window.input.moveCursor(QTextCursor.MoveOperation.End)
        QTest.keyClick(
            window.input,
            Qt.Key.Key_Return,
            Qt.KeyboardModifier.ShiftModifier,
        )
        window.input.insertPlainText("第二行")
        self.assertEqual(window.input.toPlainText(), "第一行\n第二行")
        window.input.setPlainText("你好")
        QTest.keyClick(window.input, Qt.Key.Key_Return)
        self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
        self.assertEqual(len(fake.requests), 1)
        window.close()
        self.app.processEvents()
        self.assertFalse(window.isVisible())
        window.show()
        self.assertTrue(window.isVisible())
        window.close()

    def test_23_pet_window_injects_existing_manager_into_single_chat_window(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            scanner = ReminderManager(manager, now_provider=lambda: BASE)
            pet = PetWindow(
                self.config,
                manager,
                scanner,
                chat_service=ChatService(client=FakeDeepSeekClient()),
            )
            pet.show()
            pet._open_chat_window()
            first = pet.chat_window
            self.assertIs(first.todo_manager, manager)
            send(first, "明天下午3点提醒我交作业")
            first.todo_cards[0].confirm_button.click()
            self.assertEqual(manager.get_tasks()[0].text, "交作业")
            pet._open_chat_window()
            self.assertIs(pet.chat_window, first)
            pet.close()
            self.app.processEvents()

    def test_24_json_reload_preserves_chat_created_task_and_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.json"
            manager = TodoManager(path)
            window, _fake = make_window(manager)
            send(window, "明天下午3点提醒我交作业，提前30分钟提醒")
            window.todo_cards[0].confirm_button.click()
            created = manager.get_tasks()[0]
            # This tests an immediate restart, not an expired August reminder.
            with patch("models.task.datetime", wraps=datetime) as clock:
                clock.now.return_value = BASE
                reloaded = TodoManager(path).get_task(created.id)
            self.assertIsNotNone(reloaded)
            self.assertEqual(reloaded.text, "交作业")
            self.assertEqual(reloaded.reminder_offsets, (30,))
            self.assertEqual(
                [item.to_dict() for item in reloaded.reminders],
                [item.to_dict() for item in created.reminders],
            )
            window.shutdown()

    def test_25_real_world_ambiguous_hour_and_dotted_date_build_clean_cards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            router = ChatActionRouter(
                NaturalLanguageParser(
                    now_provider=lambda: datetime(2026, 8, 27, 16, 5)
                )
            )
            window = ChatWindow(
                ChatService(client=FakeDeepSeekClient()),
                todo_manager=manager,
                action_router=router,
            )
            send(window, "我4点半下课，记得提醒我")
            first = window.todo_cards[-1]
            self.assertEqual(first.draft.title, "下课")
            self.assertEqual(first.draft.due_at, datetime(2026, 8, 27, 16, 30))
            first.cancel_button.click()

            send(window, "我8.27下午五点下课，记得提醒我")
            second = window.todo_cards[-1]
            self.assertEqual(second.draft.title, "下课")
            self.assertEqual(second.draft.due_at, datetime(2026, 8, 27, 17, 0))
            self.assertEqual(manager.get_tasks(), ())
            window.shutdown()

    def test_26_polished_chat_layers_empty_state_and_corner_pet_remain_stable(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window, _fake = make_window(
                manager,
                ui_settings=ChatUiSettings(Path(temp_dir) / "chat_ui.json"),
            )
            self.app.processEvents()

            self.assertIsNotNone(window.findChild(QFrame, "chatHeader"))
            self.assertIsNotNone(window.findChild(QFrame, "headerPetAvatar"))
            self.assertIsNotNone(window.findChild(QFrame, "chatStage"))
            self.assertIsNotNone(window.findChild(QFrame, "chatInputPanel"))
            self.assertTrue(window.empty_state.isVisible())
            self.assertTrue(window.corner_pet_layer.isVisible())
            corner_pixmap = window.corner_pet_label.pixmap()
            self.assertIsNotNone(corner_pixmap)
            self.assertFalse(corner_pixmap.isNull())
            self.assertGreaterEqual(corner_pixmap.height(), 240)

            send(window, "明天下午3点提醒我交作业")
            self.app.processEvents()
            self.assertFalse(window.empty_state.isVisible())
            self.assertIsNotNone(window.findChild(QFrame, "userMessage"))
            self.assertIsNotNone(window.findChild(QFrame, "assistantMessage"))
            self.assertIsNotNone(window.findChild(QFrame, "messagePetAvatar"))
            card = window.todo_cards[-1]
            self.assertEqual(card.property("cardState"), "pending")
            card.confirm_button.click()
            self.assertEqual(card.property("cardState"), "created")

            window.clear_chat()
            self.app.processEvents()
            self.assertTrue(window.empty_state.isVisible())
            self.assertTrue(window.corner_pet_layer.isVisible())
            window.resize(640, 680)
            self.app.processEvents()
            self.assertEqual(window.scroll.horizontalScrollBar().maximum(), 0)
            window.shutdown()

    def test_27_unified_icon_and_original_pixmap_sources_are_high_quality(
        self,
    ) -> None:
        icon_path = application_icon_source_path()
        self.assertTrue(icon_path.is_file())
        self.assertFalse(load_application_icon().isNull())
        self.assertEqual(
            UI_ASSETS.avatar_dir,
            PROJECT_ROOT / "assets" / "ui" / "avatar",
        )
        self.assertEqual(
            UI_ASSETS.character_dir,
            PROJECT_ROOT / "assets" / "ui" / "character",
        )

        window, _fake = make_window()
        self.app.processEvents()
        self.assertFalse(window.windowIcon().isNull())
        self.assertTrue(window.avatar_source_path.is_file())
        self.assertTrue(window.character_source_path.is_file())

        header_image = window.findChild(OriginalPixmapLabel, "chatAvatar")
        self.assertIsNotNone(header_image)
        self.assertGreater(
            header_image.source_pixel_size[0],
            header_image.pixmap().width(),
        )
        corner_image = window.findChild(OriginalPixmapLabel, "chatCornerPet")
        self.assertIsNotNone(corner_image)
        self.assertGreater(
            corner_image.source_pixel_size[0],
            corner_image.pixmap().width(),
        )

        send(window, "明天下午3点提醒我交作业")
        message_image = window.findChild(
            OriginalPixmapLabel,
            "chatMessageAvatar",
        )
        self.assertIsNotNone(message_image)
        self.assertGreater(
            message_image.source_pixel_size[0],
            message_image.pixmap().width(),
        )
        window.shutdown()

    def test_28_png_and_jpg_background_apply_immediately_and_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            png_path = root / "sky.png"
            jpg_path = root / "room.jpg"
            write_test_image(png_path, "#8abbea")
            write_test_image(jpg_path, "#c6a9dc")
            settings = ChatUiSettings(root / "chat_ui.json")

            window = ChatWindow(
                ChatService(client=FakeDeepSeekClient()),
                action_router=fixed_router(),
                ui_settings=settings,
            )
            window.show()
            with patch(
                "chat_window.QFileDialog.getOpenFileName",
                return_value=(str(png_path), "PNG"),
            ):
                window.change_background_action.trigger()
            self.app.processEvents()
            self.assertTrue(window.chat_stage.has_custom_background)
            self.assertEqual(window.chat_stage.background_path, png_path)
            self.assertTrue(window.corner_pet_layer.isHidden())
            self.assertEqual(settings.load_background_path(), png_path.resolve())
            window.shutdown()

            reopened = ChatWindow(
                ChatService(client=FakeDeepSeekClient()),
                action_router=fixed_router(),
                ui_settings=ChatUiSettings(root / "chat_ui.json"),
            )
            reopened.show()
            self.app.processEvents()
            self.assertTrue(reopened.chat_stage.has_custom_background)
            self.assertEqual(
                reopened.chat_stage.background_path,
                png_path.resolve(),
            )
            self.assertTrue(reopened.corner_pet_layer.isHidden())
            with patch(
                "chat_window.QFileDialog.getOpenFileName",
                return_value=(str(jpg_path), "JPG"),
            ):
                reopened.change_background_action.trigger()
            self.assertEqual(reopened.chat_stage.background_path, jpg_path)
            self.assertEqual(settings.load_background_path(), jpg_path.resolve())
            reopened.shutdown()

    def test_29_restore_default_and_invalid_background_fall_back_safely(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            png_path = root / "background.png"
            write_test_image(png_path, "#aaccee")
            settings = ChatUiSettings(root / "chat_ui.json")
            settings.save_background_path(png_path)

            window = ChatWindow(
                ChatService(client=FakeDeepSeekClient()),
                action_router=fixed_router(),
                ui_settings=settings,
            )
            self.assertTrue(window.chat_stage.has_custom_background)
            window.restore_background_action.trigger()
            self.assertFalse(window.chat_stage.has_custom_background)
            self.assertFalse(window.corner_pet_layer.isHidden())
            self.assertIsNone(settings.load_background_path())
            self.assertIsNone(
                json.loads(settings.config_path.read_text(encoding="utf-8"))[
                    "background_path"
                ]
            )
            window.shutdown()

            settings.save_background_path(png_path)
            png_path.unlink()
            fallback = ChatWindow(
                ChatService(client=FakeDeepSeekClient()),
                action_router=fixed_router(),
                ui_settings=settings,
            )
            self.assertFalse(fallback.chat_stage.has_custom_background)
            self.assertIsNone(fallback.chat_stage.background_path)
            self.assertFalse(fallback.corner_pet_layer.isHidden())
            fallback.shutdown()

            settings.config_path.write_text("{bad json", encoding="utf-8")
            damaged = ChatWindow(
                ChatService(client=FakeDeepSeekClient()),
                action_router=fixed_router(),
                ui_settings=settings,
            )
            self.assertFalse(damaged.chat_stage.has_custom_background)
            damaged.shutdown()

        self.assertIn(".png", SUPPORTED_BACKGROUND_SUFFIXES)
        self.assertIn(".jpg", SUPPORTED_BACKGROUND_SUFFIXES)
        self.assertIn(".jpeg", SUPPORTED_BACKGROUND_SUFFIXES)
        self.assertIn(".webp", SUPPORTED_BACKGROUND_SUFFIXES)
        self.assertNotEqual(
            default_chat_ui_config_path().parent,
            PROJECT_ROOT,
        )

    def test_30_input_dock_geometry_and_controls_remain_unchanged(self) -> None:
        window, _fake = make_window()
        panel = window.findChild(QFrame, "chatInputPanel")
        self.assertIsNotNone(panel)
        self.assertEqual(window.input.minimumHeight(), 74)
        self.assertEqual(window.input.maximumHeight(), 102)
        self.assertEqual(window.send_button.text(), "发送")
        self.assertEqual(
            window.input.placeholderText(),
            "和桌宠说点什么吧……",
        )
        self.assertIn("Enter 发送", window.findChild(QLabel, "chatInputHint").text())
        self.assertEqual(
            [action.text() for action in window.header_menu.actions()],
            ["AI 接口设置", "", "清空聊天", "", "更换聊天背景", "恢复默认背景"],
        )
        window.shutdown()

    def test_31_colloquial_timed_reminders_route_locally_without_false_positives(
        self,
    ) -> None:
        router = fixed_router()
        samples = (
            ("8点提醒打游戏", "打游戏", 20),
            ("8点提醒我打游戏", "打游戏", 20),
            ("8点提醒一下打游戏", "打游戏", 20),
            ("晚上8点提醒打游戏", "打游戏", 20),
            ("晚上8点提醒我打游戏", "打游戏", 20),
            ("8点提醒继续写作业", "继续写作业", 20),
            ("7点提醒下课", "下课", 19),
            ("9点提醒睡觉", "睡觉", 21),
            ("7点提醒继续写作业", "继续写作业", 19),
            ("晚上7点提醒写作业", "写作业", 19),
            ("7点叫我继续写作业", "继续写作业", 19),
            ("记得7点提醒写作业", "写作业", 19),
            ("给我设个7点提醒，继续写作业", "继续写作业", 19),
            ("帮我设置一个备忘录，晚上7点继续写作业", "继续写作业", 19),
            ("帮我建个待办，晚上7点继续写作业", "继续写作业", 19),
        )
        for text, expected_title, expected_hour in samples:
            with self.subTest(text=text):
                result = router.route(text)
                self.assertEqual(result.kind, ChatActionKind.TODO_DRAFT)
                self.assertEqual(result.draft.title, expected_title)
                self.assertEqual(
                    result.draft.due_at,
                    datetime(2026, 8, 27, expected_hour, 0),
                )

        without_pronoun = router.route("8点提醒打游戏")
        with_pronoun = router.route("8点提醒我打游戏")
        self.assertEqual(without_pronoun.kind, with_pronoun.kind)
        self.assertEqual(
            without_pronoun.draft.parser_text,
            with_pronoun.draft.parser_text,
        )

        for text in (
            "提醒功能有什么用？",
            "提醒是什么意思？",
            "你知道备忘录是什么吗？",
            "Windows备忘录好用吗？",
            "你觉得我几点写作业比较好？",
            "你觉得8点打游戏好吗？",
            "我8点打游戏怎么样？",
            "为什么需要提醒？",
        ):
            with self.subTest(text=text):
                self.assertEqual(router.route(text).kind, ChatActionKind.CHAT)

    def test_32_colloquial_todo_without_key_still_requires_confirmation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = TodoManager(root / "tasks.json")
            fake = FakeDeepSeekClient()
            fake.is_configured = False
            window, fake = make_window(
                manager,
                fake,
                ui_settings=ChatUiSettings(root / "chat_ui.json"),
            )
            before = (root / "tasks.json").read_text(encoding="utf-8")
            send(window, "7点提醒继续写作业")

            self.assertEqual(fake.requests, [])
            self.assertEqual(manager.get_tasks(), ())
            self.assertEqual(
                (root / "tasks.json").read_text(encoding="utf-8"),
                before,
            )
            self.assertEqual(len(window.todo_cards), 1)
            card = window.todo_cards[0]
            self.assertEqual(card.draft.title, "继续写作业")

            card.confirm_button.click()
            self.assertEqual(len(manager.get_tasks()), 1)
            self.assertEqual(manager.get_tasks()[0].text, "继续写作业")
            self.assertEqual(fake.requests, [])
            window.shutdown()

    def test_33_real_chatwindow_routes_both_timed_reminder_forms_identically(
        self,
    ) -> None:
        for text in ("8点提醒打游戏", "8点提醒我打游戏"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                tasks_path = root / "tasks.json"
                manager = TodoManager(tasks_path)
                fake = FakeDeepSeekClient()
                before = tasks_path.read_text(encoding="utf-8")

                window = ChatWindow(
                    ChatService(client=fake),
                    todo_manager=manager,
                    ui_settings=ChatUiSettings(root / "chat_ui.json"),
                )
                self.assertIsInstance(window.action_router, ChatActionRouter)
                window.action_router.parser._now_provider = lambda: BASE

                with (
                    patch.object(
                        window,
                        "_handle_local_action",
                        wraps=window._handle_local_action,
                    ) as local_action,
                    patch.object(
                        window.service,
                        "begin_turn",
                        wraps=window.service.begin_turn,
                    ) as begin_turn,
                ):
                    send(window, text)

                local_action.assert_called_once()
                begin_turn.assert_not_called()
                action = local_action.call_args.args[0]
                self.assertEqual(action.kind, ChatActionKind.TODO_DRAFT)
                self.assertIsInstance(action.draft, TaskDraft)
                self.assertEqual(action.draft.parser_text, "8点提醒我打游戏")
                self.assertEqual(fake.requests, [])
                self.assertEqual(manager.get_tasks(), ())
                self.assertEqual(tasks_path.read_text(encoding="utf-8"), before)

                self.assertEqual(len(window.todo_cards), 1)
                card = window.todo_cards[0]
                self.assertIsInstance(card, TodoConfirmationCard)
                self.assertIs(card.draft, action.draft)
                self.assertEqual(card.draft.title, "打游戏")

                card.confirm_button.click()
                self.app.processEvents()
                self.assertEqual(len(manager.get_tasks()), 1)
                self.assertEqual(manager.get_tasks()[0].text, "打游戏")
                self.assertEqual(fake.requests, [])
                window.shutdown()
