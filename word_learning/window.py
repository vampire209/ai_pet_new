"""Dedicated card-based English learning window for the desktop pet."""

from __future__ import annotations

import csv
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QTime
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ai.profile import (
    RELATIONSHIP_CLINGY_GIRLFRIEND,
    RELATIONSHIP_FRIEND,
    RELATIONSHIP_GIRLFRIEND,
    RELATIONSHIP_NORMAL,
    RELATIONSHIP_YANDERE,
)
from ai.profile_service import ProfileService
from models.recurrence import REPEAT_DAILY
from todo_manager import TodoManager, TodoStorageError
from ui_resources import load_application_icon, load_avatar_source
from word_learning.models import (
    LearningSettings,
    RESULT_EASY,
    RESULT_FORGOT,
    RESULT_HARD,
    RESULT_NORMAL,
    SOURCE_BUILTIN,
    SOURCE_MANUAL,
    SOURCE_READING,
    WordEntry,
    WordLearningDataError,
)
from word_learning.repository import WordLearningStorageError
from word_learning.service import WordLearningService
from word_learning.widgets import WordCardWidget, WordEditorDialog


WORD_LEARNING_STYLE = """
QWidget#wordLearningWindow {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #eef9ff, stop:0.55 #f7f7ff, stop:1 #f2edff);
    color: #34405a;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei";
    font-size: 13px;
}
QFrame#learningHeader, QFrame#learningPanel, QFrame#wordCard,
QFrame#statCard, QFrame#completionPanel {
    background: rgba(255, 255, 255, 232);
    border: 1px solid rgba(181, 197, 229, 180);
    border-radius: 18px;
}
QLabel#learningTitle { color:#303c58; font-size:21px; font-weight:700; }
QLabel#learningSubtitle, QLabel#learningHint, QLabel#studyPhonetic,
QLabel#studyQuestion, QLabel#studyTranslation { color:#7d88a4; }
QLabel#companionMessage {
    color:#6d6ba0; background:rgba(238,235,252,180);
    border-radius:10px; padding:7px 10px;
}
QLabel#progressNumber { color:#606dac; font-size:18px; font-weight:700; }
QLabel#progressCaption, QLabel#studySectionTitle { color:#8790a7; font-size:11px; }
QLabel#studyWord { color:#313e5d; font-size:34px; font-weight:700; }
QLabel#studyBackWord { color:#313e5d; font-size:27px; font-weight:700; }
QLabel#studySectionValue, QLabel#studyContext { color:#3e4962; font-size:14px; }
QLabel#studyContext {
    background:rgba(238,245,255,170); border-radius:9px; padding:7px;
}
QProgressBar {
    min-height:8px; max-height:8px; border:none; border-radius:4px;
    background:#e3e8f5; text-align:center;
}
QProgressBar::chunk { background:#8c9be0; border-radius:4px; }
QPushButton {
    min-height:34px; border-radius:11px; padding:3px 13px;
    color:#59657f; background:#f0f3fb; border:1px solid #d5dced;
}
QPushButton:hover { background:#e6ebf9; border-color:#b9c4e4; }
QPushButton#primaryLearningButton, QPushButton#revealWordButton,
QPushButton#review_easy, QPushButton#review_normal {
    color:white; background:#7c8ed8; border-color:#7183cf; font-weight:600;
}
QPushButton#primaryLearningButton:hover, QPushButton#revealWordButton:hover,
QPushButton#review_easy:hover, QPushButton#review_normal:hover { background:#899ae1; }
QPushButton#review_forgot { color:#a15970; background:#fff3f6; border-color:#eccbd5; }
QPushButton#review_hard { color:#8a714f; background:#fff9ee; border-color:#ead9b9; }
QLineEdit, QComboBox, QSpinBox, QTimeEdit {
    min-height:34px; border:1px solid #d0d9ed; border-radius:10px;
    background:#fafbff; color:#46516e; padding:0 9px;
}
QTabWidget::pane { border:none; background:transparent; }
QTabBar::tab {
    min-width:100px; min-height:34px; color:#78829e;
    background:rgba(239,242,250,190); padding:4px 10px;
    border-top-left-radius:10px; border-top-right-radius:10px;
}
QTabBar::tab:selected { color:#5969a7; background:white; font-weight:600; }
QTableWidget {
    background:rgba(255,255,255,225); border:1px solid #d9e0ef;
    border-radius:12px; gridline-color:#edf0f7; color:#46516e;
}
QHeaderView::section {
    background:#edf1fb; color:#68738e; border:none;
    border-right:1px solid #dde3f1; padding:7px; font-weight:600;
}
"""


