"""Lightweight frameless reminder bubble displayed beside the desktop pet."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.reminder import Reminder, SNOOZE_MINUTES, format_offset_duration
from models.task import Task


def reminder_type_text(reminder: Reminder) -> str:
    if reminder.type == "advance":
        return "提前提醒"
    if reminder.type == "snooze":
        return "稍后提醒"
    return "到点提醒"


def reminder_bubble_message(task: Task, reminder: Reminder) -> str:
    if reminder.type == "advance":
        return f"还有 {format_offset_duration(reminder.offset_minutes)}：{task.text}"
    if reminder.type == "snooze":
        return f"稍后时间到了：{task.text}"
    return f"时间到了：{task.text}"


class ReminderBubble(QWidget):
    """One actionable reminder surface; persistence stays in TodoManager."""

    complete_requested = Signal(str, str)
    snooze_requested = Signal(str, str, int)
    close_requested = Signal(str, str)
    view_requested = Signal(str, str)
    dismissed = Signal(str)

    def __init__(
        self,
        task: Task,
        reminder: Reminder,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.task_id = task.id
        self.reminder_id = reminder.id
        self._dismissed_emitted = False
        self.setObjectName("reminderBubble")
        self.setWindowTitle("提醒")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumWidth(380)
        self.setMaximumWidth(460)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        panel = QFrame()
        panel.setObjectName("bubblePanel")
        panel.setStyleSheet(
            "QFrame#bubblePanel { background: rgba(255, 252, 247, 242); "
            "border: 1px solid rgba(211, 132, 58, 180); border-radius: 14px; } "
            "QPushButton { min-height: 30px; padding: 3px 9px; }"
        )
        root.addWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 15, 18, 14)
        layout.setSpacing(9)

        type_label = QLabel(reminder_type_text(reminder))
        type_label.setObjectName("reminderTypeLabel")
        type_label.setStyleSheet("font-size: 16px; font-weight: 650; color: #c65d12;")
        type_label.setMinimumHeight(28)
        layout.addWidget(type_label)

        message = QLabel(reminder_bubble_message(task, reminder))
        message.setObjectName("reminderMessage")
        message.setWordWrap(True)
        message.setStyleSheet("font-size: 14px; color: #252525;")
        message.setMinimumHeight(36)
        layout.addWidget(message)

        snooze_label = QLabel("稍后提醒：")
        snooze_label.setStyleSheet("font-size: 12px; color: #666666;")
        layout.addWidget(snooze_label)
        snooze_grid = QGridLayout()
        snooze_grid.setHorizontalSpacing(7)
        snooze_grid.setVerticalSpacing(6)
        for index, minutes in enumerate(SNOOZE_MINUTES):
            button = QPushButton(f"{minutes}分钟" if minutes < 60 else "1小时")
            button.setObjectName(f"snooze{minutes}Button")
            button.clicked.connect(
                lambda _checked=False, value=minutes: self.snooze_requested.emit(
                    self.task_id,
                    self.reminder_id,
                    value,
                )
            )
            snooze_grid.addWidget(button, index // 2, index % 2)
        layout.addLayout(snooze_grid)

        action_layout = QHBoxLayout()
        action_layout.setSpacing(7)
        complete_button = QPushButton("完成")
        complete_button.setObjectName("completeTaskButton")
        complete_button.clicked.connect(
            lambda: self.complete_requested.emit(self.task_id, self.reminder_id)
        )
        action_layout.addWidget(complete_button)

        view_button = QPushButton("查看待办")
        view_button.setObjectName("viewTodoButton")
        view_button.clicked.connect(
            lambda: self.view_requested.emit(self.task_id, self.reminder_id)
        )
        action_layout.addWidget(view_button)

        close_button = QPushButton("关闭")
        close_button.setObjectName("acknowledgeButton")
        close_button.clicked.connect(
            lambda: self.close_requested.emit(self.task_id, self.reminder_id)
        )
        action_layout.addWidget(close_button)
        layout.addLayout(action_layout)

    def closeEvent(self, event: QCloseEvent) -> None:
        super().closeEvent(event)
        if not self._dismissed_emitted:
            self._dismissed_emitted = True
            self.dismissed.emit(self.reminder_id)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.raise_()
