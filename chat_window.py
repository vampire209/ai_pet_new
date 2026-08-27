"""Independent streamed AI chat window with no Todo mutation capability."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ai.chat_service import (
    ChatBusyError,
    ChatService,
    EmptyChatMessageError,
)
from ai.chat_worker import ChatStreamWorker
from ai.deepseek_client import DeepSeekClientError


class ChatInput(QTextEdit):
    """Multiline editor where Enter sends and Shift+Enter inserts a newline."""

    send_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ChatWindow(QWidget):
    """Render one streaming assistant bubble while a pool worker performs I/O."""

    def __init__(
        self,
        service: ChatService | None = None,
        parent: QWidget | None = None,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service or ChatService()
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._worker: ChatStreamWorker | None = None
        self._assistant_label: QLabel | None = None
        self._assistant_text = ""
        self._accept_chunks = False
        self._outcome_received = False
        self._message_records: list[tuple[str, QLabel]] = []
        self._setup_ui()
        if not self._service.is_configured:
            self._show_status(
                "未配置 DeepSeek API Key，请先设置 DEEPSEEK_API_KEY。",
                error=True,
            )

    def _setup_ui(self) -> None:
        self.setWindowTitle("AI 聊天")
        self.setMinimumSize(560, 600)
        self.resize(680, 760)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        heading_layout = QHBoxLayout()
        heading = QLabel("和桌宠聊聊天")
        heading.setStyleSheet("font-size: 20px; font-weight: 600; color: #222222;")
        heading_layout.addWidget(heading)
        heading_layout.addStretch(1)
        self.model_label = QLabel(self._service.config.model)
        self.model_label.setObjectName("chatModelLabel")
        self.model_label.setStyleSheet("color: #64748b; font-size: 12px;")
        heading_layout.addWidget(self.model_label)
        root.addLayout(heading_layout)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("chatScrollArea")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.message_container = QWidget()
        self.message_layout = QVBoxLayout(self.message_container)
        self.message_layout.setContentsMargins(4, 4, 4, 4)
        self.message_layout.setSpacing(10)
        self.message_layout.addStretch(1)
        self.scroll.setWidget(self.message_container)
        root.addWidget(self.scroll, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("chatStatusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(20)
        root.addWidget(self.status_label)

        self.input = ChatInput()
        self.input.setObjectName("chatInput")
        self.input.setPlaceholderText("输入消息……")
        self.input.setMinimumHeight(92)
        self.input.setMaximumHeight(160)
        self.input.setStyleSheet("QTextEdit { padding: 8px; font-size: 14px; }")
        self.input.send_requested.connect(self.send_message)
        root.addWidget(self.input)

        hint = QLabel("Enter 发送 · Shift+Enter 换行")
        hint.setStyleSheet("color: #94a3b8; font-size: 11px;")
        root.addWidget(hint)

        buttons = QHBoxLayout()
        self.clear_button = QPushButton("清空聊天")
        self.clear_button.setObjectName("clearChatButton")
        self.clear_button.clicked.connect(self._confirm_clear)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("stopChatButton")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_generation)
        buttons.addWidget(self.stop_button)
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("sendChatButton")
        self.send_button.setMinimumWidth(84)
        self.send_button.clicked.connect(self.send_message)
        buttons.addWidget(self.send_button)
        root.addLayout(buttons)

    def send_message(self) -> None:
        if self._worker is not None or self._service.is_busy:
            return
        text = self.input.toPlainText().strip()
        if not text:
            self._show_status("消息不能为空。", error=True)
            return
        try:
            messages = self._service.begin_turn(text)
        except (EmptyChatMessageError, ChatBusyError, DeepSeekClientError) as exc:
            self._show_status(str(exc), error=True)
            return

        self.input.clear()
        self._add_message("user", text)
        self._assistant_label = self._add_message("assistant", "…")
        self._assistant_text = ""
        self._accept_chunks = True
        self._outcome_received = False
        self._set_generating(True)
        self._show_status("正在生成……", error=False)

        worker = ChatStreamWorker(self._service, messages)
        self._worker = worker
        worker.signals.chunk.connect(self._on_chunk)
        worker.signals.completed.connect(self._on_completed)
        worker.signals.error.connect(self._on_error)
        worker.signals.stopped.connect(self._on_stopped)
        worker.signals.finished.connect(self._on_finished)
        self._thread_pool.start(worker)

    def stop_generation(self) -> None:
        if self._worker is None:
            return
        self._accept_chunks = False
        self.stop_button.setEnabled(False)
        self._show_status("正在停止生成……", error=False)
        self._worker.request_stop()

    def _on_chunk(self, chunk: str) -> None:
        if not self._accept_chunks or self._assistant_label is None:
            return
        self._assistant_text += chunk
        self._assistant_label.setText(self._assistant_text)
        self._scroll_to_bottom()

    def _on_completed(self, full_text: str) -> None:
        if self._outcome_received:
            return
        self._outcome_received = True
        self._accept_chunks = False
        self._assistant_text = full_text
        if self._assistant_label is not None:
            self._assistant_label.setText(full_text or "（本次没有返回文字）")
        self._service.complete_turn(full_text)
        self._show_status("回答完成。", error=False)

    def _on_stopped(self, _worker_partial: str) -> None:
        if self._outcome_received:
            return
        self._outcome_received = True
        self._accept_chunks = False
        if self._assistant_label is not None and not self._assistant_text:
            self._assistant_label.setText("（已停止）")
        self._service.stop_turn(self._assistant_text)
        self._show_status("已停止生成，已生成内容会保留。", error=False)

    def _on_error(self, message: str) -> None:
        if self._outcome_received:
            return
        self._outcome_received = True
        self._accept_chunks = False
        self._service.fail_turn()
        if self._assistant_label is not None:
            self._assistant_label.setText(f"请求失败：{message}")
        self._show_status(message, error=True)

    def _on_finished(self) -> None:
        if not self._outcome_received and self._service.is_busy:
            self._service.fail_turn()
        self._worker = None
        self._set_generating(False)
        self._scroll_to_bottom()

    def _set_generating(self, generating: bool) -> None:
        self.send_button.setEnabled(not generating)
        self.stop_button.setEnabled(generating)
        self.clear_button.setEnabled(not generating)

    def _add_message(self, role: str, text: str) -> QLabel:
        frame = QFrame()
        frame.setObjectName("userMessage" if role == "user" else "assistantMessage")
        frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        frame.setStyleSheet(
            "QFrame#userMessage { background: #e8f2ff; border: 1px solid #c6dcf5; "
            "border-radius: 8px; }"
            "QFrame#assistantMessage { background: #f4f4f5; border: 1px solid #dddddf; "
            "border-radius: 8px; }"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(4)
        author = QLabel("你" if role == "user" else "桌宠")
        author.setStyleSheet("font-weight: 600; color: #475569;")
        layout.addWidget(author)
        content = QLabel(text)
        content.setObjectName("userMessageText" if role == "user" else "assistantMessageText")
        content.setWordWrap(True)
        content.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        content.setStyleSheet("color: #1f2937; font-size: 14px;")
        layout.addWidget(content)
        insert_at = max(0, self.message_layout.count() - 1)
        self.message_layout.insertWidget(insert_at, frame)
        self._message_records.append((role, content))
        self._scroll_to_bottom()
        return content

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(
            0,
            lambda: self.scroll.verticalScrollBar().setValue(
                self.scroll.verticalScrollBar().maximum()
            ),
        )

    def _show_status(self, message: str, *, error: bool) -> None:
        color = "#b42318" if error else "#287a3f"
        self.status_label.setStyleSheet(f"color: {color}; font-size: 12px;")
        self.status_label.setText(message)

    def _confirm_clear(self) -> None:
        if self.is_generating:
            return
        reply = QMessageBox.question(
            self,
            "清空聊天",
            "确定清空当前聊天和上下文吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.clear_chat()

    def clear_chat(self) -> None:
        if self.is_generating:
            return
        try:
            self._service.clear()
        except ChatBusyError as exc:
            self._show_status(str(exc), error=True)
            return
        while self.message_layout.count():
            item = self.message_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self.message_layout.addStretch(1)
        self._message_records.clear()
        self._assistant_label = None
        self._assistant_text = ""
        self._show_status("聊天和上下文已清空。", error=False)

    def shutdown(self) -> None:
        if self._worker is not None:
            self._accept_chunks = False
            self._worker.request_stop()
        self.hide()

    @property
    def is_generating(self) -> bool:
        return self._worker is not None

    @property
    def message_texts(self) -> tuple[tuple[str, str], ...]:
        return tuple((role, label.text()) for role, label in self._message_records)

    @property
    def service(self) -> ChatService:
        return self._service

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._worker is not None:
            self.stop_generation()
        self.hide()
        event.ignore()
