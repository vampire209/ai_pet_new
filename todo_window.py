"""Todo UI with optional local event and multiple advance reminders."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QDate, QTime, Qt, Signal
from PySide6.QtGui import QCloseEvent, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from models.reminder import REMINDER_OFFSET_OPTIONS, OFFSET_LABELS
from models.task import Task, TaskDataError, parse_local_datetime
from todo_manager import TodoManager, TodoStorageError


def _default_event_datetime() -> datetime:
    return (datetime.now() + timedelta(minutes=5)).replace(second=0, microsecond=0)


def _editors_to_iso(date_edit: QDateEdit, time_edit: QTimeEdit) -> str:
    date = date_edit.date()
    time = time_edit.time()
    return datetime(
        date.year(),
        date.month(),
        date.day(),
        time.hour(),
        time.minute(),
        0,
    ).isoformat(timespec="seconds")


def _set_editors(date_edit: QDateEdit, time_edit: QTimeEdit, value: datetime) -> None:
    date_edit.setDate(QDate(value.year, value.month, value.day))
    time_edit.setTime(QTime(value.hour, value.minute))


def format_event_time(event_at: str | None) -> str:
    event = parse_local_datetime(event_at)
    if event is None:
        return ""
    if event.year == datetime.now().year:
        return f"{event.month:02d}月{event.day:02d}日 {event.hour:02d}:{event.minute:02d}"
    return (
        f"{event.year}年{event.month:02d}月{event.day:02d}日 "
        f"{event.hour:02d}:{event.minute:02d}"
    )


def format_reminder_summary(offsets: tuple[int, ...]) -> str:
    labels = [OFFSET_LABELS[value] for value in offsets if value in OFFSET_LABELS]
    return f"提醒：{chr(12289).join(labels)}" if labels else ""


def _create_reminder_selector(
    object_prefix: str,
) -> tuple[QWidget, dict[int, QCheckBox]]:
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 4, 0, 0)
    layout.setSpacing(6)
    label = QLabel("提前提醒：")
    label.setMinimumHeight(24)
    layout.addWidget(label)
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(14)
    grid.setVerticalSpacing(5)
    checks: dict[int, QCheckBox] = {}
    for index, (offset, text) in enumerate(REMINDER_OFFSET_OPTIONS):
        checkbox = QCheckBox(text)
        checkbox.setObjectName(f"{object_prefix}Reminder{offset}")
        checkbox.setMinimumHeight(28)
        grid.addWidget(checkbox, index // 3, index % 3)
        checks[offset] = checkbox
    layout.addLayout(grid)
    return container, checks


def _selected_offsets(checks: dict[int, QCheckBox]) -> tuple[int, ...]:
    return tuple(
        offset
        for offset, _label in REMINDER_OFFSET_OPTIONS
        if checks[offset].isChecked()
    )


class TaskEditDialog(QDialog):
    """Edit task text and its optional one-time local event."""

    def __init__(self, task: Task, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._task = task
        self.setWindowTitle("编辑待办")
        self.setMinimumSize(560, 430)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        layout.addWidget(QLabel("待办内容："))
        self.text_input = QLineEdit(task.text)
        self.text_input.setObjectName("editTaskInput")
        self.text_input.setMinimumHeight(38)
        self.text_input.setStyleSheet("QLineEdit { padding: 6px 10px; font-size: 14px; }")
        layout.addWidget(self.text_input)

        self.time_checkbox = QCheckBox("设置时间")
        self.time_checkbox.setObjectName("editTimeEnabled")
        self.time_checkbox.setMinimumHeight(30)
        layout.addWidget(self.time_checkbox)

        self.time_frame = QFrame()
        time_layout = QVBoxLayout(self.time_frame)
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(8)
        date_time_layout = QHBoxLayout()
        date_time_layout.setSpacing(10)
        date_time_layout.addWidget(QLabel("日期："))
        self.date_edit = QDateEdit()
        self.date_edit.setObjectName("editEventDate")
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setMinimumHeight(36)
        date_time_layout.addWidget(self.date_edit)
        date_time_layout.addWidget(QLabel("时间："))
        self.time_edit = QTimeEdit()
        self.time_edit.setObjectName("editEventTime")
        self.time_edit.setDisplayFormat("HH:mm")
        self.time_edit.setMinimumHeight(36)
        self.time_edit.setKeyboardTracking(False)
        date_time_layout.addWidget(self.time_edit)
        date_time_layout.addStretch(1)
        time_layout.addLayout(date_time_layout)
        reminder_selector, self.reminder_checks = _create_reminder_selector("edit")
        time_layout.addWidget(reminder_selector)
        layout.addWidget(self.time_frame)

        existing = parse_local_datetime(task.event_at)
        self.time_checkbox.setChecked(existing is not None)
        _set_editors(
            self.date_edit,
            self.time_edit,
            existing or _default_event_datetime(),
        )
        for offset in task.reminder_offsets:
            checkbox = self.reminder_checks.get(offset)
            if checkbox is not None:
                checkbox.setChecked(True)
        self.time_frame.setVisible(existing is not None)
        self.time_checkbox.toggled.connect(self.time_frame.setVisible)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_values(self) -> tuple[str, str | None, tuple[int, ...]]:
        event_at = (
            _editors_to_iso(self.date_edit, self.time_edit)
            if self.time_checkbox.isChecked()
            else None
        )
        offsets = _selected_offsets(self.reminder_checks) if event_at is not None else ()
        return self.text_input.text().strip(), event_at, offsets

    def _accept_if_valid(self) -> None:
        text, event_at, _offsets = self.get_values()
        if not text:
            QMessageBox.warning(self, "内容为空", "待办内容不能为空。")
            return
        event = parse_local_datetime(event_at)
        if (
            event is not None
            and event_at != self._task.event_at
            and event <= datetime.now()
        ):
            QMessageBox.warning(self, "时间无效", "提醒时间必须晚于当前时间。")
            return
        self.accept()


class TaskRow(QFrame):
    """One task row; mutations are requested through signals."""

    toggle_requested = Signal(str)
    edit_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, task: Task, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.task_id = task.id
        self.setObjectName("taskRow")
        self.setStyleSheet(
            "QFrame#taskRow { border: 1px solid #dedede; border-radius: 7px; "
            "background: #ffffff; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 9, 10, 9)
        layout.setSpacing(10)

        text_container = QWidget()
        text_container.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        text_layout = QVBoxLayout(text_container)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.checkbox = QCheckBox(task.text)
        self.checkbox.setObjectName("taskCheckbox")
        self.checkbox.setChecked(task.completed)
        self.checkbox.setMinimumHeight(32)
        self.checkbox.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        font = QFont(self.checkbox.font())
        font.setStrikeOut(task.completed)
        self.checkbox.setFont(font)
        self.checkbox.setStyleSheet(
            "QCheckBox { color: %s; font-size: 14px; spacing: 8px; }"
            % ("#888888" if task.completed else "#222222")
        )
        self.checkbox.toggled.connect(lambda _checked: self.toggle_requested.emit(self.task_id))
        text_layout.addWidget(self.checkbox)

        formatted_time = format_event_time(task.event_at)
        if formatted_time:
            event_label = QLabel(formatted_time)
            event_label.setObjectName("eventTimeLabel")
            event_label.setStyleSheet("color: #777777; font-size: 12px; padding-left: 24px;")
            event_label.setMinimumHeight(22)
            text_layout.addWidget(event_label)
            reminder_summary = format_reminder_summary(task.reminder_offsets)
            if reminder_summary:
                reminder_label = QLabel(reminder_summary)
                reminder_label.setObjectName("reminderSummaryLabel")
                reminder_label.setWordWrap(True)
                reminder_label.setStyleSheet(
                    "color: #777777; font-size: 12px; padding-left: 24px;"
                )
                reminder_label.setMinimumHeight(22)
                text_layout.addWidget(reminder_label)
        layout.addWidget(text_container, 1)

        edit_button = QPushButton("编辑")
        edit_button.setMinimumSize(58, 34)
        edit_button.clicked.connect(lambda: self.edit_requested.emit(self.task_id))
        layout.addWidget(edit_button)

        delete_button = QPushButton("删除")
        delete_button.setMinimumSize(58, 34)
        delete_button.setStyleSheet("QPushButton { color: #c0392b; }")
        delete_button.clicked.connect(lambda: self.delete_requested.emit(self.task_id))
        layout.addWidget(delete_button)


class TodoWindow(QWidget):
    """Display and edit tasks while delegating all persistence to TodoManager."""

    def __init__(self, manager: TodoManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._task_rows: dict[str, TaskRow] = {}
        self._setup_ui()
        self.refresh_tasks()

    def _setup_ui(self) -> None:
        self.setWindowTitle("我的待办")
        self.setMinimumSize(580, 560)
        self.resize(680, 700)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(14)

        title = QLabel("我的待办")
        title.setStyleSheet("font-size: 21px; font-weight: 600; color: #222222;")
        title.setMinimumHeight(34)
        root_layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setObjectName("taskScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list_container = QWidget()
        self._list_layout = QVBoxLayout(self._list_container)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(9)
        scroll.setWidget(self._list_container)
        root_layout.addWidget(scroll, 1)

        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        root_layout.addWidget(separator)

        add_layout = QHBoxLayout()
        add_layout.setSpacing(10)

        self._task_input = QLineEdit()
        self._task_input.setObjectName("taskInput")
        self._task_input.setPlaceholderText("输入待办事项...")
        self._task_input.setMinimumHeight(38)
        self._task_input.setStyleSheet("QLineEdit { padding: 6px 10px; font-size: 14px; }")
        self._task_input.returnPressed.connect(self._add_task)
        add_layout.addWidget(self._task_input, 1)

        add_button = QPushButton("添加")
        add_button.setObjectName("addTaskButton")
        add_button.setMinimumSize(76, 38)
        add_button.setDefault(True)
        add_button.clicked.connect(self._add_task)
        add_layout.addWidget(add_button)
        root_layout.addLayout(add_layout)

        self._add_time_checkbox = QCheckBox("设置时间")
        self._add_time_checkbox.setObjectName("addTimeEnabled")
        self._add_time_checkbox.setMinimumHeight(30)
        root_layout.addWidget(self._add_time_checkbox)

        self._add_time_frame = QFrame()
        add_time_layout = QVBoxLayout(self._add_time_frame)
        add_time_layout.setContentsMargins(0, 0, 0, 0)
        add_time_layout.setSpacing(8)
        add_date_time_layout = QHBoxLayout()
        add_date_time_layout.setSpacing(10)
        add_date_time_layout.addWidget(QLabel("日期："))
        self._add_date_edit = QDateEdit()
        self._add_date_edit.setObjectName("addEventDate")
        self._add_date_edit.setCalendarPopup(True)
        self._add_date_edit.setDisplayFormat("yyyy-MM-dd")
        self._add_date_edit.setMinimumHeight(36)
        add_date_time_layout.addWidget(self._add_date_edit)
        add_date_time_layout.addWidget(QLabel("时间："))
        self._add_time_edit = QTimeEdit()
        self._add_time_edit.setObjectName("addEventTime")
        self._add_time_edit.setDisplayFormat("HH:mm")
        self._add_time_edit.setMinimumHeight(36)
        self._add_time_edit.setKeyboardTracking(False)
        add_date_time_layout.addWidget(self._add_time_edit)
        add_date_time_layout.addStretch(1)
        add_time_layout.addLayout(add_date_time_layout)
        add_selector, self._add_reminder_checks = _create_reminder_selector("add")
        add_time_layout.addWidget(add_selector)
        root_layout.addWidget(self._add_time_frame)
        _set_editors(
            self._add_date_edit,
            self._add_time_edit,
            _default_event_datetime(),
        )
        self._add_time_frame.setVisible(False)
        self._add_time_checkbox.toggled.connect(self._add_time_frame.setVisible)

        self._status_label = QLabel("")
        self._status_label.setObjectName("statusLabel")
        self._status_label.setStyleSheet("color: #c0392b; font-size: 12px;")
        self._status_label.setMinimumHeight(18)
        root_layout.addWidget(self._status_label)

    def refresh_tasks(self) -> None:
        while self._list_layout.count():
            item = self._list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._task_rows.clear()

        tasks = self._manager.get_tasks()
        if not tasks:
            empty_label = QLabel("还没有待办事项")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setStyleSheet("color: #888888; font-size: 14px; padding: 28px;")
            self._list_layout.addWidget(empty_label)
        else:
            for task in tasks:
                row = TaskRow(task)
                row.toggle_requested.connect(self._toggle_task)
                row.edit_requested.connect(self._edit_task)
                row.delete_requested.connect(self._delete_task)
                self._task_rows[task.id] = row
                self._list_layout.addWidget(row)
        self._list_layout.addStretch(1)

    def _add_task(self) -> None:
        text = self._task_input.text().strip()
        if not text:
            self._status_label.setText("待办内容不能为空。")
            return
        event_at = (
            _editors_to_iso(self._add_date_edit, self._add_time_edit)
            if self._add_time_checkbox.isChecked()
            else None
        )
        reminder_offsets = (
            _selected_offsets(self._add_reminder_checks)
            if event_at is not None
            else ()
        )
        event = parse_local_datetime(event_at)
        if event is not None and event <= datetime.now():
            QMessageBox.warning(self, "时间无效", "提醒时间必须晚于当前时间。")
            return
        try:
            self._manager.add_task(text, event_at, reminder_offsets)
        except (TaskDataError, TodoStorageError) as exc:
            self._show_error(str(exc))
            return
        self._task_input.clear()
        self._add_time_checkbox.setChecked(False)
        for checkbox in self._add_reminder_checks.values():
            checkbox.setChecked(False)
        _set_editors(
            self._add_date_edit,
            self._add_time_edit,
            _default_event_datetime(),
        )
        self._status_label.clear()
        self.refresh_tasks()

    def _edit_task(self, task_id: str) -> None:
        task = self._manager.get_task(task_id)
        if task is None:
            return
        dialog = TaskEditDialog(task, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.get_values()
        if len(values) == 2:
            text, event_at = values
            reminder_offsets = task.reminder_offsets if event_at == task.event_at else ()
        else:
            text, event_at, reminder_offsets = values
        try:
            self._manager.update_task(
                task_id,
                text,
                event_at,
                reminder_offsets,
            )
        except (TaskDataError, TodoStorageError) as exc:
            self._show_error(str(exc))
            return
        self._status_label.clear()
        self.refresh_tasks()

    def _toggle_task(self, task_id: str) -> None:
        try:
            self._manager.toggle_complete(task_id)
        except TodoStorageError as exc:
            self._show_error(str(exc))
        self.refresh_tasks()

    def _delete_task(self, task_id: str) -> None:
        reply = QMessageBox.question(
            self,
            "确认删除",
            "确定删除这个任务吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            self._manager.delete_task(task_id)
        except TodoStorageError as exc:
            self._show_error(str(exc))
            return
        self._status_label.clear()
        self.refresh_tasks()

    def _show_error(self, message: str) -> None:
        self._status_label.setText(message)
        QMessageBox.critical(self, "待办保存失败", message)

    @property
    def task_count(self) -> int:
        return len(self._task_rows)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.hide()
        event.ignore()
