"""Deterministic local Chinese rules for quickly creating Todo tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import re
import unicodedata
from collections.abc import Callable

from models.recurrence import REPEAT_DAILY, REPEAT_NONE, REPEAT_WEEKLY
from models.reminder import SUPPORTED_OFFSETS


_NUMBER_TOKEN = r"(?:\d+|一个|[零〇一二两三四五六七八九十]+)"
_WEEKDAY_VALUES = {
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "日": 7,
    "天": 7,
}

_WEEKLY_PATTERN = re.compile(
    r"每(?:周|星期)\s*"
    r"(?P<days>(?:(?:周|星期)?[一二三四五六日天]\s*[、,，和及]?\s*)+)"
)
_ADVANCE_ITEM_PATTERN = rf"(?:半\s*小时|{_NUMBER_TOKEN}\s*(?:分钟|小时))"
_ADVANCE_CLAUSE_PATTERN = re.compile(
    rf"提前\s*{_ADVANCE_ITEM_PATTERN}"
    rf"(?:(?:\s*[、,，和及]\s*){_ADVANCE_ITEM_PATTERN})*\s*(?:提醒)?"
)
_EXPLICIT_NUMERIC_DATE = re.compile(
    r"(?<!\d)(?P<year>\d{4})[-/](?P<month>\d{1,2})[-/](?P<day>\d{1,2})(?!\d)"
)
_EXPLICIT_CHINESE_DATE = re.compile(
    r"(?<!\d)(?P<year>\d{4})年\s*(?P<month>\d{1,2})月\s*"
    r"(?P<day>\d{1,2})(?:日|号)?"
)
_MONTH_DAY_PATTERN = re.compile(
    r"(?<![\d年])(?P<month>\d{1,2})月\s*(?P<day>\d{1,2})(?:日|号)"
)
_NEXT_WEEK_PATTERN = re.compile(r"下(?:周|星期)\s*([一二三四五六日天])")
_WEEKDAY_PATTERN = re.compile(r"(?:周|星期)\s*([一二三四五六日天])")
_COLON_TIME_PATTERN = re.compile(
    r"(?<!\d)(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)(?!\d)"
)
_POINT_TIME_PATTERN = re.compile(
    rf"(?P<hour>{_NUMBER_TOKEN})\s*点"
    rf"(?:\s*(?P<half>半)|\s*(?P<minute>{_NUMBER_TOKEN})\s*分?)?"
)
_RELATIVE_HALF_HOUR = re.compile(r"半\s*小时\s*后")
_RELATIVE_MINUTES = re.compile(rf"(?P<value>{_NUMBER_TOKEN})\s*分钟\s*后")
_RELATIVE_HOURS = re.compile(rf"(?P<value>{_NUMBER_TOKEN})\s*小时\s*后")


@dataclass(frozen=True, slots=True)
class ParsedTask:
    """Structured parser output without any UI or persistence behavior."""

    original_text: str
    title: str
    due_at: datetime | None
    repeat_type: str = REPEAT_NONE
    repeat_weekdays: tuple[int, ...] = ()
    advance_minutes: tuple[int, ...] = ()
    confidence: float = 0.0
    warnings: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return bool(self.title and self.due_at is not None and not self.errors)

    @property
    def due_at_iso(self) -> str | None:
        if self.due_at is None:
            return None
        return self.due_at.isoformat(timespec="seconds")


class NaturalLanguageParser:
    """Parse the documented Phase 7 subset using only local regular rules."""

    def __init__(
        self,
        now_provider: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._now_provider = now_provider

    def now(self) -> datetime:
        """Return the injected naive local clock used by parsing and creation."""

        return self._now_provider().replace(microsecond=0)

    def parse(self, text: str, *, now: datetime | None = None) -> ParsedTask:
        source = _normalize_text(text)
        current = (now or self.now()).replace(microsecond=0)
        if not source:
            return ParsedTask(
                original_text=source,
                title="",
                due_at=None,
                errors=("请输入需要创建的待办。",),
            )

        errors: list[str] = []
        warnings: list[str] = []
        repeat_type, repeat_weekdays = self._parse_recurrence(source, errors)
        advance_minutes = self._parse_advance(source, errors)
        relative_due = self._parse_relative_duration(source, current, errors)
        time_result = self._parse_clock_time(source, errors)
        due_at = relative_due

        if due_at is None:
            due_at = self._parse_calendar_due(
                source,
                current,
                time_result,
                repeat_type,
                repeat_weekdays,
                errors,
            )

        title = self._extract_title(source)
        if not title:
            errors.append("没有识别到任务内容。")

        unique_errors = tuple(dict.fromkeys(errors))
        return ParsedTask(
            original_text=source,
            title=title,
            due_at=due_at,
            repeat_type=repeat_type,
            repeat_weekdays=repeat_weekdays,
            advance_minutes=advance_minutes,
            confidence=1.0 if title and due_at is not None and not unique_errors else 0.0,
            warnings=tuple(warnings),
            errors=unique_errors,
        )

    @staticmethod
    def _parse_recurrence(
        source: str,
        errors: list[str],
    ) -> tuple[str, tuple[int, ...]]:
        weekly = _WEEKLY_PATTERN.search(source)
        if weekly is not None:
            weekdays = tuple(
                sorted(
                    {
                        _WEEKDAY_VALUES[token]
                        for token in re.findall(r"[一二三四五六日天]", weekly.group("days"))
                    }
                )
            )
            if not weekdays:
                errors.append("每周重复至少需要一个星期。")
            return REPEAT_WEEKLY, weekdays
        if re.search(r"(?:每天|每日)", source):
            return REPEAT_DAILY, ()
        return REPEAT_NONE, ()

    @staticmethod
    def _parse_advance(source: str, errors: list[str]) -> tuple[int, ...]:
        values: set[int] = set()
        for clause in _ADVANCE_CLAUSE_PATTERN.finditer(source):
            for item in re.finditer(_ADVANCE_ITEM_PATTERN, clause.group(0)):
                token = re.sub(r"\s+", "", item.group(0))
                if token == "半小时":
                    minutes = 30
                elif token.endswith("小时"):
                    number_text = token[:-2]
                    minutes = _parse_number(number_text) * 60
                else:
                    number_text = token[:-2]
                    minutes = _parse_number(number_text)
                if minutes in SUPPORTED_OFFSETS:
                    values.add(minutes)
                else:
                    errors.append(f"暂不支持提前 {minutes} 分钟。")
        return tuple(sorted(values, reverse=True))

    @staticmethod
    def _parse_relative_duration(
        source: str,
        current: datetime,
        errors: list[str],
    ) -> datetime | None:
        half_match = _RELATIVE_HALF_HOUR.search(source)
        if half_match is not None:
            return current + timedelta(minutes=30)

        minute_match = _RELATIVE_MINUTES.search(source)
        if minute_match is not None:
            try:
                minutes = _parse_number(minute_match.group("value"))
            except ValueError:
                errors.append("相对分钟表达无效。")
                return None
            if minutes <= 0:
                errors.append("相对时间必须晚于当前时间。")
                return None
            return current + timedelta(minutes=minutes)

        hour_match = _RELATIVE_HOURS.search(source)
        if hour_match is not None:
            try:
                hours = _parse_number(hour_match.group("value"))
            except ValueError:
                errors.append("相对小时表达无效。")
                return None
            if hours <= 0:
                errors.append("相对时间必须晚于当前时间。")
                return None
            return current + timedelta(hours=hours)
        return None

    @staticmethod
    def _parse_clock_time(
        source: str,
        errors: list[str],
    ) -> time | None:
        period_match = re.search(r"明晚|今晚|早上|上午|中午|下午|晚上", source)
        period = period_match.group(0) if period_match is not None else ""

        colon = _COLON_TIME_PATTERN.search(source)
        if colon is not None:
            hour = int(colon.group("hour"))
            minute = int(colon.group("minute"))
        else:
            point = _POINT_TIME_PATTERN.search(source)
            if point is None:
                return None
            try:
                hour = _parse_number(point.group("hour"))
                if point.group("half"):
                    minute = 30
                elif point.group("minute"):
                    minute = _parse_number(point.group("minute"))
                else:
                    minute = 0
            except ValueError:
                errors.append("时间表达不明确，请补充具体时间。")
                return None

        if period in {"下午", "晚上", "明晚", "今晚"} and hour < 12:
            hour += 12
        elif period in {"早上", "上午"} and hour == 12:
            hour = 0
        elif period == "中午" and hour < 11:
            hour += 12

        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            errors.append("时间表达不明确，请补充有效的具体时间。")
            return None
        return time(hour, minute)

    def _parse_calendar_due(
        self,
        source: str,
        current: datetime,
        clock_time: time | None,
        repeat_type: str,
        repeat_weekdays: tuple[int, ...],
        errors: list[str],
    ) -> datetime | None:
        date_info = self._parse_date_info(source)
        has_date_expression = date_info is not None or bool(
            re.search(
                r"今天|明天|后天|今晚|明晚|下周|下星期|周[一二三四五六日天]|星期[一二三四五六日天]",
                source,
            )
        )
        has_period_expression = bool(
            re.search(r"早上|上午|中午|下午|晚上|今晚|明晚", source)
        )

        if clock_time is None:
            if has_date_expression or has_period_expression or repeat_type != REPEAT_NONE:
                errors.append("解析不完整：缺少具体时间。")
            else:
                errors.append("暂不支持该时间表达。")
            return None

        try:
            due, rolls_forward = self._resolve_date(
                source,
                current,
                clock_time,
                repeat_type,
                repeat_weekdays,
                date_info,
            )
        except ValueError:
            errors.append("日期表达无效，请重新指定日期。")
            return None

        if due <= current and not rolls_forward:
            errors.append("该时间已经过去，请重新指定时间。")
            return None
        return due

    def _resolve_date(
        self,
        source: str,
        current: datetime,
        clock_time: time,
        repeat_type: str,
        repeat_weekdays: tuple[int, ...],
        date_info: tuple[str, tuple[int, ...]] | None,
    ) -> tuple[datetime, bool]:
        if date_info is not None:
            kind, values = date_info
            if kind == "explicit":
                year, month, day = values
                return datetime.combine(date(year, month, day), clock_time), False
            if kind == "month_day":
                month, day = values
                candidate = datetime.combine(date(current.year, month, day), clock_time)
                if candidate <= current:
                    candidate = datetime.combine(
                        date(current.year + 1, month, day),
                        clock_time,
                    )
                return candidate, True
            if kind == "relative":
                day_offset = values[0]
                return datetime.combine(
                    current.date() + timedelta(days=day_offset),
                    clock_time,
                ), False
            if kind == "next_weekday":
                weekday = values[0]
                monday = current.date() - timedelta(days=current.isoweekday() - 1)
                target = monday + timedelta(days=7 + weekday - 1)
                return datetime.combine(target, clock_time), True
            if kind == "weekday":
                weekday = values[0]
                return self._next_weekday_due(current, clock_time, (weekday,)), True

        if repeat_type == REPEAT_WEEKLY:
            return self._next_weekday_due(
                current,
                clock_time,
                repeat_weekdays,
            ), True
        if repeat_type == REPEAT_DAILY:
            candidate = datetime.combine(current.date(), clock_time)
            if candidate <= current:
                candidate += timedelta(days=1)
            return candidate, True

        candidate = datetime.combine(current.date(), clock_time)
        return candidate, False

    @staticmethod
    def _parse_date_info(source: str) -> tuple[str, tuple[int, ...]] | None:
        numeric = _EXPLICIT_NUMERIC_DATE.search(source)
        if numeric is not None:
            return "explicit", (
                int(numeric.group("year")),
                int(numeric.group("month")),
                int(numeric.group("day")),
            )
        chinese = _EXPLICIT_CHINESE_DATE.search(source)
        if chinese is not None:
            return "explicit", (
                int(chinese.group("year")),
                int(chinese.group("month")),
                int(chinese.group("day")),
            )
        month_day = _MONTH_DAY_PATTERN.search(source)
        if month_day is not None:
            return "month_day", (
                int(month_day.group("month")),
                int(month_day.group("day")),
            )
        if "后天" in source:
            return "relative", (2,)
        if "明天" in source or "明晚" in source:
            return "relative", (1,)
        if "今天" in source or "今晚" in source:
            return "relative", (0,)
        next_week = _NEXT_WEEK_PATTERN.search(source)
        if next_week is not None:
            return "next_weekday", (_WEEKDAY_VALUES[next_week.group(1)],)
        if _WEEKLY_PATTERN.search(source) is None:
            weekday = _WEEKDAY_PATTERN.search(source)
            if weekday is not None:
                return "weekday", (_WEEKDAY_VALUES[weekday.group(1)],)
        return None

    @staticmethod
    def _next_weekday_due(
        current: datetime,
        clock_time: time,
        weekdays: tuple[int, ...],
    ) -> datetime:
        for day_offset in range(8):
            candidate_date = current.date() + timedelta(days=day_offset)
            candidate = datetime.combine(candidate_date, clock_time)
            if candidate.isoweekday() in weekdays and candidate > current:
                return candidate
        raise ValueError("No future weekday")

    @staticmethod
    def _extract_title(source: str) -> str:
        cleaned = source
        patterns = (
            _ADVANCE_CLAUSE_PATTERN,
            _RELATIVE_HALF_HOUR,
            _RELATIVE_MINUTES,
            _RELATIVE_HOURS,
            _EXPLICIT_NUMERIC_DATE,
            _EXPLICIT_CHINESE_DATE,
            _MONTH_DAY_PATTERN,
            _NEXT_WEEK_PATTERN,
            _WEEKLY_PATTERN,
            _WEEKDAY_PATTERN,
            _COLON_TIME_PATTERN,
            _POINT_TIME_PATTERN,
            re.compile(r"每天|每日"),
            re.compile(r"今天|明天|后天|今晚|明晚"),
            re.compile(r"早上|上午|中午|下午|晚上"),
            re.compile(r"记得提醒我|帮我提醒|请提醒我|提醒一下|提醒我"),
        )
        for pattern in patterns:
            cleaned = pattern.sub(" ", cleaned)
        cleaned = re.sub(r"[，,。；;：:、]+", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned.strip(" -—")


def parse_task_text(
    text: str,
    *,
    now: datetime | None = None,
) -> ParsedTask:
    """Convenience entry point for callers that do not need a parser object."""

    return NaturalLanguageParser().parse(text, now=now)


def _normalize_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    return re.sub(r"\s+", " ", normalized).strip()


def _parse_number(value: str) -> int:
    token = re.sub(r"\s+", "", value)
    if token == "一个":
        return 1
    if token.isdigit():
        return int(token)
    digit_values = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if "十" in token:
        left, right = token.split("十", 1)
        tens = digit_values.get(left, 1) if left else 1
        ones = digit_values.get(right, 0) if right else 0
        return tens * 10 + ones
    if len(token) == 1 and token in digit_values:
        return digit_values[token]
    if token and all(character in digit_values for character in token):
        return int("".join(str(digit_values[character]) for character in token))
    raise ValueError(f"Unsupported number: {value}")
