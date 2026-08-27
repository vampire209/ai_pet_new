"""Conservative, deterministic archival policy for non-core active memories."""

from __future__ import annotations

from datetime import datetime

from memory.memory_models import MemoryRecord


AUTO_ARCHIVE_AFTER_DAYS = 90
LOW_IMPORTANCE_MAX = 0.35
ACTIVE_MEMORY_SOFT_LIMIT = 80
SOFT_LIMIT_ARCHIVE_AFTER_DAYS = 60
SOFT_LIMIT_IMPORTANCE_MAX = 0.45


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class MemoryMaintenance:
    """Select records for archive; never permanently delete any record."""

    def select_archive_candidates(
        self,
        memories: tuple[MemoryRecord, ...],
        now: datetime,
    ) -> tuple[str, ...]:
        current = now.replace(tzinfo=None, microsecond=0)
        active = [
            memory
            for memory in memories
            if not memory.is_core and not memory.archived
        ]
        selected: list[MemoryRecord] = []
        for memory in active:
            reference = _parse_time(memory.last_used_at) or _parse_time(
                memory.updated_at
            )
            if reference is None:
                continue
            age_days = (current - reference.replace(tzinfo=None)).days
            if (
                age_days >= AUTO_ARCHIVE_AFTER_DAYS
                and memory.importance <= LOW_IMPORTANCE_MAX
            ):
                selected.append(memory)

        remaining = [memory for memory in active if memory not in selected]
        overflow = max(0, len(remaining) - ACTIVE_MEMORY_SOFT_LIMIT)
        if overflow:
            conservative: list[tuple[datetime, MemoryRecord]] = []
            for memory in remaining:
                reference = _parse_time(memory.last_used_at) or _parse_time(
                    memory.updated_at
                )
                if reference is None:
                    continue
                age_days = (current - reference.replace(tzinfo=None)).days
                if (
                    age_days >= SOFT_LIMIT_ARCHIVE_AFTER_DAYS
                    and memory.importance <= SOFT_LIMIT_IMPORTANCE_MAX
                ):
                    conservative.append((reference, memory))
            conservative.sort(key=lambda item: (item[0], item[1].importance))
            selected.extend(memory for _reference, memory in conservative[:overflow])

        return tuple(memory.id for memory in selected)
