"""Non-activating companion speech bubble, quick-ask popup, and crisp context menu style."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


# Explicit dark-frosted menu theme that prevents Windows 11 translucent-parent black-on-black menus.
CONTEXT_MENU_STYLESHEET = """
QMenu {
    background-color: #24212E;
    color: #FAF5EF;
    border: 1px solid rgba(255, 224, 200, 75);
    border-radius: 10px;
    padding: 6px 4px;
    font-family: "Microsoft YaHei UI";
    font-size: 13px;
}
QMenu::item {
    background-color: transparent;
    color: #FAF5EF;
    padding: 7px 26px 7px 14px;
    margin: 1px 4px;
    border-radius: 6px;
}
QMenu::item:selected {
    background-color: #E07A42;
    color: #FFFFFF;
}
QMenu::item:disabled {
    color: #8C8499;
}
QMenu::separator {
    height: 1px;
    background: rgba(255, 228, 208, 45);
    margin: 5px 12px;
}
QMenu::indicator {
    width: 14px;
    height: 14px;
    margin-left: 6px;
}
QMenu::right-arrow {
    margin-right: 8px;
}
"""


def apply_context_menu_style(menu: QMenu) -> QMenu:
    """Apply explicit crisp styling recursively to a QMenu and its submenus."""
    menu.setStyleSheet(CONTEXT_MENU_STYLESHEET)
    for action in menu.actions():
        sub = action.menu()
        if sub is not None:
            apply_context_menu_style(sub)
    return menu


class CompanionBubble(QWidget):
    """Frameless, non-activating companion speech bubble displayed near the pet."""

    open_chat_requested = Signal()
    dismissed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._topmost = True
        self._hovered = False
        self._auto_hide_ms = 6500

        self.setObjectName("companionBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMinimumWidth(190)
        self.setMaximumWidth(295)
        self._apply_window_flags(topmost=True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("companionBubblePanel")
        panel.setStyleSheet(
            "QFrame#companionBubblePanel {"
            "  background-color: rgba(255, 251, 246, 245);"
            "  border: 1.5px solid rgba(224, 122, 66, 195);"
            "  border-radius: 14px;"
            "}"
            "QLabel#companionSpeaker {"
            "  color: #C95F28;"
            "  font-family: 'Microsoft YaHei UI';"
            "  font-size: 11px;"
            "  font-weight: 700;"
            "}"
            "QLabel#companionText {"
            "  color: #2B2320;"
            "  font-family: 'Microsoft YaHei UI';"
            "  font-size: 13px;"
            "}"
            "QPushButton#companionLinkBtn {"
            "  background: rgba(224, 122, 66, 28);"
            "  color: #C25822;"
            "  border: none;"
            "  border-radius: 8px;"
            "  padding: 2px 8px;"
            "  font-family: 'Microsoft YaHei UI';"
            "  font-size: 11px;"
            "  font-weight: 600;"
            "}"
            "QPushButton#companionLinkBtn:hover {"
            "  background: rgba(224, 122, 66, 55);"
            "}"
            "QPushButton#companionCloseBtn {"
            "  background: transparent;"
            "  color: #9A8478;"
            "  border: none;"
            "  font-size: 13px;"
            "  padding: 0px 4px;"
            "}"
            "QPushButton#companionCloseBtn:hover {"
            "  color: #2B2320;"
            "}"
        )
        root.addWidget(panel)

        body = QVBoxLayout(panel)
        body.setContentsMargins(13, 9, 11, 10)
        body.setSpacing(5)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self._speaker_label = QLabel("克劳德娘")
        self._speaker_label.setObjectName("companionSpeaker")
        header.addWidget(self._speaker_label)
        header.addStretch(1)

        self._chat_btn = QPushButton("接着聊")
        self._chat_btn.setObjectName("companionLinkBtn")
        self._chat_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._chat_btn.clicked.connect(self._on_open_chat_clicked)
        header.addWidget(self._chat_btn)

        close_btn = QPushButton("×")
        close_btn.setObjectName("companionCloseBtn")
        close_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_btn.clicked.connect(self.dismiss)
        header.addWidget(close_btn)
        body.addLayout(header)

        self._text_label = QLabel("")
        self._text_label.setObjectName("companionText")
        self._text_label.setWordWrap(True)
        body.addWidget(self._text_label)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._on_auto_timeout)

    def _apply_window_flags(self, *, topmost: bool) -> None:
        self._topmost = bool(topmost)
        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        if self._topmost:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        was_visible = self.isVisible()
        pos = self.pos()
        self.setWindowFlags(flags)
        if was_visible:
            self.show()
            self.move(pos)

    def set_topmost(self, topmost: bool) -> None:
        if bool(topmost) == self._topmost:
            return
        self._apply_window_flags(topmost=bool(topmost))

    @property
    def text(self) -> str:
        return self._text_label.text()

    def show_message(
        self,
        text: str,
        *,
        speaker: str = "克劳德娘",
        duration_ms: int = 6500,
        show_chat_button: bool = True,
    ) -> None:
        clean = text.strip()
        if not clean:
            return
        self._speaker_label.setText(speaker)
        self._text_label.setText(clean)
        self._chat_btn.setVisible(show_chat_button)
        self._auto_hide_ms = max(0, int(duration_ms))
        self.adjustSize()
        self.show()
        self.raise_()
        self._hide_timer.stop()
        if self._auto_hide_ms > 0 and not self._hovered:
            self._hide_timer.start(self._auto_hide_ms)

    def position_near(self, pet_rect: QRect, screen_area: QRect | None) -> None:
        self.adjustSize()
        gap = 10
        left_x = pet_rect.left() - self.width() - gap
        right_x = pet_rect.right() + gap
        if screen_area is not None:
            x = left_x if left_x >= screen_area.left() + 6 else right_x
            x = max(
                screen_area.left() + 6,
                min(x, screen_area.right() - self.width() - 6),
            )
            preferred_y = pet_rect.top() + 18
            y = max(
                screen_area.top() + 6,
                min(preferred_y, screen_area.bottom() - self.height() - 6),
            )
        else:
            x = left_x
            y = pet_rect.top() + 18
        self.move(x, y)

    def dismiss(self) -> None:
        self._hide_timer.stop()
        if self.isVisible():
            self.hide()
            self.dismissed.emit()

    def _on_auto_timeout(self) -> None:
        if not self._hovered:
            self.dismiss()

    def _on_open_chat_clicked(self) -> None:
        self.dismiss()
        self.open_chat_requested.emit()

    def enterEvent(self, event) -> None:
        self._hovered = True
        self._hide_timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        if self.isVisible() and self._auto_hide_ms > 0:
            self._hide_timer.start(2600)
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.dismiss()
            event.accept()
            return
        super().mousePressEvent(event)


class QuickAskPopup(QWidget):
    """Lightweight floating 1-line prompt box ("问她一句") anchored near the pet."""

    question_submitted = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("quickAskPopup")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(286)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        panel = QFrame()
        panel.setObjectName("quickAskPanel")
        panel.setStyleSheet(
            "QFrame#quickAskPanel {"
            "  background-color: rgba(36, 33, 46, 244);"
            "  border: 1.5px solid rgba(224, 122, 66, 210);"
            "  border-radius: 16px;"
            "}"
            "QLineEdit#quickAskInput {"
            "  background: transparent;"
            "  color: #FAF5EF;"
            "  border: none;"
            "  font-family: 'Microsoft YaHei UI';"
            "  font-size: 13px;"
            "  padding: 4px 6px;"
            "}"
            "QPushButton#quickAskSend {"
            "  background-color: #E07A42;"
            "  color: #FFFFFF;"
            "  border: none;"
            "  border-radius: 10px;"
            "  padding: 5px 12px;"
            "  font-family: 'Microsoft YaHei UI';"
            "  font-size: 12px;"
            "  font-weight: 600;"
            "}"
            "QPushButton#quickAskSend:hover {"
            "  background-color: #EE894F;"
            "}"
        )
        root.addWidget(panel)

        row = QHBoxLayout(panel)
        row.setContentsMargins(10, 6, 7, 6)
        row.setSpacing(6)

        self._input = QLineEdit()
        self._input.setObjectName("quickAskInput")
        self._input.setPlaceholderText("问她一句…（回车发送，Esc 取消）")
        self._input.returnPressed.connect(self._submit)
        row.addWidget(self._input, 1)

        send_btn = QPushButton("发送")
        send_btn.setObjectName("quickAskSend")
        send_btn.clicked.connect(self._submit)
        row.addWidget(send_btn)

    @property
    def input_edit(self) -> QLineEdit:
        return self._input

    def open_near(self, anchor_rect: QRect, screen_area: QRect | None) -> None:
        self.adjustSize()
        x = anchor_rect.center().x() - self.width() // 2
        y = anchor_rect.top() - self.height() - 10
        if screen_area is not None:
            x = max(
                screen_area.left() + 6,
                min(x, screen_area.right() - self.width() - 6),
            )
            if y < screen_area.top() + 6:
                y = anchor_rect.bottom() + 8
            y = max(
                screen_area.top() + 6,
                min(y, screen_area.bottom() - self.height() - 6),
            )
        self.move(x, y)
        self._input.clear()
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus(Qt.FocusReason.PopupFocusReason)

    def _submit(self) -> None:
        text = self._input.text().strip()
        self.hide()
        if text:
            self.question_submitted.emit(text)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)
