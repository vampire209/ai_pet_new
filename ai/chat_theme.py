"""Centralized visual tokens for the desktop-pet chat space."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChatTheme:
    """Soft blue, lavender and milk-white tokens shared by chat widgets."""

    window_top: str = "#eef9ff"
    window_middle: str = "#f7f7ff"
    window_bottom: str = "#f2edff"
    surface: str = "rgba(255, 255, 255, 222)"
    surface_soft: str = "rgba(251, 252, 255, 214)"
    user_bubble_start: str = "#d9ecff"
    user_bubble_end: str = "#e3e2ff"
    pet_bubble_start: str = "#fffefa"
    pet_bubble_end: str = "#f8f4ff"
    todo_surface: str = "rgba(252, 250, 255, 238)"
    input_surface: str = "rgba(255, 255, 255, 235)"
    primary: str = "#7789dc"
    primary_hover: str = "#8496e5"
    primary_pressed: str = "#687aca"
    border: str = "rgba(180, 197, 229, 176)"
    border_lavender: str = "rgba(198, 187, 235, 190)"
    text_primary: str = "#2d3852"
    text_secondary: str = "#7d88a4"
    accent: str = "#7366b3"
    success: str = "#67957f"
    cancelled: str = "#929aad"
    danger: str = "#a96376"
    panel_radius: int = 22
    bubble_radius: int = 17
    input_radius: int = 19


THEME = ChatTheme()


CHAT_WINDOW_STYLE = f"""
QWidget#chatWindow {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 {THEME.window_top},
        stop: 0.52 {THEME.window_middle},
        stop: 1 {THEME.window_bottom}
    );
    color: {THEME.text_primary};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei";
}}
QFrame#chatHeader {{
    background: {THEME.surface};
    border: 1px solid {THEME.border};
    border-radius: {THEME.panel_radius}px;
}}
QFrame#headerPetAvatar {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 #e5f5ff, stop: 1 #ebe5ff
    );
    border: 1px solid rgba(160, 181, 226, 196);
    border-radius: 27px;
}}
QFrame#messagePetAvatar {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 #e9f7ff, stop: 1 #eee9ff
    );
    border: 1px solid rgba(178, 190, 228, 190);
    border-radius: 17px;
}}
QLabel#chatPetName {{
    color: #29354f;
    font-size: 18px;
    font-weight: 650;
}}
QLabel#chatPetStatus {{
    color: {THEME.text_secondary};
    font-size: 12px;
}}
QLabel#chatModelLabel {{
    color: #8581a9;
    background: rgba(237, 234, 251, 175);
    border: 1px solid rgba(207, 200, 236, 145);
    border-radius: 9px;
    padding: 3px 8px;
    font-size: 10px;
}}
QComboBox#chatAbilityCombo {{
    min-height: 26px;
    max-height: 26px;
    min-width: 94px;
    color: #666b98;
    background: rgba(242, 241, 253, 205);
    border: 1px solid rgba(201, 200, 232, 175);
    border-radius: 9px;
    padding: 0 8px;
    font-size: 11px;
}}
QComboBox#chatAbilityCombo:hover,
QComboBox#chatAbilityCombo:focus {{
    border-color: rgba(143, 151, 211, 215);
    background: rgba(248, 248, 255, 235);
}}
QComboBox#chatAbilityCombo:disabled {{
    color: #a4a8b8;
    background: rgba(241, 242, 247, 150);
}}
QComboBox#chatAbilityCombo::drop-down {{
    width: 18px;
    border: none;
}}
QComboBox#chatAbilityCombo QAbstractItemView {{
    color: #4f5972;
    background: #fbfbff;
    border: 1px solid #d8ddef;
    selection-color: #596aa6;
    selection-background-color: #e5ebfb;
    outline: 0;
}}
QPushButton#wordLearningButton {{
    min-height: 27px;
    max-height: 27px;
    padding: 0 9px;
    color: #626d9d;
    background: rgba(235, 242, 255, 205);
    border: 1px solid rgba(188, 202, 235, 185);
    border-radius: 9px;
    font-size: 11px;
}}
QPushButton#wordLearningButton:hover {{
    color: #5665a0;
    background: rgba(226, 235, 253, 235);
}}
QPushButton#headerClearButton {{
    min-width: 28px;
    max-width: 28px;
    min-height: 28px;
    max-height: 28px;
    padding: 0px;
    color: #8c94aa;
    background: rgba(246, 247, 252, 170);
    border: 1px solid rgba(211, 217, 232, 150);
    border-radius: 14px;
    font-size: 17px;
}}
QPushButton#headerClearButton:hover {{
    color: #6f7894;
    background: rgba(237, 240, 250, 220);
}}
QPushButton#headerClearButton::menu-indicator {{
    image: none;
    width: 0px;
}}
QMenu#chatHeaderMenu {{
    color: #4f5972;
    background: rgba(252, 252, 255, 246);
    border: 1px solid rgba(199, 207, 230, 210);
    border-radius: 10px;
    padding: 6px;
}}
QMenu#chatHeaderMenu::item {{
    min-width: 132px;
    padding: 7px 14px;
    border-radius: 7px;
}}
QMenu#chatHeaderMenu::item:selected {{
    color: #596aa6;
    background: rgba(229, 235, 251, 225);
}}
QMenu#chatHeaderMenu::separator {{
    height: 1px;
    background: rgba(211, 216, 233, 180);
    margin: 5px 8px;
}}
QFrame#chatStage {{
    background: transparent;
    border: none;
}}
QScrollArea#chatScrollArea, QWidget#chatMessageContainer {{
    background: transparent;
    border: none;
}}
QWidget#chatEmptyState {{
    background: transparent;
}}
QLabel#chatEmptyTitle {{
    color: rgba(78, 91, 126, 185);
    font-size: 21px;
    font-weight: 620;
}}
QLabel#chatEmptySubtitle {{
    color: rgba(118, 129, 158, 175);
    font-size: 13px;
}}
QLabel#chatEmptySparkle {{
    color: rgba(130, 139, 205, 150);
    font-size: 18px;
}}
QFrame#chatMascotGlow {{
    background: qradialgradient(
        cx: 0.5, cy: 0.58, radius: 0.52,
        fx: 0.5, fy: 0.58,
        stop: 0 rgba(211, 205, 255, 118),
        stop: 0.55 rgba(221, 231, 255, 72),
        stop: 1 rgba(245, 243, 255, 0)
    );
    border: none;
}}
QFrame#userMessage {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 {THEME.user_bubble_start},
        stop: 1 {THEME.user_bubble_end}
    );
    border: 1px solid rgba(177, 203, 238, 190);
    border-radius: {THEME.bubble_radius}px;
}}
QFrame#assistantMessage {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 {THEME.pet_bubble_start},
        stop: 1 {THEME.pet_bubble_end}
    );
    border: 1px solid {THEME.border_lavender};
    border-radius: {THEME.bubble_radius}px;
}}
QLabel#messageAuthor {{
    color: #8992aa;
    font-size: 10px;
    font-weight: 600;
}}
QLabel#assistantMessageText {{
    color: #38415b;
    font-size: 14px;
    padding-bottom: 1px;
}}
QLabel#userMessageText {{
    color: #30415e;
    font-size: 14px;
    padding-bottom: 1px;
}}
QFrame#suggestedVocabularyCard {{
    background: rgba(251, 252, 255, 240);
    border: 1px solid rgba(181, 190, 229, 205);
    border-radius: 17px;
}}
QWidget#suggestedVocabularyItem {{
    background: rgba(242, 246, 255, 178);
    border: 1px solid rgba(215, 222, 242, 170);
    border-radius: 10px;
}}
QLabel#suggestedVocabularyHeading {{
    color: #5f68a0;
    font-size: 15px;
    font-weight: 650;
}}
QLabel#suggestedVocabularyHint,
QLabel#suggestedVocabularyMeaning,
QLabel#suggestedVocabularyContext {{
    color: #78839d;
    font-size: 11px;
}}
QLabel#suggestedVocabularyContext {{
    color: #59657f;
    background: rgba(255, 255, 255, 175);
    border-radius: 7px;
    padding: 5px 7px;
}}
QLabel#suggestedVocabularyState,
QLabel#suggestedVocabularyStatus {{
    color: {THEME.success};
    font-size: 11px;
}}
QCheckBox#suggestedVocabularyCheck {{
    color: #3d4865;
    font-size: 13px;
    font-weight: 620;
}}
QPushButton#suggestedVocabularyContextButton,
QPushButton#suggestedVocabularySecondaryButton {{
    min-height: 24px;
    padding: 1px 8px;
    border-radius: 8px;
    color: #727d99;
    background: rgba(246, 248, 253, 210);
    border: 1px solid #dce2ef;
    font-size: 10px;
}}
QPushButton#suggestedVocabularyAddButton,
QPushButton#suggestedVocabularyStudyButton {{
    min-height: 30px;
    border-radius: 10px;
    color: white;
    background: {THEME.primary};
    border: 1px solid #6f80cf;
    font-weight: 600;
}}
QPushButton#suggestedVocabularyAddButton:disabled {{
    color: #7e8a9e;
    background: #e5e9f1;
    border-color: #d8deea;
}}
QFrame#todoConfirmationCard {{
    background: {THEME.todo_surface};
    border: 1px solid rgba(188, 178, 231, 205);
    border-radius: 19px;
}}
QFrame#todoConfirmationCard[cardState="created"] {{
    background: rgba(247, 253, 250, 238);
    border-color: rgba(162, 205, 183, 205);
}}
QFrame#todoConfirmationCard[cardState="cancelled"] {{
    background: rgba(248, 249, 252, 230);
    border-color: rgba(204, 209, 221, 190);
}}
QFrame#todoConfirmationCard[cardState="failed"] {{
    background: rgba(255, 249, 250, 232);
    border-color: rgba(222, 188, 197, 195);
}}
QFrame#todoCardFields {{
    background: rgba(244, 244, 253, 150);
    border: 1px solid rgba(220, 216, 239, 145);
    border-radius: 12px;
}}
QLabel#todoCardHeading {{
    color: {THEME.accent};
    font-size: 14px;
    font-weight: 650;
}}
QLabel#todoCardHeading[stateRole="created"] {{ color: {THEME.success}; }}
QLabel#todoCardHeading[stateRole="cancelled"] {{ color: {THEME.cancelled}; }}
QLabel#todoCardHeading[stateRole="failed"] {{ color: {THEME.danger}; }}
QLabel#todoCardTitle {{
    color: {THEME.text_primary};
    font-size: 17px;
    font-weight: 650;
}}
QLabel#todoCardFieldName {{
    color: #9298ad;
    font-size: 11px;
}}
QLabel[fieldRole="value"] {{
    color: #505b76;
    font-size: 13px;
}}
QLabel#todoCardStatus {{
    color: #756ab0;
    font-size: 12px;
    font-weight: 600;
}}
QLabel#todoCardStatus[stateRole="created"] {{ color: {THEME.success}; }}
QLabel#todoCardStatus[stateRole="cancelled"] {{ color: {THEME.cancelled}; }}
QLabel#todoCardStatus[stateRole="failed"] {{ color: {THEME.danger}; }}
QFrame#chatInputPanel {{
    background: {THEME.input_surface};
    border: 1px solid {THEME.border};
    border-radius: {THEME.input_radius}px;
}}
QTextEdit#chatInput {{
    background: rgba(247, 249, 255, 205);
    border: 1px solid rgba(211, 220, 239, 185);
    border-radius: 14px;
    padding: 10px 12px;
    color: {THEME.text_primary};
    font-size: 14px;
    selection-background-color: #c4d5f5;
}}
QTextEdit#chatInput:focus {{
    background: rgba(252, 252, 255, 238);
    border: 1px solid rgba(139, 158, 220, 215);
}}
QPushButton {{
    min-height: 34px;
    border-radius: 12px;
    padding: 3px 14px;
    font-size: 13px;
}}
QPushButton#sendChatButton, QPushButton#confirmTodoButton {{
    color: white;
    background: {THEME.primary};
    border: 1px solid #6f80cf;
    font-weight: 600;
}}
QPushButton#sendChatButton:hover, QPushButton#confirmTodoButton:hover {{
    background: {THEME.primary_hover};
}}
QPushButton#sendChatButton:pressed, QPushButton#confirmTodoButton:pressed {{
    background: {THEME.primary_pressed};
}}
QPushButton#sendChatButton:disabled, QPushButton#confirmTodoButton:disabled {{
    background: #bbc3dd;
    border-color: #bbc3dd;
}}
QPushButton#stopChatButton {{
    color: #95556a;
    background: #fff4f6;
    border: 1px solid #e9cbd4;
    font-weight: 600;
}}
QPushButton#stopChatButton:hover {{ background: #fce9ee; }}
QPushButton#voiceToggleButton, QPushButton#voicePreviewButton, QPushButton#voiceStopButton {{
    min-height: 26px;
    padding: 2px 10px;
    font-size: 12px;
    color: #69738b;
    background: rgba(242, 244, 250, 210);
    border: 1px solid #dce1ed;
}}
QPushButton#voiceToggleButton:checked {{
    color: #5365a9;
    background: #e4ecff;
    border-color: #acbce8;
}}
QPushButton#voicePreviewButton:hover {{ background: #e9edf7; }}
QPushButton#voiceStopButton:enabled {{ color: #95556a; background: #fff4f6; }}
QPushButton#voiceStopButton:disabled {{ color: #abb0c1; }}
QLabel#voiceStatusLabel {{ color: #7e85a0; font-size: 11px; }}
QPushButton#cancelTodoButton {{
    color: #69738b;
    background: rgba(242, 244, 250, 210);
    border: 1px solid #dce1ed;
}}
QPushButton#cancelTodoButton:hover {{ background: #e9edf7; }}
QLabel#chatStatusLabel {{
    color: #71836e;
    font-size: 11px;
}}
QLabel#chatStatusLabel[error="true"] {{ color: {THEME.danger}; }}
QLabel#chatInputHint {{
    color: #9aa3b8;
    font-size: 10px;
}}
QToolTip {{
    color: #4e5870;
    background: #fbfbff;
    border: 1px solid #d8ddef;
    padding: 4px 7px;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 9px;
    margin: 3px 1px;
}}
QScrollBar::handle:vertical {{
    background: rgba(174, 184, 217, 160);
    border-radius: 4px;
    min-height: 34px;
}}
QScrollBar::handle:vertical:hover {{ background: rgba(151, 164, 204, 190); }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
"""
