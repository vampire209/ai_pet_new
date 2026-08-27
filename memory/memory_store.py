"""SQLite persistence for manual long-term memory records."""

from __future__ import annotations

from pathlib import Path
import sqlite3

from PySide6.QtCore import QStandardPaths

from memory.memory_models import MemoryRecord


class MemoryStorageError(RuntimeError):
    """Raised when SQLite cannot complete a requested operation."""


def default_memory_db_path() -> Path:
    root = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.GenericConfigLocation
    )
    base = Path(root) if root else Path.home() / ".config"
    return base / "ai_pet_new" / "memory.db"


class MemoryStore:
    """Own one SQLite connection and expose persistence-only operations."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        requested = default_memory_db_path() if db_path is None else db_path
        self.is_in_memory = str(requested) == ":memory:"
        self.db_path: Path | None = None if self.is_in_memory else Path(requested)
        target = ":memory:" if self.is_in_memory else str(self.db_path)
        if self.db_path is not None:
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise MemoryStorageError(f"无法创建记忆数据目录：{exc}") from exc
        try:
            self._connection = sqlite3.connect(target)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._create_schema()
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"无法打开记忆数据库：{exc}") from exc
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    def _create_schema(self) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memories (
                        id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        is_core INTEGER NOT NULL DEFAULT 0
                            CHECK (is_core IN (0, 1)),
                        archived INTEGER NOT NULL DEFAULT 0
                            CHECK (archived IN (0, 1)),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        CHECK (NOT (archived = 1 AND is_core = 1))
                    )
                    """
                )
                self._connection.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_memories_state_updated
                    ON memories (archived, is_core, updated_at DESC)
                    """
                )
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"无法初始化记忆数据库：{exc}") from exc

    def insert(self, memory: MemoryRecord) -> None:
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO memories (
                        id, content, is_core, archived, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        memory.id,
                        memory.content,
                        int(memory.is_core),
                        int(memory.archived),
                        memory.created_at,
                        memory.updated_at,
                    ),
                )
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"无法保存记忆：{exc}") from exc

    def update(self, memory: MemoryRecord) -> bool:
        try:
            with self._connection:
                cursor = self._connection.execute(
                    """
                    UPDATE memories
                    SET content = ?, is_core = ?, archived = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        memory.content,
                        int(memory.is_core),
                        int(memory.archived),
                        memory.updated_at,
                        memory.id,
                    ),
                )
            return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"无法更新记忆：{exc}") from exc

    def delete(self, memory_id: str) -> bool:
        try:
            with self._connection:
                cursor = self._connection.execute(
                    "DELETE FROM memories WHERE id = ?",
                    (memory_id,),
                )
            return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"无法删除记忆：{exc}") from exc

    def get(self, memory_id: str) -> MemoryRecord | None:
        try:
            row = self._connection.execute(
                "SELECT * FROM memories WHERE id = ?",
                (memory_id,),
            ).fetchone()
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"无法读取记忆：{exc}") from exc
        return None if row is None else MemoryRecord.from_row(row)

    def list_memories(self, *, archived: bool | None = None) -> tuple[MemoryRecord, ...]:
        conditions = ""
        parameters: tuple[object, ...] = ()
        if archived is not None:
            conditions = "WHERE archived = ?"
            parameters = (int(archived),)
        try:
            rows = self._connection.execute(
                f"""
                SELECT * FROM memories
                {conditions}
                ORDER BY is_core DESC, updated_at DESC, created_at DESC, id DESC
                """,
                parameters,
            ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"无法读取记忆列表：{exc}") from exc
        return tuple(MemoryRecord.from_row(row) for row in rows)

    def search(
        self,
        query: str,
        *,
        archived: bool | None = None,
    ) -> tuple[MemoryRecord, ...]:
        clauses = ["instr(lower(content), lower(?)) > 0"]
        parameters: list[object] = [query]
        if archived is not None:
            clauses.append("archived = ?")
            parameters.append(int(archived))
        try:
            rows = self._connection.execute(
                f"""
                SELECT * FROM memories
                WHERE {' AND '.join(clauses)}
                ORDER BY is_core DESC, updated_at DESC, created_at DESC, id DESC
                """,
                tuple(parameters),
            ).fetchall()
        except sqlite3.Error as exc:
            raise MemoryStorageError(f"无法搜索记忆：{exc}") from exc
        return tuple(MemoryRecord.from_row(row) for row in rows)

    def close(self) -> None:
        if self._closed:
            return
        self._connection.close()
        self._closed = True
