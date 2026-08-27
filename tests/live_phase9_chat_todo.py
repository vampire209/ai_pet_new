"""Visible Phase 9 A-G probe using Fake AI and temporary Todo storage."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ai.chat_action_router import ChatActionRouter
from ai.chat_service import ChatService
from ai.fake_client import FakeDeepSeekClient
from chat_window import ChatWindow
from models.recurrence import REPEAT_DAILY, REPEAT_WEEKLY
from natural_language_parser import NaturalLanguageParser
from todo_manager import TodoManager


class LivePhase9Probe:
    """Exercise normal chat and confirmed local Todo actions without network."""

    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.failed = False
        self.finished = False
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "tasks.json"
        self.base = datetime(2026, 8, 27, 10, 0, 0)
        self.manager = TodoManager(self.path)
        self.fake = FakeDeepSeekClient(
            (("你", "好", "呀"),),
            chunk_delay_seconds=0.07,
        )
        self.service = ChatService(client=self.fake)
        self.router = ChatActionRouter(
            NaturalLanguageParser(now_provider=lambda: self.base)
        )
        self.window = ChatWindow(
            self.service,
            todo_manager=self.manager,
            action_router=self.router,
        )

    def start(self) -> None:
        self.window.show()
        self.app.processEvents()
        QTimer.singleShot(350, self.scenario_a)
        QTimer.singleShot(25_000, lambda: self.fail("Phase 9 live 测试超时"))

    def send(self, text: str) -> None:
        self.window.input.setPlainText(text)
        self.window.send_message()

    def wait_for(self, predicate, on_success, message: str, attempts: int = 300) -> None:
        if predicate():
            on_success()
            return
        if attempts <= 0:
            self.fail(message)
            return
        QTimer.singleShot(
            20,
            lambda: self.wait_for(predicate, on_success, message, attempts - 1),
        )

    def scenario_a(self) -> None:
        self.send("你好")
        self.wait_for(
            lambda: not self.window.is_generating,
            self.verify_a,
            "场景 A：普通 Fake 聊天没有结束",
        )

    def verify_a(self) -> None:
        if self.window.message_texts[-1] != ("assistant", "你好呀"):
            self.fail("场景 A：普通聊天流式结果错误")
            return
        print("LIVE_A normal_fake_stream=你好呀", flush=True)
        QTimer.singleShot(180, self.scenario_b)

    def scenario_b(self) -> None:
        before = len(self.manager.get_tasks())
        self.send("明天下午3点提醒我交作业，提前30分钟提醒")
        if len(self.window.todo_cards) != 1 or len(self.manager.get_tasks()) != before:
            self.fail("场景 B：草案阶段提前创建或确认卡缺失")
            return
        card = self.window.todo_cards[-1]
        if card.draft.advance_minutes != (30,):
            self.fail("场景 B：advance 草案错误")
            return
        card.confirm_button.click()
        task = self.manager.get_tasks()[-1]
        if (
            card.state != "created"
            or task.text != "交作业"
            or {item.type for item in task.reminders} != {"advance", "final"}
        ):
            self.fail("场景 B：确认后 Task 或 Reminder 错误")
            return
        print("LIVE_B confirmed_advance_todo_created", flush=True)
        QTimer.singleShot(180, self.scenario_c)

    def scenario_c(self) -> None:
        self.send("每天晚上8点提醒我背单词")
        card = self.window.todo_cards[-1]
        if card.draft.repeat_type != REPEAT_DAILY:
            self.fail("场景 C：daily 草案错误")
            return
        card.confirm_button.click()
        task = self.manager.get_tasks()[-1]
        if task.repeat_type != REPEAT_DAILY:
            self.fail("场景 C：daily Task 没有进入 Phase 6 recurrence")
            return
        print("LIVE_C daily_confirmed", flush=True)
        QTimer.singleShot(180, self.scenario_d)

    def scenario_d(self) -> None:
        self.send("每周一三五下午6点提醒我健身")
        card = self.window.todo_cards[-1]
        if (
            card.draft.repeat_type != REPEAT_WEEKLY
            or card.draft.repeat_weekdays != (1, 3, 5)
        ):
            self.fail("场景 D：weekly 草案错误")
            return
        card.confirm_button.click()
        task = self.manager.get_tasks()[-1]
        if task.repeat_weekdays != (1, 3, 5):
            self.fail("场景 D：weekly Task 星期错误")
            return
        print("LIVE_D weekly_135_confirmed", flush=True)
        QTimer.singleShot(180, self.scenario_e)

    def scenario_e(self) -> None:
        task_count = len(self.manager.get_tasks())
        card_count = len(self.window.todo_cards)
        self.send("明天提醒我学习")
        if (
            len(self.manager.get_tasks()) != task_count
            or len(self.window.todo_cards) != card_count
            or "几点" not in self.window.message_texts[-1][1]
        ):
            self.fail("场景 E：缺少时间时没有安全阻止创建")
            return
        print("LIVE_E incomplete_time_blocked", flush=True)
        QTimer.singleShot(180, self.scenario_f)

    def scenario_f(self) -> None:
        self.fake.is_configured = False
        request_count = len(self.fake.requests)
        self.send("明天下午4点提醒我无Key任务")
        card = self.window.todo_cards[-1]
        card.confirm_button.click()
        task = self.manager.get_tasks()[-1]
        if (
            card.state != "created"
            or task.text != "无Key任务"
            or len(self.fake.requests) != request_count
        ):
            self.fail("场景 F：无 Key 本地创建失败或错误调用 API")
            return
        print("LIVE_F no_key_local_todo_created", flush=True)
        QTimer.singleShot(180, self.scenario_g)

    def scenario_g(self) -> None:
        task_ids = {task.id for task in self.manager.get_tasks()}
        self.window.clear_chat()
        self.app.processEvents()
        if (
            self.window.message_texts
            or self.window.todo_cards
            or {task.id for task in self.manager.get_tasks()} != task_ids
        ):
            self.fail("场景 G：清空聊天错误删除了 Todo 或没有清空 UI")
            return
        print("LIVE_G clear_chat_keeps_todos", flush=True)
        print(
            "LIVE_PASS A_chat B_advance C_daily D_weekly E_incomplete F_no_key G_clear",
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
        self.window.shutdown()
        QTimer.singleShot(0, self.app.quit)

    def cleanup(self) -> None:
        self.temp_dir.cleanup()


def main() -> int:
    app = QApplication(["phase9-live-chat-todo"])
    app.setQuitOnLastWindowClosed(False)
    probe = LivePhase9Probe(app)
    probe.start()
    app.exec()
    failed = probe.failed or not probe.finished
    probe.cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
