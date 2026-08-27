"""Task model with tolerant migrations, reminders, and recurrence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Iterable
import uuid

from models.reminder import (
    Reminder,
    ReminderDataError,
    ReminderGenerator,
    SNOOZE_MINUTES,
    normalize_reminder_offsets,
)
from models.recurrence import (
    REPEAT_NONE,
    REPEAT_WEEKLY,
    align_weekly_occurrence,
    next_occurrence,
    normalize_repeat_type,
    normalize_weekdays,
)


class TaskDataError(ValueError):
    """Raised when a record cannot represent even a minimal task."""


def local_now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_task_id() -> str:
    return f"task_{uuid.uuid4().hex}"


def _optional_iso(value: Any) -> str | None:
    parsed = parse_local_datetime(value)
    return parsed.isoformat(timespec="seconds") if parsed is not None else None


def parse_local_datetime(value: Any) -> datetime | None:
    """Parse a naive local ISO value without conversion to or from UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is None else None


def normalize_event_at(value: Any, *, strict: bool = False) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    parsed = parse_local_datetime(value)
    if parsed is None:
        if strict:
            raise TaskDataError("event_at 必须是有效的电脑本地时间")
        return None
    return parsed.isoformat(timespec="seconds")


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return value == 1


@dataclass(frozen=True, slots=True)
class Task:
    """Immutable task state; reminders are persisted as independent records."""

    id: str
    text: str
    completed: bool
    created_at: str | None
    completed_at: str | None
    event_at: str | None
    reminder_offsets: tuple[int, ...]
    reminders: tuple[Reminder, ...]
    repeat_type: str = REPEAT_NONE
    repeat_weekdays: tuple[int, ...] = ()
    last_completed_at: str | None = None

    @classmethod
    def create(
        cls,
        text: str,
        event_at: str | None = None,
        reminder_offsets: Iterable[int] | None = None,
        repeat_type: str = REPEAT_NONE,
        repeat_weekdays: Iterable[int] | None = None,
        *,
        now: datetime | None = None,
    ) -> "Task":
        normalized_text = text.strip()
        if not normalized_text:
            raise TaskDataError("任务内容不能为空")
        normalized_event = normalize_event_at(event_at, strict=True)
        normalized_repeat = normalize_repeat_type(repeat_type, strict=True)
        weekdays = normalize_weekdays(repeat_weekdays, strict=True)
        if normalized_repeat != REPEAT_NONE and normalized_event is None:
            raise TaskDataError("重复任务必须设置日期和时间")
        if normalized_repeat == REPEAT_WEEKLY and not weekdays:
            raise TaskDataError("每周重复至少选择一个星期")
        if normalized_repeat != REPEAT_WEEKLY:
            weekdays = ()
        if normalized_repeat == REPEAT_WEEKLY and normalized_event is not None:
            event = parse_local_datetime(normalized_event)
            if event is not None:
                normalized_event = align_weekly_occurrence(
                    event,
                    weekdays,
                ).isoformat(timespec="seconds")
        offsets = normalize_reminder_offsets(reminder_offsets or (), strict=True)
        if normalized_event is None:
            offsets = ()
        current = now or datetime.now()
        return cls(
            id=new_task_id(),
            text=normalized_text,
            completed=False,
            created_at=current.isoformat(timespec="seconds"),
            completed_at=None,
            event_at=normalized_event,
            reminder_offsets=offsets,
            reminders=ReminderGenerator.generate(
                normalized_event,
                offsets,
                now=current,
            ),
            repeat_type=normalized_repeat,
            repeat_weekdays=weekdays,
            last_completed_at=None,
        )

    @classmethod
    def from_dict(cls, data: Any, *, now: datetime | None = None) -> "Task":
        if not isinstance(data, dict):
            raise TaskDataError("任务记录不是 JSON 对象")
        raw_text = data.get("text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise TaskDataError("任务缺少有效的 text 字段")

        raw_id = data.get("id")
        task_id = raw_id.strip() if isinstance(raw_id, str) else ""
        completed = _as_bool(data.get("completed", False))
        event_at = normalize_event_at(data.get("event_at"))
        repeat_type = normalize_repeat_type(data.get("repeat_type"))
        repeat_weekdays = normalize_weekdays(data.get("repeat_weekdays"))
        if event_at is None:
            repeat_type = REPEAT_NONE
            repeat_weekdays = ()
        elif repeat_type == REPEAT_WEEKLY and not repeat_weekdays:
            parsed_event = parse_local_datetime(event_at)
            repeat_weekdays = (
                (parsed_event.isoweekday(),) if parsed_event is not None else ()
            )
        elif repeat_type != REPEAT_WEEKLY:
            repeat_weekdays = ()
        offsets = normalize_reminder_offsets(data.get("reminder_offsets"))
        if event_at is None:
            offsets = ()

        reminders: tuple[Reminder, ...]
        if "reminders" not in data:
            migrated_final = cls._migrate_phase3_final(data, event_at)
            reminders = ReminderGenerator.generate(
                event_at,
                offsets,
                existing=migrated_final,
                now=now or datetime.now(),
            )
        else:
            parsed_reminders: list[Reminder] = []
            used_ids: set[str] = set()
            raw_reminders = data.get("reminders")
            if isinstance(raw_reminders, list):
                for raw_reminder in raw_reminders:
                    try:
                        reminder = Reminder.from_dict(raw_reminder)
                    except ReminderDataError:
                        continue
                    if reminder.id in used_ids:
                        reminder = replace(reminder, id=f"rem_{uuid.uuid4().hex}")
                    used_ids.add(reminder.id)
                    parsed_reminders.append(reminder)
            reminders = ReminderGenerator.generate(
                event_at,
                offsets,
                existing=parsed_reminders,
                now=now or datetime.now(),
                preserve_snoozes=True,
            )

        last_completed_at = _optional_iso(data.get("last_completed_at"))
        if repeat_type != REPEAT_NONE and completed:
            last_completed_at = last_completed_at or _optional_iso(
                data.get("completed_at")
            )
            completed = False

        return cls(
            id=task_id or new_task_id(),
            text=raw_text.strip(),
            completed=completed,
            created_at=_optional_iso(data.get("created_at")),
            completed_at=_optional_iso(data.get("completed_at")) if completed else None,
            event_at=event_at,
            reminder_offsets=offsets,
            reminders=reminders,
            repeat_type=repeat_type,
            repeat_weekdays=repeat_weekdays,
            last_completed_at=last_completed_at,
        )

    @staticmethod
    def _migrate_phase3_final(data: dict[str, Any], event_at: str | None) -> tuple[Reminder, ...]:
        event = parse_local_datetime(event_at)
        if event is None:
            return ()
        acknowledged = _as_bool(data.get("reminder_acknowledged", False))
        triggered = _as_bool(data.get("reminder_triggered", False)) or acknowledged
        final = Reminder.create("final", 0, event)
        return (replace(final, triggered=triggered, acknowledged=acknowledged),)

    @property
    def current_final_reminder(self) -> Reminder | None:
        if self.event_at is None:
            return None
        return next(
            (
                reminder
                for reminder in self.reminders
                if reminder.type == "final" and reminder.trigger_at == self.event_at
            ),
            None,
        )

    @property
    def reminder_triggered(self) -> bool:
        """Phase 3 compatibility view of the current final reminder."""
        final = self.current_final_reminder
        return final.triggered if final is not None else False

    @property
    def reminder_acknowledged(self) -> bool:
        """Phase 3 compatibility view of the current final reminder."""
        final = self.current_final_reminder
        return final.acknowledged if final is not None else False

    def get_reminder(self, reminder_id: str) -> Reminder | None:
        return next(
            (reminder for reminder in self.reminders if reminder.id == reminder_id),
            None,
        )

    def renamed(self, text: str) -> "Task":
        normalized = text.strip()
        if not normalized:
            raise TaskDataError("任务内容不能为空")
        return replace(self, text=normalized)

    def updated(
        self,
        text: str,
        event_at: str | None,
        reminder_offsets: Iterable[int] = (),
        repeat_type: str | None = None,
        repeat_weekdays: Iterable[int] | None = None,
        *,
        now: datetime | None = None,
    ) -> "Task":
        normalized_text = text.strip()
        if not normalized_text:
            raise TaskDataError("任务内容不能为空")
        normalized_event = normalize_event_at(event_at, strict=True)
        normalized_repeat = normalize_repeat_type(
            self.repeat_type if repeat_type is None else repeat_type,
            strict=True,
        )
        weekdays = normalize_weekdays(
            self.repeat_weekdays if repeat_weekdays is None else repeat_weekdays,
            strict=True,
        )
        if normalized_repeat != REPEAT_NONE and normalized_event is None:
            raise TaskDataError("重复任务必须设置日期和时间")
        if normalized_repeat == REPEAT_WEEKLY and not weekdays:
            raise TaskDataError("每周重复至少选择一个星期")
        if normalized_repeat != REPEAT_WEEKLY:
            weekdays = ()
        if normalized_repeat == REPEAT_WEEKLY and normalized_event is not None:
            event = parse_local_datetime(normalized_event)
            if event is not None:
                normalized_event = align_weekly_occurrence(
                    event,
                    weekdays,
                ).isoformat(timespec="seconds")
        offsets = normalize_reminder_offsets(reminder_offsets, strict=True)
        if normalized_event is None:
            offsets = ()
        return replace(
            self,
            text=normalized_text,
            event_at=normalized_event,
            reminder_offsets=offsets,
            repeat_type=normalized_repeat,
            repeat_weekdays=weekdays,
            reminders=ReminderGenerator.generate(
                normalized_event,
                offsets,
                existing=self.reminders,
                now=now or datetime.now(),
            ),
        )

    def toggled(self, *, now: datetime | None = None) -> "Task":
        if self.completed:
            return replace(self, completed=False, completed_at=None)
        return self.mark_completed(now=now)

    def mark_completed(
        self,
        reminder_id: str | None = None,
        *,
        now: datetime | None = None,
    ) -> "Task":
        if self.completed:
            return self
        completed_time = now or datetime.now()
        if self.repeat_type != REPEAT_NONE:
            target = self.get_reminder(reminder_id) if reminder_id else None
            if target is not None and target.acknowledged:
                return self

            processed = tuple(
                reminder.acknowledge()
                if reminder.id == reminder_id
                else reminder.cancel()
                for reminder in self.reminders
            )
            current_event = parse_local_datetime(self.event_at)
            if current_event is None:
                return replace(
                    self,
                    completed=True,
                    completed_at=completed_time.isoformat(timespec="seconds"),
                    reminders=processed,
                )
            next_event = next_occurrence(
                current_event,
                self.repeat_type,
                self.repeat_weekdays,
                after=completed_time,
            )
            next_event_at = next_event.isoformat(timespec="seconds")
            return replace(
                self,
                completed=False,
                completed_at=None,
                last_completed_at=completed_time.isoformat(timespec="seconds"),
                event_at=next_event_at,
                reminders=ReminderGenerator.generate(
                    next_event_at,
                    self.reminder_offsets,
                    existing=processed,
                    now=completed_time,
                ),
            )
        return replace(
            self,
            completed=True,
            completed_at=completed_time.isoformat(timespec="seconds"),
            reminders=tuple(reminder.cancel() for reminder in self.reminders),
        )

    def with_reminder_triggered(self, reminder_id: str | None = None) -> "Task":
        target_id = reminder_id
        if target_id is None:
            final = self.current_final_reminder
            target_id = final.id if final is not None else None
        if target_id is None:
            return self
        return replace(
            self,
            reminders=tuple(
                reminder.mark_triggered() if reminder.id == target_id else reminder
                for reminder in self.reminders
            ),
        )

    def with_reminder_acknowledged(self, reminder_id: str | None = None) -> "Task":
        target_id = reminder_id
        if target_id is None:
            final = self.current_final_reminder
            target_id = final.id if final is not None else None
        if target_id is None:
            return self
        return replace(
            self,
            reminders=tuple(
                reminder.acknowledge() if reminder.id == target_id else reminder
                for reminder in self.reminders
            ),
        )

    def snoozed(
        self,
        reminder_id: str,
        minutes: int,
        *,
        now: datetime | None = None,
    ) -> "Task":
        if minutes not in SNOOZE_MINUTES:
            raise TaskDataError(f"不支持稍后 {minutes} 分钟")
        current = self.get_reminder(reminder_id)
        if (
            self.completed
            or current is None
            or not current.triggered
            or current.acknowledged
        ):
            return self
        acknowledged = tuple(
            reminder.acknowledge() if reminder.id == reminder_id else reminder
            for reminder in self.reminders
        )
        trigger_at = (now or datetime.now()) + timedelta(minutes=minutes)
        snooze = Reminder.create("snooze", minutes, trigger_at)
        return replace(self, reminders=acknowledged + (snooze,))

    def with_id(self, task_id: str) -> "Task":
        return replace(self, id=task_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "completed": self.completed,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "event_at": self.event_at,
            "reminder_offsets": list(self.reminder_offsets),
            "reminders": [reminder.to_dict() for reminder in self.reminders],
            "repeat_type": self.repeat_type,
            "repeat_weekdays": list(self.repeat_weekdays),
            "last_completed_at": self.last_completed_at,
        }
