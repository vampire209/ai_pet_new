"""Blue-lavender settings window for desktop-pet profile and AI connection."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ai.ai_config import DEFAULT_BASE_URL, DEFAULT_MODEL
from ai.ai_settings_service import (
    AiConnectionSettings,
    AiSettingsService,
    AiSettingsStorageError,
    AiSettingsValidationError,
    PRESET_MODELS,
)
from ai.profile import (
    DEFAULT_PET_NAME,
    PET_NAME_MAX_LENGTH,
    RELATIONSHIP_CUSTOM,
    RELATIONSHIP_MODES,
    RELATIONSHIP_NORMAL,
    TRAIT_FIELDS,
    TRAIT_LABELS,
    PetProfile,
    ProfileValidationError,
    preset_values,
)
from ai.profile_service import ProfileService, ProfileStorageError
from ui_resources import load_application_icon


PROFILE_WINDOW_STYLE = """
QWidget#profileWindow {
    background: #f3f6ff;
    color: #48536f;
    font-family: "Microsoft YaHei UI", "Microsoft YaHei";
    font-size: 13px;
}
QTabWidget#settingsTabs::pane {
    border: none;
    background: transparent;
}
QTabWidget#settingsTabs QTabBar::tab {
    background: #e7ecfa;
    color: #5b6784;
    border-radius: 10px;
    padding: 8px 18px;
    margin-right: 8px;
    font-weight: 600;
}
QTabWidget#settingsTabs QTabBar::tab:selected {
    background: #8594d7;
    color: white;
}
QFrame#profilePanel, QFrame#aiSettingsPanel {
    background: rgba(255, 255, 255, 238);
    border: 1px solid #d9dff1;
    border-radius: 18px;
}
QLabel#profileTitle {
    color: #3d4968;
    font-size: 21px;
    font-weight: 700;
}
QLabel#profileSubtitle, QLabel#profileHint {
    color: #7c87a5;
}
QLabel#profileFieldLabel {
    color: #596580;
    font-weight: 600;
}
QLabel#profileValue {
    color: #6e75b8;
    font-weight: 700;
    min-width: 32px;
}
QLineEdit, QComboBox {
    min-height: 38px;
    color: #46516e;
    background: #f9faff;
    border: 1px solid #cfd7ed;
    border-radius: 10px;
    padding: 0 11px;
    selection-background-color: #aebcf0;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #919fdb;
}
QSlider::groove:horizontal {
    height: 6px;
    background: #dfe5f5;
    border-radius: 3px;
}
QSlider::sub-page:horizontal {
    background: #9da9e3;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 18px;
    margin: -6px 0;
    background: #7f8fd2;
    border: 2px solid #f7f8ff;
    border-radius: 9px;
}
QPushButton {
    min-height: 38px;
    border-radius: 11px;
    padding: 0 18px;
    font-weight: 600;
}
QPushButton#restoreProfileButton, QPushButton#resetAiButton, QPushButton#toggleApiKeyButton {
    color: #67728e;
    background: #edf0fa;
    border: 1px solid #d4daec;
}
QPushButton#saveProfileButton, QPushButton#saveAiButton {
    color: white;
    background: #8594d7;
    border: 1px solid #7685cb;
}
QPushButton:hover {
    background: #9ba8df;
}
QLabel#profileSaveStatus[error="true"], QLabel#aiSaveStatus[error="true"] {
    color: #c55d72;
}
QLabel#profileSaveStatus[error="false"], QLabel#aiSaveStatus[error="false"] {
    color: #668a7b;
}
"""


class ProfileWindow(QWidget):
    """Edit pet profile and AI connection settings, saving only on explicit Save."""

    def __init__(
        self,
        profile_service: ProfileService,
        parent: QWidget | None = None,
        *,
        ai_settings_service: AiSettingsService | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile_service = profile_service
        self._ai_settings_service = ai_settings_service
        self._updating_controls = False
        self._last_preset_mode = RELATIONSHIP_NORMAL
        self.sliders: dict[str, QSlider] = {}
        self.value_labels: dict[str, QLabel] = {}
        self._setup_ui()
        self._load_profile(profile_service.profile)
        profile_service.profile_changed.connect(self._load_profile)
        if ai_settings_service is not None:
            self._load_ai_settings(ai_settings_service.settings)
            ai_settings_service.settings_changed.connect(self._load_ai_settings)
        else:
            self._load_ai_settings(AiConnectionSettings())
            self.save_ai_button.setEnabled(False)
            self._show_ai_status("当前窗口未连接 AI 设置服务。", error=False)

    def _setup_ui(self) -> None:
        self.setObjectName("profileWindow")
        self.setWindowTitle("桌宠设置")
        self.setWindowIcon(load_application_icon())
        self.setMinimumSize(500, 700)
        self.resize(540, 740)
        self.setStyleSheet(PROFILE_WINDOW_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 22)
        root.setSpacing(14)

        title = QLabel("桌宠设置")
        title.setObjectName("profileTitle")
        root.addWidget(title)
        subtitle = QLabel("名字、性格与 AI 接口设置会在保存后立即生效。")
        subtitle.setObjectName("profileSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("settingsTabs")
        self.tabs.addTab(self._build_profile_tab(), "角色与人格")
        self.tabs.addTab(self._build_ai_tab(), "AI 接口设置")
        root.addWidget(self.tabs, 1)

    def _build_profile_tab(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 10, 0, 0)
        page_layout.setSpacing(12)

        panel = QFrame()
        panel.setObjectName("profilePanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(20, 18, 20, 20)
        panel_layout.setSpacing(14)

        panel_layout.addWidget(self._field_label("桌宠名字"))
        self.name_edit = QLineEdit()
        self.name_edit.setObjectName("profileNameEdit")
        self.name_edit.setMaxLength(PET_NAME_MAX_LENGTH)
        self.name_edit.setPlaceholderText(DEFAULT_PET_NAME)
        panel_layout.addWidget(self.name_edit)

        panel_layout.addWidget(self._field_label("关系模式"))
        self.relationship_combo = QComboBox()
        self.relationship_combo.setObjectName("profileRelationshipCombo")
        for mode in RELATIONSHIP_MODES:
            self.relationship_combo.addItem(mode, mode)
        self.relationship_combo.currentIndexChanged.connect(
            self._on_relationship_changed
        )
        panel_layout.addWidget(self.relationship_combo)

        hint = QLabel("手动调整任意滑杆后，关系模式会切换为“自定义”。")
        hint.setObjectName("profileHint")
        hint.setWordWrap(True)
        panel_layout.addWidget(hint)

        for field in TRAIT_FIELDS:
            row = QHBoxLayout()
            row.setSpacing(10)
            label = self._field_label(TRAIT_LABELS[field])
            label.setMinimumWidth(62)
            row.addWidget(label)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setObjectName(f"profileSlider_{field}")
            slider.setRange(0, 100)
            slider.setSingleStep(1)
            slider.valueChanged.connect(
                lambda value, trait=field: self._on_slider_changed(trait, value)
            )
            row.addWidget(slider, 1)
            value_label = QLabel("0")
            value_label.setObjectName("profileValue")
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            row.addWidget(value_label)
            panel_layout.addLayout(row)
            self.sliders[field] = slider
            self.value_labels[field] = value_label

        page_layout.addWidget(panel, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("profileSaveStatus")
        self.status_label.setMinimumHeight(20)
        page_layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.restore_button = QPushButton("恢复该模式默认值")
        self.restore_button.setObjectName("restoreProfileButton")
        self.restore_button.clicked.connect(self.restore_selected_preset)
        buttons.addWidget(self.restore_button)
        buttons.addStretch(1)
        self.save_button = QPushButton("保存设置")
        self.save_button.setObjectName("saveProfileButton")
        self.save_button.clicked.connect(self.save_settings)
        buttons.addWidget(self.save_button)
        page_layout.addLayout(buttons)
        return page

    def _build_ai_tab(self) -> QWidget:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 10, 0, 0)
        page_layout.setSpacing(12)

        panel = QFrame()
        panel.setObjectName("aiSettingsPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(20, 18, 20, 20)
        panel_layout.setSpacing(14)

        panel_layout.addWidget(self._field_label("DeepSeek API Key"))
        key_row = QHBoxLayout()
        key_row.setSpacing(8)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setObjectName("aiApiKeyEdit")
        self.api_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_edit.setPlaceholderText("留空则自动读取环境变量 DEEPSEEK_API_KEY")
        key_row.addWidget(self.api_key_edit, 1)

        self.toggle_api_key_button = QPushButton("显示")
        self.toggle_api_key_button.setObjectName("toggleApiKeyButton")
        self.toggle_api_key_button.setCheckable(True)
        self.toggle_api_key_button.toggled.connect(self._toggle_api_key_visibility)
        key_row.addWidget(self.toggle_api_key_button)
        panel_layout.addLayout(key_row)

        panel_layout.addWidget(self._field_label("API Base URL"))
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setObjectName("aiBaseUrlEdit")
        self.base_url_edit.setPlaceholderText(DEFAULT_BASE_URL)
        panel_layout.addWidget(self.base_url_edit)

        panel_layout.addWidget(self._field_label("模型名称"))
        self.model_combo = QComboBox()
        self.model_combo.setObjectName("aiModelCombo")
        self.model_combo.setEditable(True)
        for model_name in PRESET_MODELS:
            self.model_combo.addItem(model_name)
        panel_layout.addWidget(self.model_combo)

        ai_hint = QLabel(
            "API Key 仅保存在本机用户配置目录（不会写入项目仓库或日志）。\n"
            "保存后无需重启桌宠，下一次聊天立即生效。"
        )
        ai_hint.setObjectName("profileHint")
        ai_hint.setWordWrap(True)
        panel_layout.addWidget(ai_hint)
        panel_layout.addStretch(1)

        page_layout.addWidget(panel, 1)

        self.ai_status_label = QLabel("")
        self.ai_status_label.setObjectName("aiSaveStatus")
        self.ai_status_label.setMinimumHeight(20)
        page_layout.addWidget(self.ai_status_label)

        buttons = QHBoxLayout()
        self.reset_ai_button = QPushButton("恢复默认接口")
        self.reset_ai_button.setObjectName("resetAiButton")
        self.reset_ai_button.clicked.connect(self.reset_ai_defaults)
        buttons.addWidget(self.reset_ai_button)
        buttons.addStretch(1)
        self.save_ai_button = QPushButton("保存 AI 设置")
        self.save_ai_button.setObjectName("saveAiButton")
        self.save_ai_button.clicked.connect(self.save_ai_settings)
        buttons.addWidget(self.save_ai_button)
        page_layout.addLayout(buttons)
        return page

    def open_tab(self, tab: str = "profile") -> None:
        self.tabs.setCurrentIndex(1 if tab == "ai" else 0)

    def _toggle_api_key_visibility(self, visible: bool) -> None:
        self.api_key_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        self.toggle_api_key_button.setText("隐藏" if visible else "显示")

    def _load_ai_settings(self, settings: AiConnectionSettings) -> None:
        self.api_key_edit.setText(settings.api_key)
        self.base_url_edit.setText(settings.base_url)
        self.model_combo.setCurrentText(settings.model)

    def reset_ai_defaults(self) -> None:
        self.base_url_edit.setText(DEFAULT_BASE_URL)
        self.model_combo.setCurrentText(DEFAULT_MODEL)
        self._show_ai_status("已填入默认地址和模型，点击保存后生效。", error=False)

    def save_ai_settings(self) -> bool:
        if self._ai_settings_service is None:
            self._show_ai_status("AI 设置服务不可用。", error=True)
            return False
        try:
            settings = AiConnectionSettings(
                api_key=self.api_key_edit.text(),
                base_url=self.base_url_edit.text(),
                model=self.model_combo.currentText(),
            )
            self._ai_settings_service.save(settings)
        except (AiSettingsValidationError, AiSettingsStorageError, OSError) as exc:
            self._show_ai_status(str(exc), error=True)
            return False
        self._show_ai_status("AI 接口设置已保存，下一次聊天立即生效。", error=False)
        return True

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("profileFieldLabel")
        return label

    def _load_profile(self, profile: PetProfile) -> None:
        self._updating_controls = True
        try:
            self.name_edit.setText(profile.pet_name)
            index = self.relationship_combo.findData(profile.relationship_mode)
            self.relationship_combo.setCurrentIndex(max(0, index))
            if profile.relationship_mode != RELATIONSHIP_CUSTOM:
                self._last_preset_mode = profile.relationship_mode
            for field in TRAIT_FIELDS:
                value = getattr(profile, field)
                self.sliders[field].setValue(value)
                self.value_labels[field].setText(str(value))
        finally:
            self._updating_controls = False

    def _on_relationship_changed(self, _index: int) -> None:
        if self._updating_controls:
            return
        mode = self.relationship_combo.currentData()
        if mode == RELATIONSHIP_CUSTOM:
            return
        self._last_preset_mode = mode
        self._set_trait_values(preset_values(mode))

    def _on_slider_changed(self, field: str, value: int) -> None:
        self.value_labels[field].setText(str(value))
        if self._updating_controls:
            return
        mode = self.relationship_combo.currentData()
        if mode != RELATIONSHIP_CUSTOM:
            self._last_preset_mode = mode
            custom_index = self.relationship_combo.findData(RELATIONSHIP_CUSTOM)
            with QSignalBlocker(self.relationship_combo):
                self.relationship_combo.setCurrentIndex(custom_index)

    def _set_trait_values(self, values: dict[str, int]) -> None:
        self._updating_controls = True
        try:
            for field, value in values.items():
                self.sliders[field].setValue(value)
                self.value_labels[field].setText(str(value))
        finally:
            self._updating_controls = False

    def restore_selected_preset(self) -> None:
        mode = self.relationship_combo.currentData()
        if mode == RELATIONSHIP_CUSTOM:
            mode = self._last_preset_mode
        if mode == RELATIONSHIP_CUSTOM:
            mode = RELATIONSHIP_NORMAL
        self._last_preset_mode = mode
        with QSignalBlocker(self.relationship_combo):
            self.relationship_combo.setCurrentIndex(
                self.relationship_combo.findData(mode)
            )
        self._set_trait_values(preset_values(mode))
        self._show_status(f"已恢复“{mode}”默认参数，保存后生效。", error=False)

    def edited_profile(self) -> PetProfile:
        return PetProfile(
            pet_name=self.name_edit.text(),
            relationship_mode=self.relationship_combo.currentData(),
            **{field: slider.value() for field, slider in self.sliders.items()},
        )

    def save_settings(self) -> bool:
        try:
            profile = self.edited_profile()
            self._profile_service.save(profile)
        except (ProfileValidationError, ProfileStorageError, OSError) as exc:
            self._show_status(str(exc), error=True)
            return False
        self._show_status("设置已保存，并会用于下一次聊天。", error=False)
        return True

    def _show_status(self, message: str, *, error: bool) -> None:
        self.status_label.setProperty("error", error)
        style = self.status_label.style()
        style.unpolish(self.status_label)
        style.polish(self.status_label)
        self.status_label.setText(message)

    def _show_ai_status(self, message: str, *, error: bool) -> None:
        self.ai_status_label.setProperty("error", error)
        style = self.ai_status_label.style()
        style.unpolish(self.ai_status_label)
        style.polish(self.ai_status_label)
        self.ai_status_label.setText(message)

    @property
    def profile_service(self) -> ProfileService:
        return self._profile_service

    @property
    def ai_settings_service(self) -> AiSettingsService | None:
        return self._ai_settings_service

    def showEvent(self, event: QShowEvent) -> None:
        self._load_profile(self._profile_service.profile)
        if self._ai_settings_service is not None:
            self._load_ai_settings(self._ai_settings_service.settings)
        super().showEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.hide()
        event.ignore()
