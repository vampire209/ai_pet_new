"""Recurrence helpers shared by the task model and the Todo UI."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable


REPEAT_NONE = "none"
REPEAT_DAILY = "daily"
REPEAT_WEEKLY = "weekly"
REPEAT_TYPES = (REPEAT_NONE, REPEAT_DAILY, REPEAT_WEEKLY)

REPEAT_OPTIONS = (
    (REPEAT_NONE, "不重复"),
    (REPEAT_DAILY, "每天"),
    (REPEAT_WEEKLY, "每周"),
)

WEEKDAY_OPTIONS = (
    (1, "周一"),
    (2, "周二"),
    (3, "周三"),
    (4, "周四"),
    (5, "周五"),
    (6, "周六"),
    (7, "周日"),
)


class RecurrenceDataError(ValueError):
    """Raised when recurrence settings cannot form a valid schedule."""


def normalize_repeat_type(value: object, *, strict: bool = False) -> str:
    normalized = str(value or REPEAT_NONE).strip().lower()
    if normalized in REPEAT_TYPES:
        return normalized
    if strict:
        raise RecurrenceDataError(f"Unsupported repeat type: {value!r}")
    return REPEAT_NONE


def normalize_weekdays(
    values: Iterable[object] | None,
    *,
    strict: bool = False,
) -> tuple[int, ...]:
    if values is None:
        return ()
    if not isinstance(values, (list, tuple, set)):
        if strict:
            raise RecurrenceDataError("Weekdays must be a list")
        return ()
    weekdays: set[int] = set()
    for value in values:
        try:
            weekday = int(value)
        except (TypeError, ValueError):
            if strict:
                raise RecurrenceDataError(f"Invalid weekday: {value!r}") from None
            continue
        if 1 <= weekday <= 7:
            weekdays.add(weekday)
        elif strict:
            raise RecurrenceDataError(f"Weekday must be between 1 and 7: {weekday}")
    return tuple(sorted(weekdays))


def align_weekly_occurrence(
    anchor: datetime,
    weekdays: Iterable[object],
) -> datetime:
    """Return the first selected weekday on or after ``anchor``."""

    normalized = normalize_weekdays(weekdays, strict=True)
    if not normalized:
        raise RecurrenceDataError("Weekly recurrence needs at least one weekday")
    for day_offset in range(7):
        candidate = anchor + timedelta(days=day_offset)
        if candidate.isoweekday() in normalized:
            return candidate
    raise RecurrenceDataError("Unable to align weekly occurrence")


def next_occurrence(
    current: datetime,
    repeat_type: object,
    weekdays: Iterable[object] | None = None,
    *,
    after: datetime | None = None,
) -> datetime:
    """Calculate the next scheduled occurrence strictly after ``after``.

    ``current`` supplies the stable wall-clock time. Advancing from this value
    keeps snooze operations from shifting the underlying schedule.
    """

    normalized_type = normalize_repeat_type(repeat_type, strict=True)
    threshold = after if after is not None else current

    if normalized_type == REPEAT_DAILY:
        candidate = current + timedelta(days=1)
        while candidate <= threshold:
            candidate += timedelta(days=1)
        return candidate

    if normalized_type == REPEAT_WEEKLY:
        normalized_days = normalize_weekdays(weekdays, strict=True)
        if not normalized_days:
            raise RecurrenceDataError("Weekly recurrence needs at least one weekday")

        candidate_date = current.date() + timedelta(days=1)
        if candidate_date < threshold.date():
            candidate_date = threshold.date()
        for day_offset in range(15):
            date_value = candidate_date + timedelta(days=day_offset)
            candidate = datetime.combine(date_value, current.time())
            if candidate.isoweekday() in normalized_days and candidate > threshold:
                return candidate
        raise RecurrenceDataError("Unable to calculate next weekly occurrence")

    raise RecurrenceDataError("One-time tasks do not have a next occurrence")
