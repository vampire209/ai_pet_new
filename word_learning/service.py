"""Application service for local word cards, review progress, and statistics."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Mapping

from PySide6.QtCore import QObject, Signal

from word_learning.models import (
    DailyStudyStats,
    LearningSettings,
    LearningStats,
    RESULT_EASY,
    RESULT_NORMAL,
    SOURCE_BUILTIN,
    SOURCE_MANUAL,
    TodayProgress,
    WordEntry,
    WordLearningDataError,
    local_timestamp,
    mastery_label,
    normalize_source,
    normalize_word,
    today_key,
)
from word_learning.repository import WordLearningRepository
from word_learning.review_scheduler import ReviewScheduler
from word_learning.reading_suggestions import SuggestedVocabulary


@dataclass(frozen=True, slots=True)
class AddWordResult:
    word: WordEntry
    created: bool
    context_added: bool = False


@dataclass(frozen=True, slots=True)
class BatchAddResult:
    items: tuple[AddWordResult, ...]

    @property
    def created_count(self) -> int:
        return sum(item.created for item in self.items)

    @property
    def existing_count(self) -> int:
        return len(self.items) - self.created_count

    @property
    def context_count(self) -> int:
        return sum(item.context_added for item in self.items)

    @property
    def new_words(self) -> tuple[WordEntry, ...]:
        return tuple(item.word for item in self.items if item.created)


class WordLearningService(QObject):
    """Keep UI independent from SQLite and deterministic review calculations."""

    changed = Signal()
    settings_changed = Signal(object)

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        repository: WordLearningRepository | None = None,
        scheduler: ReviewScheduler | None = None,
        now_provider=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.repository = repository or WordLearningRepository(db_path)
        self.scheduler = scheduler or ReviewScheduler()
        self._now_provider = now_provider or datetime.now

    @property
    def db_path(self) -> Path | None:
        return self.repository.db_path

    @property
    def settings(self) -> LearningSettings:
        return self.repository.load_settings()

    def now(self) -> datetime:
        return self._now_provider().replace(microsecond=0)

    def save_settings(self, settings: LearningSettings) -> LearningSettings:
        self.repository.save_settings(settings)
        self.settings_changed.emit(settings)
        self.changed.emit()
        return settings

    def update_reminder_task_id(self, task_id: str | None) -> LearningSettings:
        updated = replace(self.settings, word_reminder_task_id=task_id)
        return self.save_settings(updated)

    def add_word(
        self,
        word: str,
        meaning: str = "",
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
    ) -> AddWordResult:
        normalized = normalize_word(word)
        existing = self.repository.get_word_by_text(normalized)
        if existing is not None:
            context_added = False
            word_updated = False
            if normalize_source(source) == "reading" and source_context.strip():
                context_added = self.repository.add_word_context(
                    existing.id,
                    source_context,
                    source="reading",
                    created_at=local_timestamp(self.now()),
                )
                if existing.source != "reading" or not existing.source_context:
                    existing = replace(
                        existing,
                        source="reading",
                        source_context=existing.source_context or source_context,
                    )
                    self.repository.update_word(existing)
                    word_updated = True
                if context_added or word_updated:
                    self.changed.emit()
            return AddWordResult(existing, False, context_added)

        catalog = self.repository.get_catalog_entry(normalized)
        if catalog is not None:
            meaning = meaning or str(catalog["meaning"])
            phonetic = phonetic or str(catalog["phonetic"] or "")
            part_of_speech = part_of_speech or str(
                catalog["part_of_speech"] or ""
            )
            exam_meaning = exam_meaning or str(catalog["exam_meaning"] or "")
            collocations = collocations or str(catalog["collocations"] or "")
            example = example or str(catalog["example"] or "")
            example_translation = example_translation or str(
                catalog["example_translation"] or ""
            )
            note = note or str(catalog["note"] or "")

        entry = WordEntry.create(
            normalized,
            meaning,
            phonetic=phonetic,
            part_of_speech=part_of_speech,
            exam_meaning=exam_meaning,
            collocations=collocations,
            example=example,
            example_translation=example_translation,
            note=note,
            source=normalize_source(source),
            source_context=source_context,
            now=self.now(),
        )
        self.repository.insert_word(entry)
        context_added = False
        if entry.source == "reading" and entry.source_context:
            context_added = self.repository.add_word_context(
                entry.id,
                entry.source_context,
                source="reading",
                created_at=entry.created_at,
            )
        self.changed.emit()
        return AddWordResult(entry, True, context_added)

    def add_reading_word(
        self,
        word: str,
        meaning: str,
        *,
        source_context: str,
        **details: str,
    ) -> AddWordResult:
        return self.add_word(
            word,
            meaning,
            source="reading",
            source_context=source_context,
            **details,
        )

    def remove_word(self, word_id: str) -> bool:
        removed = self.repository.delete_word(word_id)
        if removed:
            self.changed.emit()
        return removed

    def get_word(self, word_id: str) -> WordEntry | None:
        return self.repository.get_word(word_id)

    def get_word_by_text(self, word: str) -> WordEntry | None:
        return self.repository.get_word_by_text(normalize_word(word))

    def get_word_contexts(self, word_id: str) -> tuple[str, ...]:
        return self.repository.list_word_contexts(word_id)

    def add_reading_suggestions(
        self,
        suggestions: Iterable[SuggestedVocabulary],
    ) -> BatchAddResult:
        results: list[AddWordResult] = []
        seen: set[str] = set()
        for suggestion in suggestions:
            key = normalize_word(suggestion.word).casefold()
            if key in seen:
                continue
            seen.add(key)
            results.append(
                self.add_word(
                    suggestion.word,
                    suggestion.meaning,
                    part_of_speech=suggestion.part_of_speech,
                    exam_meaning=suggestion.exam_meaning,
                    source="reading",
                    source_context=suggestion.source_sentence,
                )
            )
        return BatchAddResult(tuple(results))

    def search_words(
        self,
        query: str = "",
        *,
        mastery: str | None = None,
        source: str | None = None,
    ) -> tuple[WordEntry, ...]:
        needle = query.strip().lower()
        return tuple(
            word
            for word in self.repository.list_words()
            if (not needle or needle in word.word.lower() or needle in word.meaning.lower())
            and (not mastery or mastery_label(word.mastery) == mastery)
            and (not source or word.source == source)
        )

    def set_starred(self, word_id: str, starred: bool) -> WordEntry:
        word = self.repository.get_word(word_id)
        if word is None:
            raise WordLearningDataError("没有找到这个单词。")
        updated = self.repository.replace_starred(word, starred)
        self.changed.emit()
        return updated

    def get_today_new_words(self) -> tuple[WordEntry, ...]:
        current = self.now()
        stats = self.repository.get_daily_stats(today_key(current))
        remaining = max(0, self.settings.daily_new_word_count - stats.new_words_count)
        words = list(
            self.repository.list_unstudied(
                remaining,
                exclude_reading_date=today_key(current),
            )
        )
        shortage = remaining - len(words)
        if shortage > 0:
            for catalog in self.repository.list_catalog_not_added(shortage):
                created = self.add_word(
                    str(catalog["word"]),
                    str(catalog["meaning"]),
                    phonetic=str(catalog["phonetic"] or ""),
                    part_of_speech=str(catalog["part_of_speech"] or ""),
                    exam_meaning=str(catalog["exam_meaning"] or ""),
                    collocations=str(catalog["collocations"] or ""),
                    example=str(catalog["example"] or ""),
                    example_translation=str(catalog["example_translation"] or ""),
                    note=str(catalog["note"] or ""),
                    source=SOURCE_BUILTIN,
                )
                if created.created:
                    words.append(created.word)
        return tuple(words[:remaining])

    def get_today_reviews(self) -> tuple[WordEntry, ...]:
        current = self.now()
        stats = self.repository.get_daily_stats(today_key(current))
        limit = self.settings.daily_review_limit
        remaining = None if limit is None else max(0, limit - stats.review_count)
        return self.repository.list_due(
            current.isoformat(timespec="seconds"),
            exclude_date=today_key(current),
            limit=remaining,
        )

    def get_extra_reviews(self, limit: int = 20) -> tuple[WordEntry, ...]:
        studied = [word for word in self.repository.list_words() if word.review_count > 0]
        studied.sort(
            key=lambda word: (
                not word.is_starred,
                word.mastery,
                -word.wrong_count,
                word.last_reviewed_at or "",
            )
        )
        return tuple(studied[: max(0, limit)])

    def mark_review_result(
        self,
        word_id: str,
        result: str,
        *,
        count_for_daily: bool = True,
    ) -> WordEntry:
        word = self.repository.get_word(word_id)
        if word is None:
            raise WordLearningDataError("没有找到这个单词。")
        was_new = word.review_count == 0
        schedule = self.scheduler.calculate_next_review(word, result, now=self.now())
        updated = replace(
            word,
            last_reviewed_at=schedule.last_reviewed_at,
            next_review_at=schedule.next_review_at,
            review_count=schedule.review_count,
            correct_count=schedule.correct_count,
            wrong_count=schedule.wrong_count,
            mastery=schedule.mastery,
        )
        self.repository.update_word(updated)
        if count_for_daily:
            self._record_daily_result(was_new=was_new, result=result)
        self.changed.emit()
        return updated

    def _record_daily_result(self, *, was_new: bool, result: str) -> None:
        key = today_key(self.now())
        stats = self.repository.get_daily_stats(key)
        correct = result in {RESULT_NORMAL, RESULT_EASY}
        updated = replace(
            stats,
            new_words_count=stats.new_words_count + int(was_new),
            review_count=stats.review_count + int(not was_new),
            correct_count=stats.correct_count + int(correct),
            wrong_count=stats.wrong_count + int(not correct),
        )
        progress = self.get_today_progress(stats_override=updated)
        self.repository.save_daily_stats(
            replace(updated, completed=progress.completed)
        )

    def get_today_progress(
        self,
        *,
        stats_override: DailyStudyStats | None = None,
    ) -> TodayProgress:
        current = self.now()
        key = today_key(current)
        stats = stats_override or self.repository.get_daily_stats(key)
        settings = self.settings
        due_remaining = len(
            self.repository.list_due(
                current.isoformat(timespec="seconds"),
                exclude_date=key,
            )
        )
        available_reviews = stats.review_count + due_remaining
        review_goal = (
            available_reviews
            if settings.daily_review_limit is None
            else min(settings.daily_review_limit, available_reviews)
        )
        new_available = (
            stats.new_words_count
            + len(
                self.repository.list_unstudied(
                    settings.daily_new_word_count,
                    exclude_reading_date=key,
                )
            )
            + len(self.repository.list_catalog_not_added(settings.daily_new_word_count))
        )
        new_goal = min(settings.daily_new_word_count, new_available)
        completed = (
            stats.new_words_count >= new_goal
            and stats.review_count >= review_goal
            and stats.total_count > 0
        )
        return TodayProgress(
            new_words_done=stats.new_words_count,
            new_words_goal=new_goal,
            reviews_done=stats.review_count,
            reviews_goal=review_goal,
            completed=completed,
        )

    def get_stats(self) -> LearningStats:
        current = self.now()
        key = today_key(current)
        all_daily = self.repository.list_daily_stats()
        daily_by_date = {item.study_date: item for item in all_daily}
        recent = tuple(
            daily_by_date.get(
                (current.date() - timedelta(days=offset)).isoformat(),
                DailyStudyStats(
                    (current.date() - timedelta(days=offset)).isoformat()
                ),
            )
            for offset in range(6, -1, -1)
        )
        words = self.repository.list_words()
        return LearningStats(
            today=daily_by_date.get(key, DailyStudyStats(key)),
            study_streak=self._calculate_streak(all_daily, current.date()),
            total_words=len(words),
            mastered_words=sum(word.mastery >= 85 for word in words),
            due_words=len(
                self.repository.list_due(current.isoformat(timespec="seconds"))
            ),
            recent_seven_days=recent,
        )

    @staticmethod
    def _calculate_streak(
        stats: Iterable[DailyStudyStats],
        current_date: date,
    ) -> int:
        active_dates = sorted(
            {
                date.fromisoformat(item.study_date)
                for item in stats
                if item.total_count > 0
            },
            reverse=True,
        )
        if not active_dates or (current_date - active_dates[0]).days > 1:
            return 0
        streak = 1
        for previous, following in zip(active_dates, active_dates[1:]):
            if (previous - following).days != 1:
                break
            streak += 1
        return streak

    def import_catalog(self, entries: Iterable[Mapping[str, object]]) -> int:
        validated: list[dict[str, str]] = []
        for raw in entries:
            word = WordEntry.create(
                str(raw.get("word", "")),
                str(raw.get("meaning", "")),
                phonetic=str(raw.get("phonetic", "")),
                part_of_speech=str(raw.get("part_of_speech", "")),
                exam_meaning=str(raw.get("exam_meaning", "")),
                collocations=str(raw.get("collocations", "")),
                example=str(raw.get("example", "")),
                example_translation=str(raw.get("example_translation", "")),
                note=str(raw.get("note", "")),
            )
            validated.append(
                {
                    "word": word.word,
                    "phonetic": word.phonetic,
                    "part_of_speech": word.part_of_speech,
                    "meaning": word.meaning,
                    "exam_meaning": word.exam_meaning,
                    "collocations": word.collocations,
                    "example": word.example,
                    "example_translation": word.example_translation,
                    "note": word.note,
                }
            )
        imported = self.repository.import_catalog_entries(validated)
        if imported:
            self.changed.emit()
        return imported

    def close(self) -> None:
        self.repository.close()
