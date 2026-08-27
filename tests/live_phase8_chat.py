"""Visible Phase 8 chat probe using only the deterministic fake client."""

from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ai.chat_service import ChatService
from ai.fake_client import FakeDeepSeekClient, fake_api_error
from chat_window import ChatWindow


class LivePhase8Probe:
    """Exercise A-E without a key, billable request, or network access."""

    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.failed = False
        self.finished = False
        self.fake = FakeDeepSeekClient(
            (
                ("你", "好", "呀"),
                ("学习", "加油"),
                ("你刚才说的是", "傅里叶变换"),
                ("慢" for _ in range(100)),
                fake_api_error("模拟 API 异常"),
            ),
            chunk_delay_seconds=0.06,
        )
        self.service = ChatService(client=self.fake)
        self.window = ChatWindow(self.service)

    def start(self) -> None:
        self.window.show()
        self.app.processEvents()
        QTimer.singleShot(300, self.scenario_a)
        QTimer.singleShot(20_000, lambda: self.fail("Phase 8 live 测试超时"))

    def send(self, text: str) -> None:
        self.window.input.setPlainText(text)
        self.window.send_message()

    def wait_for(self, predicate, on_success, message: str, attempts: int = 250) -> None:
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
            lambda: self.window.is_generating
            and len(self.window.message_texts) == 2
            and self.window.message_texts[-1][1]
            not in {"", "…", "桌宠正在想……", "你好呀"},
            self.finish_a,
            "场景 A：没有观察到逐块更新",
        )

    def finish_a(self) -> None:
        partial = self.window.message_texts[-1][1]
        self.wait_for(
            lambda: not self.window.is_generating,
            lambda: self.verify_a(partial),
            "场景 A：流式回答没有结束",
        )

    def verify_a(self, partial: str) -> None:
        if partial == "你好呀" or self.window.message_texts[-1] != (
            "assistant",
            "你好呀",
        ):
            self.fail("场景 A：流式内容或最终内容错误")
            return
        print(f"LIVE_A streaming_partial={partial} final=你好呀", flush=True)
        QTimer.singleShot(120, self.scenario_b_first)

    def scenario_b_first(self) -> None:
        self.send("我今天在学傅里叶变换")
        self.wait_for(
            lambda: not self.window.is_generating,
            self.scenario_b_second,
            "场景 B：第一轮没有结束",
        )

    def scenario_b_second(self) -> None:
        self.send("刚才我说我在学什么？")
        self.wait_for(
            lambda: not self.window.is_generating,
            self.verify_b,
            "场景 B：第二轮没有结束",
        )

    def verify_b(self) -> None:
        request = self.fake.requests[-1]
        contents = [message["content"] for message in request]
        if (
            "我今天在学傅里叶变换" not in contents
            or "学习加油" not in contents
            or self.window.message_texts[-1][1] != "你刚才说的是傅里叶变换"
        ):
            self.fail("场景 B：第二轮没有携带上一轮上下文")
            return
        print("LIVE_B multi_turn_context_preserved", flush=True)
        QTimer.singleShot(120, self.scenario_c)

    def scenario_c(self) -> None:
        self.send("生成一段很长的回答")
        self.wait_for(
            lambda: self.window.is_generating
            and self.window.message_texts[-1][1]
            not in {"", "…", "桌宠正在想……"},
            self.stop_c,
            "场景 C：停止前没有收到首个分块",
        )

    def stop_c(self) -> None:
        partial = self.window.message_texts[-1][1]
        self.window.stop_generation()
        self.wait_for(
            lambda: not self.window.is_generating,
            lambda: self.verify_c(partial),
            "场景 C：停止操作没有完成",
        )

    def verify_c(self, partial: str) -> None:
        if self.window.message_texts[-1][1] != partial:
            self.fail("场景 C：停止后仍追加了分块")
            return
        print(f"LIVE_C stopped_partial={partial}", flush=True)
        QTimer.singleShot(120, self.scenario_d)

    def scenario_d(self) -> None:
        self.send("模拟失败")
        self.wait_for(
            lambda: not self.window.is_generating,
            self.verify_d,
            "场景 D：异常请求没有结束",
        )

    def verify_d(self) -> None:
        if (
            "模拟 API 异常" not in self.window.status_label.text()
            or not self.window.send_button.isEnabled()
        ):
            self.fail("场景 D：异常没有安全显示或窗口未恢复")
            return
        print("LIVE_D safe_error_and_window_recovered", flush=True)
        QTimer.singleShot(120, self.scenario_e)

    def scenario_e(self) -> None:
        self.window.clear_chat()
        self.app.processEvents()
        if (
            self.window.message_texts
            or self.service.history
            or self.service.messages[0]["role"] != "system"
        ):
            self.fail("场景 E：清空没有同时重置 UI 与上下文")
            return
        print("LIVE_E clear_ui_and_context", flush=True)
        print("LIVE_PASS A_stream B_context C_stop D_error E_clear", flush=True)
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


def main() -> int:
    app = QApplication(["phase8-live-fake-chat"])
    app.setQuitOnLastWindowClosed(False)
    probe = LivePhase8Probe(app)
    probe.start()
    app.exec()
    return 1 if probe.failed or not probe.finished else 0


if __name__ == "__main__":
    raise SystemExit(main())
