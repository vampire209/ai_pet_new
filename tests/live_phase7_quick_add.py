"""Visible Phase 7 quick-add probe with an injected local clock."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

from models.recurrence import REPEAT_DAILY, REPEAT_WEEKLY
from natural_language_parser import NaturalLanguageParser
from todo_manager import TodoManager
from todo_window import TodoWindow


class LivePhase7Probe:
    def __init__(self, app: QApplication) -> None:
        self.app = app
        self.failed = False
        self.finished = False
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "tasks.json"
        self.base = datetime(2026, 8, 27, 10, 0, 0)
        self.parser = NaturalLanguageParser(now_provider=lambda: self.base)
        self.manager = TodoManager(self.path)
        self.window = TodoWindow(self.manager, parser=self.parser)
        self.input = self.window.findChild(QLineEdit, "quickTaskInput")
        self.parse_button = self.window.findChild(QPushButton, "parseQuickTaskButton")
        self.confirm_button = self.window.findChild(
            QPushButton,
            "confirmQuickTaskButton",
        )

    def start(self) -> None:
        self.window.show()
        self.app.processEvents()
        QTimer.singleShot(400, self.scenario_a)
        QTimer.singleShot(15_000, lambda: self.fail("Phase 7 live 测试超时"))

    def parse(self, text: str):
        self.input.setText(text)
        self.parse_button.click()
        self.app.processEvents()
        return self.window._quick_add.parsed_task

    def confirm(self) -> None:
        self.confirm_button.click()
        self.app.processEvents()

    def scenario_a(self) -> None:
        parsed = self.parse(
            "明天下午3点提醒我交作业，提前30分钟提醒"
        )
        preview_time = self.window.findChild(QLabel, "quickPreviewTime").text()
        if (
            parsed is None
            or parsed.title != "交作业"
            or parsed.due_at != datetime(2026, 8, 28, 15, 0)
            or parsed.advance_minutes != (30,)
            or preview_time != "2026-08-28 15:00"
            or self.manager.get_tasks()
        ):
            self.fail("场景 A：预览内容错误或解析时提前创建")
            return
        self.confirm()
        if len(self.manager.get_tasks()) != 1:
            self.fail("场景 A：确认后没有创建任务")
            return
        print("LIVE_A preview_confirm_created advance30", flush=True)
        QTimer.singleShot(400, self.scenario_b)

    def scenario_b(self) -> None:
        parsed = self.parse("30分钟后提醒我拿快递")
        if parsed is None or parsed.due_at != self.base + timedelta(minutes=30):
            self.fail("场景 B：相对时间没有使用注入时钟")
            return
        self.confirm()
        if self.manager.get_tasks()[-1].event_at != "2026-08-27T10:30:00":
            self.fail("场景 B：相对时间任务创建结果错误")
            return
        print("LIVE_B relative_30_minutes", flush=True)
        QTimer.singleShot(400, self.scenario_c)

    def scenario_c(self) -> None:
        parsed = self.parse("每天晚上8点提醒我背单词")
        if parsed is None or parsed.repeat_type != REPEAT_DAILY:
            self.fail("场景 C：daily 预览错误")
            return
        self.confirm()
        created = self.manager.get_tasks()[-1]
        if created.repeat_type != REPEAT_DAILY or created.event_at != "2026-08-27T20:00:00":
            self.fail("场景 C：daily Task 创建错误")
            return
        print("LIVE_C daily_created", flush=True)
        QTimer.singleShot(400, self.scenario_d)

    def scenario_d(self) -> None:
        parsed = self.parse("每周一三五下午6点提醒我健身")
        if (
            parsed is None
            or parsed.repeat_type != REPEAT_WEEKLY
            or parsed.repeat_weekdays != (1, 3, 5)
        ):
            self.fail("场景 D：weekly 预览错误")
            return
        self.confirm()
        created = self.manager.get_tasks()[-1]
        if (
            created.repeat_type != REPEAT_WEEKLY
            or created.repeat_weekdays != (1, 3, 5)
            or created.event_at != "2026-08-28T18:00:00"
        ):
            self.fail("场景 D：weekly Task 创建错误")
            return
        print("LIVE_D weekly_135_created", flush=True)
        QTimer.singleShot(400, self.scenario_e)

    def scenario_e(self) -> None:
        before_count = len(self.manager.get_tasks())
        parsed = self.parse("明天提醒我学习")
        status = self.window.findChild(QLabel, "quickParseStatus").text()
        if (
            parsed is not None
            or len(self.manager.get_tasks()) != before_count
            or "缺少具体时间" not in status
        ):
            self.fail("场景 E：缺少时间时没有安全阻止创建")
            return
        print("LIVE_E incomplete_blocked", flush=True)
        print("LIVE_PASS A_preview B_relative C_daily D_weekly E_error", flush=True)
        self.finished = True
        self.shutdown()

    def fail(self, message: str) -> None:
        if self.failed or self.finished:
            return
        self.failed = True
        print(f"LIVE_FAIL {message}", flush=True)
        self.shutdown()

    def shutdown(self) -> None:
        self.window.close()
        QTimer.singleShot(0, self.app.quit)

    def cleanup(self) -> None:
        self.temp_dir.cleanup()


def main() -> int:
    app = QApplication(["phase7-live-quick-add"])
    app.setQuitOnLastWindowClosed(False)
    probe = LivePhase7Probe(app)
    probe.start()
    app.exec()
    failed = probe.failed or not probe.finished
    probe.cleanup()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
