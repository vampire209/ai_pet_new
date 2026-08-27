"""Phase 7 local Chinese natural-language parsing acceptance tests."""

from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

from models.recurrence import REPEAT_DAILY, REPEAT_NONE, REPEAT_WEEKLY
from natural_language_parser import NaturalLanguageParser
from todo_manager import TodoManager
from todo_window import TodoWindow


class Phase7Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase7-tests"])
        cls.base = datetime(2026, 8, 27, 10, 0, 0)
        cls.parser = NaturalLanguageParser(now_provider=lambda: cls.base)

    def assert_valid(
        self,
        text: str,
        expected_due: datetime,
        expected_title: str,
    ):
        parsed = self.parser.parse(text)
        self.assertTrue(parsed.is_valid, parsed.errors)
        self.assertEqual(parsed.due_at, expected_due)
        self.assertEqual(parsed.title, expected_title)
        return parsed

    def test_01_tomorrow_numeric_and_chinese_afternoon(self) -> None:
        expected = datetime(2026, 8, 28, 15, 0)
        first = self.assert_valid(
            "明天下午3点提醒我交作业",
            expected,
            "交作业",
        )
        second = self.assert_valid(
            "明天下午三点提醒我交作业",
            expected,
            "交作业",
        )
        self.assertEqual(first.repeat_type, REPEAT_NONE)
        self.assertEqual(second.repeat_type, REPEAT_NONE)

    def test_02_tonight_tomorrow_night_and_half_hour_clock(self) -> None:
        self.assert_valid(
            "明晚8点提醒我学习",
            datetime(2026, 8, 28, 20, 0),
            "学习",
        )
        self.assert_valid(
            "明晚八点半提醒我学习",
            datetime(2026, 8, 28, 20, 30),
            "学习",
        )
        evening_parser = NaturalLanguageParser(
            now_provider=lambda: datetime(2026, 8, 27, 18, 0)
        )
        parsed = evening_parser.parse("今晚八点提醒我读书")
        self.assertTrue(parsed.is_valid)
        self.assertEqual(parsed.due_at, datetime(2026, 8, 27, 20, 0))

    def test_03_day_after_tomorrow_and_all_required_clock_forms(self) -> None:
        self.assert_valid(
            "后天上午9:30提醒我交报告",
            datetime(2026, 8, 29, 9, 30),
            "交报告",
        )
        samples = (
            ("明天上午8点提醒我任务一", datetime(2026, 8, 28, 8, 0)),
            ("明天上午8:30提醒我任务二", datetime(2026, 8, 28, 8, 30)),
            ("明天上午8点30提醒我任务三", datetime(2026, 8, 28, 8, 30)),
            ("明天上午八点提醒我任务四", datetime(2026, 8, 28, 8, 0)),
            ("明天上午八点半提醒我任务五", datetime(2026, 8, 28, 8, 30)),
            ("明天下午12点提醒我午餐", datetime(2026, 8, 28, 12, 0)),
        )
        for text, expected in samples:
            with self.subTest(text=text):
                self.assertEqual(self.parser.parse(text).due_at, expected)

    def test_04_relative_minutes_half_hour_and_hours_use_injected_clock(self) -> None:
        self.assert_valid(
            "30分钟后提醒我拿快递",
            self.base + timedelta(minutes=30),
            "拿快递",
        )
        self.assert_valid(
            "半小时后提醒我拿快递",
            self.base + timedelta(minutes=30),
            "拿快递",
        )
        self.assert_valid(
            "2小时后提醒我开会",
            self.base + timedelta(hours=2),
            "开会",
        )
        self.assert_valid(
            "1小时后提醒我喝水",
            self.base + timedelta(hours=1),
            "喝水",
        )

    def test_05_weekday_uses_today_if_future_and_next_week_if_past(self) -> None:
        friday_morning = NaturalLanguageParser(
            now_provider=lambda: datetime(2026, 8, 28, 10, 0)
        )
        future = friday_morning.parse("周五下午4点提醒我开会")
        self.assertTrue(future.is_valid)
        self.assertEqual(future.due_at, datetime(2026, 8, 28, 16, 0))

        friday_evening = NaturalLanguageParser(
            now_provider=lambda: datetime(2026, 8, 28, 17, 0)
        )
        passed = friday_evening.parse("星期五下午4点提醒我开会")
        self.assertTrue(passed.is_valid)
        self.assertEqual(passed.due_at, datetime(2026, 9, 4, 16, 0))

    def test_06_next_week_means_next_natural_week(self) -> None:
        parsed = self.assert_valid(
            "下周一上午10点提醒我交报告",
            datetime(2026, 8, 31, 10, 0),
            "交报告",
        )
        self.assertEqual(parsed.repeat_type, REPEAT_NONE)

    def test_07_month_day_uses_current_or_next_year(self) -> None:
        self.assert_valid(
            "8月30日下午3点提醒我考试",
            datetime(2026, 8, 30, 15, 0),
            "考试",
        )
        after_date = NaturalLanguageParser(
            now_provider=lambda: datetime(2026, 9, 1, 10, 0)
        )
        parsed = after_date.parse("8月30日下午3点提醒我考试")
        self.assertTrue(parsed.is_valid)
        self.assertEqual(parsed.due_at, datetime(2027, 8, 30, 15, 0))

    def test_08_explicit_numeric_slash_and_chinese_dates(self) -> None:
        expected = datetime(2026, 8, 30, 15, 0)
        for text in (
            "2026-08-30 15:00 提醒我考试",
            "2026/08/30 15:00 提醒我考试",
            "2026年8月30日下午3点提醒我考试",
        ):
            with self.subTest(text=text):
                self.assert_valid(text, expected, "考试")

    def test_09_single_multiple_and_deduplicated_advance(self) -> None:
        thirty = self.parser.parse(
            "明天下午3点提醒我交作业，提前30分钟提醒"
        )
        self.assertEqual(thirty.advance_minutes, (30,))
        half = self.parser.parse(
            "明天下午3点提醒我交作业，提前半小时提醒"
        )
        self.assertEqual(half.advance_minutes, (30,))
        multiple = self.parser.parse(
            "明晚8点提醒我学习，提前60分钟、30分钟、5分钟提醒"
        )
        self.assertEqual(multiple.advance_minutes, (60, 30, 5))
        duplicate = self.parser.parse(
            "明晚8点提醒我学习，提前1小时和60分钟和30分钟提醒"
        )
        self.assertEqual(duplicate.advance_minutes, (60, 30))
        ten = self.parser.parse("明晚8点提醒我学习，提前10分钟提醒")
        self.assertEqual(ten.advance_minutes, (10,))

    def test_10_daily_words_reuse_phase6_daily(self) -> None:
        for prefix in ("每天", "每日"):
            with self.subTest(prefix=prefix):
                parsed = self.parser.parse(f"{prefix}晚上8点提醒我背单词")
                self.assertTrue(parsed.is_valid)
                self.assertEqual(parsed.title, "背单词")
                self.assertEqual(parsed.due_at, datetime(2026, 8, 27, 20, 0))
                self.assertEqual(parsed.repeat_type, REPEAT_DAILY)
                self.assertEqual(parsed.repeat_weekdays, ())

    def test_11_weekly_single_and_multiple_spellings_reuse_iso_weekdays(self) -> None:
        samples = (
            ("每周一下午6点提醒我健身", (1,)),
            ("每周一三五下午6点提醒我健身", (1, 3, 5)),
            ("每周一、三、五下午6点提醒我健身", (1, 3, 5)),
            ("每周一周三周五下午6点提醒我健身", (1, 3, 5)),
        )
        for text, weekdays in samples:
            with self.subTest(text=text):
                parsed = self.parser.parse(text)
                self.assertTrue(parsed.is_valid, parsed.errors)
                self.assertEqual(parsed.title, "健身")
                self.assertEqual(parsed.repeat_type, REPEAT_WEEKLY)
                self.assertEqual(parsed.repeat_weekdays, weekdays)
                expected_day = 31 if weekdays == (1,) else 28
                self.assertEqual(parsed.due_at.day, expected_day)
                self.assertEqual(parsed.due_at.hour, 18)

    def test_12_title_punctuation_and_extra_spaces_are_cleaned_safely(self) -> None:
        samples = (
            "明天下午3点，提醒我交作业",
            "明天下午3点, 提醒我交作业",
            "  明天  下午  3 点   提醒我  交作业  ",
            "明天下午3点记得提醒我交作业",
            "明天下午3点帮我提醒交作业",
            "明天下午3点提醒一下交作业",
        )
        for text in samples:
            with self.subTest(text=text):
                parsed = self.parser.parse(text)
                self.assertTrue(parsed.is_valid, parsed.errors)
                self.assertEqual(parsed.title, "交作业")
                self.assertEqual(parsed.due_at, datetime(2026, 8, 28, 15, 0))
        retained = self.parser.parse(
            "明天下午3点提醒我检查提醒设置"
        )
        self.assertEqual(retained.title, "检查提醒设置")

    def test_13_incomplete_past_and_unsupported_expressions_fail_safely(self) -> None:
        no_time = self.parser.parse("明天提醒我写作业")
        self.assertFalse(no_time.is_valid)
        self.assertIn("缺少具体时间", "".join(no_time.errors))

        no_date_or_hour = self.parser.parse("下午提醒我学习")
        self.assertFalse(no_date_or_hour.is_valid)
        self.assertIn("缺少具体时间", "".join(no_date_or_hour.errors))

        evening = NaturalLanguageParser(
            now_provider=lambda: datetime(2026, 8, 27, 18, 0)
        )
        past = evening.parse("今天下午3点提醒我学习")
        self.assertFalse(past.is_valid)
        self.assertIn("已经过去", "".join(past.errors))

        unsupported = self.parser.parse("等会提醒我喝水")
        self.assertFalse(unsupported.is_valid)
        self.assertIn("暂不支持", "".join(unsupported.errors))

    def test_14_parse_preview_does_not_create_until_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window = TodoWindow(manager, parser=self.parser)
            input_box = window.findChild(QLineEdit, "quickTaskInput")
            parse_button = window.findChild(QPushButton, "parseQuickTaskButton")
            confirm_button = window.findChild(QPushButton, "confirmQuickTaskButton")
            input_box.setText(
                "明天下午3点提醒我交作业，提前30分钟提醒"
            )
            parse_button.click()
            self.app.processEvents()

            self.assertEqual(manager.get_tasks(), ())
            self.assertFalse(window._quick_add.preview_frame.isHidden())
            self.assertEqual(
                window.findChild(QLabel, "quickPreviewTitle").text(),
                "交作业",
            )
            self.assertEqual(
                window.findChild(QLabel, "quickPreviewTime").text(),
                "2026-08-28 15:00",
            )
            self.assertEqual(
                window.findChild(QLabel, "quickPreviewRepeat").text(),
                "不重复",
            )
            self.assertEqual(
                window.findChild(QLabel, "quickPreviewAdvance").text(),
                "30分钟",
            )

            confirm_button.click()
            self.app.processEvents()
            self.assertEqual(len(manager.get_tasks()), 1)
            created = manager.get_tasks()[0]
            self.assertEqual(created.text, "交作业")
            self.assertEqual(created.event_at, "2026-08-28T15:00:00")
            self.assertEqual(created.reminder_offsets, (30,))
            window.deleteLater()

    def test_15_cancel_or_parse_failure_never_creates_a_task(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window = TodoWindow(manager, parser=self.parser)
            input_box = window.findChild(QLineEdit, "quickTaskInput")
            parse_button = window.findChild(QPushButton, "parseQuickTaskButton")
            cancel_button = window.findChild(QPushButton, "cancelQuickTaskButton")

            input_box.setText("明天下午3点提醒我交作业")
            parse_button.click()
            cancel_button.click()
            self.assertEqual(manager.get_tasks(), ())
            self.assertTrue(window._quick_add.preview_frame.isHidden())

            input_box.setText("明天提醒我学习")
            parse_button.click()
            self.app.processEvents()
            self.assertEqual(manager.get_tasks(), ())
            self.assertTrue(window._quick_add.preview_frame.isHidden())
            status = window.findChild(QLabel, "quickParseStatus").text()
            self.assertIn("缺少具体时间", status)
            window.deleteLater()

    def test_16_confirm_creation_generates_advance_final_and_survives_json_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tasks.json"
            manager = TodoManager(path)
            window = TodoWindow(manager, parser=self.parser)
            window._quick_add.input.setText(
                "明天下午3点提醒我交作业，提前30分钟提醒"
            )
            window._quick_add.parse_input()
            window._quick_add.confirm_creation()
            created = manager.get_tasks()[0]
            reminders = {item.type: item for item in created.reminders}
            self.assertEqual(reminders["advance"].trigger_at, "2026-08-28T14:30:00")
            self.assertEqual(reminders["final"].trigger_at, "2026-08-28T15:00:00")

            saved = json.loads(path.read_text(encoding="utf-8"))[0]
            self.assertEqual(saved["text"], "交作业")
            self.assertEqual(saved["repeat_type"], REPEAT_NONE)
            restarted = TodoManager(path).get_task(created.id)
            self.assertEqual(restarted.event_at, created.event_at)
            self.assertEqual(
                [item.to_dict() for item in restarted.reminders],
                [item.to_dict() for item in created.reminders],
            )
            window.deleteLater()

    def test_17_natural_daily_task_uses_phase6_completion_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            parsed = self.parser.parse("每天晚上8点提醒我背单词")
            task = manager.add_task(
                parsed.title,
                parsed.due_at_iso,
                parsed.advance_minutes,
                parsed.repeat_type,
                parsed.repeat_weekdays,
                now=self.base,
            )
            self.assertEqual(task.repeat_type, REPEAT_DAILY)
            completed = manager.complete_task(
                task.id,
                now=datetime(2026, 8, 27, 20, 0),
            )
            self.assertFalse(completed.completed)
            self.assertEqual(completed.event_at, "2026-08-28T20:00:00")

    def test_18_natural_weekly_task_uses_phase6_weekday_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            parsed = self.parser.parse("每周一三五下午6点提醒我健身")
            task = manager.add_task(
                parsed.title,
                parsed.due_at_iso,
                parsed.advance_minutes,
                parsed.repeat_type,
                parsed.repeat_weekdays,
                now=self.base,
            )
            self.assertEqual(task.repeat_type, REPEAT_WEEKLY)
            self.assertEqual(task.repeat_weekdays, (1, 3, 5))
            self.assertEqual(task.event_at, "2026-08-28T18:00:00")
            completed = manager.complete_task(
                task.id,
                now=datetime(2026, 8, 28, 18, 0),
            )
            self.assertEqual(completed.event_at, "2026-08-31T18:00:00")
            self.assertFalse(completed.completed)

    def test_19_expired_preview_is_checked_again_before_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            clock = [datetime(2026, 8, 27, 10, 0)]
            parser = NaturalLanguageParser(now_provider=lambda: clock[0])
            manager = TodoManager(Path(temp_dir) / "tasks.json")
            window = TodoWindow(manager, parser=parser)
            window._quick_add.input.setText("30分钟后提醒我拿快递")
            window._quick_add.parse_input()
            self.assertIsNotNone(window._quick_add.parsed_task)
            clock[0] = datetime(2026, 8, 27, 10, 31)
            window._quick_add.confirm_creation()
            self.assertEqual(manager.get_tasks(), ())
            self.assertIn(
                "已经过去",
                window.findChild(QLabel, "quickParseStatus").text(),
            )
            window.deleteLater()

    def test_20_unqualified_hour_chooses_only_future_afternoon_candidate(self) -> None:
        parser = NaturalLanguageParser(
            now_provider=lambda: datetime(2026, 8, 27, 16, 5)
        )
        parsed = parser.parse("我4点半下课，记得提醒我")
        self.assertTrue(parsed.is_valid, parsed.errors)
        self.assertEqual(parsed.due_at, datetime(2026, 8, 27, 16, 30))
        self.assertEqual(parsed.title, "下课")

    def test_21_dotted_month_day_and_leading_subject_are_cleaned(self) -> None:
        parser = NaturalLanguageParser(
            now_provider=lambda: datetime(2026, 8, 27, 16, 5)
        )
        parsed = parser.parse("我8.27下午五点下课，记得提醒我")
        self.assertTrue(parsed.is_valid, parsed.errors)
        self.assertEqual(parsed.due_at, datetime(2026, 8, 27, 17, 0))
        self.assertEqual(parsed.title, "下课")

    def test_22_explicit_period_and_existing_daily_scenarios_stay_stable(self) -> None:
        parser = NaturalLanguageParser(
            now_provider=lambda: datetime(2026, 8, 27, 16, 5)
        )
        tomorrow = parser.parse("明天下午3点提醒我交作业")
        self.assertTrue(tomorrow.is_valid, tomorrow.errors)
        self.assertEqual(tomorrow.due_at, datetime(2026, 8, 28, 15, 0))
        self.assertEqual(tomorrow.title, "交作业")

        daily = parser.parse("每天晚上8点提醒我背单词")
        self.assertTrue(daily.is_valid, daily.errors)
        self.assertEqual(daily.due_at, datetime(2026, 8, 27, 20, 0))
        self.assertEqual(daily.repeat_type, REPEAT_DAILY)
        self.assertEqual(daily.title, "背单词")

        dawn = parser.parse("明天凌晨4点提醒我出发")
        self.assertTrue(dawn.is_valid, dawn.errors)
        self.assertEqual(dawn.due_at, datetime(2026, 8, 28, 4, 0))

    def test_23_supported_date_forms_leave_only_the_task_title(self) -> None:
        parser = NaturalLanguageParser(
            now_provider=lambda: datetime(2026, 8, 27, 16, 5)
        )
        samples = (
            ("我8.27下午五点下课，记得提醒我", datetime(2026, 8, 27, 17, 0)),
            ("我8月27日下午五点下课，记得提醒我", datetime(2026, 8, 27, 17, 0)),
            ("我8月27号下午五点下课，记得提醒我", datetime(2026, 8, 27, 17, 0)),
            ("我2026-08-27下午五点下课，记得提醒我", datetime(2026, 8, 27, 17, 0)),
            ("我今天下午五点下课，记得提醒我", datetime(2026, 8, 27, 17, 0)),
            ("我明天下午五点下课，记得提醒我", datetime(2026, 8, 28, 17, 0)),
            ("我后天下午五点下课，记得提醒我", datetime(2026, 8, 29, 17, 0)),
        )
        for text, due_at in samples:
            with self.subTest(text=text):
                parsed = parser.parse(text)
                self.assertTrue(parsed.is_valid, parsed.errors)
                self.assertEqual(parsed.due_at, due_at)
                self.assertEqual(parsed.title, "下课")

    def test_24_title_cleanup_never_removes_internal_pronoun(self) -> None:
        parser = NaturalLanguageParser(
            now_provider=lambda: datetime(2026, 8, 27, 16, 5)
        )
        incomplete = parser.parse("提醒我买《我的世界》")
        self.assertEqual(incomplete.title, "买《我的世界》")
        complete = parser.parse("明天下午3点提醒我买《我的世界》")
        self.assertTrue(complete.is_valid, complete.errors)
        self.assertEqual(complete.title, "买《我的世界》")

    def test_25_two_future_12_hour_candidates_require_period_confirmation(self) -> None:
        parser = NaturalLanguageParser(
            now_provider=lambda: datetime(2026, 8, 27, 3, 0)
        )
        parsed = parser.parse("我4点半下课，记得提醒我")
        self.assertFalse(parsed.is_valid)
        self.assertIsNone(parsed.due_at)
        self.assertIn("歧义", "".join(parsed.errors))
        self.assertIn("下午4:30", "".join(parsed.errors))
        self.assertEqual(parsed.title, "下课")
