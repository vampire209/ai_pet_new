"""Structured result produced by the local long-term-memory extractor."""

from __future__ import annotations

from dataclasses import dataclass

from memory.memory_models import normalize_memory_content


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    content: str
    category: str
    topic_key: str
    importance: float
    is_long_term: bool
    confidence: float
    explicit_update: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "content", normalize_memory_content(self.content))
        category = str(self.category or "general").strip().lower() or "general"
        topic_key = str(self.topic_key or "").strip().lower()
        object.__setattr__(self, "category", category)
        object.__setattr__(self, "topic_key", topic_key)
        importance = float(self.importance)
        confidence = float(self.confidence)
        if not 0.0 <= importance <= 1.0:
            raise ValueError("候选记忆重要度必须在 0 到 1 之间。")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("候选记忆置信度必须在 0 到 1 之间。")
        object.__setattr__(self, "importance", importance)
        object.__setattr__(self, "confidence", confidence)
