"""Timer-driven independent reminder scanner with a serial display queue."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import QObject, QTimer, Signal

from models.reminder import Reminder
from models.task import Task
from todo_manager import TodoManager, TodoStorageError


class ReminderManager(QObject):
    """Queue due reminders and persist each one only when it is displayed."""

    CHECK_INTERVAL_MS = 10_000

    reminder_due = Signal(object)
    reminder_ready = Signal(object, object)
    scan_failed = Signal(str)
    waiting_changed = Signal(bool)
    active_invalidated = Signal(str)

    def __init__(
        self,
        todo_manager: TodoManager,
        *,
        interval_ms: int = CHECK_INTERVAL_MS,
        now_provider: Callable[[], datetime] = datetime.now,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._todo_manager = todo_manager
        self._now_provider = now_provider
        self._pending_reminders: deque[tuple[str, str]] = deque()
        self._queued_ids: set[str] = set()
        self._active_reminder_id: str | None = None
        self._active_task_id: str | None = None
        self._waiting_state: bool | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(max(100, int(interval_ms)))
        self._timer.timeout.connect(self.check_now)
        self._todo_manager.subscribe(self._on_tasks_changed)

    def start(self, *, immediate: bool = True) -> None:
        if not self._timer.isActive():
            self._timer.start()
        if immediate:
            QTimer.singleShot(0, self.check_now)
        else:
            self.refresh_waiting_state()

    def stop(self) -> None:
        self._timer.stop()

    def now(self) -> datetime:
        return self._now_provider()

    def check_now(self) -> None:
        now = self._now_provider()
        for task in self._todo_manager.get_tasks():
            if task.completed:
                continue
            for reminder in task.reminders:
                trigger_at = reminder.trigger_datetime
                if (
                    reminder.triggered
                    or reminder.cancelled
                    or reminder.id in self._queued_ids
                    or trigger_at is None
                    or now < trigger_at
                ):
                    continue
                self._pending_reminders.append((task.id, reminder.id))
                self._queued_ids.add(reminder.id)
        self._dispatch_next()
        self.refresh_waiting_state(now=now)

    def _dispatch_next(self) -> None:
        if self._active_reminder_id is not None:
            return
        while self._pending_reminders:
            task_id, reminder_id = self._pending_reminders.popleft()
            task = self._todo_manager.get_task(task_id)
            reminder = task.get_reminder(reminder_id) if task is not None else None
            if (
                task is None
                or task.completed
                or reminder is None
                or reminder.triggered
                or reminder.cancelled
            ):
                self._queued_ids.discard(reminder_id)
                continue
            try:
                updated_task = self._todo_manager.mark_reminder_triggered(
                    task_id,
                    reminder_id,
                )
            except TodoStorageError as exc:
                self._queued_ids.discard(reminder_id)
                self.scan_failed.emit(str(exc))
                continue
            if updated_task is None:
                self._queued_ids.discard(reminder_id)
                continue
            updated_reminder = updated_task.get_reminder(reminder_id)
            if updated_reminder is None:
                self._queued_ids.discard(reminder_id)
                continue
            self._active_reminder_id = reminder_id
            self._active_task_id = task_id
            self.reminder_ready.emit(updated_task, updated_reminder)
            self.reminder_due.emit(updated_task)
            return

    def reminder_finished(self, reminder_id: str) -> None:
        if reminder_id != self._active_reminder_id:
            return
        self._queued_ids.discard(reminder_id)
        self._active_reminder_id = None
        self._active_task_id = None
        self._dispatch_next()
        self.refresh_waiting_state()

    def clear_queue(self) -> None:
        self._pending_reminders.clear()
        self._queued_ids.clear()
        self._active_reminder_id = None
        self._active_task_id = None

    def _on_tasks_changed(self) -> None:
        self._prune_pending()
        invalidated_id: str | None = None
        if self._active_reminder_id is not None:
            task = self._todo_manager.get_task(self._active_task_id or "")
            reminder = (
                task.get_reminder(self._active_reminder_id)
                if task is not None
                else None
            )
            if (
                task is None
                or task.completed
                or reminder is None
                or reminder.cancelled
            ):
                invalidated_id = self._active_reminder_id
                self._queued_ids.discard(invalidated_id)
                self._active_reminder_id = None
                self._active_task_id = None
        self.refresh_waiting_state()
        if invalidated_id is not None:
            self.active_invalidated.emit(invalidated_id)
            self._dispatch_next()

    def _prune_pending(self) -> None:
        now = self._now_provider()
        retained: deque[tuple[str, str]] = deque()
        for task_id, reminder_id in self._pending_reminders:
            task = self._todo_manager.get_task(task_id)
            reminder = task.get_reminder(reminder_id) if task is not None else None
            trigger_at = reminder.trigger_datetime if reminder is not None else None
            if (
                task is not None
                and not task.completed
                and reminder is not None
                and not reminder.triggered
                and not reminder.cancelled
                and trigger_at is not None
                and trigger_at <= now
            ):
                retained.append((task_id, reminder_id))
            else:
                self._queued_ids.discard(reminder_id)
        self._pending_reminders = retained

    def has_future_reminders(self, *, now: datetime | None = None) -> bool:
        current = now or self._now_provider()
        for task in self._todo_manager.get_tasks():
            if task.completed:
                continue
            for reminder in task.reminders:
                trigger_at = reminder.trigger_datetime
                if (
                    not reminder.triggered
                    and not reminder.cancelled
                    and trigger_at is not None
                    and trigger_at > current
                ):
                    return True
        return False

    def refresh_waiting_state(
        self,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> bool:
        waiting = self.has_future_reminders(now=now)
        if force or waiting != self._waiting_state:
            self._waiting_state = waiting
            self.waiting_changed.emit(waiting)
        return waiting

    @property
    def pending_reminders(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._pending_reminders)

    @property
    def active_reminder_id(self) -> str | None:
        return self._active_reminder_id

    @property
    def waiting_state(self) -> bool:
        if self._waiting_state is None:
            return self.refresh_waiting_state()
        return self._waiting_state

    @property
    def is_active(self) -> bool:
        return self._timer.isActive()
