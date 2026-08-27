"""Simple blue-lavender UI for manually managed long-term memories."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from memory.memory_models import MemoryDataError, MemoryRecord
from memory.memory_service import (
    MemoryNotFoundError,
    MemoryService,
    MemoryStateError,
)
from memory.memory_store import MemoryStorageError
from ui_resources import load_application_icon


MEMORY_WINDOW_STYLE = """
QWidget#memoryManagerWindow {
    background: #f3f6ff;
    color: #48536f;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei";
    font-size: 13px;
}
QLabel#memoryWindowTitle {
    color: #3d4968;
    font-size: 22px;
    font-weight: 700;
}
QLabel#memoryWindowSubtitle, QLabel#memoryEmptyLabel {
    color: #7c87a5;
}
QLineEdit#memorySearchEdit {
    min-height: 40px;
    color: #46516e;
    background: rgba(255, 255, 255, 235);
    border: 1px solid #cfd7ed;
    border-radius: 12px;
    padding: 0 13px;
}
QLineEdit#memorySearchEdit:focus {
    border: 1px solid #919fdb;
}
QScrollArea#memoryScrollArea {
    background: transparent;
    border: none;
}
QWidget#memoryListContainer {
    background: transparent;
}
QLabel#memorySectionTitle {
    color: #505c7b;
    font-size: 15px;
    font-weight: 700;
    padding: 5px 2px;
}
QFrame#memoryItem {
    background: rgba(255, 255, 255, 242);
    border: 1px solid #d8deef;
    border-radius: 14px;
}
QLabel#memoryContent {
    color: #46516e;
    font-size: 14px;
}
QLabel#memoryMeta {
    color: #929bb3;
    font-size: 10px;
}
QPushButton {
    min-height: 32px;
    border-radius: 9px;
    padding: 0 12px;
    color: #606b88;
    background: #edf0fa;
    border: 1px solid #d5dbed;
}
QPushButton:hover {
    background: #e1e6f6;
}
QPushButton#memoryStarButton {
    min-width: 35px;
    max-width: 35px;
    padding: 0;
    color: #d19a3e;
    font-size: 20px;
    background: #fff8e8;
    border: 1px solid #eee0b9;
}
QPushButton#addMemoryButton {
    color: white;
    background: #8594d7;
    border: 1px solid #7685cb;
    font-weight: 700;
}
QPushButton#deleteMemoryButton {
    color: #b85f70;
    background: #fff1f4;
    border: 1px solid #efd1d8;
}
"""


class MemoryItemWidget(QFrame):
    """Render one record and emit intent without touching SQLite."""

    set_core_requested = Signal(str, bool)
    edit_requested = Signal(str)
    archive_requested = Signal(str)
    restore_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(
        self,
        memory: MemoryRecord,
        *,
        archived_view: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.memory_id = memory.id
        self.setObjectName("memoryItem")
        self.setProperty("memoryState", "archived" if archived_view else "core" if memory.is_core else "active")

        root = QVBoxLayout(self)
        root.setContentsMargins(13, 11, 13, 11)
        root.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        if not archived_view:
            star = QPushButton("★" if memory.is_core else "☆")
            star.setObjectName("memoryStarButton")
            star.setToolTip("取消核心" if memory.is_core else "设为核心")
            star.clicked.connect(
                lambda _checked=False: self.set_core_requested.emit(
                    self.memory_id,
                    not memory.is_core,
                )
            )
            top.addWidget(star, 0, Qt.AlignmentFlag.AlignTop)
        content = QLabel(memory.content)
        content.setObjectName("memoryContent")
        content.setTextFormat(Qt.TextFormat.PlainText)
        content.setWordWrap(True)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(content, 1)
        root.addLayout(top)

        meta = QLabel(f"更新于 {memory.updated_at.replace('T', ' ')}")
        meta.setObjectName("memoryMeta")
        root.addWidget(meta)

        actions = QHBoxLayout()
        actions.setSpacing(7)
        actions.addStretch(1)
        if archived_view:
            restore = QPushButton("恢复")
            restore.clicked.connect(
                lambda _checked=False: self.restore_requested.emit(self.memory_id)
            )
            actions.addWidget(restore)
        else:
            edit = QPushButton("编辑")
            edit.clicked.connect(
                lambda _checked=False: self.edit_requested.emit(self.memory_id)
            )
            actions.addWidget(edit)
            if not memory.is_core:
                archive = QPushButton("归档")
                archive.clicked.connect(
                    lambda _checked=False: self.archive_requested.emit(self.memory_id)
                )
                actions.addWidget(archive)
        delete = QPushButton("删除")
        delete.setObjectName("deleteMemoryButton")
        delete.clicked.connect(
            lambda _checked=False: self.delete_requested.emit(self.memory_id)
        )
        actions.addWidget(delete)
        root.addLayout(actions)


class MemoryManagerWindow(QWidget):
    """Manage core, active, and archived memory through MemoryService only."""

    def __init__(
        self,
        memory_service: MemoryService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._memory_service = memory_service
        self._showing_archived = False
        self._rendered_memory_ids: list[str] = []
        self._setup_ui()
        memory_service.memories_changed.connect(self.refresh)
        self.refresh()

    def _setup_ui(self) -> None:
        self.setObjectName("memoryManagerWindow")
        self.setWindowTitle("记忆管理")
        self.setWindowIcon(load_application_icon())
        self.setMinimumSize(580, 620)
        self.resize(680, 760)
        self.setStyleSheet(MEMORY_WINDOW_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 22)
        root.setSpacing(13)

        self.title_label = QLabel("记忆管理")
        self.title_label.setObjectName("memoryWindowTitle")
        root.addWidget(self.title_label)
        subtitle = QLabel(
            "管理桌宠的长期记忆。部分具有长期价值的聊天内容会自动保存为活跃记忆。"
        )
        subtitle.setObjectName("memoryWindowSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("memorySearchEdit")
        self.search_edit.setPlaceholderText("搜索记忆……")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.refresh)
        root.addWidget(self.search_edit)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("memoryScrollArea")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_container = QWidget()
        self.list_container.setObjectName("memoryListContainer")
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(2, 2, 2, 2)
        self.list_layout.setSpacing(9)
        self.scroll.setWidget(self.list_container)
        root.addWidget(self.scroll, 1)

        buttons = QHBoxLayout()
        self.add_button = QPushButton("新增记忆")
        self.add_button.setObjectName("addMemoryButton")
        self.add_button.clicked.connect(self._prompt_new_memory)
        buttons.addWidget(self.add_button)
        buttons.addStretch(1)
        self.archive_view_button = QPushButton("查看归档")
        self.archive_view_button.setObjectName("archiveViewButton")
        self.archive_view_button.clicked.connect(self.toggle_archive_view)
        buttons.addWidget(self.archive_view_button)
        root.addLayout(buttons)

    def refresh(self, _text: str | None = None) -> None:
        self._clear_list()
        query = self.search_edit.text().strip()
        self._rendered_memory_ids.clear()
        if self._showing_archived:
            memories = self._memory_service.search_memories(query, archived=True)
            self._add_section("归档记忆", memories, archived_view=True)
        else:
            memories = self._memory_service.search_memories(query, archived=False)
            core = tuple(memory for memory in memories if memory.is_core)
            active = tuple(memory for memory in memories if not memory.is_core)
            self._add_section("核心记忆", core, archived_view=False)
            self._add_section("活跃记忆", active, archived_view=False)
        self.list_layout.addStretch(1)

    def _clear_list(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add_section(
        self,
        title: str,
        memories: tuple[MemoryRecord, ...],
        *,
        archived_view: bool,
    ) -> None:
        heading = QLabel(f"{title} · {len(memories)}")
        heading.setObjectName("memorySectionTitle")
        self.list_layout.addWidget(heading)
        if not memories:
            empty = QLabel("暂无记忆")
            empty.setObjectName("memoryEmptyLabel")
            empty.setContentsMargins(8, 4, 8, 9)
            self.list_layout.addWidget(empty)
            return
        for memory in memories:
            row = MemoryItemWidget(memory, archived_view=archived_view)
            row.set_core_requested.connect(self._set_core)
            row.edit_requested.connect(self._prompt_edit_memory)
            row.archive_requested.connect(self._archive_memory)
            row.restore_requested.connect(self._restore_memory)
            row.delete_requested.connect(self._confirm_delete_memory)
            self.list_layout.addWidget(row)
            self._rendered_memory_ids.append(memory.id)

    def toggle_archive_view(self) -> None:
        self._showing_archived = not self._showing_archived
        self.title_label.setText("归档记忆" if self._showing_archived else "记忆管理")
        self.archive_view_button.setText("返回记忆" if self._showing_archived else "查看归档")
        self.add_button.setVisible(not self._showing_archived)
        self.refresh()

    def _prompt_new_memory(self) -> None:
        content, accepted = QInputDialog.getMultiLineText(
            self,
            "新增记忆",
            "记忆内容：",
        )
        if not accepted:
            return
        try:
            self._memory_service.create_memory(content)
        except (MemoryDataError, MemoryStorageError) as exc:
            self._show_error(str(exc))

    def _prompt_edit_memory(self, memory_id: str) -> None:
        try:
            memory = self._memory_service.get_memory(memory_id)
        except (MemoryNotFoundError, MemoryStorageError) as exc:
            self._show_error(str(exc))
            return
        content, accepted = QInputDialog.getMultiLineText(
            self,
            "编辑记忆",
            "记忆内容：",
            memory.content,
        )
        if not accepted:
            return
        try:
            self._memory_service.update_memory(memory_id, content)
        except (MemoryDataError, MemoryNotFoundError, MemoryStorageError) as exc:
            self._show_error(str(exc))

    def _set_core(self, memory_id: str, is_core: bool) -> None:
        try:
            self._memory_service.set_core(memory_id, is_core)
        except (MemoryNotFoundError, MemoryStateError, MemoryStorageError) as exc:
            self._show_error(str(exc))

    def _archive_memory(self, memory_id: str) -> None:
        try:
            self._memory_service.archive_memory(memory_id)
        except (MemoryNotFoundError, MemoryStorageError) as exc:
            self._show_error(str(exc))

    def _restore_memory(self, memory_id: str) -> None:
        try:
            self._memory_service.restore_memory(memory_id)
        except (MemoryNotFoundError, MemoryStorageError) as exc:
            self._show_error(str(exc))

    def _confirm_delete_memory(self, memory_id: str) -> None:
        reply = QMessageBox.question(
            self,
            "永久删除记忆",
            "确定删除这条记忆吗？此操作无法撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._memory_service.delete_memory(memory_id)
        except (MemoryNotFoundError, MemoryStorageError) as exc:
            self._show_error(str(exc))

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, "记忆操作失败", message)

    @property
    def memory_service(self) -> MemoryService:
        return self._memory_service

    @property
    def showing_archived(self) -> bool:
        return self._showing_archived

    @property
    def rendered_memory_ids(self) -> tuple[str, ...]:
        return tuple(self._rendered_memory_ids)

    def showEvent(self, event: QShowEvent) -> None:
        self.refresh()
        super().showEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.hide()
        event.ignore()
