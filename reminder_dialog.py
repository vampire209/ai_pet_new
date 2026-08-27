"""Always-on-top dialog for one advance or final reminder."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.reminder import Reminder, format_offset_duration
from models.task import Task


def format_reminder_message(reminder: Reminder) -> str:
    if reminder.type == "advance":
        return f"还有：{format_offset_duration(reminder.offset_minutes)}"
    return "时间到了"


class ReminderDialog(QDialog):
    """Show exactly one queued reminder without completing its task."""

    view_requested = Signal(str, str)
    acknowledge_requested = Signal(str, str)

    def __init__(
        self,
        task: Task,
        reminder: Reminder,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task_id = task.id
        self.reminder_id = reminder.id
        self.setWindowTitle("提醒")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setMinimumSize(360, 210)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        title = QLabel("提醒")
        title.setStyleSheet("font-size: 20px; font-weight: 600; color: #d35400;")
        title.setMinimumHeight(34)
        layout.addWidget(title)

        task_label = QLabel(task.text)
        task_label.setObjectName("reminderTaskText")
        task_label.setWordWrap(True)
        task_label.setStyleSheet("font-size: 16px; color: #222222;")
        task_label.setMinimumHeight(34)
        layout.addWidget(task_label)

        message = QLabel(format_reminder_message(reminder))
        message.setObjectName("reminderMessage")
        message.setStyleSheet("font-size: 14px; color: #666666;")
        message.setMinimumHeight(28)
        layout.addWidget(message)
        layout.addStretch(1)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        view_button = QPushButton("查看待办")
        view_button.setObjectName("viewTodoButton")
        view_button.setMinimumSize(92, 36)
        view_button.clicked.connect(self._view)
        button_layout.addWidget(view_button)

        acknowledge_button = QPushButton("知道了")
        acknowledge_button.setObjectName("acknowledgeButton")
        acknowledge_button.setMinimumSize(82, 36)
        acknowledge_button.clicked.connect(self._acknowledge)
        button_layout.addWidget(acknowledge_button)
        layout.addLayout(button_layout)

    def _view(self) -> None:
        self.view_requested.emit(self.task_id, self.reminder_id)
        self.accept()

    def _acknowledge(self) -> None:
        self.acknowledge_requested.emit(self.task_id, self.reminder_id)
        self.accept()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
