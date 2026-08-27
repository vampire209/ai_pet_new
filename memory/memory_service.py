"""Business rules for core, active, and archived manual memories."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from memory.memory_models import (
    MemoryRecord,
    local_timestamp,
    normalize_memory_content,
)
from memory.memory_store import MemoryStore


class MemoryNotFoundError(LookupError):
    """Raised when a requested memory no longer exists."""


class MemoryStateError(ValueError):
    """Raised when a requested manual state transition is invalid."""


class MemoryService(QObject):
    """Keep state rules out of both SQLite code and the Qt window."""

    memories_changed = Signal()

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        store: MemoryStore | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store or MemoryStore(db_path)

    @property
    def db_path(self) -> Path | None:
        return self.store.db_path

    def create_memory(
        self,
        content: str,
        *,
        now: datetime | None = None,
    ) -> MemoryRecord:
        memory = MemoryRecord.create(content, now=now)
        self.store.insert(memory)
        self.memories_changed.emit()
        return memory

    def get_memory(self, memory_id: str) -> MemoryRecord:
        memory = self.store.get(memory_id)
        if memory is None:
            raise MemoryNotFoundError("这条记忆不存在或已被删除。")
        return memory

    def update_memory(
        self,
        memory_id: str,
        content: str,
        *,
        now: datetime | None = None,
    ) -> MemoryRecord:
        current = self.get_memory(memory_id)
        updated = replace(
            current,
            content=normalize_memory_content(content),
            updated_at=local_timestamp(now),
        )
        self._save_existing(updated)
        return updated

    def delete_memory(self, memory_id: str) -> None:
        if not self.store.delete(memory_id):
            raise MemoryNotFoundError("这条记忆不存在或已被删除。")
        self.memories_changed.emit()

    def set_core(
        self,
        memory_id: str,
        is_core: bool = True,
        *,
        now: datetime | None = None,
    ) -> MemoryRecord:
        current = self.get_memory(memory_id)
        if current.archived and is_core:
            raise MemoryStateError("归档记忆需要先恢复，才能设为核心。")
        updated = replace(
            current,
            is_core=bool(is_core),
            updated_at=local_timestamp(now),
        )
        self._save_existing(updated)
        return updated

    def archive_memory(
        self,
        memory_id: str,
        *,
        now: datetime | None = None,
    ) -> MemoryRecord:
        current = self.get_memory(memory_id)
        updated = replace(
            current,
            is_core=False,
            archived=True,
            updated_at=local_timestamp(now),
        )
        self._save_existing(updated)
        return updated

    def restore_memory(
        self,
        memory_id: str,
        *,
        now: datetime | None = None,
    ) -> MemoryRecord:
        current = self.get_memory(memory_id)
        updated = replace(
            current,
            is_core=False,
            archived=False,
            updated_at=local_timestamp(now),
        )
        self._save_existing(updated)
        return updated

    def core_memories(self) -> tuple[MemoryRecord, ...]:
        return tuple(
            memory
            for memory in self.store.list_memories(archived=False)
            if memory.is_core
        )

    def active_memories(self) -> tuple[MemoryRecord, ...]:
        return tuple(
            memory
            for memory in self.store.list_memories(archived=False)
            if not memory.is_core
        )

    def archived_memories(self) -> tuple[MemoryRecord, ...]:
        return self.store.list_memories(archived=True)

    def all_memories(self) -> tuple[MemoryRecord, ...]:
        return self.store.list_memories()

    def search_memories(
        self,
        query: str,
        *,
        archived: bool | None = None,
    ) -> tuple[MemoryRecord, ...]:
        normalized = str(query or "").strip()
        if not normalized:
            return self.store.list_memories(archived=archived)
        return self.store.search(normalized, archived=archived)

    def _save_existing(self, memory: MemoryRecord) -> None:
        if not self.store.update(memory):
            raise MemoryNotFoundError("这条记忆不存在或已被删除。")
        self.memories_changed.emit()

    def close(self) -> None:
        self.store.close()
