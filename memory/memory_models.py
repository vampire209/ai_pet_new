"""Validated immutable records for manually managed long-term memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from uuid import uuid4


MEMORY_CONTENT_MAX_LENGTH = 1000


class MemoryDataError(ValueError):
    """Raised when memory content or persisted fields are invalid."""


def normalize_memory_content(value: object) -> str:
    if not isinstance(value, str):
        raise MemoryDataError("记忆内容必须是文字。")
    normalized = value.strip()
    if not normalized:
        raise MemoryDataError("记忆内容不能为空。")
    if len(normalized) > MEMORY_CONTENT_MAX_LENGTH:
        raise MemoryDataError(
            f"记忆内容最多 {MEMORY_CONTENT_MAX_LENGTH} 个字符。"
        )
    return normalized


def local_timestamp(now: datetime | None = None) -> str:
    current = now or datetime.now()
    if current.tzinfo is not None:
        current = current.replace(tzinfo=None)
    return current.replace(microsecond=0).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: str
    content: str
    is_core: bool
    archived: bool
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise MemoryDataError("记忆 ID 无效。")
        object.__setattr__(self, "content", normalize_memory_content(self.content))
        if not isinstance(self.is_core, bool) or not isinstance(self.archived, bool):
            raise MemoryDataError("记忆状态无效。")
        if self.archived and self.is_core:
            raise MemoryDataError("归档记忆不能同时是核心记忆。")
        for field in ("created_at", "updated_at"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise MemoryDataError("记忆时间字段无效。")
            try:
                datetime.fromisoformat(value)
            except ValueError as exc:
                raise MemoryDataError("记忆时间字段格式无效。") from exc

    @classmethod
    def create(
        cls,
        content: str,
        *,
        now: datetime | None = None,
    ) -> "MemoryRecord":
        timestamp = local_timestamp(now)
        return cls(
            id=f"memory_{uuid4().hex}",
            content=content,
            is_core=False,
            archived=False,
            created_at=timestamp,
            updated_at=timestamp,
        )
    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "MemoryRecord":
        return cls(
            id=str(row["id"]),
            content=str(row["content"]),
            is_core=bool(row["is_core"]),
            archived=bool(row["archived"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
