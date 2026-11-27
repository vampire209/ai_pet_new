"""Blue-lavender settings window for the independent desktop-pet profile."""

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
    QVBoxLayout,
    QWidget,
)

from ai.ability_modes import ABILITY_MODE_LABELS
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
QFrame#profilePanel {
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
QPushButton#restoreProfileButton {
    color: #67728e;
    background: #edf0fa;
    border: 1px solid #d4daec;
}
QPushButton#saveProfileButton {
    color: white;
    background: #8594d7;
    border: 1px solid #7685cb;
}
QPushButton:hover {
    background: #9ba8df;
}
QLabel#profileSaveStatus[error="true"] {
    color: #c55d72;
}
QLabel#profileSaveStatus[error="false"] {
    color: #668a7b;
}
"""


class ProfileWindow(QWidget):
    """Edit a working copy and persist only when Save is explicitly used."""

    def __init__(
        self,
        profile_service: ProfileService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._profile_service = profile_service
        self._updating_controls = False
        self._last_preset_mode = RELATIONSHIP_NORMAL
        self.sliders: dict[str, QSlider] = {}
        self.value_labels: dict[str, QLabel] = {}
        self._setup_ui()
        self._load_profile(profile_service.profile)
        profile_service.profile_changed.connect(self._load_profile)

    def _setup_ui(self) -> None:
        self.setObjectName("profileWindow")
        self.setWindowTitle("桌宠设置")
        self.setWindowIcon(load_application_icon())
        self.setMinimumSize(500, 740)
        self.resize(540, 780)
        self.setStyleSheet(PROFILE_WINDOW_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 20, 22, 22)
        root.setSpacing(14)

        title = QLabel("桌宠设置")
        title.setObjectName("profileTitle")
        root.addWidget(title)
        subtitle = QLabel("名字、关系与能力设置会在保存后立即影响下一次聊天。")
        subtitle.setObjectName("profileSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

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

        panel_layout.addWidget(self._field_label("能力模式"))
        self.ability_combo = QComboBox()
        self.ability_combo.setObjectName("profileAbilityCombo")
        for ability_mode, label in ABILITY_MODE_LABELS.items():
            self.ability_combo.addItem(label, ability_mode)
        panel_layout.addWidget(self.ability_combo)

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

        root.addWidget(panel, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("profileSaveStatus")
        self.status_label.setMinimumHeight(20)
        root.addWidget(self.status_label)

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
        root.addLayout(buttons)

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
            ability_index = self.ability_combo.findData(profile.ability_mode)
            self.ability_combo.setCurrentIndex(max(0, ability_index))
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
            ability_mode=self.ability_combo.currentData(),
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

    @property
    def profile_service(self) -> ProfileService:
        return self._profile_service

    def showEvent(self, event: QShowEvent) -> None:
        self._load_profile(self._profile_service.profile)
        super().showEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.hide()
        event.ignore()
