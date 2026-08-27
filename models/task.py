"""Task model with tolerant Phase 3 migration and independent reminders."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Iterable
import uuid

from models.reminder import (
    Reminder,
    ReminderDataError,
    ReminderGenerator,
    normalize_reminder_offsets,
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

    @classmethod
    def create(
        cls,
        text: str,
        event_at: str | None = None,
        reminder_offsets: Iterable[int] | None = None,
        *,
        now: datetime | None = None,
    ) -> "Task":
        normalized_text = text.strip()
        if not normalized_text:
            raise TaskDataError("任务内容不能为空")
        normalized_event = normalize_event_at(event_at, strict=True)
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
            )

        return cls(
            id=task_id or new_task_id(),
            text=raw_text.strip(),
            completed=completed,
            created_at=_optional_iso(data.get("created_at")),
            completed_at=_optional_iso(data.get("completed_at")) if completed else None,
            event_at=event_at,
            reminder_offsets=offsets,
            reminders=reminders,
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
        *,
        now: datetime | None = None,
    ) -> "Task":
        normalized_text = text.strip()
        if not normalized_text:
            raise TaskDataError("任务内容不能为空")
        normalized_event = normalize_event_at(event_at, strict=True)
        offsets = normalize_reminder_offsets(reminder_offsets, strict=True)
        if normalized_event is None:
            offsets = ()
        return replace(
            self,
            text=normalized_text,
            event_at=normalized_event,
            reminder_offsets=offsets,
            reminders=ReminderGenerator.generate(
                normalized_event,
                offsets,
                existing=self.reminders,
                now=now or datetime.now(),
            ),
        )

    def toggled(self) -> "Task":
        if self.completed:
            return replace(self, completed=False, completed_at=None)
        return replace(self, completed=True, completed_at=local_now_iso())

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
        }
