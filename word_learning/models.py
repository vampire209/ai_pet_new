"""Validated immutable records for local vocabulary learning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import re
from typing import Any, Mapping
from uuid import uuid4


SOURCE_BUILTIN = "builtin"
SOURCE_READING = "reading"
SOURCE_MANUAL = "manual"
WORD_SOURCES = (SOURCE_BUILTIN, SOURCE_READING, SOURCE_MANUAL)

RESULT_FORGOT = "forgot"
RESULT_HARD = "hard"
RESULT_NORMAL = "normal"
RESULT_EASY = "easy"
REVIEW_RESULTS = (RESULT_FORGOT, RESULT_HARD, RESULT_NORMAL, RESULT_EASY)

WORD_MAX_LENGTH = 80
TEXT_MAX_LENGTH = 2000
_WORD_PATTERN = re.compile(r"^[A-Za-z][A-Za-z'’-]*(?:[ -][A-Za-z][A-Za-z'’-]*)*$")


class WordLearningDataError(ValueError):
    """Raised when persisted or user-entered learning data is invalid."""


def local_timestamp(now: datetime | None = None) -> str:
    current = now or datetime.now()
    if current.tzinfo is not None:
        current = current.replace(tzinfo=None)
    return current.replace(microsecond=0).isoformat(timespec="seconds")


def normalize_word(value: object) -> str:
    if not isinstance(value, str):
        raise WordLearningDataError("单词必须是文字。")
    normalized = " ".join(value.strip().split()).lower()
    if not normalized:
        raise WordLearningDataError("单词不能为空。")
    if len(normalized) > WORD_MAX_LENGTH or not _WORD_PATTERN.fullmatch(normalized):
        raise WordLearningDataError("请输入有效的英文单词或短语。")
    return normalized


def normalize_text(value: object, *, required: bool = False) -> str:
    normalized = str(value or "").strip()
    if required and not normalized:
        raise WordLearningDataError("核心词义不能为空。")
    if len(normalized) > TEXT_MAX_LENGTH:
        raise WordLearningDataError("单词内容过长。")
    return normalized


def normalize_source(value: object) -> str:
    normalized = str(value or SOURCE_MANUAL).strip().lower()
    return normalized if normalized in WORD_SOURCES else SOURCE_MANUAL


def mastery_label(mastery: int) -> str:
    if mastery < 20:
        return "陌生"
    if mastery < 40:
        return "较弱"
    if mastery < 65:
        return "一般"
    if mastery < 85:
        return "熟悉"
    return "已掌握"


@dataclass(frozen=True, slots=True)
class WordEntry:
    id: str
    word: str
    phonetic: str
    part_of_speech: str
    meaning: str
    exam_meaning: str
    collocations: str
    example: str
    example_translation: str
    note: str
    source: str
    source_context: str
    created_at: str
    last_reviewed_at: str | None = None
    next_review_at: str | None = None
    review_count: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    mastery: int = 0
    is_starred: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise WordLearningDataError("单词 ID 无效。")
        object.__setattr__(self, "word", normalize_word(self.word))
        for field in (
            "phonetic",
            "part_of_speech",
            "exam_meaning",
            "collocations",
            "example",
            "example_translation",
            "note",
            "source_context",
        ):
            object.__setattr__(self, field, normalize_text(getattr(self, field)))
        object.__setattr__(self, "meaning", normalize_text(self.meaning, required=True))
        object.__setattr__(self, "source", normalize_source(self.source))
        for field in ("created_at", "last_reviewed_at", "next_review_at"):
            value = getattr(self, field)
            if value is None and field != "created_at":
                continue
            try:
                datetime.fromisoformat(str(value))
            except (TypeError, ValueError) as exc:
                raise WordLearningDataError("单词时间字段无效。") from exc
        for field in ("review_count", "correct_count", "wrong_count"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise WordLearningDataError("复习计数无效。")
        if not isinstance(self.mastery, int) or not 0 <= self.mastery <= 100:
            raise WordLearningDataError("掌握度必须在 0～100 之间。")

    @classmethod
    def create(
        cls,
        word: str,
        meaning: str,
        *,
        phonetic: str = "",
        part_of_speech: str = "",
        exam_meaning: str = "",
        collocations: str = "",
        example: str = "",
        example_translation: str = "",
        note: str = "",
        source: str = SOURCE_MANUAL,
        source_context: str = "",
        now: datetime | None = None,
    ) -> "WordEntry":
        normalized_word = normalize_word(word)
        return cls(
            id=f"word_{uuid4().hex}",
            word=normalized_word,
            phonetic=phonetic,
            part_of_speech=part_of_speech,
            meaning=meaning,
            exam_meaning=exam_meaning,
            collocations=collocations,
            example=example,
            example_translation=example_translation,
            note=note,
            source=source,
            source_context=source_context,
            created_at=local_timestamp(now),
        )

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "WordEntry":
        return cls(
            id=str(row["id"]),
            word=str(row["word"]),
            phonetic=str(row["phonetic"] or ""),
            part_of_speech=str(row["part_of_speech"] or ""),
            meaning=str(row["meaning"]),
            exam_meaning=str(row["exam_meaning"] or ""),
            collocations=str(row["collocations"] or ""),
            example=str(row["example"] or ""),
            example_translation=str(row["example_translation"] or ""),
            note=str(row["note"] or ""),
            source=str(row["source"] or SOURCE_MANUAL),
            source_context=str(row["source_context"] or ""),
            created_at=str(row["created_at"]),
            last_reviewed_at=row["last_reviewed_at"],
            next_review_at=row["next_review_at"],
            review_count=int(row["review_count"] or 0),
            correct_count=int(row["correct_count"] or 0),
            wrong_count=int(row["wrong_count"] or 0),
            mastery=int(row["mastery"] or 0),
            is_starred=bool(row["is_starred"]),
        )

    @property
    def mastery_text(self) -> str:
        return mastery_label(self.mastery)


@dataclass(frozen=True, slots=True)
class LearningSettings:
    daily_new_word_count: int = 20
    daily_review_limit: int | None = 50
    word_reminder_enabled: bool = False
    word_reminder_time: str = "19:00"
    word_reminder_task_id: str | None = None
    reading_vocab_suggestion_limit: int = 8

    def __post_init__(self) -> None:
        if self.daily_new_word_count not in range(1, 101):
            raise WordLearningDataError("每日新词数量必须在 1～100 之间。")
        if self.daily_review_limit is not None and self.daily_review_limit not in range(1, 501):
            raise WordLearningDataError("每日复习上限必须在 1～500 之间。")
        if self.reading_vocab_suggestion_limit not in range(1, 21):
            raise WordLearningDataError("每篇阅读建议生词数量必须在 1～20 之间。")
        try:
            datetime.strptime(self.word_reminder_time, "%H:%M")
        except (TypeError, ValueError) as exc:
            raise WordLearningDataError("背词提醒时间无效。") from exc


@dataclass(frozen=True, slots=True)
class DailyStudyStats:
    study_date: str
    new_words_count: int = 0
    review_count: int = 0
    correct_count: int = 0
    wrong_count: int = 0
    completed: bool = False

    @property
    def total_count(self) -> int:
        return self.new_words_count + self.review_count

    @property
    def accuracy(self) -> int:
        attempts = self.correct_count + self.wrong_count
        return 0 if attempts == 0 else round(self.correct_count * 100 / attempts)


@dataclass(frozen=True, slots=True)
class TodayProgress:
    new_words_done: int
    new_words_goal: int
    reviews_done: int
    reviews_goal: int
    completed: bool

    @property
    def total_done(self) -> int:
        return self.new_words_done + self.reviews_done

    @property
    def total_goal(self) -> int:
        return self.new_words_goal + self.reviews_goal


@dataclass(frozen=True, slots=True)
class LearningStats:
    today: DailyStudyStats
    study_streak: int
    total_words: int
    mastered_words: int
    due_words: int
    recent_seven_days: tuple[DailyStudyStats, ...]


def today_key(now: datetime | None = None) -> str:
    return (now or datetime.now()).date().isoformat()


def parse_study_date(value: str) -> date:
    return date.fromisoformat(value)
