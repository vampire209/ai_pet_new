"""Phase 8 DeepSeek chat service, streaming worker, and UI tests."""

from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
import sys
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ai.ai_config import (
    ALTERNATE_MODEL,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_THINKING_TYPE,
    DeepSeekConfig,
)
from ai.chat_service import ChatService
from ai.chat_worker import ChatStreamWorker
from ai.deepseek_client import (
    DeepSeekClient,
    DeepSeekClientError,
    MissingAPIKeyError,
    user_facing_error,
)
from ai.fake_client import FakeDeepSeekClient, fake_api_error
from ai.persona import DEFAULT_SYSTEM_PROMPT
from chat_window import ChatWindow
from config import AppConfig
from natural_language_parser import NaturalLanguageParser
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager


def wait_until(predicate, app: QApplication, timeout_seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        QTest.qWait(10)
    app.processEvents()
    return bool(predicate())


class MockDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class MockChoice:
    def __init__(self, content: str | None) -> None:
        self.delta = MockDelta(content)


class MockChunk:
    def __init__(self, content: str | None) -> None:
        self.choices = [MockChoice(content)]


class MockStream(list):
    def __init__(self, values) -> None:
        super().__init__(values)
        self.closed = False

    def close(self) -> None:
        self.closed = True


class MockCompletions:
    def __init__(self, stream=None, error: Exception | None = None) -> None:
        self.stream = stream or MockStream([MockChunk("你"), MockChunk("好")])
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.stream


class MockSDKClient:
    def __init__(self, completions: MockCompletions | None = None) -> None:
        self.completions = completions or MockCompletions()
        self.chat = type("MockChat", (), {"completions": self.completions})()


class Phase8Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase8-tests"])
        cls.config = AppConfig.load()

    def test_01_missing_key_safe_failure_and_secret_never_in_error(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            service = ChatService(client=DeepSeekClient(DeepSeekConfig()))
            with self.assertRaises(MissingAPIKeyError) as context:
                service.begin_turn("你好")
            self.assertIn("未配置", str(context.exception))
            self.assertFalse(service.is_busy)

        secret = "sk-secret-must-not-appear"
        safe = user_facing_error(RuntimeError(f"request failed with {secret}"))
        self.assertNotIn(secret, safe)
        self.assertNotIn("RuntimeError", safe)

    def test_02_default_config_model_switch_and_official_stream_parameters(self) -> None:
        config = DeepSeekConfig()
        self.assertEqual(config.base_url, DEFAULT_BASE_URL)
        self.assertEqual(config.model, DEFAULT_MODEL)
        self.assertEqual(config.thinking_type, DEFAULT_THINKING_TYPE)
        switched = config.with_model(ALTERNATE_MODEL)
        self.assertEqual(switched.model, "deepseek-v4-pro")

        captured_factory: dict = {}
        sdk = MockSDKClient()

        def factory(**kwargs):
            captured_factory.update(kwargs)
            return sdk

        client = DeepSeekClient(
            switched,
            api_key="test-only-key",
            client_factory=factory,
        )
        chunks = list(
            client.stream_chat(
                ({"role": "user", "content": "你好"},),
            )
        )
        self.assertEqual(chunks, ["你", "好"])
        self.assertEqual(captured_factory["base_url"], DEFAULT_BASE_URL)
        self.assertEqual(captured_factory["timeout"], config.timeout_seconds)
        call = sdk.completions.calls[0]
        self.assertEqual(call["model"], "deepseek-v4-pro")
        self.assertTrue(call["stream"])
        self.assertEqual(
            call["extra_body"],
            {"thinking": {"type": "disabled"}},
        )

    def test_03_api_errors_are_mapped_to_short_chinese_messages(self) -> None:
        class AuthenticationError(Exception):
            status_code = 401

        class APITimeoutError(Exception):
            pass

        class APIConnectionError(Exception):
            pass

        class RateLimitError(Exception):
            status_code = 429

        class BalanceError(Exception):
            status_code = 402

        class ModelNotFoundError(Exception):
            status_code = 404

        class ServerError(Exception):
            status_code = 503

        cases = (
            (AuthenticationError("secret"), "认证失败"),
            (APITimeoutError("secret"), "超时"),
            (APIConnectionError("secret"), "网络连接失败"),
            (RateLimitError("secret"), "过于频繁"),
            (BalanceError("secret"), "余额或额度不足"),
            (ModelNotFoundError("secret"), "模型不可用"),
            (ServerError("secret"), "服务暂时异常"),
            (RuntimeError("secret"), "请求失败"),
        )
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                message = user_facing_error(error)
                self.assertIn(expected, message)
                self.assertNotIn("secret", message)

    def test_04_system_persona_and_first_user_message_are_present(self) -> None:
        fake = FakeDeepSeekClient((("你好呀",),))
        service = ChatService(client=fake)
        messages = service.begin_turn("你好")
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], DEFAULT_SYSTEM_PROMPT)
        self.assertIn("可以帮助用户创建", messages[0]["content"])
        self.assertIn("一次性、每日、每周和提前提醒", messages[0]["content"])
        self.assertIn("只有用户点击确认", messages[0]["content"])
        self.assertIn("到时候会提醒你", messages[0]["content"])
        self.assertIn("先让你确认再创建", messages[0]["content"])
        self.assertIn("不能修改、删除或完成", messages[0]["content"])
        self.assertIn("不能操作 Windows 系统提醒", messages[0]["content"])
        self.assertNotIn("不能直接创建待办", messages[0]["content"])
        self.assertEqual(messages[-1], {"role": "user", "content": "你好"})

    def test_05_assistant_history_and_second_turn_include_prior_context(self) -> None:
        fake = FakeDeepSeekClient((("感觉不错。",), ("傅里叶变换。",)))
        service = ChatService(client=fake)
        first_messages = service.begin_turn("我今天在学傅里叶变换")
        first_reply = "".join(service.stream_response(first_messages, Event()))
        service.complete_turn(first_reply)
        self.assertEqual(
            service.history,
            (
                {"role": "user", "content": "我今天在学傅里叶变换"},
                {"role": "assistant", "content": "感觉不错。"},
            ),
        )

        second = service.begin_turn("刚才我说我在学什么？")
        self.assertEqual([item["role"] for item in second], ["system", "user", "assistant", "user"])
        self.assertEqual(second[1]["content"], "我今天在学傅里叶变换")
        self.assertEqual(second[-1]["content"], "刚才我说我在学什么？")
        second_reply = "".join(service.stream_response(second, Event()))
        service.complete_turn(second_reply)

    def test_06_clear_removes_history_but_preserves_system_persona(self) -> None:
        service = ChatService(client=FakeDeepSeekClient())
        service.begin_turn("第一轮")
        service.complete_turn("回答")
        service.clear()
        self.assertEqual(service.history, ())
        self.assertEqual(len(service.messages), 1)
        self.assertEqual(service.messages[0]["role"], "system")
        self.assertEqual(service.messages[0]["content"], DEFAULT_SYSTEM_PROMPT)

    def test_07_context_limit_discards_oldest_complete_rounds(self) -> None:
        service = ChatService(
            client=FakeDeepSeekClient(),
            max_context_rounds=2,
        )
        for number in range(4):
            service.begin_turn(f"问题{number}")
            service.complete_turn(f"回答{number}")
        self.assertEqual(
            [item["content"] for item in service.history],
            ["问题2", "回答2", "问题3", "回答3"],
        )
        self.assertEqual(service.messages[0]["role"], "system")

    def test_08_fake_stream_and_worker_completed_signals(self) -> None:
        fake = FakeDeepSeekClient((("你", "好", "呀"),))
        service = ChatService(client=fake)
        messages = service.begin_turn("Hello")
        worker = ChatStreamWorker(service, messages)
        chunks: list[str] = []
        completed: list[str] = []
        finished: list[bool] = []
        worker.signals.chunk.connect(chunks.append)
        worker.signals.completed.connect(completed.append)
        worker.signals.finished.connect(lambda: finished.append(True))
        worker.run()
        self.assertEqual(chunks, ["你", "好", "呀"])
        self.assertEqual(completed, ["你好呀"])
        self.assertEqual(finished, [True])

    def test_09_worker_emits_safe_error_without_crashing(self) -> None:
        service = ChatService(
            client=FakeDeepSeekClient((fake_api_error("模拟 API 异常"),))
        )
        messages = service.begin_turn("触发错误")
        worker = ChatStreamWorker(service, messages)
        errors: list[str] = []
        finished: list[bool] = []
        worker.signals.error.connect(errors.append)
        worker.signals.finished.connect(lambda: finished.append(True))
        worker.run()
        self.assertEqual(errors, ["模拟 API 异常"])
        self.assertEqual(finished, [True])
        service.fail_turn()

    def test_10_chat_window_streams_into_one_bubble_and_blocks_duplicate_send(self) -> None:
        fake = FakeDeepSeekClient(
            (("你", "好", "呀"),),
            chunk_delay_seconds=0.01,
        )
        window = ChatWindow(ChatService(client=fake))
        window.show()

        window.input.setPlainText("   ")
        window.send_message()
        self.assertEqual(fake.requests, [])
        self.assertIn("不能为空", window.status_label.text())

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
        window.input.setPlainText("这条不能并发发送")
        window.send_message()
        self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
        self.assertEqual(len(fake.requests), 1)
        self.assertEqual(
            window.message_texts,
            (("user", "你好"), ("assistant", "你好呀")),
        )
        self.assertEqual(
            len([role for role, _text in window.message_texts if role == "assistant"]),
            1,
        )
        self.assertTrue(window.send_button.isEnabled())
        window.shutdown()

    def test_11_stop_keeps_partial_text_and_restores_controls(self) -> None:
        fake = FakeDeepSeekClient(
            ((str(number) for number in range(100)),),
            chunk_delay_seconds=0.01,
        )
        service = ChatService(client=fake)
        window = ChatWindow(service)
        window.show()
        window.input.setPlainText("请慢慢回答")
        window.send_message()
        self.assertTrue(
            wait_until(
                lambda: len(window.message_texts) == 2
                and window.message_texts[-1][1]
                not in {"", "…", "桌宠正在想……"},
                self.app,
            )
        )
        partial = window.message_texts[-1][1]
        window.stop_generation()
        self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
        self.app.processEvents()
        self.assertEqual(window.message_texts[-1][1], partial)
        self.assertIn("已停止", window.status_label.text())
        self.assertTrue(window.send_button.isEnabled())
        self.assertFalse(window.stop_button.isEnabled())
        self.assertEqual(service.history[-1]["content"], partial)
        window.shutdown()

    def test_12_error_recovers_and_clear_resets_ui_and_context(self) -> None:
        fake = FakeDeepSeekClient(
            (fake_api_error("模拟 API 异常"), ("恢复", "成功")),
            chunk_delay_seconds=0.005,
        )
        service = ChatService(client=fake)
        window = ChatWindow(service)
        window.show()
        window.input.setPlainText("第一次")
        window.send_message()
        self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
        self.assertIn("模拟 API 异常", window.status_label.text())
        self.assertEqual(service.history, ())
        self.assertTrue(window.send_button.isEnabled())

        window.input.setPlainText("第二次")
        window.send_message()
        self.assertTrue(wait_until(lambda: not window.is_generating, self.app))
        self.assertEqual(window.message_texts[-1], ("assistant", "恢复成功"))
        self.assertNotEqual(service.history, ())
        window.clear_chat()
        self.app.processEvents()
        self.assertEqual(window.message_texts, ())
        self.assertEqual(service.history, ())
        self.assertEqual(service.messages[0]["role"], "system")
        self.assertIn("已清空", window.status_label.text())
        window.shutdown()

    def test_13_pet_chat_menu_is_single_instance_and_close_keeps_pet_alive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            scanner = ReminderManager(manager)
            service = ChatService(client=FakeDeepSeekClient())
            pet = PetWindow(
                self.config,
                manager,
                scanner,
                chat_service=service,
            )
            pet.show()
            menu = pet._create_context_menu()
            self.assertEqual(
                [action.text() for action in menu.actions()],
                ["打开待办", "AI聊天", "桌宠设置", "记忆管理", "问她一句", "", "外观与行为", "始终置顶", "隐藏到托盘", "", "退出"],
            )
            menu.actions()[1].trigger()
            self.app.processEvents()
            first = pet.chat_window
            self.assertIsNotNone(first)
            self.assertTrue(first.isVisible())
            pet._open_chat_window()
            self.assertIs(pet.chat_window, first)
            first.close()
            self.app.processEvents()
            self.assertFalse(first.isVisible())
            self.assertTrue(pet.isVisible())
            pet._open_chat_window()
            self.app.processEvents()
            self.assertIs(pet.chat_window, first)
            self.assertTrue(first.isVisible())
            pet.close()
            self.app.processEvents()

    def test_14_chat_stream_does_not_block_todo_or_reminder_animation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = datetime(2026, 8, 27, 14, 0, 0)
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            scanner = ReminderManager(manager, now_provider=lambda: base)
            fake = FakeDeepSeekClient(
                (("流" for _ in range(300)),),
                chunk_delay_seconds=0.005,
            )
            pet = PetWindow(
                self.config,
                manager,
                scanner,
                chat_service=ChatService(client=fake),
            )
            pet.show()
            pet._open_chat_window()
            chat = pet.chat_window
            chat.input.setPlainText("保持后台生成")
            chat.send_message()
            self.assertTrue(wait_until(lambda: chat.is_generating, self.app))

            ordinary = manager.add_task("聊天期间新增待办", now=base)
            self.assertIsNotNone(manager.get_task(ordinary.id))
            due = manager.add_task(
                "聊天期间到点提醒",
                (base - timedelta(seconds=1)).isoformat(timespec="seconds"),
                now=base - timedelta(minutes=1),
            )
            scanner.check_now()
            self.assertTrue(
                wait_until(lambda: len(pet.reminder_bubbles) == 1, self.app)
            )
            self.assertEqual(pet.animation_state, "remind")
            self.assertTrue(chat.is_generating)
            self.assertEqual(pet.reminder_bubbles[0].task_id, due.id)

            chat.stop_generation()
            self.assertTrue(wait_until(lambda: not chat.is_generating, self.app))
            pet.close()
            self.app.processEvents()

    def test_15_phase7_parser_and_todo_creation_remain_independent_from_ai(self) -> None:
        base = datetime(2026, 8, 27, 10, 0, 0)
        parser = NaturalLanguageParser(now_provider=lambda: base)
        parsed = parser.parse("明天下午3点提醒我完成高数作业，提前30分钟提醒")
        self.assertTrue(parsed.is_valid, parsed.errors)
        self.assertEqual(parsed.title, "完成高数作业")

        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            service = ChatService(client=FakeDeepSeekClient())
            before = manager.get_tasks()
            messages = service.begin_turn("帮我创建明天下午三点的待办")
            reply = "".join(service.stream_response(messages, Event()))
            service.complete_turn(reply)
            self.assertEqual(manager.get_tasks(), before)
            created = manager.add_task(
                parsed.title,
                parsed.due_at_iso,
                parsed.advance_minutes,
                parsed.repeat_type,
                parsed.repeat_weekdays,
                now=base,
            )
            self.assertEqual(created.text, "完成高数作业")
            self.assertEqual(created.reminder_offsets, (30,))
            self.assertIn("只有用户点击确认", DEFAULT_SYSTEM_PROMPT)
            self.assertIn("绝不能跳过确认卡", DEFAULT_SYSTEM_PROMPT)
            self.assertIn("不能修改、删除或完成", DEFAULT_SYSTEM_PROMPT)
