"""Route explicit local chat actions without involving the DeepSeek client."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import re

from models.recurrence import (
    REPEAT_DAILY,
    REPEAT_NONE,
    REPEAT_WEEKLY,
    WEEKDAY_OPTIONS,
)
from models.reminder import format_offset_duration
from natural_language_parser import NaturalLanguageParser, ParsedTask


class ChatActionKind(str, Enum):
    CHAT = "chat"
    TODO_DRAFT = "todo_draft"
    TODO_INCOMPLETE = "todo_incomplete"
    UNSUPPORTED_TODO_OPERATION = "unsupported_todo_operation"


@dataclass(frozen=True, slots=True)
class TaskDraft:
    """Immutable preview created by the Phase 7 parser, not persisted state."""

    original_text: str
    parser_text: str
    title: str
    due_at: datetime
    repeat_type: str
    repeat_weekdays: tuple[int, ...]
    advance_minutes: tuple[int, ...]

    @classmethod
    def from_parsed(
        cls,
        original_text: str,
        parser_text: str,
        parsed: ParsedTask,
    ) -> "TaskDraft":
        if not parsed.is_valid or parsed.due_at is None:
            raise ValueError("只有完整解析结果才能生成待办草案")
        return cls(
            original_text=original_text.strip(),
            parser_text=parser_text,
            title=parsed.title,
            due_at=parsed.due_at.replace(microsecond=0),
            repeat_type=parsed.repeat_type,
            repeat_weekdays=parsed.repeat_weekdays,
            advance_minutes=parsed.advance_minutes,
        )

    @property
    def due_at_iso(self) -> str:
        return self.due_at.isoformat(timespec="seconds")

    @property
    def time_text(self) -> str:
        return self.due_at.strftime("%Y-%m-%d %H:%M")

    @property
    def repeat_text(self) -> str:
        if self.repeat_type == REPEAT_DAILY:
            return "每天"
        if self.repeat_type == REPEAT_WEEKLY:
            labels = dict(WEEKDAY_OPTIONS)
            days = "、".join(
                labels[weekday]
                for weekday in self.repeat_weekdays
                if weekday in labels
            )
            return f"每周 · {days}" if days else "每周"
        return "不重复"

    @property
    def advance_text(self) -> str:
        if not self.advance_minutes:
            return "无"
        return "、".join(
            format_offset_duration(minutes)
            for minutes in self.advance_minutes
        )

    def is_expired(self, now: datetime) -> bool:
        return self.due_at <= now.replace(microsecond=0)

    def controlled_success_message(self) -> str:
        details = [f"已创建待办：{self.title}", self.time_text]
        if self.repeat_type != REPEAT_NONE:
            details.append(self.repeat_text)
        if self.advance_minutes:
            details.append(f"提前提醒：{self.advance_text}")
        return "；".join(details) + "。"


@dataclass(frozen=True, slots=True)
class ChatActionResult:
    kind: ChatActionKind
    user_text: str
    message: str = ""
    draft: TaskDraft | None = None
    parser_errors: tuple[str, ...] = ()

    @property
    def is_local(self) -> bool:
        return self.kind != ChatActionKind.CHAT


_CLOCK_CUE = (
    r"(?:(?:今天|明天|后天|今晚|明晚|凌晨|早上|上午|中午|下午|晚上)\s*)?"
    r"(?:[0-2]?\d|[零〇一二两三四五六七八九十]{1,3})\s*"
    r"(?:点(?:半|[零〇一二两三四五六七八九十\d]{1,3}分?)?|[:：][0-5]?\d)"
)

_TASK_CONTENT_LOOKAHEAD = r"(?=\s*[，,：:]?\s*[^\s，,。！？?!])"
_TIMED_REMINDER_COMMAND = (
    rf"{_CLOCK_CUE}\s*(?:提醒(?:我|一下)?|叫我)"
    rf"{_TASK_CONTENT_LOOKAHEAD}"
)

_INFORMATIONAL_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?:提醒(?:功能)?|备忘录|待办).*(?:是什么|有什么用|怎么用|好用吗)",
        r"(?:你知道|你觉得|请问|介绍一下).*(?:提醒|备忘录|待办)",
    )
)

_CREATE_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"提醒我",
        r"帮我提醒",
        r"请提醒我",
        r"记得提醒我",
        r"别忘了提醒我",
        r"帮我设置(?:一个)?提醒",
        r"给我设置(?:一个)?提醒",
        r"设置(?:一个)?提醒",
        r"帮我设置(?:一个)?(?:备忘录|备忘提醒)",
        r"给我设置(?:一个)?(?:备忘录|备忘提醒)",
        r"帮我记一下",
        r"帮我建(?:个|一个)?(?:待办|备忘录)",
        r"创建(?:一个)?待办",
        r"加(?:一个|个)?待办",
        _TIMED_REMINDER_COMMAND,
    )
)

_NORMALIZATION_PATTERNS = (
    (
        re.compile(
            rf"(?:给我|帮我)\s*设(?:置)?(?:个|一个)?\s*"
            rf"(?P<clock>{_CLOCK_CUE})\s*提醒"
        ),
        r"\g<clock>提醒我",
    ),
    (
        re.compile(rf"记得\s*(?P<clock>{_CLOCK_CUE})\s*提醒"),
        r"\g<clock>提醒我",
    ),
    (
        re.compile(
            rf"(?P<clock>{_CLOCK_CUE})\s*"
            rf"(?:提醒(?:我|一下)?|叫我){_TASK_CONTENT_LOOKAHEAD}"
        ),
        r"\g<clock>提醒我",
    ),
) + tuple(
    (re.compile(pattern), "提醒我")
    for pattern in (
        r"别忘了提醒我",
        r"帮我设置(?:一个)?提醒",
        r"给我设置(?:一个)?提醒",
        r"设置(?:一个)?提醒",
        r"帮我设置(?:一个)?(?:备忘录|备忘提醒)",
        r"给我设置(?:一个)?(?:备忘录|备忘提醒)",
        r"帮我记一下",
        r"帮我建(?:个|一个)?(?:待办|备忘录)",
        r"创建(?:一个)?待办",
        r"加(?:一个|个)?待办",
    )
)

_PROHIBITED_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"(?:把|将).*(?:待办|任务|提醒).*(?:删掉|删除|移除|清空)",
        r"(?:删掉|删除|移除|清空).*(?:待办|任务|提醒)",
        r"(?:把|将).*(?:待办|任务|提醒).*(?:修改|更改|改成|改到)",
        r"(?:修改|更改).*(?:待办|任务|提醒)",
        r"(?:把|将).*(?:待办|任务).*(?:标记完成|设为完成|完成|勾选)",
        r"(?:标记完成|设为完成|勾选).*(?:待办|任务)",
    )
)


class ChatActionRouter:
    """Recognize a deliberately small set of local Todo chat commands."""

    def __init__(self, parser: NaturalLanguageParser | None = None) -> None:
        self.parser = parser or NaturalLanguageParser()

    def now(self) -> datetime:
        return self.parser.now()

    def route(self, text: str) -> ChatActionResult:
        normalized = str(text or "").strip()
        if not normalized:
            return ChatActionResult(ChatActionKind.CHAT, normalized)

        if any(pattern.search(normalized) for pattern in _PROHIBITED_PATTERNS):
            return ChatActionResult(
                ChatActionKind.UNSUPPORTED_TODO_OPERATION,
                normalized,
                "目前我只能在你确认后创建新待办，还不能删除、修改或完成已有待办。你可以在“我的待办”里操作。",
            )

        if any(pattern.search(normalized) for pattern in _INFORMATIONAL_PATTERNS):
            return ChatActionResult(ChatActionKind.CHAT, normalized)

        if not any(pattern.search(normalized) for pattern in _CREATE_PATTERNS):
            return ChatActionResult(ChatActionKind.CHAT, normalized)

        parser_text = self._normalize_for_parser(normalized)
        parsed = self.parser.parse(parser_text)
        if parsed.is_valid:
            draft = TaskDraft.from_parsed(normalized, parser_text, parsed)
            return ChatActionResult(
                ChatActionKind.TODO_DRAFT,
                normalized,
                "好呀，我先帮你整理一下，确认后我再创建。",
                draft=draft,
            )

        errors = parsed.errors or ("没有识别到完整的任务时间。",)
        return ChatActionResult(
            ChatActionKind.TODO_INCOMPLETE,
            normalized,
            self._incomplete_message(errors),
            parser_errors=errors,
        )

    @staticmethod
    def _normalize_for_parser(text: str) -> str:
        normalized = text
        for pattern, replacement in _NORMALIZATION_PATTERNS:
            normalized = pattern.sub(replacement, normalized, count=1)
        return normalized

    @staticmethod
    def _incomplete_message(errors: tuple[str, ...]) -> str:
        combined = " ".join(errors)
        if "缺少具体时间" in combined:
            return "可以呀，不过你想让我几点提醒你？请重新输入包含日期和具体时间的完整句子。"
        if "已经过去" in combined:
            return "这个时间已经过去了，请重新告诉我一个未来的日期和具体时间。"
        return f"我还没能完整理解这个提醒：{errors[0]}请换一种完整说法再试试。"
