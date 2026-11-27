"""Transparent desktop pet with todo and queued reminder integration."""

from __future__ import annotations

import sys

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QContextMenuEvent,
    QMouseEvent,
    QPixmap,
    QScreen,
    QShowEvent,
)
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QMessageBox

from animation_manager import AnimationManager
from animation_state import AnimationStateController
from ai.chat_service import ChatService
from ai.profile_service import ProfileService
from chat_window import ChatWindow
from config import AppConfig
from models.reminder import Reminder
from models.task import Task, TaskDataError
from memory.memory_service import MemoryService
from memory_window import MemoryManagerWindow
from reminder_bubble import ReminderBubble
from reminder_manager import ReminderManager
from profile_window import ProfileWindow
from word_learning.service import WordLearningService
from word_learning.window import WordLearningWindow
from todo_manager import TodoManager
from todo_manager import TodoStorageError
from todo_window import TodoWindow


class PetWindow(QLabel):
    """Frameless topmost pet with threshold-based left-button dragging."""

    def __init__(
        self,
        config: AppConfig,
        todo_manager: TodoManager,
        reminder_manager: ReminderManager | None = None,
        parent=None,
        chat_service: ChatService | None = None,
        profile_service: ProfileService | None = None,
        memory_service: MemoryService | None = None,
        word_learning_service: WordLearningService | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._todo_manager = todo_manager
        self._reminder_manager = reminder_manager or ReminderManager(todo_manager)
        self._todo_window: TodoWindow | None = None
        self._chat_service = chat_service
        self._chat_window: ChatWindow | None = None
        self._profile_service = profile_service or ProfileService()
        self._profile_window: ProfileWindow | None = None
        self._memory_service = memory_service
        self._memory_window: MemoryManagerWindow | None = None
        self._word_learning_service = word_learning_service
        self._word_learning_window: WordLearningWindow | None = None
        self._reminder_bubbles: dict[str, ReminderBubble] = {}
        self._press_global: QPoint | None = None
        self._drag_offset = QPoint()
        self._dragging = False
        self._screen_signal_connected = False

        self._setup_window()

        screen = QApplication.primaryScreen()
        initial_dpr = screen.devicePixelRatio() if screen is not None else 1.0
        self._animation_manager = AnimationManager(
            config,
            device_pixel_ratio=initial_dpr,
            parent=self,
        )
        self._animation_manager.frame_changed.connect(self._show_frame)
        self._state_controller = AnimationStateController(
            self._animation_manager,
            parent=self,
        )
        self._reminder_manager.reminder_ready.connect(self._on_reminder_due)
        self._reminder_manager.scan_failed.connect(self._on_reminder_scan_failed)
        self._reminder_manager.waiting_changed.connect(self.set_waiting)
        self._reminder_manager.active_invalidated.connect(
            self._close_invalidated_bubble
        )
        self._state_controller.set_waiting(self._reminder_manager.waiting_state)

        if self._animation_manager.current_animation is None:
            self._show_missing_asset_message()
        self._set_initial_position()

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: transparent;")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("按住左键拖动，双击打开待办，右键打开功能菜单")

    def _show_frame(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self.setText("")
        self.setPixmap(pixmap)
        logical_size = pixmap.deviceIndependentSize().toSize()
        if not logical_size.isEmpty():
            self.setFixedSize(logical_size)

    def _show_missing_asset_message(self) -> None:
        self.clear()
        self.setText("桌宠 idle 素材缺失")
        self.setStyleSheet(
            "background: rgba(40, 40, 40, 190); color: white; "
            "padding: 12px; border-radius: 8px;"
        )
        self.adjustSize()

    def _set_initial_position(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = available.x() + available.width() - self.width() - self._config.margin_x
        y = available.y() + available.height() - self.height() - self._config.margin_y
        self.move(max(available.x(), x), max(available.y(), y))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and not self._screen_signal_connected:
            handle.screenChanged.connect(self._on_screen_changed)
            self._screen_signal_connected = True
            self._on_screen_changed(handle.screen())

    def _on_screen_changed(self, screen: QScreen | None) -> None:
        if screen is not None:
            self._animation_manager.set_device_pixel_ratio(screen.devicePixelRatio())
        for bubble in self._reminder_bubbles.values():
            self._position_bubble(bubble)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self._press_global = event.globalPosition().toPoint()
        self._drag_offset = self._press_global - self.pos()
        self._dragging = False
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._press_global is None
            or not (event.buttons() & Qt.MouseButton.LeftButton)
        ):
            super().mouseMoveEvent(event)
            return

        current_global = event.globalPosition().toPoint()
        if not self._dragging:
            distance = (current_global - self._press_global).manhattanLength()
            threshold = QApplication.styleHints().startDragDistance()
            if distance < threshold:
                event.accept()
                return
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._state_controller.set_moving(True)

        self.move(current_global - self._drag_offset)
        for bubble in self._reminder_bubbles.values():
            self._position_bubble(bubble)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        if self._dragging:
            self._state_controller.set_moving(False)
        self._dragging = False
        self._press_global = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._dragging:
            super().mouseDoubleClickEvent(event)
            return
        self._press_global = None
        self._open_todo_window()
        event.accept()

    def _open_todo_window(self, task_id: str | None = None) -> None:
        if self._todo_window is None:
            self._todo_window = TodoWindow(self._todo_manager)
        else:
            self._todo_window.refresh_tasks()

        if self._todo_window.isMinimized():
            state = self._todo_window.windowState() & ~Qt.WindowState.WindowMinimized
            self._todo_window.setWindowState(state | Qt.WindowState.WindowActive)
        self._todo_window.show()
        self._todo_window.raise_()
        self._todo_window.activateWindow()

    def _open_chat_window(self) -> None:
        if self._chat_window is None:
            word_learning_service = self._ensure_word_learning_service()
            self._chat_window = ChatWindow(
                self._chat_service or ChatService(),
                todo_manager=self._todo_manager,
                profile_service=self._profile_service,
                memory_service=self._memory_service,
                word_learning_service=word_learning_service,
            )
            self._chat_window.word_learning_requested.connect(
                self._open_word_learning_window
            )
            self._chat_window.word_learning_words_requested.connect(
                self._open_word_learning_words
            )
        if self._chat_window.isMinimized():
            state = self._chat_window.windowState() & ~Qt.WindowState.WindowMinimized
            self._chat_window.setWindowState(state | Qt.WindowState.WindowActive)
        self._chat_window.show()
        self._chat_window.raise_()
        self._chat_window.activateWindow()

    def _open_profile_window(self) -> None:
        if self._profile_window is None:
            self._profile_window = ProfileWindow(self._profile_service)
        if self._profile_window.isMinimized():
            state = self._profile_window.windowState() & ~Qt.WindowState.WindowMinimized
            self._profile_window.setWindowState(state | Qt.WindowState.WindowActive)
        self._profile_window.show()
        self._profile_window.raise_()
        self._profile_window.activateWindow()

    def _open_memory_window(self) -> None:
        if self._memory_service is None:
            self._memory_service = MemoryService()
        if self._memory_window is None:
            self._memory_window = MemoryManagerWindow(self._memory_service)
        if self._memory_window.isMinimized():
            state = self._memory_window.windowState() & ~Qt.WindowState.WindowMinimized
            self._memory_window.setWindowState(state | Qt.WindowState.WindowActive)
        self._memory_window.show()
        self._memory_window.raise_()
        self._memory_window.activateWindow()

    def _ensure_word_learning_service(self) -> WordLearningService:
        if self._word_learning_service is None:
            data_path = self._todo_manager.data_path.parent / "word_learning.db"
            self._word_learning_service = WordLearningService(data_path)
        return self._word_learning_service

    def _open_word_learning_window(self, page: str = "today") -> None:
        service = self._ensure_word_learning_service()
        if self._word_learning_window is None:
            self._word_learning_window = WordLearningWindow(
                service,
                profile_service=self._profile_service,
                todo_manager=self._todo_manager,
            )
        self._word_learning_window.open_page(page)
        if self._word_learning_window.isMinimized():
            state = (
                self._word_learning_window.windowState()
                & ~Qt.WindowState.WindowMinimized
            )
            self._word_learning_window.setWindowState(
                state | Qt.WindowState.WindowActive
            )
        self._word_learning_window.show()
        self._word_learning_window.raise_()
        self._word_learning_window.activateWindow()

    def _open_word_learning_words(self, word_ids) -> None:
        service = self._ensure_word_learning_service()
        words = [
            word
            for word_id in tuple(word_ids)
            if (word := service.get_word(str(word_id))) is not None
        ]
        if not words:
            return
        self._open_word_learning_window("today")
        if self._word_learning_window is not None:
            self._word_learning_window.start_session("reading", words=words)

    def _on_reminder_due(self, task: Task, reminder: Reminder) -> None:
        existing = self._reminder_bubbles.get(reminder.id)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return

        bubble = ReminderBubble(task, reminder)
        bubble.view_requested.connect(self._view_reminder_task)
        bubble.close_requested.connect(self._acknowledge_reminder)
        bubble.complete_requested.connect(self._complete_reminder_task)
        bubble.snooze_requested.connect(self._snooze_reminder)
        bubble.dismissed.connect(self._reminder_bubble_finished)
        self._reminder_bubbles[reminder.id] = bubble
        self._state_controller.set_reminding(True)
        bubble.adjustSize()
        self._position_bubble(bubble)
        bubble.show()
        bubble.raise_()

    def _view_reminder_task(self, task_id: str, reminder_id: str) -> None:
        self._open_todo_window(task_id)
        self._close_bubble(reminder_id)

    def _acknowledge_reminder(self, task_id: str, reminder_id: str) -> None:
        try:
            self._todo_manager.acknowledge_reminder(task_id, reminder_id)
        except TodoStorageError as exc:
            QMessageBox.critical(None, "提醒保存失败", str(exc))
            return
        if self._todo_window is not None:
            self._todo_window.refresh_tasks()
        self._close_bubble(reminder_id)

    def _complete_reminder_task(self, task_id: str, reminder_id: str) -> None:
        try:
            self._todo_manager.complete_task(
                task_id,
                reminder_id,
                now=self._reminder_manager.now(),
            )
        except TodoStorageError as exc:
            QMessageBox.critical(None, "待办保存失败", str(exc))
            return
        if self._todo_window is not None:
            self._todo_window.refresh_tasks()
        self._close_bubble(reminder_id)

    def _snooze_reminder(
        self,
        task_id: str,
        reminder_id: str,
        minutes: int,
    ) -> None:
        try:
            self._todo_manager.snooze_reminder(
                task_id,
                reminder_id,
                minutes,
                now=self._reminder_manager.now(),
            )
        except (TaskDataError, TodoStorageError) as exc:
            QMessageBox.critical(None, "提醒保存失败", str(exc))
            return
        if self._todo_window is not None:
            self._todo_window.refresh_tasks()
        self._close_bubble(reminder_id)

    def _close_bubble(self, reminder_id: str) -> None:
        bubble = self._reminder_bubbles.get(reminder_id)
        if bubble is not None:
            bubble.close()

    def _close_invalidated_bubble(self, reminder_id: str) -> None:
        self._close_bubble(reminder_id)

    def _reminder_bubble_finished(self, reminder_id: str) -> None:
        bubble = self._reminder_bubbles.pop(reminder_id, None)
        self._reminder_manager.reminder_finished(reminder_id)
        self._state_controller.set_reminding(bool(self._reminder_bubbles))
        if bubble is not None:
            bubble.deleteLater()

    def _position_bubble(self, bubble: ReminderBubble) -> None:
        screen = QApplication.screenAt(self.geometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        bubble.adjustSize()
        gap = 12
        left_x = self.x() - bubble.width() - gap
        right_x = self.x() + self.width() + gap
        x = left_x if left_x >= area.left() else right_x
        max_x = area.right() - bubble.width() + 1
        x = max(area.left(), min(x, max_x))
        centered_y = self.y() + (self.height() - bubble.height()) // 2
        max_y = area.bottom() - bubble.height() + 1
        y = max(area.top(), min(centered_y, max_y))
        bubble.move(x, y)

    @staticmethod
    def _on_reminder_scan_failed(message: str) -> None:
        print(f"[提醒扫描失败] {message}", file=sys.stderr)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = self._create_context_menu()
        menu.exec(event.globalPos())
        event.accept()

    def _create_context_menu(self) -> QMenu:
        menu = QMenu(self)
        todo_action = QAction("打开待办", menu)
        todo_action.triggered.connect(lambda _checked=False: self._open_todo_window())
        menu.addAction(todo_action)
        chat_action = QAction("AI聊天", menu)
        chat_action.triggered.connect(lambda _checked=False: self._open_chat_window())
        menu.addAction(chat_action)
        profile_action = QAction("桌宠设置", menu)
        profile_action.triggered.connect(
            lambda _checked=False: self._open_profile_window()
        )
        menu.addAction(profile_action)
        memory_action = QAction("记忆管理", menu)
        memory_action.triggered.connect(
            lambda _checked=False: self._open_memory_window()
        )
        menu.addAction(memory_action)
        menu.addSeparator()

        exit_action = QAction("退出", menu)
        exit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(exit_action)
        return menu

    def set_waiting(self, active: bool) -> None:
        """Apply the centralized future-reminder wait state."""
        self._state_controller.set_waiting(active)

    def set_reminding(self, active: bool) -> None:
        """Reserved integration point for a future reminder manager."""
        self._state_controller.set_reminding(active)

    @property
    def animation_state(self) -> str:
        return self._state_controller.current_state

    @property
    def current_animation(self) -> str | None:
        return self._animation_manager.current_animation

    @property
    def current_frame_index(self) -> int:
        return self._animation_manager.current_frame_index

    @property
    def todo_window(self) -> TodoWindow | None:
        return self._todo_window

    @property
    def chat_window(self) -> ChatWindow | None:
        return self._chat_window

    @property
    def profile_window(self) -> ProfileWindow | None:
        return self._profile_window

    @property
    def profile_service(self) -> ProfileService:
        return self._profile_service

    @property
    def memory_window(self) -> MemoryManagerWindow | None:
        return self._memory_window

    @property
    def memory_service(self) -> MemoryService | None:
        return self._memory_service

    @property
    def word_learning_window(self) -> WordLearningWindow | None:
        return self._word_learning_window

    @property
    def word_learning_service(self) -> WordLearningService | None:
        return self._word_learning_service

    @property
    def reminder_dialogs(self) -> tuple[ReminderBubble, ...]:
        """Phase 3/4 compatibility alias for the active reminder surface."""
        return tuple(self._reminder_bubbles.values())

    @property
    def reminder_bubbles(self) -> tuple[ReminderBubble, ...]:
        return tuple(self._reminder_bubbles.values())

    def closeEvent(self, event: QCloseEvent) -> None:
        self._animation_manager.stop()
        self._reminder_manager.stop()
        self._reminder_manager.clear_queue()
        if self._todo_window is not None:
            self._todo_window.hide()
        if self._chat_window is not None:
            self._chat_window.shutdown()
        if self._profile_window is not None:
            self._profile_window.hide()
        if self._memory_window is not None:
            self._memory_window.hide()
        if self._word_learning_window is not None:
            self._word_learning_window.hide()
        if self._memory_service is not None:
            self._memory_service.close()
        if self._word_learning_service is not None:
            self._word_learning_service.close()
        for bubble in list(self._reminder_bubbles.values()):
            bubble.close()
        self._reminder_bubbles.clear()
        super().closeEvent(event)
