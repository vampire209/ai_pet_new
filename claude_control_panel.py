"""Claude-chan themed unified floating Control Panel (Status / Quick Chat / Settings).

Modeled after White Dragon's polished expandable card HUD, redesigned in Claude-chan's
Warm Parchment × Sunset Terracotta × Leather Espresso visual language, with Gemini
quick-launch and full integration for Todo, Memory, Companion Chat, and Pet Settings.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Sequence

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QKeySequence, QMouseEvent, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ai.ai_settings_service import (
    AiConnectionSettings,
    AiSettingsService,
    AiSettingsValidationError,
    PRESET_MODELS,
)
from character_pack import CharacterPack
from companion_service import CompanionReply
from models.task import Task
from pet_state import DEFAULT_GEMINI_URL, PetWindowState
from pet_status_pill import compute_todo_status_snapshot


_CLAUDE_PANEL_QSS = """
QFrame#claudePanelRoot {
    background-color: rgba(255, 251, 245, 250);
    border: 1.5px solid #E8C9B0;
    border-radius: 18px;
}
QWidget#claudePanelHeader {
    background: transparent;
}
QLabel#claudePanelTitle {
    color: #36251F;
    font-family: "Microsoft YaHei UI";
    font-size: 14px;
    font-weight: 700;
}
QPushButton#geminiHeaderBtn {
    background-color: #FCE9D8;
    color: #B84F19;
    border: 1px solid #EDC4A2;
    border-radius: 9px;
    padding: 4px 10px;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
    font-weight: 600;
}
QPushButton#geminiHeaderBtn:hover {
    background-color: #F8D8BC;
}
QPushButton#collapseHeaderBtn {
    background-color: transparent;
    color: #6B5349;
    border: 1px solid transparent;
    border-radius: 9px;
    padding: 4px 9px;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
}
QPushButton#collapseHeaderBtn:hover {
    background-color: #F4E6D8;
    color: #36251F;
}
QFrame#navStrip {
    background-color: #F7EDE2;
    border: 1px solid #EFE0D1;
    border-radius: 12px;
}
QPushButton[navBtn="true"] {
    background: transparent;
    color: #5E483E;
    border: none;
    border-radius: 8px;
    padding: 6px 10px;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
    font-weight: 600;
}
QPushButton[navBtn="true"]:hover {
    background-color: #EFE0D0;
    color: #36251F;
}
QPushButton[navBtn="true"]:checked {
    background-color: #FFFFFF;
    color: #C9591F;
    border: 1px solid #E7C3A6;
}
QPushButton[subTab="true"] {
    background-color: transparent;
    color: #6E564B;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 4px 10px;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
    font-weight: 600;
}
QPushButton[subTab="true"]:hover {
    background-color: #F5E8DA;
}
QPushButton[subTab="true"]:checked {
    background-color: #FCE8D6;
    color: #B84F19;
    border: 1px solid #E4AE85;
}
QLabel[sectionHeader="true"] {
    background-color: #F5E6D7;
    color: #7A4B2E;
    border-radius: 8px;
    padding: 5px 10px;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
    font-weight: 700;
}
QLabel[hintText="true"] {
    color: #826A5E;
    font-family: "Microsoft YaHei UI";
    font-size: 11px;
}
QCheckBox {
    color: #382822;
    font-family: "Microsoft YaHei UI";
    font-size: 12.5px;
    spacing: 8px;
    padding: 2px 0px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1.5px solid #CFA88B;
    background-color: #FFFFFF;
}
QCheckBox::indicator:checked {
    background-color: #D96B2F;
    border-color: #D96B2F;
}
QLineEdit, QComboBox {
    background-color: #FFFFFF;
    color: #332520;
    border: 1px solid #E2C5AD;
    border-radius: 8px;
    padding: 5px 9px;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
}
QLineEdit:focus, QComboBox:focus {
    border: 1.5px solid #D96B2F;
}
QPushButton[secondaryBtn="true"] {
    background-color: #FFFFFF;
    color: #4A3730;
    border: 1px solid #E3C8B2;
    border-radius: 9px;
    padding: 6px 12px;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
    font-weight: 600;
}
QPushButton[secondaryBtn="true"]:hover {
    background-color: #FAF0E6;
    border-color: #D99B72;
}
QPushButton[primaryBtn="true"] {
    background-color: #D96B2F;
    color: #FFFFFF;
    border: none;
    border-radius: 9px;
    padding: 6px 14px;
    font-family: "Microsoft YaHei UI";
    font-size: 12px;
    font-weight: 700;
}
QPushButton[primaryBtn="true"]:hover {
    background-color: #E77C42;
}
QPushButton[primaryBtn="true"]:disabled {
    background-color: #E8C5AD;
    color: #FFF7F0;
}
QProgressBar#claudeProgress {
    background-color: #F0DFCF;
    border: none;
    border-radius: 5px;
    height: 9px;
    text-align: center;
}
QProgressBar#claudeProgress::chunk {
    background-color: #D96B2F;
    border-radius: 5px;
}
QScrollArea {
    background: transparent;
    border: none;
}
"""


class ClaudeControlPanel(QWidget):
    """Unified Claude-themed floating Control Hub anchored to the pet or freely detached."""

    open_gemini_requested = Signal(str)
    open_todo_requested = Signal()
    open_memory_requested = Signal()
    open_full_chat_requested = Signal()
    open_profile_requested = Signal()
    open_characters_dir_requested = Signal()
    open_data_dir_requested = Signal()
    reset_position_requested = Signal()
    recover_requested = Signal()
    clear_companion_session_requested = Signal()
    quick_chat_submitted = Signal(str)
    complete_task_requested = Signal(str)
    state_applied = Signal(object)  # emits new PetWindowState
    topmost_toggled = Signal(bool)
    detach_toggled = Signal(bool)
    drag_moved = Signal(int, int)
    drag_finished = Signal()
    collapsed = Signal()

    def __init__(
        self,
        *,
        ai_settings_service: AiSettingsService | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._ai_settings_service = ai_settings_service
        self._topmost = True
        self._state = PetWindowState()
        self._packs: tuple[CharacterPack, ...] = ()
        self._pet_name = "克劳德娘"
        self._drag_origin: QPoint | None = None
        self._drag_last: QPoint | None = None
        self._dragging = False
        self._syncing = False

        self.setObjectName("claudeControlPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(446, 526)
        self._apply_window_flags(topmost=True)
        self.setStyleSheet(_CLAUDE_PANEL_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        root = QFrame()
        root.setObjectName("claudePanelRoot")
        outer.addWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(14, 12, 14, 12)
        main_layout.setSpacing(9)

        # --- 1. Draggable Header ---
        self._header_widget = QWidget()
        self._header_widget.setObjectName("claudePanelHeader")
        header_row = QHBoxLayout(self._header_widget)
        header_row.setContentsMargins(2, 0, 2, 0)
        header_row.setSpacing(6)

        self._title_label = QLabel("✦ 克劳德娘 · 控制面板")
        self._title_label.setObjectName("claudePanelTitle")
        header_row.addWidget(self._title_label, 1)

        self._gemini_btn = QPushButton("↗ Gemini")
        self._gemini_btn.setObjectName("geminiHeaderBtn")
        self._gemini_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._gemini_btn.clicked.connect(
            lambda: self.open_gemini_requested.emit(self._state.gemini_url)
        )
        header_row.addWidget(self._gemini_btn)

        collapse_btn = QPushButton("— 收起")
        collapse_btn.setObjectName("collapseHeaderBtn")
        collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        collapse_btn.clicked.connect(self.collapse_panel)
        header_row.addWidget(collapse_btn)
        main_layout.addWidget(self._header_widget)

        # --- 2. Primary Navigation Strip ---
        nav_frame = QFrame()
        nav_frame.setObjectName("navStrip")
        nav_row = QHBoxLayout(nav_frame)
        nav_row.setContentsMargins(5, 4, 5, 4)
        nav_row.setSpacing(4)

        self._nav_status_btn = self._make_nav_btn("≡ 状态", checkable=True)
        self._nav_chat_btn = self._make_nav_btn("💬 聊天", checkable=True)
        self._nav_settings_btn = self._make_nav_btn("⚙ 设置", checkable=True)
        self._nav_pin_btn = self._make_nav_btn("📌 置顶", checkable=True)
        self._nav_detach_btn = self._make_nav_btn("✥ 分离", checkable=True)
        self._nav_detach_btn.setToolTip("开启后，克劳德娘走动时面板固定不动，可按住顶部自由拖动")

        self._nav_status_btn.clicked.connect(lambda: self.select_page("status"))
        self._nav_chat_btn.clicked.connect(lambda: self.select_page("chat"))
        self._nav_settings_btn.clicked.connect(lambda: self.select_page("settings"))
        self._nav_pin_btn.toggled.connect(self._on_nav_pin_toggled)
        self._nav_detach_btn.toggled.connect(self._on_nav_detach_toggled)

        for btn in (self._nav_status_btn, self._nav_chat_btn, self._nav_settings_btn):
            nav_row.addWidget(btn)
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #DFC7B3;")
        nav_row.addWidget(sep)
        nav_row.addWidget(self._nav_pin_btn)
        nav_row.addWidget(self._nav_detach_btn)
        main_layout.addWidget(nav_frame)

        # --- 3. Main Stacked Pages ---
        self._pages = QStackedWidget()
        self._pages.addWidget(self._build_status_page())
        self._pages.addWidget(self._build_chat_page())
        self._pages.addWidget(self._build_settings_page())
        main_layout.addWidget(self._pages, 1)

        # Ctrl+S shortcut applies settings when on Settings page
        shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        shortcut.activated.connect(self._apply_settings_clicked)

        self.select_page("status")

    def _make_nav_btn(self, text: str, *, checkable: bool) -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("navBtn", "true")
        btn.setCheckable(checkable)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _make_sub_tab(self, text: str) -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("subTab", "true")
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _apply_window_flags(self, *, topmost: bool) -> None:
        self._topmost = bool(topmost)
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
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

    # ------------------------------------------------------------------
    # Page 1: Status / Todo / Memory Hub
    # ------------------------------------------------------------------
    def _build_status_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(10)

        # Warm summary card
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background-color: #FFF5EA; border: 1px solid #EED4BD; border-radius: 14px; }"
        )
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(7)

        top_row = QHBoxLayout()
        self._status_headline = QLabel("待命中 · 一切就绪")
        self._status_headline.setStyleSheet(
            "color: #36251F; font-size: 14px; font-weight: 700; border: none; background: transparent;"
        )
        self._status_pct_label = QLabel("100%")
        self._status_pct_label.setStyleSheet(
            "color: #C9591F; font-size: 14px; font-weight: 700; border: none; background: transparent;"
        )
        top_row.addWidget(self._status_headline, 1)
        top_row.addWidget(self._status_pct_label)
        card_layout.addLayout(top_row)

        self._status_progress = QProgressBar()
        self._status_progress.setObjectName("claudeProgress")
        self._status_progress.setRange(0, 100)
        self._status_progress.setValue(100)
        self._status_progress.setTextVisible(False)
        card_layout.addWidget(self._status_progress)

        self._status_subline = QLabel("记忆库随时守护 · 提醒引擎运行中")
        self._status_subline.setStyleSheet(
            "color: #7D6256; font-size: 11.5px; border: none; background: transparent;"
        )
        card_layout.addWidget(self._status_subline)
        layout.addWidget(card)

        # Pending todos quick list
        todo_header = QLabel("待办快览（勾选即可完成）")
        todo_header.setProperty("sectionHeader", "true")
        layout.addWidget(todo_header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._todo_list_host = QWidget()
        self._todo_list_layout = QVBoxLayout(self._todo_list_host)
        self._todo_list_layout.setContentsMargins(4, 2, 4, 2)
        self._todo_list_layout.setSpacing(5)
        self._todo_list_layout.addStretch(1)
        scroll.setWidget(self._todo_list_host)
        layout.addWidget(scroll, 1)

        # Quick entrance grid
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(7)
        btn_todo = QPushButton("📝 完整待办与提醒")
        btn_todo.setProperty("secondaryBtn", "true")
        btn_todo.clicked.connect(self.open_todo_requested.emit)
        btn_mem = QPushButton("📚 长期记忆管理")
        btn_mem.setProperty("secondaryBtn", "true")
        btn_mem.clicked.connect(self.open_memory_requested.emit)
        btn_chat = QPushButton("🎙 完整聊天（含语音）")
        btn_chat.setProperty("secondaryBtn", "true")
        btn_chat.clicked.connect(self.open_full_chat_requested.emit)
        btn_gem = QPushButton("✦ 打开 Gemini")
        btn_gem.setProperty("primaryBtn", "true")
        btn_gem.clicked.connect(
            lambda: self.open_gemini_requested.emit(self._state.gemini_url)
        )
        grid.addWidget(btn_todo, 0, 0)
        grid.addWidget(btn_mem, 0, 1)
        grid.addWidget(btn_chat, 1, 0)
        grid.addWidget(btn_gem, 1, 1)
        layout.addLayout(grid)
        return page

    # ------------------------------------------------------------------
    # Page 2: Docked Quick Chat (Moves WITH Claude-chan!)
    # ------------------------------------------------------------------
    def _build_chat_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        hint = QLabel("独立 30 轮轻量陪聊（跟随桌宠移动，不污染长期记忆）")
        hint.setProperty("hintText", "true")
        bar.addWidget(hint, 1)

        full_btn = QPushButton("完整聊天窗")
        full_btn.setProperty("secondaryBtn", "true")
        full_btn.clicked.connect(self.open_full_chat_requested.emit)
        bar.addWidget(full_btn)

        clear_btn = QPushButton("清空")
        clear_btn.setProperty("secondaryBtn", "true")
        clear_btn.clicked.connect(self._on_clear_chat_clicked)
        bar.addWidget(clear_btn)
        layout.addLayout(bar)

        self._chat_scroll = QScrollArea()
        self._chat_scroll.setWidgetResizable(True)
        self._chat_scroll.setStyleSheet(
            "QScrollArea { background-color: #FAF2E8; border: 1px solid #EFE0D1; border-radius: 12px; }"
        )
        self._chat_host = QWidget()
        self._chat_host.setStyleSheet("background: transparent;")
        self._chat_vbox = QVBoxLayout(self._chat_host)
        self._chat_vbox.setContentsMargins(10, 10, 10, 10)
        self._chat_vbox.setSpacing(8)
        self._chat_vbox.addStretch(1)
        self._chat_scroll.setWidget(self._chat_host)
        layout.addWidget(self._chat_scroll, 1)

        input_row = QHBoxLayout()
        input_row.setSpacing(6)
        self._chat_input = QLineEdit()
        self._chat_input.setObjectName("panelChatInput")
        self._chat_input.setPlaceholderText("和克劳德娘聊两句…（回车发送）")
        self._chat_input.returnPressed.connect(self._submit_panel_chat)
        input_row.addWidget(self._chat_input, 1)

        send_btn = QPushButton("发送")
        send_btn.setProperty("primaryBtn", "true")
        send_btn.clicked.connect(self._submit_panel_chat)
        input_row.addWidget(send_btn)
        layout.addLayout(input_row)
        return page

    def _submit_panel_chat(self) -> None:
        text = self._chat_input.text().strip()
        if not text:
            return
        self._chat_input.clear()
        self.append_chat_message("user", text)
        self.quick_chat_submitted.emit(text)

    def _on_clear_chat_clicked(self) -> None:
        self.clear_chat_messages()
        self.clear_companion_session_requested.emit()

    def clear_chat_messages(self) -> None:
        while self._chat_vbox.count() > 1:
            item = self._chat_vbox.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def append_chat_message(self, role: str, text: str) -> None:
        clean = text.strip()
        if not clean:
            return
        row = QWidget()
        row.setStyleSheet("background: transparent;")
        hbox = QHBoxLayout(row)
        hbox.setContentsMargins(0, 0, 0, 0)

        bubble = QLabel(clean)
        bubble.setWordWrap(True)
        bubble.setMaximumWidth(315)
        if role == "user":
            bubble.setStyleSheet(
                "background-color: #D96B2F; color: #FFFFFF; border-radius: 12px; "
                "padding: 7px 11px; font-family: 'Microsoft YaHei UI'; font-size: 12.5px;"
            )
            hbox.addStretch(1)
            hbox.addWidget(bubble)
        else:
            bubble.setStyleSheet(
                "background-color: #FFFFFF; color: #36251F; border: 1px solid #E9CEB7; "
                "border-radius: 12px; padding: 7px 11px; font-family: 'Microsoft YaHei UI'; font-size: 12.5px;"
            )
            hbox.addWidget(bubble)
            hbox.addStretch(1)

        self._chat_vbox.insertWidget(max(0, self._chat_vbox.count() - 1), row)
        bar = self._chat_scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def sync_chat_history(self, messages: Sequence[dict[str, str]]) -> None:
        self.clear_chat_messages()
        if not messages:
            self.append_chat_message(
                "assistant",
                f"嗨～我是{self._pet_name}！有什么想聊的、或者要我陪你做的事直接发给我吧。",
            )
            return
        for msg in messages:
            self.append_chat_message(msg.get("role", "assistant"), msg.get("content", ""))

    # ------------------------------------------------------------------
    # Page 3: Settings (White Dragon tabbed layout in Claude theme)
    # ------------------------------------------------------------------
    def _build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(8)

        # Sub-tab bar
        tab_row = QHBoxLayout()
        tab_row.setSpacing(4)
        self._sub_tabs: dict[str, QPushButton] = {}
        for key, label in (
            ("panel", "面板"),
            ("character", "人物"),
            ("gemini", "Gemini / AI"),
            ("companion", "闲聊"),
            ("maintenance", "维护"),
        ):
            btn = self._make_sub_tab(label)
            btn.clicked.connect(lambda _=False, k=key: self.select_settings_tab(k))
            self._sub_tabs[key] = btn
            tab_row.addWidget(btn)
        tab_row.addStretch(1)
        layout.addLayout(tab_row)

        self._settings_stack = QStackedWidget()
        self._settings_stack.addWidget(self._build_settings_panel_tab())
        self._settings_stack.addWidget(self._build_settings_character_tab())
        self._settings_stack.addWidget(self._build_settings_gemini_tab())
        self._settings_stack.addWidget(self._build_settings_companion_tab())
        self._settings_stack.addWidget(self._build_settings_maintenance_tab())
        layout.addWidget(self._settings_stack, 1)

        self._settings_status_label = QLabel("设置已保存 · Ctrl+S 应用修改")
        self._settings_status_label.setProperty("hintText", "true")
        layout.addWidget(self._settings_status_label)

        # Footer buttons (matches White Dragon's 4-button bar)
        footer = QHBoxLayout()
        footer.setSpacing(7)
        reset_btn = QPushButton("本页恢复默认")
        reset_btn.setProperty("secondaryBtn", "true")
        reset_btn.clicked.connect(self._reset_current_settings_tab)

        discard_btn = QPushButton("放弃修改")
        discard_btn.setProperty("secondaryBtn", "true")
        discard_btn.clicked.connect(self._discard_settings_clicked)

        apply_btn = QPushButton("应用")
        apply_btn.setObjectName("applySettingsBtn")
        apply_btn.setProperty("primaryBtn", "true")
        apply_btn.clicked.connect(self._apply_settings_clicked)

        back_btn = QPushButton("返回")
        back_btn.setProperty("secondaryBtn", "true")
        back_btn.clicked.connect(lambda: self.select_page("status"))

        for btn in (reset_btn, discard_btn, apply_btn, back_btn):
            footer.addWidget(btn, 1)
        layout.addLayout(footer)

        self.select_settings_tab("panel")
        return page

    def _build_settings_panel_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(2, 2, 6, 4)
        layout.setSpacing(7)

        sec = QLabel("显示哪些内容")
        sec.setProperty("sectionHeader", "true")
        layout.addWidget(sec)

        tip = QLabel("勾选与设置点击“应用”后生效；切换页签保留草稿。")
        tip.setProperty("hintText", "true")
        layout.addWidget(tip)

        self._chk_detach = QCheckBox("面板与人物分离（人物走动时胶囊与面板固定，可单独拖动）")
        self._chk_char_visible = QCheckBox("显示人物（关闭后仅保留独立胶囊/面板）")
        self._chk_topmost = QCheckBox("始终置顶")
        self._chk_fullscreen_yield = QCheckBox("视频全屏时临时取消置顶（待办提醒优先）")
        yield_tip = QLabel("提醒处理完毕后，若视频仍在全屏则继续让位；退出全屏恢复置顶。")
        yield_tip.setProperty("hintText", "true")
        yield_tip.setWordWrap(True)
        self._chk_mouse_through = QCheckBox("鼠标穿透（可从托盘「找回组件并关闭穿透」恢复）")
        self._chk_pill_visible = QCheckBox("显示头顶灵动胶囊（关闭后仅保留人物）")
        self._chk_position_locked = QCheckBox("锁定人物位置（禁止拖拽与自主漫步）")

        for widget in (
            self._chk_detach,
            self._chk_char_visible,
            self._chk_topmost,
            self._chk_fullscreen_yield,
            yield_tip,
            self._chk_mouse_through,
            self._chk_pill_visible,
            self._chk_position_locked,
        ):
            layout.addWidget(widget)
        layout.addStretch(1)
        scroll.setWidget(host)
        return scroll

    def _build_settings_character_tab(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(2, 2, 6, 4)
        layout.setSpacing(9)

        sec = QLabel("角色形象与桌面行为")
        sec.setProperty("sectionHeader", "true")
        layout.addWidget(sec)

        pack_row = QHBoxLayout()
        pack_row.addWidget(QLabel("当前角色包："))
        self._pack_combo = QComboBox()
        pack_row.addWidget(self._pack_combo, 1)
        open_packs_btn = QPushButton("打开目录")
        open_packs_btn.setProperty("secondaryBtn", "true")
        open_packs_btn.clicked.connect(self.open_characters_dir_requested.emit)
        pack_row.addWidget(open_packs_btn)
        layout.addLayout(pack_row)

        self._chk_wander = QCheckBox("开启自主漫步（闲置时在桌面轻步走动并转身）")
        layout.addWidget(self._chk_wander)

        persona_sec = QLabel("人设与性格微调")
        persona_sec.setProperty("sectionHeader", "true")
        layout.addWidget(persona_sec)

        persona_tip = QLabel("桌宠昵称、关系设定与温柔/黏人/活泼等性格维度在专属人设窗中调整。")
        persona_tip.setProperty("hintText", "true")
        persona_tip.setWordWrap(True)
        layout.addWidget(persona_tip)

        open_profile_btn = QPushButton("✿ 打开桌宠人设微调窗口")
        open_profile_btn.setProperty("secondaryBtn", "true")
        open_profile_btn.clicked.connect(self.open_profile_requested.emit)
        layout.addWidget(open_profile_btn)
        layout.addStretch(1)
        return host

    def _build_settings_gemini_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(2, 2, 6, 4)
        layout.setSpacing(8)

        sec1 = QLabel("Gemini 快捷入口（替代原白龙 GPT 入口）")
        sec1.setProperty("sectionHeader", "true")
        layout.addWidget(sec1)

        self._gemini_url_edit = QLineEdit(DEFAULT_GEMINI_URL)
        self._gemini_url_edit.setPlaceholderText("https://gemini.google.com/")
        layout.addWidget(self._gemini_url_edit)

        preset_row = QHBoxLayout()
        p_gem = QPushButton("Gemini 官网")
        p_gem.setProperty("secondaryBtn", "true")
        p_gem.clicked.connect(lambda: self._gemini_url_edit.setText("https://gemini.google.com/"))
        p_studio = QPushButton("AI Studio")
        p_studio.setProperty("secondaryBtn", "true")
        p_studio.clicked.connect(lambda: self._gemini_url_edit.setText("https://aistudio.google.com/"))
        p_test = QPushButton("立即打开")
        p_test.setProperty("primaryBtn", "true")
        p_test.clicked.connect(
            lambda: self.open_gemini_requested.emit(
                self._gemini_url_edit.text().strip() or DEFAULT_GEMINI_URL
            )
        )
        preset_row.addWidget(p_gem)
        preset_row.addWidget(p_studio)
        preset_row.addWidget(p_test)
        layout.addLayout(preset_row)

        sec2 = QLabel("内置 AI 对话接口（DeepSeek 兼容）")
        sec2.setProperty("sectionHeader", "true")
        layout.addWidget(sec2)

        self._api_key_edit = QLineEdit()
        self._api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key_edit.setPlaceholderText("API Key（留空时自动使用 DEEPSEEK_API_KEY 环境变量）")
        layout.addWidget(self._api_key_edit)

        self._base_url_edit = QLineEdit()
        self._base_url_edit.setPlaceholderText("Base URL（默认 https://api.deepseek.com）")
        layout.addWidget(self._base_url_edit)

        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        for model in PRESET_MODELS:
            self._model_combo.addItem(model)
        layout.addWidget(self._model_combo)

        layout.addStretch(1)
        scroll.setWidget(host)
        return scroll

    def _build_settings_companion_tab(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(2, 2, 6, 4)
        layout.setSpacing(9)

        sec = QLabel("主动陪聊气泡与前台感知")
        sec.setProperty("sectionHeader", "true")
        layout.addWidget(sec)

        self._chk_bubble = QCheckBox("开启主动陪聊气泡（轻点戳一下 Q 弹吐槽）")
        self._chk_aware = QCheckBox("开启前台窗口感知（写代码 / 刷 B 站自动搭话）")
        aware_tip = QLabel("内置 20 秒停留过滤与 5 分钟冷却；全屏看片、游戏或待办提醒时自动静音。")
        aware_tip.setProperty("hintText", "true")
        aware_tip.setWordWrap(True)

        clear_btn = QPushButton("清空 30 轮陪聊短会话")
        clear_btn.setProperty("secondaryBtn", "true")
        clear_btn.clicked.connect(self._on_clear_chat_clicked)

        layout.addWidget(self._chk_bubble)
        layout.addWidget(self._chk_aware)
        layout.addWidget(aware_tip)
        layout.addWidget(clear_btn)
        layout.addStretch(1)
        return host

    def _build_settings_maintenance_tab(self) -> QWidget:
        host = QWidget()
        layout = QVBoxLayout(host)
        layout.setContentsMargins(2, 2, 6, 4)
        layout.setSpacing(8)

        sec = QLabel("位置恢复与个人数据")
        sec.setProperty("sectionHeader", "true")
        layout.addWidget(sec)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        actions = (
            ("📍 回到屏幕右下角", self.reset_position_requested.emit),
            ("🛟 找回组件并关闭穿透", self.recover_requested.emit),
            ("📝 打开完整待办", self.open_todo_requested.emit),
            ("📚 打开记忆管理", self.open_memory_requested.emit),
            ("📂 打开角色包目录", self.open_characters_dir_requested.emit),
            ("🗂 打开用户数据目录", self.open_data_dir_requested.emit),
        )
        for idx, (label, callback) in enumerate(actions):
            btn = QPushButton(label)
            btn.setProperty("secondaryBtn", "true")
            btn.clicked.connect(callback)
            grid.addWidget(btn, idx // 2, idx % 2)
        layout.addLayout(grid)
        layout.addStretch(1)
        return host

    # ------------------------------------------------------------------
    # Navigation & Sync
    # ------------------------------------------------------------------
    def select_page(self, page_name: str) -> None:
        index_map = {"status": 0, "chat": 1, "settings": 2}
        idx = index_map.get(page_name, 0)
        self._pages.setCurrentIndex(idx)
        self._nav_status_btn.setChecked(idx == 0)
        self._nav_chat_btn.setChecked(idx == 1)
        self._nav_settings_btn.setChecked(idx == 2)
        suffix = ("状态", "聊天", "设置")[idx]
        self._title_label.setText(f"✦ {self._pet_name} · {suffix}")
        if idx == 1:
            self._chat_input.setFocus()

    def select_settings_tab(self, tab_name: str) -> None:
        order = ("panel", "character", "gemini", "companion", "maintenance")
        idx = order.index(tab_name) if tab_name in order else 0
        self._settings_stack.setCurrentIndex(idx)
        for key, btn in self._sub_tabs.items():
            btn.setChecked(key == order[idx])

    def sync_from_pet(
        self,
        *,
        state: PetWindowState,
        packs: Sequence[CharacterPack],
        tasks: Sequence[Task],
        pet_name: str = "克劳德娘",
        memory_count: int | None = None,
        active_reminder_count: int = 0,
    ) -> None:
        self._syncing = True
        try:
            self._state = state
            self._packs = tuple(packs)
            self._pet_name = pet_name.strip() or "克劳德娘"
            self._nav_pin_btn.setChecked(state.always_on_top)
            self._nav_detach_btn.setChecked(state.panel_detached)

            # Update title keeping current tab suffix
            suffix = ("状态", "聊天", "设置")[self._pages.currentIndex()]
            self._title_label.setText(f"✦ {self._pet_name} · {suffix}")

            # Sync Status page
            snap = compute_todo_status_snapshot(
                tasks,
                active_reminder_count=active_reminder_count,
                pet_name=self._pet_name,
            )
            self._status_headline.setText(snap.status_text)
            self._status_pct_label.setText(snap.progress_label)
            self._status_progress.setValue(int(round(snap.progress_ratio * 100)))
            mem_part = f"已记住 {memory_count} 条长期记忆 · " if memory_count is not None else ""
            pending_count = sum(1 for t in tasks if not t.completed)
            self._status_subline.setText(
                f"{mem_part}待办剩余 {pending_count} 项 · Gemini 快捷入口已就绪"
            )
            self._rebuild_todo_preview(tasks)

            # Sync Settings drafts
            self._load_settings_draft(state, packs)
        finally:
            self._syncing = False

    def _rebuild_todo_preview(self, tasks: Sequence[Task]) -> None:
        while self._todo_list_layout.count() > 1:
            item = self._todo_list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        pending = [t for t in tasks if not t.completed][:6]
        if not pending:
            empty = QLabel("当前没有未完成待办，尽情享受轻松时光吧~")
            empty.setProperty("hintText", "true")
            self._todo_list_layout.insertWidget(0, empty)
            return

        for task in pending:
            cb = QCheckBox(task.text)
            cb.setChecked(False)
            cb.toggled.connect(
                lambda checked, tid=task.id: self.complete_task_requested.emit(tid)
                if checked
                else None
            )
            self._todo_list_layout.insertWidget(
                max(0, self._todo_list_layout.count() - 1), cb
            )

    def _load_settings_draft(
        self,
        state: PetWindowState,
        packs: Sequence[CharacterPack],
    ) -> None:
        self._chk_detach.setChecked(state.panel_detached)
        self._chk_char_visible.setChecked(state.character_visible)
        self._chk_topmost.setChecked(state.always_on_top)
        self._chk_fullscreen_yield.setChecked(state.fullscreen_yield_enabled)
        self._chk_mouse_through.setChecked(state.mouse_through)
        self._chk_pill_visible.setChecked(state.status_pill_enabled)
        self._chk_position_locked.setChecked(state.position_locked)
        self._chk_wander.setChecked(state.wandering_enabled)
        self._chk_bubble.setChecked(state.companion_bubble_enabled)
        self._chk_aware.setChecked(state.window_awareness_enabled)
        self._gemini_url_edit.setText(state.gemini_url)

        self._pack_combo.clear()
        selected_idx = 0
        for idx, pack in enumerate(packs):
            self._pack_combo.addItem(pack.name, pack.pack_id)
            if pack.pack_id == state.character_pack:
                selected_idx = idx
        if packs:
            self._pack_combo.setCurrentIndex(selected_idx)

        if self._ai_settings_service is not None:
            ai_cfg = self._ai_settings_service.settings
            self._api_key_edit.setText(ai_cfg.api_key)
            self._base_url_edit.setText(ai_cfg.base_url)
            self._model_combo.setCurrentText(ai_cfg.model)

    def _on_nav_pin_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._chk_topmost.setChecked(checked)
        self.topmost_toggled.emit(checked)

    def _on_nav_detach_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._chk_detach.setChecked(checked)
        self.detach_toggled.emit(checked)

    def _reset_current_settings_tab(self) -> None:
        idx = self._settings_stack.currentIndex()
        if idx == 0:  # panel
            self._chk_detach.setChecked(False)
            self._chk_char_visible.setChecked(True)
            self._chk_topmost.setChecked(True)
            self._chk_fullscreen_yield.setChecked(True)
            self._chk_mouse_through.setChecked(False)
            self._chk_pill_visible.setChecked(True)
            self._chk_position_locked.setChecked(False)
        elif idx == 1:  # character
            self._chk_wander.setChecked(True)
        elif idx == 2:  # gemini
            self._gemini_url_edit.setText(DEFAULT_GEMINI_URL)
        elif idx == 3:  # companion
            self._chk_bubble.setChecked(True)
            self._chk_aware.setChecked(True)
        self._settings_status_label.setText("已恢复本页默认草稿，点击“应用”生效")

    def _discard_settings_clicked(self) -> None:
        self._load_settings_draft(self._state, self._packs)
        self._settings_status_label.setText("已放弃未应用的草稿修改")

    def _apply_settings_clicked(self) -> None:
        gemini_url = self._gemini_url_edit.text().strip() or DEFAULT_GEMINI_URL
        if not (gemini_url.startswith("https://") or gemini_url.startswith("http://")):
            self._settings_status_label.setText("Gemini 网址必须以 http:// 或 https:// 开头")
            return

        if self._ai_settings_service is not None:
            try:
                ai_settings = AiConnectionSettings(
                    api_key=self._api_key_edit.text(),
                    base_url=self._base_url_edit.text(),
                    model=self._model_combo.currentText(),
                )
                self._ai_settings_service.save(ai_settings)
            except AiSettingsValidationError as exc:
                self._settings_status_label.setText(f"AI 接口校验失败：{exc}")
                return

        pack_id = self._pack_combo.currentData() or self._state.character_pack
        # Ensure user never hides BOTH character and pill at the same time
        char_visible = self._chk_char_visible.isChecked()
        pill_visible = self._chk_pill_visible.isChecked()
        if not char_visible and not pill_visible:
            pill_visible = True
            self._chk_pill_visible.setChecked(True)

        updated = replace(
            self._state,
            panel_detached=self._chk_detach.isChecked(),
            character_visible=char_visible,
            always_on_top=self._chk_topmost.isChecked(),
            fullscreen_yield_enabled=self._chk_fullscreen_yield.isChecked(),
            mouse_through=self._chk_mouse_through.isChecked(),
            status_pill_enabled=pill_visible,
            position_locked=self._chk_position_locked.isChecked(),
            wandering_enabled=self._chk_wander.isChecked(),
            character_pack=str(pack_id),
            companion_bubble_enabled=self._chk_bubble.isChecked(),
            window_awareness_enabled=self._chk_aware.isChecked(),
            gemini_url=gemini_url,
        )
        self._state = updated
        self._settings_status_label.setText("设置已保存并立即生效 · Ctrl+S 应用修改")
        self.state_applied.emit(updated)

    # ------------------------------------------------------------------
    # Positioning, Collapse & Header Dragging
    # ------------------------------------------------------------------
    def position_near(self, anchor_rect: QRect, screen_area: QRect | None) -> None:
        x = anchor_rect.center().x() - self.width() // 2
        y = anchor_rect.top() - self.height() - 10
        if screen_area is not None:
            x = max(
                screen_area.left() + 8,
                min(x, screen_area.right() - self.width() - 8),
            )
            if y < screen_area.top() + 8:
                # Prefer placing beside the pet if there is room above is tight
                left_x = anchor_rect.left() - self.width() - 12
                right_x = anchor_rect.right() + 12
                if left_x >= screen_area.left() + 8:
                    x = left_x
                    y = anchor_rect.bottom() - self.height()
                elif right_x + self.width() <= screen_area.right() - 8:
                    x = right_x
                    y = anchor_rect.bottom() - self.height()
                else:
                    y = screen_area.top() + 8
            y = max(
                screen_area.top() + 8,
                min(y, screen_area.bottom() - self.height() - 8),
            )
        self.move(x, y)

    def collapse_panel(self) -> None:
        if self.isVisible():
            self.hide()
            self.collapsed.emit()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            if event.position().y() <= 46:
                self._drag_origin = event.globalPosition().toPoint()
                self._drag_last = self._drag_origin
                self._dragging = False
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            current = event.globalPosition().toPoint()
            if not self._dragging:
                if (current - self._drag_origin).manhattanLength() >= QApplication.styleHints().startDragDistance():
                    self._dragging = True
                    self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._dragging and self._drag_last is not None:
                delta = current - self._drag_last
                self._drag_last = current
                self.drag_moved.emit(delta.x(), delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag_origin is not None:
            was_dragging = self._dragging
            self._drag_origin = None
            self._drag_last = None
            self._dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if was_dragging:
                self.drag_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)
