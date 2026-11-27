"""Compact chat cards for selecting and saving reading vocabulary."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from word_learning.reading_suggestions import SuggestedVocabulary
from word_learning.repository import WordLearningStorageError
from word_learning.service import BatchAddResult, WordLearningService


class SuggestedVocabularyItem(QWidget):
    """One selectable suggestion with an optional source-sentence expansion."""

    def __init__(
        self,
        suggestion: SuggestedVocabulary,
        *,
        already_exists: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.suggestion = suggestion
        self.processed = False
        self.setObjectName("suggestedVocabularyItem")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(9, 7, 9, 7)
        layout.setSpacing(3)

        heading = QHBoxLayout()
        self.checkbox = QCheckBox(suggestion.word)
        self.checkbox.setObjectName("suggestedVocabularyCheck")
        self.checkbox.setChecked(True)
        heading.addWidget(self.checkbox, 1)
        self.state_label = QLabel("已在单词本" if already_exists else "")
        self.state_label.setObjectName("suggestedVocabularyState")
        heading.addWidget(self.state_label)
        self.context_button = QPushButton("查看原句")
        self.context_button.setObjectName("suggestedVocabularyContextButton")
        self.context_button.setCheckable(True)
        self.context_button.toggled.connect(self._toggle_context)
        heading.addWidget(self.context_button)
        layout.addLayout(heading)

        description = " · ".join(
            part
            for part in (suggestion.part_of_speech, suggestion.meaning)
            if part
        )
        self.description_label = QLabel(description)
        self.description_label.setObjectName("suggestedVocabularyMeaning")
        self.description_label.setWordWrap(True)
        layout.addWidget(self.description_label)
        self.context_label = QLabel(suggestion.source_sentence)
        self.context_label.setObjectName("suggestedVocabularyContext")
        self.context_label.setWordWrap(True)
        self.context_label.hide()
        layout.addWidget(self.context_label)

    def _toggle_context(self, shown: bool) -> None:
        self.context_button.setText("收起原句" if shown else "查看原句")
        self.context_label.setVisible(shown)

    def mark_processed(self, *, created: bool, context_added: bool) -> None:
        self.processed = True
        self.checkbox.setChecked(False)
        self.checkbox.setEnabled(False)
        if created:
            text = "✓ 已添加"
        elif context_added:
            text = "✓ 已在单词本 · 已保存本句"
        else:
            text = "✓ 已在单词本"
        self.state_label.setText(text)


class SuggestedVocabularyCard(QFrame):
    """Select, batch-add, and optionally study one response's suggestions."""

    study_requested = Signal(object)

    def __init__(
        self,
        suggestions: tuple[SuggestedVocabulary, ...],
        service: WordLearningService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("suggestedVocabularyCard")
        self.setMinimumWidth(380)
        self.service = service
        self.suggestions = suggestions
        self._new_word_ids: list[str] = []
        self.items: list[SuggestedVocabularyItem] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 11, 13, 12)
        layout.setSpacing(7)
        heading = QLabel("本篇建议生词")
        heading.setObjectName("suggestedVocabularyHeading")
        layout.addWidget(heading)
        subtitle = QLabel("已默认勾选高价值词；加入前可以取消不想记的内容。")
        subtitle.setObjectName("suggestedVocabularyHint")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        for suggestion in suggestions:
            item = SuggestedVocabularyItem(
                suggestion,
                already_exists=service.get_word_by_text(suggestion.word) is not None,
            )
            self.items.append(item)
            layout.addWidget(item)

        select_row = QHBoxLayout()
        select_all = QPushButton("全部选择")
        select_all.setObjectName("suggestedVocabularySecondaryButton")
        select_all.clicked.connect(self.select_all)
        select_row.addWidget(select_all)
        select_none = QPushButton("全部取消")
        select_none.setObjectName("suggestedVocabularySecondaryButton")
        select_none.clicked.connect(self.clear_selection)
        select_row.addWidget(select_none)
        select_row.addStretch(1)
        layout.addLayout(select_row)

        self.status_label = QLabel("")
        self.status_label.setObjectName("suggestedVocabularyStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        actions = QHBoxLayout()
        self.add_button = QPushButton("加入单词本")
        self.add_button.setObjectName("suggestedVocabularyAddButton")
        self.add_button.clicked.connect(self.add_selected)
        actions.addWidget(self.add_button)
        self.study_button = QPushButton("现在学习这些词")
        self.study_button.setObjectName("suggestedVocabularyStudyButton")
        self.study_button.clicked.connect(self._request_study)
        self.study_button.hide()
        actions.addWidget(self.study_button)
        layout.addLayout(actions)

    @property
    def has_pending(self) -> bool:
        return any(not item.processed for item in self.items)

    @property
    def new_word_ids(self) -> tuple[str, ...]:
        return tuple(self._new_word_ids)

    def select_all(self) -> None:
        for item in self.items:
            if not item.processed:
                item.checkbox.setChecked(True)

    def clear_selection(self) -> None:
        for item in self.items:
            if not item.processed:
                item.checkbox.setChecked(False)

    def add_all_pending(self) -> str:
        self.select_all()
        return self.add_selected()

    def add_selected(self) -> str:
        selected_items = [
            item
            for item in self.items
            if not item.processed and item.checkbox.isChecked()
        ]
        if not selected_items:
            message = "请先勾选要加入的单词。" if self.has_pending else "这些词已经处理过了。"
            self.status_label.setText(message)
            return message
        try:
            result = self.service.add_reading_suggestions(
                item.suggestion for item in selected_items
            )
        except (ValueError, WordLearningStorageError) as exc:
            message = f"加入失败：{exc}"
            self.status_label.setText(message)
            return message

        self._apply_result(selected_items, result)
        if result.created_count and result.existing_count:
            message = (
                f"新增 {result.created_count} 个，"
                f"跳过 {result.existing_count} 个已存在词。"
            )
        elif result.created_count:
            message = f"已加入 {result.created_count} 个单词。"
        else:
            message = f"这 {result.existing_count} 个词已在单词本中。"
        if result.context_count and result.existing_count:
            message += " 新阅读原句已保存。"
        self.status_label.setText(message)
        self.add_button.setText("加入其余单词" if self.has_pending else "已加入")
        self.add_button.setEnabled(self.has_pending)
        self.study_button.setVisible(bool(self._new_word_ids))
        return message

    def _apply_result(
        self,
        selected_items: list[SuggestedVocabularyItem],
        result: BatchAddResult,
    ) -> None:
        for item, saved in zip(selected_items, result.items):
            item.mark_processed(
                created=saved.created,
                context_added=saved.context_added,
            )
            if saved.created and saved.word.id not in self._new_word_ids:
                self._new_word_ids.append(saved.word.id)

    def _request_study(self) -> None:
        if self._new_word_ids:
            self.study_requested.emit(tuple(self._new_word_ids))
