"""Validated immutable records shared by manual and chat-created memories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from uuid import uuid4


MEMORY_CONTENT_MAX_LENGTH = 1000
MEMORY_CATEGORY_MAX_LENGTH = 64
MEMORY_TOPIC_KEY_MAX_LENGTH = 160
SOURCE_MANUAL = "manual"
SOURCE_CHAT_AUTO = "chat_auto"


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


def normalize_optional_timestamp(value: object) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise MemoryDataError("可选记忆时间字段无效。")
    normalized = value.strip()
    if not normalized:
        return None
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MemoryDataError("可选记忆时间字段格式无效。") from exc
    return normalized


def _row_value(row: Mapping[str, Any], key: str, default: Any) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: str
    content: str
    is_core: bool
    archived: bool
    created_at: str
    updated_at: str
    category: str = "general"
    importance: float = 0.5
    last_used_at: str | None = None
    use_count: int = 0
    expires_at: str | None = None
    topic_key: str = ""
    source: str = SOURCE_MANUAL

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

        category = str(self.category or "general").strip().lower() or "general"
        if len(category) > MEMORY_CATEGORY_MAX_LENGTH:
            raise MemoryDataError("记忆分类过长。")
        object.__setattr__(self, "category", category)

        try:
            importance = float(self.importance)
        except (TypeError, ValueError) as exc:
            raise MemoryDataError("记忆重要度无效。") from exc
        if not 0.0 <= importance <= 1.0:
            raise MemoryDataError("记忆重要度必须在 0 到 1 之间。")
        object.__setattr__(self, "importance", importance)

        if isinstance(self.use_count, bool):
            raise MemoryDataError("记忆使用次数无效。")
        try:
            use_count = int(self.use_count)
        except (TypeError, ValueError) as exc:
            raise MemoryDataError("记忆使用次数无效。") from exc
        if use_count < 0:
            raise MemoryDataError("记忆使用次数不能为负数。")
        object.__setattr__(self, "use_count", use_count)

        object.__setattr__(
            self,
            "last_used_at",
            normalize_optional_timestamp(self.last_used_at),
        )
        object.__setattr__(
            self,
            "expires_at",
            normalize_optional_timestamp(self.expires_at),
        )

        topic_key = str(self.topic_key or "").strip().lower()
        if len(topic_key) > MEMORY_TOPIC_KEY_MAX_LENGTH:
            raise MemoryDataError("记忆主题标识过长。")
        object.__setattr__(self, "topic_key", topic_key)

        source = str(self.source or SOURCE_MANUAL).strip().lower()
        if source not in {SOURCE_MANUAL, SOURCE_CHAT_AUTO}:
            source = SOURCE_MANUAL
        object.__setattr__(self, "source", source)

    @classmethod
    def create(
        cls,
        content: str,
        *,
        now: datetime | None = None,
        category: str = "general",
        importance: float = 0.5,
        topic_key: str = "",
        source: str = SOURCE_MANUAL,
    ) -> "MemoryRecord":
        timestamp = local_timestamp(now)
        return cls(
            id=f"memory_{uuid4().hex}",
            content=content,
            is_core=False,
            archived=False,
            created_at=timestamp,
            updated_at=timestamp,
            category=category,
            importance=importance,
            topic_key=topic_key,
            source=source,
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
            category=str(_row_value(row, "category", "general") or "general"),
            importance=float(_row_value(row, "importance", 0.5) or 0.0),
            last_used_at=_row_value(row, "last_used_at", None),
            use_count=int(_row_value(row, "use_count", 0) or 0),
            expires_at=_row_value(row, "expires_at", None),
            topic_key=str(_row_value(row, "topic_key", "") or ""),
            source=str(_row_value(row, "source", SOURCE_MANUAL) or SOURCE_MANUAL),
        )
