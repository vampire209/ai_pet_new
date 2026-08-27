"""Business rules for manual and conservative chat-created memories."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
import logging
import re

from PySide6.QtCore import QObject, Signal

from memory.memory_candidate import MemoryCandidate
from memory.memory_extractor import MemoryExtractor, infer_memory_metadata
from memory.memory_maintenance import MemoryMaintenance
from memory.memory_models import (
    MemoryRecord,
    SOURCE_CHAT_AUTO,
    SOURCE_MANUAL,
    local_timestamp,
    normalize_memory_content,
)
from memory.memory_store import MemoryStore


LOGGER = logging.getLogger(__name__)


class MemoryNotFoundError(LookupError):
    """Raised when a requested memory no longer exists."""


class MemoryStateError(ValueError):
    """Raised when a requested manual state transition is invalid."""


@dataclass(frozen=True, slots=True)
class MemoryMatchDiagnostic:
    """Explain why one existing row matched an automatic candidate."""

    memory_id: str
    content: str
    source: str
    is_core: bool
    score: int
    rule: str


@dataclass(frozen=True, slots=True)
class MemoryCaptureTrace:
    """Last extractor/upsert decision for local diagnostics and regression tests."""

    user_text: str
    candidate: MemoryCandidate | None
    existing_matches: tuple[MemoryMatchDiagnostic, ...]
    action: str
    result_memory_id: str | None = None
    skip_reason: str | None = None


def _normalized_fact(text: str) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).lower()


def _signal_concepts(text: str) -> tuple[str, ...]:
    concepts: list[str] = []
    compact = text.lower().replace(" ", "")
    for cue, label in (
        ("傅里叶", "傅里叶变换"),
        ("z变换", "Z 变换"),
        ("拉普拉斯", "拉普拉斯变换"),
        ("卷积", "卷积"),
        ("频域", "频域分析"),
    ):
        if cue in compact and label not in concepts:
            concepts.append(label)
    return tuple(concepts)


class MemoryService(QObject):
    """Keep state, deduplication, extraction, and maintenance out of Qt UI."""

    memories_changed = Signal()

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        store: MemoryStore | None = None,
        extractor: MemoryExtractor | None = None,
        maintenance: MemoryMaintenance | None = None,
        now_provider: Callable[[], datetime] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store or MemoryStore(db_path)
        self.extractor = extractor or MemoryExtractor()
        self.maintenance = maintenance or MemoryMaintenance()
        self._now_provider = now_provider or datetime.now
        self._last_capture_trace: MemoryCaptureTrace | None = None

    @property
    def db_path(self) -> Path | None:
        return self.store.db_path

    @property
    def last_capture_trace(self) -> MemoryCaptureTrace | None:
        return self._last_capture_trace

    def now(self) -> datetime:
        return self._now_provider().replace(tzinfo=None, microsecond=0)

    def _resolve_now(self, now: datetime | None) -> datetime:
        return (now or self.now()).replace(tzinfo=None, microsecond=0)

    def create_memory(
        self,
        content: str,
        *,
        now: datetime | None = None,
        category: str | None = None,
        importance: float = 0.5,
        topic_key: str | None = None,
        source: str = SOURCE_MANUAL,
    ) -> MemoryRecord:
        inferred_category, inferred_topic = infer_memory_metadata(content)
        memory = MemoryRecord.create(
            content,
            now=self._resolve_now(now),
            category=category or inferred_category,
            importance=importance,
            topic_key=inferred_topic if topic_key is None else topic_key,
            source=source,
        )
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
        normalized = normalize_memory_content(content)
        category, topic_key = infer_memory_metadata(normalized)
        updated = replace(
            current,
            content=normalized,
            category=category,
            topic_key=topic_key,
            source=SOURCE_MANUAL,
            updated_at=local_timestamp(self._resolve_now(now)),
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
            updated_at=local_timestamp(self._resolve_now(now)),
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
            updated_at=local_timestamp(self._resolve_now(now)),
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
            updated_at=local_timestamp(self._resolve_now(now)),
        )
        self._save_existing(updated)
        return updated

    def capture_user_message(
        self,
        user_text: str,
        *,
        now: datetime | None = None,
    ) -> MemoryRecord | None:
        """Extract and upsert from a user message only; never accepts AI output."""

        candidate = self.extractor.extract(user_text)
        if candidate is None:
            self._record_capture_trace(
                MemoryCaptureTrace(
                    user_text=str(user_text),
                    candidate=None,
                    existing_matches=(),
                    action="skip",
                    skip_reason="extractor_returned_no_candidate",
                )
            )
            return None
        if not candidate.is_long_term:
            self._record_capture_trace(
                MemoryCaptureTrace(
                    user_text=str(user_text),
                    candidate=candidate,
                    existing_matches=(),
                    action="skip",
                    skip_reason="candidate_not_long_term",
                )
            )
            return None
        if candidate.confidence < self.extractor.minimum_confidence:
            self._record_capture_trace(
                MemoryCaptureTrace(
                    user_text=str(user_text),
                    candidate=candidate,
                    existing_matches=(),
                    action="skip",
                    skip_reason="candidate_confidence_below_threshold",
                )
            )
            return None
        return self.upsert_candidate(
            candidate,
            now=now,
            diagnostic_user_text=str(user_text),
        )

    def upsert_candidate(
        self,
        candidate: MemoryCandidate,
        *,
        now: datetime | None = None,
        diagnostic_user_text: str = "",
    ) -> MemoryRecord | None:
        if not candidate.is_long_term:
            self._record_capture_trace(
                MemoryCaptureTrace(
                    user_text=diagnostic_user_text,
                    candidate=candidate,
                    existing_matches=(),
                    action="skip",
                    skip_reason="candidate_not_long_term",
                )
            )
            return None
        current_time = self._resolve_now(now)
        existing, matches = self._find_candidate_match(candidate)
        if existing is None:
            created = self.create_memory(
                candidate.content,
                now=current_time,
                category=candidate.category,
                importance=candidate.importance,
                topic_key=candidate.topic_key,
                source=SOURCE_CHAT_AUTO,
            )
            self._record_capture_trace(
                MemoryCaptureTrace(
                    user_text=diagnostic_user_text,
                    candidate=candidate,
                    existing_matches=matches,
                    action="create",
                    result_memory_id=created.id,
                )
            )
            return created

        same_content = _normalized_fact(existing.content) == _normalized_fact(
            candidate.content
        )
        if existing.source == SOURCE_MANUAL and not candidate.explicit_update:
            self._record_capture_trace(
                MemoryCaptureTrace(
                    user_text=diagnostic_user_text,
                    candidate=candidate,
                    existing_matches=matches,
                    action="skip",
                    result_memory_id=existing.id,
                    skip_reason=(
                        "existing_manual_memory_protected_without_explicit_update"
                    ),
                )
            )
            return existing

        content = (
            existing.content
            if same_content
            else self._merge_content(existing, candidate)
        )
        updated = replace(
            existing,
            content=content,
            category=candidate.category or existing.category,
            topic_key=candidate.topic_key or existing.topic_key,
            importance=max(existing.importance, candidate.importance),
            source=(
                SOURCE_MANUAL
                if existing.source == SOURCE_MANUAL
                else SOURCE_CHAT_AUTO
            ),
            updated_at=local_timestamp(current_time),
        )
        self._save_existing(updated)
        self._record_capture_trace(
            MemoryCaptureTrace(
                user_text=diagnostic_user_text,
                candidate=candidate,
                existing_matches=matches,
                action="update",
                result_memory_id=updated.id,
            )
        )
        return updated

    def _find_candidate_match(
        self,
        candidate: MemoryCandidate,
    ) -> tuple[MemoryRecord | None, tuple[MemoryMatchDiagnostic, ...]]:
        candidate_fact = _normalized_fact(candidate.content)
        best: tuple[int, MemoryRecord] | None = None
        matches: list[MemoryMatchDiagnostic] = []
        for memory in (*self.core_memories(), *self.active_memories()):
            category, inferred_topic = infer_memory_metadata(memory.content)
            topic = memory.topic_key or inferred_topic
            score = 0
            rule = ""
            if candidate.topic_key and topic == candidate.topic_key:
                score = 100
                rule = "topic_key_same"
            else:
                existing_fact = _normalized_fact(memory.content)
                if existing_fact == candidate_fact:
                    score = 95
                    rule = "normalized_text_equal"
                elif (
                    len(candidate_fact) >= 6
                    and (
                        candidate_fact in existing_fact
                        or existing_fact in candidate_fact
                    )
                ):
                    score = 70
                    rule = "normalized_text_contains"
                elif category == candidate.category:
                    candidate_terms = set(
                        re.findall(r"[\u4e00-\u9fff]{2,}", candidate.content)
                    )
                    existing_terms = set(
                        re.findall(r"[\u4e00-\u9fff]{2,}", memory.content)
                    )
                    if candidate_terms & existing_terms:
                        score = 45
                        rule = "category_term_overlap"
            if score:
                matches.append(
                    MemoryMatchDiagnostic(
                        memory_id=memory.id,
                        content=memory.content,
                        source=memory.source,
                        is_core=memory.is_core,
                        score=score,
                        rule=rule,
                    )
                )
                if best is None or score > best[0]:
                    best = (score, memory)
        return (
            None if best is None else best[1],
            tuple(matches),
        )

    def _record_capture_trace(self, trace: MemoryCaptureTrace) -> None:
        self._last_capture_trace = trace
        candidate = trace.candidate
        if candidate is None:
            LOGGER.debug(
                "自动记忆提取：message=%r candidate=None",
                trace.user_text,
            )
        else:
            LOGGER.info(
                "自动记忆候选：message=%r content=%r category=%s "
                "topic_key=%s importance=%.2f confidence=%.2f "
                "explicit_update=%s",
                trace.user_text,
                candidate.content,
                candidate.category,
                candidate.topic_key,
                candidate.importance,
                candidate.confidence,
                candidate.explicit_update,
            )
        for match in trace.existing_matches:
            LOGGER.info(
                "自动记忆匹配：id=%s content=%r source=%s core=%s "
                "score=%d rule=%s",
                match.memory_id,
                match.content,
                match.source,
                match.is_core,
                match.score,
                match.rule,
            )
        log = LOGGER.warning if trace.action == "skip" and candidate else LOGGER.info
        log(
            "自动记忆判定：action=%s result_memory_id=%s skip_reason=%s",
            trace.action,
            trace.result_memory_id,
            trace.skip_reason,
        )

    @staticmethod
    def _merge_content(
        existing: MemoryRecord,
        candidate: MemoryCandidate,
    ) -> str:
        if candidate.topic_key == "learning:signal_system":
            concepts = list(_signal_concepts(existing.content))
            for concept in _signal_concepts(candidate.content):
                if concept not in concepts:
                    concepts.append(concept)
            if concepts:
                return (
                    "用户近期正在学习信号与系统，内容包括"
                    + "、".join(concepts)
                    + "。"
                )
        if candidate.explicit_update:
            return candidate.content
        return candidate.content

    def mark_memories_used(
        self,
        memory_ids: tuple[str, ...],
        *,
        now: datetime | None = None,
    ) -> int:
        changed = self.store.mark_used(
            memory_ids,
            local_timestamp(self._resolve_now(now)),
        )
        if changed:
            self.memories_changed.emit()
        return changed

    def run_maintenance(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[MemoryRecord, ...]:
        current = self._resolve_now(now)
        candidate_ids = self.maintenance.select_archive_candidates(
            self.all_memories(),
            current,
        )
        archived: list[MemoryRecord] = []
        for memory_id in candidate_ids:
            memory = self.get_memory(memory_id)
            if memory.is_core or memory.archived:
                continue
            archived.append(self.archive_memory(memory_id, now=current))
        return tuple(archived)

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

    def list_active_memories(self) -> tuple[MemoryRecord, ...]:
        """Explicit query alias used by integration checks and callers."""

        return self.active_memories()

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
