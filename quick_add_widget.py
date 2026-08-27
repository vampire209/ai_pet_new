"""Preview-first Todo widget for local natural-language task creation."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.recurrence import REPEAT_DAILY, REPEAT_WEEKLY, WEEKDAY_OPTIONS
from models.reminder import format_offset_duration
from natural_language_parser import NaturalLanguageParser, ParsedTask


class QuickAddWidget(QFrame):
    """Parse text, show a visible preview, then request normal Task creation."""

    create_requested = Signal(object)

    def __init__(
        self,
        parser: NaturalLanguageParser | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._parser = parser or NaturalLanguageParser()
        self._parsed_task: ParsedTask | None = None
        self.setObjectName("quickAddPanel")
        self.setStyleSheet(
            "QFrame#quickAddPanel { border: 1px solid #d9e2ef; "
            "border-radius: 8px; background: #f8fbff; }"
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(8)

        heading = QLabel("快速添加")
        heading.setStyleSheet("font-size: 14px; font-weight: 600; color: #334155;")
        root.addWidget(heading)

        input_layout = QHBoxLayout()
        input_layout.setSpacing(8)
        self.input = QLineEdit()
        self.input.setObjectName("quickTaskInput")
        self.input.setPlaceholderText("例如：明天下午三点提醒我交作业，提前30分钟")
        self.input.setMinimumHeight(38)
        self.input.setStyleSheet("QLineEdit { padding: 6px 10px; font-size: 14px; }")
        self.input.returnPressed.connect(self.parse_input)
        input_layout.addWidget(self.input, 1)

        parse_button = QPushButton("解析")
        parse_button.setObjectName("parseQuickTaskButton")
        parse_button.setMinimumSize(72, 38)
        parse_button.clicked.connect(self.parse_input)
        input_layout.addWidget(parse_button)
        root.addLayout(input_layout)

        self.status_label = QLabel("")
        self.status_label.setObjectName("quickParseStatus")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #b42318; font-size: 12px;")
        self.status_label.setMinimumHeight(18)
        root.addWidget(self.status_label)

        self.preview_frame = QFrame()
        self.preview_frame.setObjectName("quickPreviewFrame")
        self.preview_frame.setStyleSheet(
            "QFrame#quickPreviewFrame { border: 1px solid #bad2ee; "
            "border-radius: 6px; background: #ffffff; }"
        )
        preview_layout = QVBoxLayout(self.preview_frame)
        preview_layout.setContentsMargins(12, 10, 12, 10)
        preview_layout.setSpacing(8)

        preview_title = QLabel("请确认解析结果")
        preview_title.setStyleSheet("font-weight: 600; color: #1f3b5b;")
        preview_layout.addWidget(preview_title)

        fields = QGridLayout()
        fields.setHorizontalSpacing(12)
        fields.setVerticalSpacing(5)
        self.title_value = self._add_field(fields, 0, "任务：", "quickPreviewTitle")
        self.time_value = self._add_field(fields, 1, "时间：", "quickPreviewTime")
        self.repeat_value = self._add_field(fields, 2, "重复：", "quickPreviewRepeat")
        self.advance_value = self._add_field(
            fields,
            3,
            "提前提醒：",
            "quickPreviewAdvance",
        )
        preview_layout.addLayout(fields)

        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel_button = QPushButton("取消")
        cancel_button.setObjectName("cancelQuickTaskButton")
        cancel_button.clicked.connect(self.cancel_preview)
        actions.addWidget(cancel_button)
        confirm_button = QPushButton("确认创建")
        confirm_button.setObjectName("confirmQuickTaskButton")
        confirm_button.clicked.connect(self.confirm_creation)
        actions.addWidget(confirm_button)
        preview_layout.addLayout(actions)
        root.addWidget(self.preview_frame)
        self.preview_frame.setVisible(False)

    @staticmethod
    def _add_field(
        layout: QGridLayout,
        row: int,
        caption: str,
        object_name: str,
    ) -> QLabel:
        caption_label = QLabel(caption)
        caption_label.setStyleSheet("color: #64748b;")
        layout.addWidget(caption_label, row, 0)
        value_label = QLabel("")
        value_label.setObjectName(object_name)
        value_label.setWordWrap(True)
        value_label.setStyleSheet("color: #1f2937;")
        layout.addWidget(value_label, row, 1)
        return value_label

    def parse_input(self) -> None:
        result = self._parser.parse(self.input.text())
        if not result.is_valid:
            self._parsed_task = None
            self.preview_frame.setVisible(False)
            self.status_label.setStyleSheet("color: #b42318; font-size: 12px;")
            self.status_label.setText("；".join(result.errors))
            return

        self._parsed_task = result
        self.title_value.setText(result.title)
        self.time_value.setText(result.due_at.strftime("%Y-%m-%d %H:%M"))
        self.repeat_value.setText(self._format_repeat(result))
        self.advance_value.setText(self._format_advance(result))
        self.status_label.setStyleSheet("color: #b42318; font-size: 12px;")
        self.status_label.clear()
        self.preview_frame.setVisible(True)

    @staticmethod
    def _format_repeat(result: ParsedTask) -> str:
        if result.repeat_type == REPEAT_DAILY:
            return "每天"
        if result.repeat_type == REPEAT_WEEKLY:
            labels = dict(WEEKDAY_OPTIONS)
            selected = "、".join(
                labels[weekday]
                for weekday in result.repeat_weekdays
                if weekday in labels
            )
            return f"每{selected}"
        return "不重复"

    @staticmethod
    def _format_advance(result: ParsedTask) -> str:
        if not result.advance_minutes:
            return "无"
        return "、".join(
            format_offset_duration(minutes)
            for minutes in result.advance_minutes
        )

    def confirm_creation(self) -> None:
        if self._parsed_task is not None and self._parsed_task.is_valid:
            self.create_requested.emit(self._parsed_task)

    def cancel_preview(self) -> None:
        self._parsed_task = None
        self.preview_frame.setVisible(False)
        self.status_label.clear()

    def creation_succeeded(self, title: str) -> None:
        self._parsed_task = None
        self.preview_frame.setVisible(False)
        self.input.clear()
        self.status_label.setStyleSheet("color: #287a3f; font-size: 12px;")
        self.status_label.setText(f"已创建：{title}")

    def show_creation_error(self, message: str) -> None:
        self.status_label.setStyleSheet("color: #b42318; font-size: 12px;")
        self.status_label.setText(message)

    @property
    def parsed_task(self) -> ParsedTask | None:
        return self._parsed_task
