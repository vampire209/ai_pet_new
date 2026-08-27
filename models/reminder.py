"""Independent local reminder records and deterministic generation rules."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Iterable
import uuid


REMINDER_OFFSET_OPTIONS: tuple[tuple[int, str], ...] = (
    (5, "提前5分钟"),
    (30, "提前30分钟"),
    (60, "提前1小时"),
    (180, "提前3小时"),
    (720, "提前12小时"),
    (1440, "提前1天"),
    (2880, "提前2天"),
    (4320, "提前3天"),
    (10080, "提前1周"),
)
SUPPORTED_OFFSETS = tuple(value for value, _label in REMINDER_OFFSET_OPTIONS)
OFFSET_LABELS = dict(REMINDER_OFFSET_OPTIONS)
SNOOZE_MINUTES: tuple[int, ...] = (5, 10, 30, 60)


class ReminderDataError(ValueError):
    """Raised when one reminder record cannot be recovered safely."""


def new_reminder_id() -> str:
    return f"rem_{uuid.uuid4().hex}"


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return value == 1


def _parse_local(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is None else None


def normalize_reminder_offsets(values: Any, *, strict: bool = False) -> tuple[int, ...]:
    """Return supported, unique offsets in stable UI order."""
    if values is None:
        return ()
    if not isinstance(values, (list, tuple, set)):
        if strict:
            raise ReminderDataError("reminder_offsets 必须是列表")
        return ()
    selected: set[int] = set()
    for raw_value in values:
        if isinstance(raw_value, bool):
            continue
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            continue
        if value in SUPPORTED_OFFSETS:
            selected.add(value)
        elif strict:
            raise ReminderDataError(f"不支持的提前提醒分钟数：{value}")
    return tuple(value for value in SUPPORTED_OFFSETS if value in selected)


def format_offset_duration(offset_minutes: int) -> str:
    labels = {
        5: "5分钟",
        10: "10分钟",
        30: "30分钟",
        60: "1小时",
        180: "3小时",
        720: "12小时",
        1440: "1天",
        2880: "2天",
        4320: "3天",
        10080: "1周",
    }
    return labels.get(offset_minutes, f"{offset_minutes}分钟")


@dataclass(frozen=True, slots=True)
class Reminder:
    """One independently persisted advance, final, or snooze reminder."""

    id: str
    type: str
    offset_minutes: int
    trigger_at: str
    triggered: bool
    acknowledged: bool
    cancelled: bool = False

    @property
    def trigger_datetime(self) -> datetime | None:
        return _parse_local(self.trigger_at)

    @property
    def identity(self) -> tuple[str, int, str]:
        return self.type, self.offset_minutes, self.trigger_at

    @classmethod
    def create(cls, reminder_type: str, offset_minutes: int, trigger_at: datetime) -> "Reminder":
        return cls(
            id=new_reminder_id(),
            type=reminder_type,
            offset_minutes=offset_minutes,
            trigger_at=trigger_at.isoformat(timespec="seconds"),
            triggered=False,
            acknowledged=False,
            cancelled=False,
        )

    @classmethod
    def from_dict(cls, data: Any) -> "Reminder":
        if not isinstance(data, dict):
            raise ReminderDataError("提醒记录不是 JSON 对象")
        reminder_type = data.get("type")
        if reminder_type not in {"advance", "final", "snooze"}:
            raise ReminderDataError("提醒 type 无效")
        try:
            offset = int(data.get("offset_minutes", 0))
        except (TypeError, ValueError) as exc:
            raise ReminderDataError("提醒 offset_minutes 无效") from exc
        if reminder_type == "final":
            offset = 0
        elif reminder_type == "advance" and offset not in SUPPORTED_OFFSETS:
            raise ReminderDataError("提前提醒 offset_minutes 不受支持")
        elif reminder_type == "snooze" and offset not in SNOOZE_MINUTES:
            raise ReminderDataError("稍后提醒 offset_minutes 不受支持")
        trigger = _parse_local(data.get("trigger_at"))
        if trigger is None:
            raise ReminderDataError("提醒 trigger_at 无效")
        raw_id = data.get("id")
        reminder_id = raw_id.strip() if isinstance(raw_id, str) else ""
        acknowledged = _as_bool(data.get("acknowledged", False))
        triggered = _as_bool(data.get("triggered", False)) or acknowledged
        cancelled = _as_bool(data.get("cancelled", False))
        return cls(
            id=reminder_id or new_reminder_id(),
            type=reminder_type,
            offset_minutes=offset,
            trigger_at=trigger.isoformat(timespec="seconds"),
            triggered=triggered,
            acknowledged=acknowledged,
            cancelled=cancelled,
        )

    def mark_triggered(self) -> "Reminder":
        if self.triggered or self.cancelled:
            return self
        return replace(self, triggered=True, acknowledged=False)

    def acknowledge(self) -> "Reminder":
        if not self.triggered or self.acknowledged:
            return self
        return replace(self, acknowledged=True)

    def cancel(self) -> "Reminder":
        if self.triggered or self.cancelled:
            return self
        return replace(self, cancelled=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "offset_minutes": self.offset_minutes,
            "trigger_at": self.trigger_at,
            "triggered": self.triggered,
            "acknowledged": self.acknowledged,
            "cancelled": self.cancelled,
        }


class ReminderGenerator:
    """Build configured reminders while retaining already-triggered history."""

    @staticmethod
    def generate(
        event_at: str | None,
        reminder_offsets: Iterable[int],
        *,
        existing: Iterable[Reminder] = (),
        now: datetime | None = None,
        preserve_snoozes: bool = False,
    ) -> tuple[Reminder, ...]:
        existing_items = tuple(existing)
        triggered_history = [item for item in existing_items if item.triggered]
        pending_snoozes = [
            item
            for item in existing_items
            if item.type == "snooze" and not item.triggered
        ] if preserve_snoozes else []
        event = _parse_local(event_at)
        if event is None:
            return tuple(triggered_history + pending_snoozes)

        specifications: list[tuple[str, int, datetime]] = []
        for offset in sorted(normalize_reminder_offsets(tuple(reminder_offsets)), reverse=True):
            trigger = event - timedelta(minutes=offset)
            if now is not None and trigger <= now:
                continue
            specifications.append(("advance", offset, trigger))
        specifications.append(("final", 0, event))

        by_identity: dict[tuple[str, int, str], list[Reminder]] = {}
        for reminder in existing_items:
            by_identity.setdefault(reminder.identity, []).append(reminder)

        configured: list[Reminder] = []
        configured_ids: set[str] = set()
        for reminder_type, offset, trigger in specifications:
            trigger_iso = trigger.isoformat(timespec="seconds")
            identity = reminder_type, offset, trigger_iso
            candidates = by_identity.get(identity, [])
            reminder = candidates[0] if candidates else Reminder.create(reminder_type, offset, trigger)
            configured.append(reminder)
            configured_ids.add(reminder.id)

        orphaned_history = [
            item for item in triggered_history if item.id not in configured_ids
        ]
        return tuple(orphaned_history + pending_snoozes + configured)
