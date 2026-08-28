"""Stable local CRUD and JSON persistence for Todo tasks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from datetime import datetime
from typing import Callable, Iterable

from PySide6.QtCore import QStandardPaths

from config import PROJECT_ROOT
from models.task import Task, TaskDataError, new_task_id


_EVENT_UNCHANGED = object()
_OFFSETS_UNCHANGED = object()
_REPEAT_TYPE_UNCHANGED = object()
_REPEAT_WEEKDAYS_UNCHANGED = object()


class TodoStorageError(RuntimeError):
    """A user-visible persistence failure."""


def default_tasks_data_path() -> Path:
    """Keep packaged writes out of PyInstaller's read-only/temporary bundle."""

    if not getattr(sys, "frozen", False):
        return PROJECT_ROOT / "data" / "tasks.json"
    root = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericConfigLocation
    )
    base = Path(root) if root else Path.home() / ".config"
    return base / "ai_pet_new" / "tasks.json"


class TodoManager:
    """Own task state and save each mutation atomically to local JSON."""

    def __init__(self, data_path: Path | str | None = None) -> None:
        self._data_path = (
            Path(data_path) if data_path else default_tasks_data_path()
        )
        self._tasks: list[Task] = []
        self._change_listeners: list[Callable[[], None]] = []
        self._initialize_storage()
        self._load()

    @property
    def data_path(self) -> Path:
        return self._data_path

    def _initialize_storage(self) -> None:
        try:
            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            if not self._data_path.exists():
                self._write_tasks([])
        except OSError as exc:
            raise TodoStorageError(f"无法初始化待办数据文件：{exc}") from exc

    def _load(self) -> None:
        try:
            raw_text = self._data_path.read_text(encoding="utf-8")
        except OSError as exc:
            self._warn(f"无法读取 {self._data_path.name}，本次使用空列表：{exc}")
            self._tasks = []
            return

        if not raw_text.strip():
            self._warn(f"{self._data_path.name} 是空文件，已按空列表处理。")
            self._tasks = []
            try:
                self._write_tasks([])
            except TodoStorageError as exc:
                self._warn(str(exc))
            return

        try:
            records = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            self._warn(f"{self._data_path.name} JSON 损坏，本次跳过全部记录：{exc}")
            self._tasks = []
            return

        if not isinstance(records, list):
            self._warn(f"{self._data_path.name} 顶层不是数组，本次按空列表处理。")
            self._tasks = []
            return

        loaded: list[Task] = []
        used_ids: set[str] = set()
        for index, record in enumerate(records):
            try:
                task = Task.from_dict(record)
            except TaskDataError as exc:
                self._warn(f"跳过第 {index + 1} 条无效任务：{exc}")
                continue

            if task.id in used_ids:
                self._warn(f"第 {index + 1} 条任务 ID 重复，已生成新 ID。")
                task = task.with_id(new_task_id())
            used_ids.add(task.id)
            loaded.append(task)
        self._tasks = loaded

    def _write_tasks(self, tasks: list[Task]) -> None:
        """Write to a sibling temporary file, then replace the JSON atomically."""
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._data_path.with_suffix(self._data_path.suffix + ".tmp")
        try:
            with temporary_path.open("w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    [task.to_dict() for task in tasks],
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temporary_path.replace(self._data_path)
        except OSError as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise TodoStorageError(f"保存待办失败：{exc}") from exc

    @staticmethod
    def _warn(message: str) -> None:
        print(f"[待办数据警告] {message}", file=sys.stderr)

    def get_tasks(self) -> tuple[Task, ...]:
        return tuple(self._tasks)

    def get_task(self, task_id: str) -> Task | None:
        return next((task for task in self._tasks if task.id == task_id), None)

    def subscribe(self, listener: Callable[[], None]) -> None:
        if listener not in self._change_listeners:
            self._change_listeners.append(listener)

    def unsubscribe(self, listener: Callable[[], None]) -> None:
        if listener in self._change_listeners:
            self._change_listeners.remove(listener)

    def _notify_changed(self) -> None:
        for listener in tuple(self._change_listeners):
            try:
                listener()
            except Exception as exc:
                self._warn(f"任务变更监听器执行失败：{exc}")

    def add_task(
        self,
        text: str,
        event_at: str | None = None,
        reminder_offsets: Iterable[int] | None = None,
        repeat_type: str = "none",
        repeat_weekdays: Iterable[int] | None = None,
        *,
        now: datetime | None = None,
    ) -> Task:
        task = Task.create(
            text,
            event_at,
            reminder_offsets,
            repeat_type,
            repeat_weekdays,
            now=now,
        )
        self._tasks.append(task)
        try:
            self._write_tasks(self._tasks)
        except TodoStorageError:
            self._tasks.pop()
            raise
        self._notify_changed()
        return task

    def update_task(
        self,
        task_id: str,
        text: str,
        event_at: str | None | object = _EVENT_UNCHANGED,
        reminder_offsets: Iterable[int] | object = _OFFSETS_UNCHANGED,
        repeat_type: str | object = _REPEAT_TYPE_UNCHANGED,
        repeat_weekdays: Iterable[int] | object = _REPEAT_WEEKDAYS_UNCHANGED,
        *,
        now: datetime | None = None,
    ) -> Task | None:
        index = self._index_of(task_id)
        if index is None:
            return None
        previous = self._tasks[index]
        if (
            event_at is _EVENT_UNCHANGED
            and reminder_offsets is _OFFSETS_UNCHANGED
            and repeat_type is _REPEAT_TYPE_UNCHANGED
            and repeat_weekdays is _REPEAT_WEEKDAYS_UNCHANGED
        ):
            updated = previous.renamed(text)
        else:
            next_event = previous.event_at if event_at is _EVENT_UNCHANGED else event_at
            next_offsets = (
                previous.reminder_offsets
                if reminder_offsets is _OFFSETS_UNCHANGED
                else reminder_offsets
            )
            next_repeat_type = (
                previous.repeat_type
                if repeat_type is _REPEAT_TYPE_UNCHANGED
                else repeat_type
            )
            next_repeat_weekdays = (
                previous.repeat_weekdays
                if repeat_weekdays is _REPEAT_WEEKDAYS_UNCHANGED
                else repeat_weekdays
            )
            updated = previous.updated(
                text,
                next_event,
                next_offsets,
                next_repeat_type,
                next_repeat_weekdays,
                now=now,
            )
        self._tasks[index] = updated
        try:
            self._write_tasks(self._tasks)
        except TodoStorageError:
            self._tasks[index] = previous
            raise
        self._notify_changed()
        return updated

    def toggle_complete(
        self,
        task_id: str,
        *,
        now: datetime | None = None,
    ) -> Task | None:
        index = self._index_of(task_id)
        if index is None:
            return None
        previous = self._tasks[index]
        updated = previous.toggled(now=now)
        self._tasks[index] = updated
        try:
            self._write_tasks(self._tasks)
        except TodoStorageError:
            self._tasks[index] = previous
            raise
        self._notify_changed()
        return updated

    def complete_task(
        self,
        task_id: str,
        reminder_id: str | None = None,
        *,
        now: datetime | None = None,
    ) -> Task | None:
        return self._replace_task(
            task_id,
            lambda task: task.mark_completed(reminder_id, now=now),
        )

    def delete_task(self, task_id: str) -> bool:
        index = self._index_of(task_id)
        if index is None:
            return False
        removed = self._tasks.pop(index)
        try:
            self._write_tasks(self._tasks)
        except TodoStorageError:
            self._tasks.insert(index, removed)
            raise
        self._notify_changed()
        return True

    def mark_reminder_triggered(
        self,
        task_id: str,
        reminder_id: str | None = None,
    ) -> Task | None:
        return self._replace_task(
            task_id,
            lambda task: task.with_reminder_triggered(reminder_id),
        )

    def acknowledge_reminder(
        self,
        task_id: str,
        reminder_id: str | None = None,
    ) -> Task | None:
        return self._replace_task(
            task_id,
            lambda task: task.with_reminder_acknowledged(reminder_id),
        )

    def snooze_reminder(
        self,
        task_id: str,
        reminder_id: str,
        minutes: int,
        *,
        now: datetime | None = None,
    ) -> Task | None:
        return self._replace_task(
            task_id,
            lambda task: task.snoozed(reminder_id, minutes, now=now),
        )

    def _replace_task(self, task_id: str, transform) -> Task | None:
        index = self._index_of(task_id)
        if index is None:
            return None
        previous = self._tasks[index]
        updated = transform(previous)
        if updated == previous:
            return previous
        self._tasks[index] = updated
        try:
            self._write_tasks(self._tasks)
        except TodoStorageError:
            self._tasks[index] = previous
            raise
        self._notify_changed()
        return updated

    def _index_of(self, task_id: str) -> int | None:
        for index, task in enumerate(self._tasks):
            if task.id == task_id:
                return index
        return None
