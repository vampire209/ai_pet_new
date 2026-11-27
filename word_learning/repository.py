"""SQLite storage and additive migration for local vocabulary learning."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Iterable, Mapping
from uuid import uuid4

from PySide6.QtCore import QStandardPaths

from word_learning.catalog import STARTER_CATALOG
from word_learning.models import (
    DailyStudyStats,
    LearningSettings,
    WordEntry,
    local_timestamp,
)


class WordLearningStorageError(RuntimeError):
    """Raised when the vocabulary database cannot complete an operation."""


def default_word_learning_db_path() -> Path:
    root = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericConfigLocation
    )
    base = Path(root) if root else Path.home() / ".config"
    return base / "ai_pet_new" / "word_learning.db"


_WORD_COLUMNS = (
    ("phonetic", "TEXT NOT NULL DEFAULT ''"),
    ("part_of_speech", "TEXT NOT NULL DEFAULT ''"),
    ("exam_meaning", "TEXT NOT NULL DEFAULT ''"),
    ("collocations", "TEXT NOT NULL DEFAULT ''"),
    ("example", "TEXT NOT NULL DEFAULT ''"),
    ("example_translation", "TEXT NOT NULL DEFAULT ''"),
    ("note", "TEXT NOT NULL DEFAULT ''"),
    ("source", "TEXT NOT NULL DEFAULT 'manual'"),
    ("source_context", "TEXT NOT NULL DEFAULT ''"),
    ("created_at", "TEXT NOT NULL DEFAULT ''"),
    ("last_reviewed_at", "TEXT"),
    ("next_review_at", "TEXT"),
    ("review_count", "INTEGER NOT NULL DEFAULT 0"),
    ("correct_count", "INTEGER NOT NULL DEFAULT 0"),
    ("wrong_count", "INTEGER NOT NULL DEFAULT 0"),
    ("mastery", "INTEGER NOT NULL DEFAULT 0"),
    ("is_starred", "INTEGER NOT NULL DEFAULT 0"),
)

_DAILY_COLUMNS = (
    ("new_words_count", "INTEGER NOT NULL DEFAULT 0"),
    ("review_count", "INTEGER NOT NULL DEFAULT 0"),
    ("correct_count", "INTEGER NOT NULL DEFAULT 0"),
    ("wrong_count", "INTEGER NOT NULL DEFAULT 0"),
    ("completed", "INTEGER NOT NULL DEFAULT 0"),
)

_SETTINGS_COLUMNS = (
    ("daily_new_word_count", "INTEGER NOT NULL DEFAULT 20"),
    ("daily_review_limit", "INTEGER DEFAULT 50"),
    ("word_reminder_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ("word_reminder_time", "TEXT NOT NULL DEFAULT '19:00'"),
    ("word_reminder_task_id", "TEXT"),
    ("reading_vocab_suggestion_limit", "INTEGER NOT NULL DEFAULT 8"),
)


class WordLearningRepository:
    """Own one connection; callers never issue SQL directly."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        requested = default_word_learning_db_path() if db_path is None else db_path
        self.is_in_memory = str(requested) == ":memory:"
        self.db_path: Path | None = None if self.is_in_memory else Path(requested)
        target = ":memory:" if self.is_in_memory else str(self.db_path)
        if self.db_path is not None:
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise WordLearningStorageError(
                    f"无法创建英语学习数据目录：{exc}"
                ) from exc
        try:
            self._connection = sqlite3.connect(target)
            self._connection.row_factory = sqlite3.Row
            self._create_and_migrate_schema()
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法打开英语学习数据库：{exc}") from exc
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    def _create_and_migrate_schema(self) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS words (
                        id TEXT PRIMARY KEY,
                        word TEXT NOT NULL,
                        phonetic TEXT NOT NULL DEFAULT '',
                        part_of_speech TEXT NOT NULL DEFAULT '',
                        meaning TEXT NOT NULL,
                        exam_meaning TEXT NOT NULL DEFAULT '',
                        collocations TEXT NOT NULL DEFAULT '',
                        example TEXT NOT NULL DEFAULT '',
                        example_translation TEXT NOT NULL DEFAULT '',
                        note TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT 'manual',
                        source_context TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        last_reviewed_at TEXT,
                        next_review_at TEXT,
                        review_count INTEGER NOT NULL DEFAULT 0,
                        correct_count INTEGER NOT NULL DEFAULT 0,
                        wrong_count INTEGER NOT NULL DEFAULT 0,
                        mastery INTEGER NOT NULL DEFAULT 0,
                        is_starred INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                self._add_missing_columns("words", _WORD_COLUMNS)
                self._connection.execute(
                    "UPDATE words SET created_at = ? WHERE created_at = ''",
                    (local_timestamp(),),
                )
                self._connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_words_word "
                    "ON words (word COLLATE NOCASE)"
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_words_due "
                    "ON words (next_review_at, mastery, wrong_count)"
                )
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS word_contexts (
                        id TEXT PRIMARY KEY,
                        word_id TEXT NOT NULL,
                        context TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT 'reading',
                        created_at TEXT NOT NULL,
                        UNIQUE(word_id, context)
                    )
                    """
                )
                self._connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_word_contexts_word "
                    "ON word_contexts (word_id, created_at)"
                )
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO word_contexts (
                        id, word_id, context, source, created_at
                    )
                    SELECT
                        'context_' || lower(hex(randomblob(16))),
                        id,
                        source_context,
                        'reading',
                        created_at
                    FROM words
                    WHERE source_context <> ''
                    """
                )

                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS word_catalog (
                        word TEXT PRIMARY KEY COLLATE NOCASE,
                        phonetic TEXT NOT NULL DEFAULT '',
                        part_of_speech TEXT NOT NULL DEFAULT '',
                        meaning TEXT NOT NULL,
                        exam_meaning TEXT NOT NULL DEFAULT '',
                        collocations TEXT NOT NULL DEFAULT '',
                        example TEXT NOT NULL DEFAULT '',
                        example_translation TEXT NOT NULL DEFAULT '',
                        note TEXT NOT NULL DEFAULT ''
                    )
                    """
                )
                self._connection.executemany(
                    """
                    INSERT OR IGNORE INTO word_catalog (
                        word, phonetic, part_of_speech, meaning, exam_meaning,
                        collocations, example, example_translation, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        (
                            item["word"],
                            item.get("phonetic", ""),
                            item.get("part_of_speech", ""),
                            item["meaning"],
                            item.get("exam_meaning", ""),
                            item.get("collocations", ""),
                            item.get("example", ""),
                            item.get("example_translation", ""),
                            item.get("note", ""),
                        )
                        for item in STARTER_CATALOG
                    ),
                )

                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS daily_study_stats (
                        study_date TEXT PRIMARY KEY,
                        new_words_count INTEGER NOT NULL DEFAULT 0,
                        review_count INTEGER NOT NULL DEFAULT 0,
                        correct_count INTEGER NOT NULL DEFAULT 0,
                        wrong_count INTEGER NOT NULL DEFAULT 0,
                        completed INTEGER NOT NULL DEFAULT 0
                    )
                    """
                )
                self._add_missing_columns("daily_study_stats", _DAILY_COLUMNS)
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS learning_settings (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        daily_new_word_count INTEGER NOT NULL DEFAULT 20,
                        daily_review_limit INTEGER DEFAULT 50,
                        word_reminder_enabled INTEGER NOT NULL DEFAULT 0,
                        word_reminder_time TEXT NOT NULL DEFAULT '19:00',
                        word_reminder_task_id TEXT,
                        reading_vocab_suggestion_limit INTEGER NOT NULL DEFAULT 8
                    )
                    """
                )
                self._add_missing_columns("learning_settings", _SETTINGS_COLUMNS)
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO learning_settings (
                        id, daily_new_word_count, daily_review_limit,
                        word_reminder_enabled, word_reminder_time,
                        reading_vocab_suggestion_limit
                    ) VALUES (1, 20, 50, 0, '19:00', 8)
                    """
                )
        except sqlite3.Error as exc:
            raise WordLearningStorageError(
                f"无法初始化或升级英语学习数据库：{exc}"
            ) from exc

    def _add_missing_columns(
        self,
        table: str,
        columns: Iterable[tuple[str, str]],
    ) -> None:
        existing = {
            str(row["name"])
            for row in self._connection.execute(f"PRAGMA table_info({table})")
        }
        for name, declaration in columns:
            if name not in existing:
                self._connection.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                )

    @staticmethod
    def _word_values(word: WordEntry) -> tuple[object, ...]:
        return (
            word.id,
            word.word,
            word.phonetic,
            word.part_of_speech,
            word.meaning,
            word.exam_meaning,
            word.collocations,
            word.example,
            word.example_translation,
            word.note,
            word.source,
            word.source_context,
            word.created_at,
            word.last_reviewed_at,
            word.next_review_at,
            word.review_count,
            word.correct_count,
            word.wrong_count,
            word.mastery,
            int(word.is_starred),
        )

    def insert_word(self, word: WordEntry) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO words (
                        id, word, phonetic, part_of_speech, meaning,
                        exam_meaning, collocations, example,
                        example_translation, note, source, source_context,
                        created_at, last_reviewed_at, next_review_at,
                        review_count, correct_count, wrong_count, mastery,
                        is_starred
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._word_values(word),
                )
        except sqlite3.IntegrityError as exc:
            raise WordLearningStorageError("这个词已经在单词本里了。") from exc
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法保存单词：{exc}") from exc

    def update_word(self, word: WordEntry) -> None:
        try:
            with self._connection:
                cursor = self._connection.execute(
                    """
                    UPDATE words SET
                        word = ?, phonetic = ?, part_of_speech = ?, meaning = ?,
                        exam_meaning = ?, collocations = ?, example = ?,
                        example_translation = ?, note = ?, source = ?,
                        source_context = ?, created_at = ?, last_reviewed_at = ?,
                        next_review_at = ?, review_count = ?, correct_count = ?,
                        wrong_count = ?, mastery = ?, is_starred = ?
                    WHERE id = ?
                    """,
                    (*self._word_values(word)[1:], word.id),
                )
            if cursor.rowcount == 0:
                raise WordLearningStorageError("没有找到要更新的单词。")
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法更新单词：{exc}") from exc

    def delete_word(self, word_id: str) -> bool:
        try:
            with self._connection:
                self._connection.execute(
                    "DELETE FROM word_contexts WHERE word_id = ?",
                    (word_id,),
                )
                cursor = self._connection.execute(
                    "DELETE FROM words WHERE id = ?",
                    (word_id,),
                )
            return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法删除单词：{exc}") from exc

    def get_word(self, word_id: str) -> WordEntry | None:
        return self._fetch_word("SELECT * FROM words WHERE id = ?", (word_id,))

    def get_word_by_text(self, word: str) -> WordEntry | None:
        return self._fetch_word(
            "SELECT * FROM words WHERE word = ? COLLATE NOCASE",
            (word,),
        )

    def _fetch_word(
        self,
        query: str,
        parameters: tuple[object, ...],
    ) -> WordEntry | None:
        try:
            row = self._connection.execute(query, parameters).fetchone()
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法读取单词：{exc}") from exc
        return None if row is None else WordEntry.from_row(row)

    def add_word_context(
        self,
        word_id: str,
        context: str,
        *,
        source: str = "reading",
        created_at: str | None = None,
    ) -> bool:
        normalized = str(context or "").strip()
        if not normalized:
            return False
        try:
            with self._connection:
                before = self._connection.total_changes
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO word_contexts (
                        id, word_id, context, source, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        f"context_{uuid4().hex}",
                        word_id,
                        normalized,
                        source,
                        created_at or local_timestamp(),
                    ),
                )
                return self._connection.total_changes > before
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法保存阅读原句：{exc}") from exc

    def list_word_contexts(self, word_id: str) -> tuple[str, ...]:
        try:
            rows = self._connection.execute(
                """
                SELECT context FROM word_contexts
                WHERE word_id = ? ORDER BY created_at ASC, id ASC
                """,
                (word_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法读取阅读原句：{exc}") from exc
        return tuple(str(row["context"]) for row in rows)

    def list_words(self) -> tuple[WordEntry, ...]:
        try:
            rows = self._connection.execute(
                """
                SELECT * FROM words
                ORDER BY is_starred DESC, mastery ASC, created_at DESC, word ASC
                """
            ).fetchall()
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法读取单词本：{exc}") from exc
        return tuple(WordEntry.from_row(row) for row in rows)

    def list_unstudied(
        self,
        limit: int,
        *,
        exclude_reading_date: str | None = None,
    ) -> tuple[WordEntry, ...]:
        clauses = ["review_count = 0"]
        parameters: list[object] = []
        if exclude_reading_date:
            clauses.append(
                "NOT (source = 'reading' AND substr(created_at, 1, 10) >= ?)"
            )
            parameters.append(exclude_reading_date)
        parameters.append(max(0, limit))
        try:
            rows = self._connection.execute(
                f"""
                SELECT * FROM words WHERE {' AND '.join(clauses)}
                ORDER BY CASE WHEN source = 'reading' THEN 0 ELSE 1 END,
                         created_at ASC, word ASC LIMIT ?
                """,
                tuple(parameters),
            ).fetchall()
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法读取待学新词：{exc}") from exc
        return tuple(WordEntry.from_row(row) for row in rows)

    def list_due(
        self,
        due_at: str,
        *,
        exclude_date: str | None = None,
        limit: int | None = None,
    ) -> tuple[WordEntry, ...]:
        clauses = ["review_count > 0", "next_review_at <= ?"]
        parameters: list[object] = [due_at]
        if exclude_date:
            clauses.append(
                "(last_reviewed_at IS NULL OR substr(last_reviewed_at, 1, 10) <> ?)"
            )
            parameters.append(exclude_date)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            parameters.append(max(0, limit))
        try:
            rows = self._connection.execute(
                f"""
                SELECT * FROM words
                WHERE {' AND '.join(clauses)}
                ORDER BY next_review_at ASC, mastery ASC,
                         wrong_count DESC, last_reviewed_at ASC
                {limit_sql}
                """,
                tuple(parameters),
            ).fetchall()
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法读取待复习单词：{exc}") from exc
        return tuple(WordEntry.from_row(row) for row in rows)

    def get_catalog_entry(self, word: str) -> Mapping[str, object] | None:
        try:
            row = self._connection.execute(
                "SELECT * FROM word_catalog WHERE word = ? COLLATE NOCASE",
                (word,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法读取示例词库：{exc}") from exc
        return None if row is None else dict(row)

    def list_catalog_not_added(self, limit: int) -> tuple[Mapping[str, object], ...]:
        try:
            rows = self._connection.execute(
                """
                SELECT c.* FROM word_catalog c
                LEFT JOIN words w ON w.word = c.word COLLATE NOCASE
                WHERE w.id IS NULL
                ORDER BY c.rowid ASC LIMIT ?
                """,
                (max(0, limit),),
            ).fetchall()
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法读取示例词库：{exc}") from exc
        return tuple(dict(row) for row in rows)

    def import_catalog_entries(self, entries: Iterable[Mapping[str, object]]) -> int:
        values = [
            (
                str(item.get("word", "")).strip().lower(),
                str(item.get("phonetic", "")),
                str(item.get("part_of_speech", "")),
                str(item.get("meaning", "")),
                str(item.get("exam_meaning", "")),
                str(item.get("collocations", "")),
                str(item.get("example", "")),
                str(item.get("example_translation", "")),
                str(item.get("note", "")),
            )
            for item in entries
        ]
        try:
            with self._connection:
                before = self._connection.total_changes
                self._connection.executemany(
                    """
                    INSERT OR IGNORE INTO word_catalog (
                        word, phonetic, part_of_speech, meaning, exam_meaning,
                        collocations, example, example_translation, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                return self._connection.total_changes - before
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法导入词库：{exc}") from exc

    def load_settings(self) -> LearningSettings:
        try:
            row = self._connection.execute(
                "SELECT * FROM learning_settings WHERE id = 1"
            ).fetchone()
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法读取学习设置：{exc}") from exc
        if row is None:
            return LearningSettings()
        try:
            return LearningSettings(
                daily_new_word_count=int(row["daily_new_word_count"]),
                daily_review_limit=(
                    None
                    if row["daily_review_limit"] is None
                    else int(row["daily_review_limit"])
                ),
                word_reminder_enabled=bool(row["word_reminder_enabled"]),
                word_reminder_time=str(row["word_reminder_time"]),
                word_reminder_task_id=row["word_reminder_task_id"],
                reading_vocab_suggestion_limit=int(
                    row["reading_vocab_suggestion_limit"]
                ),
            )
        except (TypeError, ValueError):
            return LearningSettings()

    def save_settings(self, settings: LearningSettings) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    UPDATE learning_settings SET
                        daily_new_word_count = ?, daily_review_limit = ?,
                        word_reminder_enabled = ?, word_reminder_time = ?,
                        word_reminder_task_id = ?,
                        reading_vocab_suggestion_limit = ? WHERE id = 1
                    """,
                    (
                        settings.daily_new_word_count,
                        settings.daily_review_limit,
                        int(settings.word_reminder_enabled),
                        settings.word_reminder_time,
                        settings.word_reminder_task_id,
                        settings.reading_vocab_suggestion_limit,
                    ),
                )
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法保存学习设置：{exc}") from exc

    def get_daily_stats(self, study_date: str) -> DailyStudyStats:
        try:
            row = self._connection.execute(
                "SELECT * FROM daily_study_stats WHERE study_date = ?",
                (study_date,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法读取学习统计：{exc}") from exc
        if row is None:
            return DailyStudyStats(study_date)
        return DailyStudyStats(
            study_date=str(row["study_date"]),
            new_words_count=int(row["new_words_count"]),
            review_count=int(row["review_count"]),
            correct_count=int(row["correct_count"]),
            wrong_count=int(row["wrong_count"]),
            completed=bool(row["completed"]),
        )

    def save_daily_stats(self, stats: DailyStudyStats) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO daily_study_stats (
                        study_date, new_words_count, review_count,
                        correct_count, wrong_count, completed
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(study_date) DO UPDATE SET
                        new_words_count = excluded.new_words_count,
                        review_count = excluded.review_count,
                        correct_count = excluded.correct_count,
                        wrong_count = excluded.wrong_count,
                        completed = excluded.completed
                    """,
                    (
                        stats.study_date,
                        stats.new_words_count,
                        stats.review_count,
                        stats.correct_count,
                        stats.wrong_count,
                        int(stats.completed),
                    ),
                )
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法保存学习统计：{exc}") from exc

    def list_daily_stats(self) -> tuple[DailyStudyStats, ...]:
        try:
            rows = self._connection.execute(
                "SELECT * FROM daily_study_stats ORDER BY study_date DESC"
            ).fetchall()
        except sqlite3.Error as exc:
            raise WordLearningStorageError(f"无法读取学习统计：{exc}") from exc
        return tuple(
            DailyStudyStats(
                study_date=str(row["study_date"]),
                new_words_count=int(row["new_words_count"]),
                review_count=int(row["review_count"]),
                correct_count=int(row["correct_count"]),
                wrong_count=int(row["wrong_count"]),
                completed=bool(row["completed"]),
            )
            for row in rows
        )

    def replace_starred(self, word: WordEntry, starred: bool) -> WordEntry:
        updated = replace(word, is_starred=bool(starred))
        self.update_word(updated)
        return updated

    def close(self) -> None:
        if self._closed:
            return
        self._connection.close()
        self._closed = True