_SOURCE_LABELS = {
    SOURCE_BUILTIN: "考研词库（示例）",
    SOURCE_READING: "阅读生词",
    SOURCE_MANUAL: "手动添加",
}

_START_MESSAGES = {
    RELATIONSHIP_NORMAL: "准备好了，我们开始今天的单词学习。",
    RELATIONSHIP_FRIEND: "来吧，今天这组词一起拿下。",
    RELATIONSHIP_GIRLFRIEND: "我们慢慢来，记不住也没关系。",
    RELATIONSHIP_CLINGY_GIRLFRIEND: "今天也陪你一起背呀，先从这一组开始。",
    RELATIONSHIP_YANDERE: "我会温柔地陪你学完这一组，不给你压力。",
}

_FINISH_MESSAGES = {
    RELATIONSHIP_NORMAL: "本轮完成，休息一下也可以。",
    RELATIONSHIP_FRIEND: "做得不错，这一轮顺利完成。",
    RELATIONSHIP_GIRLFRIEND: "完成啦，今天也很认真。",
    RELATIONSHIP_CLINGY_GIRLFRIEND: "这一轮完成啦，我就知道陪着你会更顺利。",
    RELATIONSHIP_YANDERE: "这一轮完成了，我会继续温和地陪着你。",
}


class WordLearningWindow(QWidget):
    """Four-tab learning dashboard plus an offline card study flow."""

    def __init__(
        self,
        service: WordLearningService,
        *,
        profile_service: ProfileService | None = None,
        todo_manager: TodoManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.profile_service = profile_service
        self.todo_manager = todo_manager
        self._session_words: list[WordEntry] = []
        self._session_index = 0
        self._session_kind = "new"
        self._session_correct = 0
        self._session_wrong = 0
        self._session_difficult_ids: list[str] = []
        self._setup_ui()
        self._setup_shortcuts()
        self.service.changed.connect(self.refresh_all)
        self.refresh_all()

    def _setup_ui(self) -> None:
        self.setObjectName("wordLearningWindow")
        self.setWindowTitle("英语学习")
        self.setWindowIcon(load_application_icon())
        self.setMinimumSize(760, 650)
        self.resize(920, 760)
        self.setStyleSheet(WORD_LEARNING_STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 18)
        root.setSpacing(12)

        header = QFrame()
        header.setObjectName("learningHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 10, 16, 10)
        avatar = QLabel()
        avatar.setFixedSize(48, 48)
        avatar.setPixmap(
            load_avatar_source().pixmap.scaled(
                46,
                46,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        header_layout.addWidget(avatar)
        copy = QVBoxLayout()
        title = QLabel("英语学习")
        title.setObjectName("learningTitle")
        copy.addWidget(title)
        subtitle = QLabel("聊天负责讲解，卡片负责长期记忆与复习")
        subtitle.setObjectName("learningSubtitle")
        copy.addWidget(subtitle)
        header_layout.addLayout(copy)
        header_layout.addStretch(1)
        close_button = QPushButton("返回桌宠")
        close_button.clicked.connect(self.hide)
        header_layout.addWidget(close_button)
        root.addWidget(header)

        self.main_stack = QStackedWidget()
        self.dashboard = self._build_dashboard()
        self.study_page = self._build_study_page()
        self.completion_page = self._build_completion_page()
        self.main_stack.addWidget(self.dashboard)
        self.main_stack.addWidget(self.study_page)
        self.main_stack.addWidget(self.completion_page)
        root.addWidget(self.main_stack, 1)

        self.footer_status = QLabel("")
        self.footer_status.setObjectName("learningHint")
        root.addWidget(self.footer_status)

    def _build_dashboard(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.today_tab = self._build_today_tab()
        self.wordbook_tab = self._build_wordbook_tab()
        self.stats_tab = self._build_stats_tab()
        self.settings_tab = self._build_settings_tab()
        self.tabs.addTab(self.today_tab, "今日学习")
        self.tabs.addTab(self.wordbook_tab, "单词本")
        self.tabs.addTab(self.stats_tab, "学习统计")
        self.tabs.addTab(self.settings_tab, "设置")
        self.tabs.currentChanged.connect(lambda _index: self.refresh_all())
        layout.addWidget(self.tabs)
        return page

    def _build_today_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 14, 4, 4)
        layout.setSpacing(12)
        grid = QGridLayout()
        self.new_progress_label, self.new_progress_bar = self._progress_card(
            grid, 0, "今日新词"
        )
        self.review_progress_label, self.review_progress_bar = self._progress_card(
            grid, 1, "今日复习"
        )
        self.total_progress_label, self.total_progress_bar = self._progress_card(
            grid, 2, "今日总进度"
        )
        layout.addLayout(grid)
        self.today_complete_label = QLabel("今日任务已完成")
        self.today_complete_label.setObjectName("learningTitle")
        self.today_complete_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.today_complete_label)
        self.today_companion = QLabel("")
        self.today_companion.setObjectName("companionMessage")
        self.today_companion.setWordWrap(True)
        layout.addWidget(self.today_companion)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        self.start_new_button = QPushButton("开始背新词")
        self.start_new_button.setObjectName("primaryLearningButton")
        self.start_new_button.clicked.connect(lambda: self.start_session("new"))
        buttons.addWidget(self.start_new_button)
        self.start_review_button = QPushButton("开始复习")
        self.start_review_button.clicked.connect(lambda: self.start_session("review"))
        buttons.addWidget(self.start_review_button)
        self.continue_button = QPushButton("继续学习")
        self.continue_button.clicked.connect(self._continue_learning)
        buttons.addWidget(self.continue_button)
        self.extra_review_button = QPushButton("额外复习")
        self.extra_review_button.clicked.connect(lambda: self.start_session("extra"))
        buttons.addWidget(self.extra_review_button)
        layout.addLayout(buttons)
        return page

    @staticmethod
    def _progress_card(
        grid: QGridLayout,
        column: int,
        caption: str,
    ) -> tuple[QLabel, QProgressBar]:
        card = QFrame()
        card.setObjectName("statCard")
        layout = QVBoxLayout(card)
        title = QLabel(caption)
        title.setObjectName("progressCaption")
        layout.addWidget(title)
        value = QLabel("0 / 0")
        value.setObjectName("progressNumber")
        layout.addWidget(value)
        progress = QProgressBar()
        progress.setTextVisible(False)
        layout.addWidget(progress)
        grid.addWidget(card, 0, column)
        return value, progress

    def _build_study_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        top = QHBoxLayout()
        exit_button = QPushButton("结束本轮")
        exit_button.clicked.connect(self._finish_session)
        top.addWidget(exit_button)
        top.addStretch(1)
        self.study_count_label = QLabel("1 / 1")
        self.study_count_label.setObjectName("progressNumber")
        top.addWidget(self.study_count_label)
        layout.addLayout(top)
        self.study_progress = QProgressBar()
        self.study_progress.setTextVisible(False)
        layout.addWidget(self.study_progress)
        self.study_companion = QLabel("")
        self.study_companion.setObjectName("companionMessage")
        self.study_companion.setWordWrap(True)
        layout.addWidget(self.study_companion)
        self.word_card = WordCardWidget()
        self.word_card.rated.connect(self._record_card_result)
        layout.addWidget(self.word_card, 1)
        return page

    def _build_completion_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(90, 70, 90, 70)
        panel = QFrame()
        panel.setObjectName("completionPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(34, 30, 34, 30)
        panel_layout.setSpacing(13)
        title = QLabel("本轮完成")
        title.setObjectName("learningTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_layout.addWidget(title)
        self.completion_numbers = QLabel("")
        self.completion_numbers.setObjectName("progressNumber")
        self.completion_numbers.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_layout.addWidget(self.completion_numbers)
        self.completion_difficult = QLabel("")
        self.completion_difficult.setWordWrap(True)
        self.completion_difficult.setAlignment(Qt.AlignmentFlag.AlignCenter)
        panel_layout.addWidget(self.completion_difficult)
        self.completion_companion = QLabel("")
        self.completion_companion.setObjectName("companionMessage")
        self.completion_companion.setWordWrap(True)
        panel_layout.addWidget(self.completion_companion)
        buttons = QHBoxLayout()
        end_button = QPushButton("结束")
        end_button.setObjectName("primaryLearningButton")
        end_button.clicked.connect(self._finish_session)
        buttons.addWidget(end_button)
        self.review_wrong_button = QPushButton("复习本轮错词")
        self.review_wrong_button.clicked.connect(self._review_wrong_words)
        buttons.addWidget(self.review_wrong_button)
        panel_layout.addLayout(buttons)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def _build_wordbook_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 14, 4, 4)
        controls = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索单词或释义")
        self.search_edit.textChanged.connect(lambda _text: self.refresh_wordbook())
        controls.addWidget(self.search_edit, 1)
        self.mastery_filter = QComboBox()
        self.mastery_filter.addItem("全部掌握度", None)
        for label in ("陌生", "较弱", "一般", "熟悉", "已掌握"):
            self.mastery_filter.addItem(label, label)
        self.mastery_filter.currentIndexChanged.connect(
            lambda _index: self.refresh_wordbook()
        )
        controls.addWidget(self.mastery_filter)
        self.source_filter = QComboBox()
        self.source_filter.addItem("全部来源", None)
        for source, label in _SOURCE_LABELS.items():
            self.source_filter.addItem(label, source)
        self.source_filter.currentIndexChanged.connect(
            lambda _index: self.refresh_wordbook()
        )
        controls.addWidget(self.source_filter)
        layout.addLayout(controls)

        actions = QHBoxLayout()
        add_button = QPushButton("添加单词")
        add_button.clicked.connect(lambda: self._open_add_dialog(reading=False))
        actions.addWidget(add_button)
        reading_button = QPushButton("添加阅读生词")
        reading_button.clicked.connect(lambda: self._open_add_dialog(reading=True))
        actions.addWidget(reading_button)
        import_button = QPushButton("导入词库")
        import_button.clicked.connect(self._import_catalog)
        actions.addWidget(import_button)
        actions.addStretch(1)
        self.wordbook_count_label = QLabel("")
        actions.addWidget(self.wordbook_count_label)
        layout.addLayout(actions)

        self.word_table = QTableWidget(0, 7)
        self.word_table.setHorizontalHeaderLabels(
            ("单词", "核心义", "掌握度", "来源", "下次复习", "收藏", "操作")
        )
        self.word_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.word_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.word_table.verticalHeader().hide()
        header = self.word_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.word_table, 1)
        return page

    def _build_stats_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 14, 4, 4)
        grid = QGridLayout()
        self.stats_labels: dict[str, QLabel] = {}
        for index, (key, title) in enumerate(
            (
                ("today", "今日学习"),
                ("streak", "连续学习"),
                ("total", "单词本"),
                ("mastered", "已掌握"),
                ("due", "待复习"),
                ("accuracy", "今日正确率"),
            )
        ):
            card = QFrame()
            card.setObjectName("statCard")
            card_layout = QVBoxLayout(card)
            caption = QLabel(title)
            caption.setObjectName("progressCaption")
            value = QLabel("0")
            value.setObjectName("progressNumber")
            card_layout.addWidget(caption)
            card_layout.addWidget(value)
            grid.addWidget(card, index // 3, index % 3)
            self.stats_labels[key] = value
        layout.addLayout(grid)
        heading = QLabel("最近 7 天")
        heading.setObjectName("learningTitle")
        layout.addWidget(heading)
        self.recent_table = QTableWidget(7, 4)
        self.recent_table.setHorizontalHeaderLabels(("日期", "新词", "复习", "正确率"))
        self.recent_table.verticalHeader().hide()
        self.recent_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.recent_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.recent_table, 1)
        return page

    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(4, 14, 4, 4)
        panel = QFrame()
        panel.setObjectName("learningPanel")
        layout = QGridLayout(panel)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setHorizontalSpacing(16)
        layout.setVerticalSpacing(14)
        layout.addWidget(QLabel("每日新词数量"), 0, 0)
        self.daily_new_combo = QComboBox()
        for count in (10, 20, 30, 50):
            self.daily_new_combo.addItem(str(count), count)
        self.daily_new_combo.addItem("自定义", -1)
        self.daily_new_combo.currentIndexChanged.connect(self._toggle_custom_count)
        layout.addWidget(self.daily_new_combo, 0, 1)
        self.custom_new_spin = QSpinBox()
        self.custom_new_spin.setRange(1, 100)
        self.custom_new_spin.setSuffix(" 个")
        layout.addWidget(self.custom_new_spin, 0, 2)
        layout.addWidget(QLabel("每日复习上限"), 1, 0)
        self.review_limit_combo = QComboBox()
        for count in (20, 50, 100):
            self.review_limit_combo.addItem(str(count), count)
        self.review_limit_combo.addItem("不限", None)
        layout.addWidget(self.review_limit_combo, 1, 1, 1, 2)
        layout.addWidget(QLabel("每篇阅读建议生词"), 2, 0)
        self.reading_suggestion_limit = QSpinBox()
        self.reading_suggestion_limit.setRange(1, 20)
        self.reading_suggestion_limit.setSuffix(" 个")
        layout.addWidget(self.reading_suggestion_limit, 2, 1, 1, 2)
        self.reminder_enabled = QCheckBox("使用现有 Todo / 提醒系统每日提醒背词")
        layout.addWidget(self.reminder_enabled, 3, 0, 1, 2)
        self.reminder_time = QTimeEdit()
        self.reminder_time.setDisplayFormat("HH:mm")
        layout.addWidget(self.reminder_time, 3, 2)
        hint = QLabel("关闭时不会自动创建提醒；开启后只维护一个每日循环待办。")
        hint.setObjectName("learningHint")
        hint.setWordWrap(True)
        layout.addWidget(hint, 4, 0, 1, 3)
        save_button = QPushButton("保存学习设置")
        save_button.setObjectName("primaryLearningButton")
        save_button.clicked.connect(self._save_settings)
        layout.addWidget(save_button, 5, 2)
        outer.addWidget(panel)
        outer.addStretch(1)
        return page

    def _setup_shortcuts(self) -> None:
        bindings = (
            ("Space", self._shortcut_reveal),
            ("1", lambda: self._shortcut_rate(RESULT_FORGOT)),
            ("2", lambda: self._shortcut_rate(RESULT_HARD)),
            ("3", lambda: self._shortcut_rate(RESULT_NORMAL)),
            ("4", lambda: self._shortcut_rate(RESULT_EASY)),
        )
        shortcuts: list[QShortcut] = []
        for key, callback in bindings:
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.activated.connect(callback)
            shortcuts.append(shortcut)
        self._shortcuts = tuple(shortcuts)

    def _shortcut_reveal(self) -> None:
        if self.main_stack.currentWidget() is self.study_page:
            self.word_card.reveal()

    def _shortcut_rate(self, result: str) -> None:
        if self.main_stack.currentWidget() is self.study_page:
            self.word_card.rate(result)

    def open_page(self, page: str = "today") -> None:
        self.main_stack.setCurrentWidget(self.dashboard)
        indexes = {"today": 0, "wordbook": 1, "stats": 2, "settings": 3}
        if page == "new":
            self.start_session("new")
            return
        if page == "review":
            self.start_session("review")
            return
        self.tabs.setCurrentIndex(indexes.get(page, 0))
        self.refresh_all()

    def start_session(
        self,
        kind: str,
        *,
        words: list[WordEntry] | None = None,
    ) -> bool:
        try:
            if words is None:
                if kind == "new":
                    words = list(self.service.get_today_new_words())
                elif kind == "review":
                    words = list(self.service.get_today_reviews())
                else:
                    words = list(self.service.get_extra_reviews())
        except (WordLearningDataError, WordLearningStorageError) as exc:
            self._show_status(str(exc), error=True)
            return False
        if not words:
            message = "今天没有待学新词。" if kind == "new" else "现在没有待复习单词。"
            self._show_status(message, error=False)
            return False
        self._session_words = words
        self._session_index = 0
        self._session_kind = kind
        self._session_correct = 0
        self._session_wrong = 0
        self._session_difficult_ids = []
        self.study_companion.setText(self._relationship_message(_START_MESSAGES))
        self.main_stack.setCurrentWidget(self.study_page)
        self._show_current_word()
        return True

    def _show_current_word(self) -> None:
        total = len(self._session_words)
        if self._session_index >= total:
            self._show_completion()
            return
        self.word_card.set_word(self._session_words[self._session_index])
        self.study_count_label.setText(f"{self._session_index + 1} / {total}")
        self.study_progress.setRange(0, total)
        self.study_progress.setValue(self._session_index)

    def _record_card_result(self, result: str) -> None:
        word = self.word_card.word
        if word is None:
            return
        try:
            self.service.mark_review_result(
                word.id,
                result,
                count_for_daily=self._session_kind in {"new", "review"},
            )
        except (WordLearningDataError, WordLearningStorageError) as exc:
            self._show_status(str(exc), error=True)
            return
        if result in {RESULT_NORMAL, RESULT_EASY}:
            self._session_correct += 1
        else:
            self._session_wrong += 1
            self._session_difficult_ids.append(word.id)
            if self._session_wrong == 3:
                self.study_companion.setText("连续遇到几个难词也没关系，先看懂再继续。")
        self._session_index += 1
        self._show_current_word()

    def _show_completion(self) -> None:
        total = len(self._session_words)
        accuracy = 0 if total == 0 else round(self._session_correct * 100 / total)
        self.completion_numbers.setText(
            f"学习 {total} 个 · 正确 {self._session_correct} · "
            f"模糊/错误 {self._session_wrong} · 正确率 {accuracy}%"
        )
        difficult = [
            word.word
            for word_id in self._session_difficult_ids
            if (word := self.service.get_word(word_id)) is not None
        ]
        self.completion_difficult.setText(
            "本轮较难单词：" + "、".join(difficult) if difficult else "这一轮没有标记难词"
        )
        self.review_wrong_button.setVisible(bool(difficult))
        self.completion_companion.setText(self._relationship_message(_FINISH_MESSAGES))
        self.main_stack.setCurrentWidget(self.completion_page)
        self.refresh_all()

    def _finish_session(self) -> None:
        self.main_stack.setCurrentWidget(self.dashboard)
        self.tabs.setCurrentIndex(0)
        self.refresh_all()

    def _review_wrong_words(self) -> None:
        words = [
            word
            for word_id in dict.fromkeys(self._session_difficult_ids)
            if (word := self.service.get_word(word_id)) is not None
        ]
        self.start_session("wrong", words=words)

    def _continue_learning(self) -> None:
        if self.service.get_today_reviews():
            self.start_session("review")
        else:
            self.start_session("new")

    def _relationship_message(self, messages: dict[str, str]) -> str:
        mode = (
            self.profile_service.profile.relationship_mode
            if self.profile_service is not None
            else RELATIONSHIP_NORMAL
        )
        return messages.get(mode, messages[RELATIONSHIP_NORMAL])

    def refresh_all(self) -> None:
        if not hasattr(self, "tabs"):
            return
        try:
            self.refresh_today()
            self.refresh_wordbook()
            self.refresh_stats()
            self._load_settings_controls()
        except (WordLearningDataError, WordLearningStorageError) as exc:
            self._show_status(str(exc), error=True)

    def refresh_today(self) -> None:
        progress = self.service.get_today_progress()
        self._set_progress(
            self.new_progress_label,
            self.new_progress_bar,
            progress.new_words_done,
            progress.new_words_goal,
        )
        self._set_progress(
            self.review_progress_label,
            self.review_progress_bar,
            progress.reviews_done,
            progress.reviews_goal,
        )
        self._set_progress(
            self.total_progress_label,
            self.total_progress_bar,
            progress.total_done,
            progress.total_goal,
        )
        self.today_complete_label.setVisible(progress.completed)
        self.extra_review_button.setVisible(progress.completed)
        self.start_new_button.setEnabled(
            progress.new_words_done < progress.new_words_goal
        )
        self.start_review_button.setEnabled(
            progress.reviews_done < progress.reviews_goal
        )
        self.continue_button.setEnabled(
            self.start_new_button.isEnabled() or self.start_review_button.isEnabled()
        )
        self.today_companion.setText(
            "今日任务已经完成，可以安心休息，也可以做一轮额外复习。"
            if progress.completed
            else "按自己的节奏来，打开页面不会被计入学习记录。"
        )

    @staticmethod
    def _set_progress(
        label: QLabel,
        bar: QProgressBar,
        done: int,
        goal: int,
    ) -> None:
        label.setText(f"{done} / {goal}")
        bar.setRange(0, max(1, goal))
        bar.setValue(min(done, max(1, goal)))

    def refresh_wordbook(self) -> None:
        words = self.service.search_words(
            self.search_edit.text(),
            mastery=self.mastery_filter.currentData(),
            source=self.source_filter.currentData(),
        )
        self.wordbook_count_label.setText(f"共 {len(words)} 词")
        self.word_table.setRowCount(len(words))
        for row, word in enumerate(words):
            self.word_table.setItem(row, 0, QTableWidgetItem(word.word))
            self.word_table.setItem(row, 1, QTableWidgetItem(word.meaning))
            self.word_table.setItem(row, 2, QTableWidgetItem(word.mastery_text))
            self.word_table.setItem(
                row,
                3,
                QTableWidgetItem(_SOURCE_LABELS.get(word.source, "手动添加")),
            )
            next_review = (
                word.next_review_at.replace("T", " ")[:16]
                if word.next_review_at
                else "尚未学习"
            )
            self.word_table.setItem(row, 4, QTableWidgetItem(next_review))
            star_button = QPushButton("★" if word.is_starred else "☆")
            star_button.setToolTip("取消收藏" if word.is_starred else "收藏")
            star_button.clicked.connect(
                lambda _checked=False, item=word: self.service.set_starred(
                    item.id, not item.is_starred
                )
            )
            self.word_table.setCellWidget(row, 5, star_button)
            delete_button = QPushButton("删除")
            delete_button.clicked.connect(
                lambda _checked=False, item=word: self._delete_word(item)
            )
            self.word_table.setCellWidget(row, 6, delete_button)

    def _delete_word(self, word: WordEntry) -> None:
        answer = QMessageBox.question(
            self,
            "删除单词",
            f"确定从单词本删除 {word.word} 吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.service.remove_word(word.id)

    def _open_add_dialog(self, *, reading: bool) -> None:
        dialog = WordEditorDialog(reading=reading, parent=self)
        if dialog.exec() != dialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            result = self.service.add_word(**values)
        except (WordLearningDataError, WordLearningStorageError) as exc:
            self._show_status(str(exc), error=True)
            return
        self._show_status(
            "已加入单词本。" if result.created else "这个词已经在单词本里了。",
            error=False,
        )
        self.refresh_wordbook()

    def _import_catalog(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "导入词库",
            "",
            "词库文件 (*.json *.csv)",
        )
        if not selected:
            return
        path = Path(selected)
        try:
            if path.suffix.lower() == ".json":
                raw = json.loads(path.read_text(encoding="utf-8"))
                entries = raw.get("words", []) if isinstance(raw, dict) else raw
            else:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    entries = list(csv.DictReader(handle))
            if not isinstance(entries, list):
                raise WordLearningDataError("词库顶层必须是数组或包含 words 数组。")
            imported = self.service.import_catalog(entries)
        except (OSError, csv.Error, json.JSONDecodeError, WordLearningDataError, WordLearningStorageError) as exc:
            self._show_status(f"导入失败：{exc}", error=True)
            return
        self._show_status(f"已导入 {imported} 个候选词，之后会进入每日新词。", error=False)

    def refresh_stats(self) -> None:
        stats = self.service.get_stats()
        today = stats.today
        self.stats_labels["today"].setText(
            f"新词 {today.new_words_count} · 复习 {today.review_count}"
        )
        self.stats_labels["streak"].setText(f"{stats.study_streak} 天")
        self.stats_labels["total"].setText(f"{stats.total_words} 词")
        self.stats_labels["mastered"].setText(str(stats.mastered_words))
        self.stats_labels["due"].setText(str(stats.due_words))
        self.stats_labels["accuracy"].setText(f"{today.accuracy}%")
        for row, item in enumerate(stats.recent_seven_days):
            for column, value in enumerate(
                (
                    item.study_date[5:],
                    str(item.new_words_count),
                    str(item.review_count),
                    f"{item.accuracy}%",
                )
            ):
                self.recent_table.setItem(row, column, QTableWidgetItem(value))

    def _load_settings_controls(self) -> None:
        settings = self.service.settings
        with QSignalBlocker(self.daily_new_combo):
            index = self.daily_new_combo.findData(settings.daily_new_word_count)
            if index < 0:
                index = self.daily_new_combo.findData(-1)
            self.daily_new_combo.setCurrentIndex(index)
        self.custom_new_spin.setValue(settings.daily_new_word_count)
        self.custom_new_spin.setVisible(self.daily_new_combo.currentData() == -1)
        with QSignalBlocker(self.review_limit_combo):
            index = self.review_limit_combo.findData(settings.daily_review_limit)
            self.review_limit_combo.setCurrentIndex(max(0, index))
        self.reading_suggestion_limit.setValue(
            settings.reading_vocab_suggestion_limit
        )
        self.reminder_enabled.setChecked(settings.word_reminder_enabled)
        parsed = datetime.strptime(settings.word_reminder_time, "%H:%M").time()
        self.reminder_time.setTime(QTime(parsed.hour, parsed.minute))

    def _toggle_custom_count(self, _index: int) -> None:
        self.custom_new_spin.setVisible(self.daily_new_combo.currentData() == -1)

    def _save_settings(self) -> None:
        current = self.service.settings
        selected_new = self.daily_new_combo.currentData()
        daily_new = self.custom_new_spin.value() if selected_new == -1 else selected_new
        time_text = self.reminder_time.time().toString("HH:mm")
        try:
            settings = LearningSettings(
                daily_new_word_count=int(daily_new),
                daily_review_limit=self.review_limit_combo.currentData(),
                word_reminder_enabled=self.reminder_enabled.isChecked(),
                word_reminder_time=time_text,
                word_reminder_task_id=current.word_reminder_task_id,
                reading_vocab_suggestion_limit=(
                    self.reading_suggestion_limit.value()
                ),
            )
            self.service.save_settings(settings)
            reminder_note = self._sync_existing_reminder(settings)
        except (WordLearningDataError, WordLearningStorageError, TodoStorageError) as exc:
            self._show_status(str(exc), error=True)
            return
        self._show_status("学习设置已保存。" + reminder_note, error=False)
        self.refresh_all()

    def _sync_existing_reminder(self, settings: LearningSettings) -> str:
        if self.todo_manager is None:
            return " 当前窗口未接入提醒服务。" if settings.word_reminder_enabled else ""
        task_id = settings.word_reminder_task_id
        existing = self.todo_manager.get_task(task_id) if task_id else None
        if not settings.word_reminder_enabled:
            if existing is not None:
                self.todo_manager.delete_task(existing.id)
            if task_id:
                self.service.update_reminder_task_id(None)
            return " 每日背词提醒已关闭。"

        clock = datetime.strptime(settings.word_reminder_time, "%H:%M").time()
        now = self.service.now()
        event_at = datetime.combine(now.date(), clock)
        if event_at <= now:
            event_at += timedelta(days=1)
        if existing is None:
            task = self.todo_manager.add_task(
                "背单词",
                event_at.isoformat(timespec="seconds"),
                (),
                REPEAT_DAILY,
                (),
                now=now,
            )
            self.service.update_reminder_task_id(task.id)
        else:
            self.todo_manager.update_task(
                existing.id,
                "背单词",
                event_at.isoformat(timespec="seconds"),
                (),
                REPEAT_DAILY,
                (),
                now=now,
            )
        return f" 已复用现有提醒系统，每天 {settings.word_reminder_time} 提醒。"

    def _show_status(self, message: str, *, error: bool) -> None:
        self.footer_status.setStyleSheet("color:#a85f72;" if error else "color:#647f73;")
        self.footer_status.setText(message)

    def showEvent(self, event: QShowEvent) -> None:
        self.refresh_all()
        super().showEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.hide()
        event.ignore()
