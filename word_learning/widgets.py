"""Reusable card and editor widgets for the English learning window."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from word_learning.models import (
    RESULT_EASY,
    RESULT_FORGOT,
    RESULT_HARD,
    RESULT_NORMAL,
    SOURCE_MANUAL,
    SOURCE_READING,
    WordEntry,
)


class WordCardWidget(QFrame):
    """Two-face recall card; the answer is hidden until explicitly revealed."""

    rated = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("wordCard")
        self._word: WordEntry | None = None
        self.initial_judgment: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 25, 28, 25)
        root.setSpacing(16)
        self.faces = QStackedWidget()
        self.faces.addWidget(self._build_front())
        self.faces.addWidget(self._build_back())
        root.addWidget(self.faces)

    def _build_front(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 5, 0, 5)
        layout.setSpacing(13)
        self.front_word = QLabel("")
        self.front_word.setObjectName("studyWord")
        self.front_word.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.front_word)
        self.front_phonetic = QLabel("")
        self.front_phonetic.setObjectName("studyPhonetic")
        self.front_phonetic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.front_phonetic)
        question = QLabel("你记得这个词是什么意思吗？")
        question.setObjectName("studyQuestion")
        question.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(question)
        layout.addStretch(1)

        judgment_row = QHBoxLayout()
        for text, value in (("认识", "known"), ("模糊", "fuzzy"), ("不认识", "unknown")):
            button = QPushButton(text)
            button.setObjectName("judgmentButton")
            button.clicked.connect(
                lambda _checked=False, result=value: self.reveal(result)
            )
            judgment_row.addWidget(button)
        layout.addLayout(judgment_row)
        reveal_button = QPushButton("显示释义  Space")
        reveal_button.setObjectName("revealWordButton")
        reveal_button.clicked.connect(self.reveal)
        layout.addWidget(reveal_button)
        return page

    def _build_back(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.back_word = QLabel("")
        self.back_word.setObjectName("studyBackWord")
        layout.addWidget(self.back_word)
        self.back_phonetic = QLabel("")
        self.back_phonetic.setObjectName("studyPhonetic")
        layout.addWidget(self.back_phonetic)
        self.meaning = self._section(layout, "核心词义")
        self.exam_meaning = self._section(layout, "考研常见")
        self.collocations = self._section(layout, "常见搭配")
        self.example = self._section(layout, "例句")
        self.example_translation = QLabel("")
        self.example_translation.setObjectName("studyTranslation")
        self.example_translation.setWordWrap(True)
        layout.addWidget(self.example_translation)
        self.source_context_heading = QLabel("你是在这句话里遇到它的")
        self.source_context_heading.setObjectName("studySectionTitle")
        layout.addWidget(self.source_context_heading)
        self.source_context = QLabel("")
        self.source_context.setObjectName("studyContext")
        self.source_context.setWordWrap(True)
        layout.addWidget(self.source_context)
        self.note = self._section(layout, "易错点")
        layout.addStretch(1)

        result_row = QHBoxLayout()
        for text, result in (
            ("1  忘了", RESULT_FORGOT),
            ("2  困难", RESULT_HARD),
            ("3  一般", RESULT_NORMAL),
            ("4  简单", RESULT_EASY),
        ):
            button = QPushButton(text)
            button.setObjectName(f"review_{result}")
            button.clicked.connect(
                lambda _checked=False, value=result: self.rate(value)
            )
            result_row.addWidget(button)
        layout.addLayout(result_row)
        return page

    @staticmethod
    def _section(layout: QVBoxLayout, title: str) -> QLabel:
        heading = QLabel(title)
        heading.setObjectName("studySectionTitle")
        layout.addWidget(heading)
        value = QLabel("")
        value.setObjectName("studySectionValue")
        value.setWordWrap(True)
        layout.addWidget(value)
        return value

    @property
    def word(self) -> WordEntry | None:
        return self._word

    @property
    def is_revealed(self) -> bool:
        return self.faces.currentIndex() == 1

    def set_word(self, word: WordEntry) -> None:
        self._word = word
        self.initial_judgment = None
        self.front_word.setText(word.word)
        self.front_phonetic.setText(word.phonetic)
        self.back_word.setText(word.word)
        self.back_phonetic.setText(
            "  ".join(item for item in (word.phonetic, word.part_of_speech) if item)
        )
        self.meaning.setText(word.meaning)
        self._set_optional(self.exam_meaning, word.exam_meaning)
        self._set_optional(self.collocations, word.collocations)
        self._set_optional(self.example, word.example)
        self._set_optional(self.example_translation, word.example_translation)
        self._set_optional(self.note, word.note)
        has_context = bool(word.source_context)
        self.source_context_heading.setVisible(has_context)
        self.source_context.setVisible(has_context)
        self.source_context.setText(word.source_context)
        self.faces.setCurrentIndex(0)

    @staticmethod
    def _set_optional(label: QLabel, text: str) -> None:
        label.setText(text)
        label.setVisible(bool(text))
        previous = label.parentWidget().layout().indexOf(label) - 1
        if previous >= 0:
            heading_item = label.parentWidget().layout().itemAt(previous)
            heading = heading_item.widget() if heading_item is not None else None
            if isinstance(heading, QLabel) and heading.objectName() == "studySectionTitle":
                heading.setVisible(bool(text))

    def reveal(self, judgment: str | None = None) -> None:
        if self._word is None:
            return
        if judgment:
            self.initial_judgment = judgment
        self.faces.setCurrentIndex(1)

    def rate(self, result: str) -> None:
        if self._word is None or not self.is_revealed:
            return
        self.rated.emit(result)


class WordEditorDialog(QDialog):
    """Compact manual/reading word form; no model request is involved."""

    def __init__(
        self,
        *,
        reading: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.reading = reading
        self.setWindowTitle("添加阅读生词" if reading else "添加单词")
        self.setMinimumWidth(470)
        form = QFormLayout(self)
        form.setContentsMargins(20, 18, 20, 18)
        form.setSpacing(10)
        self.word_edit = QLineEdit()
        self.meaning_edit = QLineEdit()
        self.phonetic_edit = QLineEdit()
        self.pos_edit = QLineEdit()
        self.exam_meaning_edit = QLineEdit()
        self.collocations_edit = QTextEdit()
        self.collocations_edit.setMaximumHeight(62)
        self.example_edit = QLineEdit()
        self.translation_edit = QLineEdit()
        self.context_edit = QTextEdit()
        self.context_edit.setMaximumHeight(72)
        form.addRow("单词", self.word_edit)
        form.addRow("核心词义", self.meaning_edit)
        form.addRow("音标", self.phonetic_edit)
        form.addRow("词性", self.pos_edit)
        form.addRow("考研常见义", self.exam_meaning_edit)
        form.addRow("1～2 个搭配", self.collocations_edit)
        form.addRow("例句", self.example_edit)
        form.addRow("例句翻译", self.translation_edit)
        if reading:
            form.addRow("阅读原句", self.context_edit)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self) -> dict[str, str]:
        return {
            "word": self.word_edit.text(),
            "meaning": self.meaning_edit.text(),
            "phonetic": self.phonetic_edit.text(),
            "part_of_speech": self.pos_edit.text(),
            "exam_meaning": self.exam_meaning_edit.text(),
            "collocations": self.collocations_edit.toPlainText(),
            "example": self.example_edit.text(),
            "example_translation": self.translation_edit.text(),
            "source": SOURCE_READING if self.reading else SOURCE_MANUAL,
            "source_context": self.context_edit.toPlainText() if self.reading else "",
        }
