"""Simple deterministic interval scheduling, isolated from storage and UI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from word_learning.models import (
    RESULT_EASY,
    RESULT_FORGOT,
    RESULT_HARD,
    RESULT_NORMAL,
    REVIEW_RESULTS,
    WordEntry,
    WordLearningDataError,
    local_timestamp,
)


@dataclass(frozen=True, slots=True)
class ReviewSchedule:
    last_reviewed_at: str
    next_review_at: str
    review_count: int
    correct_count: int
    wrong_count: int
    mastery: int


class ReviewScheduler:
    """Use stable fixed gradients with a 90-day maximum interval."""

    _NORMAL_DAYS = (3, 7, 14, 30, 60, 90)
    _EASY_DAYS = (7, 14, 30, 60, 90)

    def calculate_next_review(
        self,
        word: WordEntry,
        result: str,
        *,
        now: datetime | None = None,
    ) -> ReviewSchedule:
        if result not in REVIEW_RESULTS:
            raise WordLearningDataError("未知的复习结果。")
        current = (now or datetime.now()).replace(microsecond=0)

        if result == RESULT_FORGOT:
            delta = timedelta(minutes=10)
            mastery = max(0, word.mastery - 25)
            correct_count = 0
            wrong_count = word.wrong_count + 1
        elif result == RESULT_HARD:
            delta = timedelta(days=1)
            mastery = min(100, word.mastery + 3)
            correct_count = word.correct_count
            wrong_count = word.wrong_count + 1
        elif result == RESULT_NORMAL:
            index = min(word.correct_count, len(self._NORMAL_DAYS) - 1)
            delta = timedelta(days=self._NORMAL_DAYS[index])
            mastery = min(100, word.mastery + 10)
            correct_count = word.correct_count + 1
            wrong_count = word.wrong_count
        else:
            index = min(word.correct_count, len(self._EASY_DAYS) - 1)
            delta = timedelta(days=self._EASY_DAYS[index])
            mastery = min(100, word.mastery + 18)
            correct_count = word.correct_count + 1
            wrong_count = word.wrong_count

        return ReviewSchedule(
            last_reviewed_at=local_timestamp(current),
            next_review_at=local_timestamp(current + delta),
            review_count=word.review_count + 1,
            correct_count=correct_count,
            wrong_count=wrong_count,
            mastery=mastery,
        )
