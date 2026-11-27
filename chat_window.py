"""Desktop-pet styled streamed chat UI with confirmed local Todo drafts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import logging
import re

from PySide6.QtCore import (
    QEvent,
    QPointF,
    QRectF,
    QSignalBlocker,
    Qt,
    QThreadPool,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QFileDialog,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ai.ai_settings_service import AiConnectionSettings, AiSettingsService
from ai.chat_action_router import (
    ChatActionKind,
    ChatActionRouter,
    ChatActionResult,
    TaskDraft,
)
from ai.chat_service import (
    ChatBusyError,
    ChatService,
    EmptyChatMessageError,
)
from ai.chat_theme import CHAT_WINDOW_STYLE
from ai.chat_ui_config import ChatUiSettings, ChatUiSettingsError
from ai.chat_worker import ChatStreamWorker
from ai.deepseek_client import DeepSeekClientError
from ai.persona_builder import PersonaBuilder
from ai.profile import DEFAULT_PET_NAME, PetProfile
from ai.profile_service import ProfileService
from models.task import TaskDataError
from memory.memory_service import MemoryService
from todo_manager import TodoManager, TodoStorageError
from ui_resources import (
    UI_ASSETS,
    load_application_icon,
    load_avatar_source,
    load_character_source,
)
from voice.settings_store import VoiceSettingsStore
from voice.voice_service import VoiceService
from voice.voice_models import VoiceState
from voice.runtime import create_desktop_voice_service


_MARKDOWN_BOLD = re.compile(r"(?<!\*)\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)")
_MARKDOWN_CODE = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
_MARKDOWN_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+")
_MARKDOWN_RULE = re.compile(r"(?m)^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$")
_MARKDOWN_BULLET = re.compile(r"(?m)^(\s*)[-*]\s+(?=\S)")
LOGGER = logging.getLogger(__name__)


def clean_chat_markdown(text: str) -> str:
    """Remove only recognizable lightweight Markdown from plain-text labels."""

    cleaned = _MARKDOWN_BOLD.sub(r"\1", text)
    cleaned = _MARKDOWN_CODE.sub(r"\1", cleaned)
    cleaned = _MARKDOWN_HEADING.sub("", cleaned)
    cleaned = _MARKDOWN_RULE.sub("", cleaned)
    cleaned = _MARKDOWN_BULLET.sub(r"\1• ", cleaned)
    return cleaned


class ChatInput(QTextEdit):
    """Multiline editor where Enter sends and Shift+Enter inserts a newline."""

    send_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}
            and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.send_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ChatBackdrop(QFrame):
    """Paint a restrained blue-lavender atmosphere behind chat content."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("chatStage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._background_path: Path | None = None
        self._background_pixmap = QPixmap()

    def set_background_image(self, path: Path) -> bool:
        source = Path(path)
        pixmap = QPixmap(str(source))
        if pixmap.isNull():
            return False
        self._background_path = source
        self._background_pixmap = pixmap
        self.update()
        return True

    def restore_default_background(self) -> None:
        self._background_path = None
        self._background_pixmap = QPixmap()
        self.update()

    @property
    def background_path(self) -> Path | None:
        return self._background_path

    @property
    def has_custom_background(self) -> bool:
        return not self._background_pixmap.isNull()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bounds = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        clip = QPainterPath()
        clip.addRoundedRect(bounds, 23, 23)
        painter.setClipPath(clip)

        gradient = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
        gradient.setColorAt(0.0, QColor(244, 251, 255, 238))
        gradient.setColorAt(0.52, QColor(248, 249, 255, 232))
        gradient.setColorAt(1.0, QColor(240, 236, 255, 236))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(bounds, 23, 23)

        if not self._background_pixmap.isNull():
            self._draw_cover_pixmap(painter, bounds)
            veil = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
            veil.setColorAt(0.0, QColor(244, 250, 255, 112))
            veil.setColorAt(0.55, QColor(248, 248, 255, 100))
            veil.setColorAt(1.0, QColor(237, 232, 255, 108))
            painter.fillRect(bounds, veil)

        painter.setPen(Qt.PenStyle.NoPen)
        glow_specs = (
            (0.12, 0.23, 92, QColor(170, 218, 249, 32)),
            (0.47, 0.12, 62, QColor(202, 191, 248, 24)),
            (0.84, 0.73, 132, QColor(192, 177, 247, 32)),
        )
        for x_ratio, y_ratio, radius, color in glow_specs:
            painter.setBrush(color)
            center = QPointF(self.width() * x_ratio, self.height() * y_ratio)
            painter.drawEllipse(center, radius, radius)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(142, 166, 216, 38), 1.1))
        painter.drawEllipse(
            QPointF(self.width() * 0.18, self.height() * 0.72),
            34,
            34,
        )
        painter.drawEllipse(
            QPointF(self.width() * 0.73, self.height() * 0.18),
            19,
            19,
        )

        painter.setPen(QPen(QColor(124, 144, 205, 70), 1.2))
        for x_ratio, y_ratio, size in (
            (0.09, 0.45, 4.5),
            (0.36, 0.18, 3.5),
            (0.62, 0.34, 3.0),
            (0.88, 0.31, 4.0),
        ):
            center = QPointF(self.width() * x_ratio, self.height() * y_ratio)
            painter.drawLine(
                QPointF(center.x() - size, center.y()),
                QPointF(center.x() + size, center.y()),
            )
            painter.drawLine(
                QPointF(center.x(), center.y() - size),
                QPointF(center.x(), center.y() + size),
            )

        painter.setClipping(False)
        painter.setPen(QPen(QColor(183, 201, 232, 150), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(bounds, 23, 23)
        painter.end()

    def _draw_cover_pixmap(self, painter: QPainter, target: QRectF) -> None:
        source_width = float(self._background_pixmap.width())
        source_height = float(self._background_pixmap.height())
        if source_width <= 0 or source_height <= 0:
            return

        target_ratio = target.width() / max(1.0, target.height())
        source_ratio = source_width / source_height
        if source_ratio > target_ratio:
            crop_width = source_height * target_ratio
            source = QRectF(
                (source_width - crop_width) / 2.0,
                0.0,
                crop_width,
                source_height,
            )
        else:
            crop_height = source_width / max(target_ratio, 0.001)
            source = QRectF(
                0.0,
                (source_height - crop_height) / 2.0,
                source_width,
                crop_height,
            )
        painter.drawPixmap(target, self._background_pixmap, source)


class OriginalPixmapLabel(QLabel):
    """Scale only from an original source and build a DPR-aware display pixmap."""

    def __init__(
        self,
        source_pixmap: QPixmap,
        box_size: int,
        *,
        target_size: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._source_pixmap = source_pixmap
        self._target_size = target_size or box_size
        self._last_dpr = 0.0
        self.setFixedSize(box_size, box_size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._refresh_from_original()

    @property
    def source_pixmap(self) -> QPixmap:
        return self._source_pixmap

    @property
    def source_pixel_size(self) -> tuple[int, int]:
        return self._source_pixmap.width(), self._source_pixmap.height()

    def _refresh_from_original(self) -> None:
        if self._source_pixmap.isNull():
            self.clear()
            return
        dpr = max(1.0, float(self.devicePixelRatioF()))
        physical_size = max(1, round(self._target_size * dpr))
        display = self._source_pixmap.scaled(
            physical_size,
            physical_size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        display.setDevicePixelRatio(dpr)
        self._last_dpr = dpr
        super().setPixmap(display)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._refresh_from_original()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.DevicePixelRatioChange:
            self._refresh_from_original()


class PetAvatarWidget(QFrame):
    """Frame a head-focused crop of the existing transparent pet artwork."""

    def __init__(
        self,
        pixmap: QPixmap,
        size: int,
        *,
        object_name: str,
        image_object_name: str,
        crop_fallback_character: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        layout = QVBoxLayout(self)
        inset = 3 if size >= 48 else 2
        layout.setContentsMargins(inset, inset, inset, inset)
        source = pixmap
        if crop_fallback_character and not pixmap.isNull():
            crop_side = max(1, int(min(pixmap.width(), pixmap.height()) * 0.58))
            crop_x = max(0, (pixmap.width() - crop_side) // 2)
            source = pixmap.copy(crop_x, 0, crop_side, crop_side)

        target = max(1, size - inset * 2)
        self.image_label = OriginalPixmapLabel(source, target)
        self.image_label.setObjectName(image_object_name)
        self.image_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )
        layout.addWidget(self.image_label)


def _apply_soft_shadow(widget: QWidget, *, blur: float, y_offset: float) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y_offset)
    effect.setColor(QColor(72, 87, 145, 34))
    widget.setGraphicsEffect(effect)


class TodoConfirmationCard(QFrame):
    """Inline preview that cannot mutate Todo until its confirm signal is used."""

    confirm_requested = Signal(object)
    cancel_requested = Signal(object)

    def __init__(self, draft: TaskDraft, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.draft = draft
        self.state = "pending"
        self.created_task_id: str | None = None
        self.setObjectName("todoConfirmationCard")
        self.setProperty("cardState", self.state)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(9)

        self.heading_label = QLabel("⏰ 待办确认")
        self.heading_label.setObjectName("todoCardHeading")
        layout.addWidget(self.heading_label)

        self.title_label = QLabel(draft.title)
        self.title_label.setObjectName("todoCardTitle")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        fields_panel = QFrame()
        fields_panel.setObjectName("todoCardFields")
        fields = QGridLayout(fields_panel)
        fields.setContentsMargins(12, 10, 12, 10)
        fields.setHorizontalSpacing(12)
        fields.setVerticalSpacing(8)
        self._add_field(fields, 0, "时间", draft.time_text, "todoCardTime")
        self._add_field(fields, 1, "重复", draft.repeat_text, "todoCardRepeat")
        self._add_field(
            fields,
            2,
            "提前提醒",
            draft.advance_text,
            "todoCardAdvance",
        )
        layout.addWidget(fields_panel)

        self.status_label = QLabel("确认后才会写入我的待办")
        self.status_label.setObjectName("todoCardStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setObjectName("cancelTodoButton")
        self.cancel_button.clicked.connect(lambda: self.cancel_requested.emit(self))
        actions.addWidget(self.cancel_button)
        self.confirm_button = QPushButton("确认创建")
        self.confirm_button.setObjectName("confirmTodoButton")
        self.confirm_button.clicked.connect(lambda: self.confirm_requested.emit(self))
        actions.addWidget(self.confirm_button)
        layout.addLayout(actions)

    @staticmethod
    def _add_field(
        layout: QGridLayout,
        row: int,
        name: str,
        value: str,
        value_object_name: str,
    ) -> None:
        name_label = QLabel(name)
        name_label.setObjectName("todoCardFieldName")
        value_label = QLabel(value)
        value_label.setObjectName(value_object_name)
        value_label.setProperty("fieldRole", "value")
        value_label.setWordWrap(True)
        layout.addWidget(name_label, row, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(value_label, row, 1)
        layout.setColumnStretch(1, 1)

    def mark_created(self, task_id: str) -> None:
        if self.state != "pending":
            return
        self.state = "created"
        self.created_task_id = task_id
        self.heading_label.setText("✓ 已创建")
        self.status_label.setText("已保存到我的待办")
        self._apply_visual_state()
        self._set_read_only()

    def mark_cancelled(self, text: str = "已取消，不会创建待办") -> None:
        if self.state != "pending":
            return
        self.state = "cancelled"
        self.heading_label.setText("已取消")
        self.status_label.setText(text)
        self._apply_visual_state()
        self._set_read_only()

    def mark_failed(self, text: str) -> None:
        if self.state != "pending":
            return
        self.state = "failed"
        self.heading_label.setText("创建失败")
        self.status_label.setText(text)
        self._apply_visual_state()
        self._set_read_only()

    def invalidate(self) -> None:
        self.mark_cancelled("聊天已清空，这张草案不会创建")

    def _set_read_only(self) -> None:
        self.confirm_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.confirm_button.hide()
        self.cancel_button.hide()

    def _apply_visual_state(self) -> None:
        self.setProperty("cardState", self.state)
        self.heading_label.setProperty("stateRole", self.state)
        self.status_label.setProperty("stateRole", self.state)
        for widget in (self, self.heading_label, self.status_label):
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()


class ChatWindow(QWidget):
    """Render normal AI streams and confirmed local Todo actions safely."""

    ai_settings_requested = Signal()

    def __init__(
        self,
        service: ChatService | None = None,
        parent: QWidget | None = None,
        thread_pool: QThreadPool | None = None,
        *,
        todo_manager: TodoManager | None = None,
        action_router: ChatActionRouter | None = None,
        ui_settings: ChatUiSettings | None = None,
        profile_service: ProfileService | None = None,
        persona_builder: PersonaBuilder | None = None,
        memory_service: MemoryService | None = None,
        voice_service: VoiceService | None = None,
        ai_settings_service: AiSettingsService | None = None,
        voice_settings_store: VoiceSettingsStore | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = service or ChatService(memory_service=memory_service)
        if service is not None and memory_service is not None:
            self._service.set_memory_service(memory_service)
        self._todo_manager = todo_manager
        self._action_router = action_router or ChatActionRouter()
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._ui_settings = ui_settings or ChatUiSettings()
        self._profile_service = profile_service
        self._memory_service = memory_service
        self._ai_settings_service = ai_settings_service
        self._voice_settings_store = voice_settings_store
        self._voice_service = voice_service or create_desktop_voice_service(parent=self)
        if self._voice_settings_store is not None:
            restored_voice = self._voice_settings_store.apply_to(
                self._voice_service.settings
            )
            self._voice_service.configure(restored_voice)
        self._accept_voice_reply = False
        self._voice_streaming_turn = False
        self._persona_builder = persona_builder or PersonaBuilder()
        self._pet_name = DEFAULT_PET_NAME
        if profile_service is not None:
            self._pet_name = profile_service.profile.pet_name
            self._service.set_system_prompt_provider(
                lambda service=profile_service, builder=self._persona_builder: (
                    builder.build(service.profile)
                )
            )
        self._worker: ChatStreamWorker | None = None
        self._assistant_label: QLabel | None = None
        self._assistant_text = ""
        self._accept_chunks = False
        self._outcome_received = False
        self._message_records: list[tuple[str, QLabel]] = []
        self._message_bubbles: list[QFrame] = []
        self._message_rows: list[QWidget] = []
        self._todo_cards: list[TodoConfirmationCard] = []
        self._last_row_role: str | None = None
        avatar_source = load_avatar_source()
        character_source = load_character_source()
        self._avatar_source_path = avatar_source.path
        self._character_source_path = character_source.path
        self._avatar_pixmap = avatar_source.pixmap
        self._character_pixmap = character_source.pixmap
        self._avatar_uses_idle_fallback = (
            avatar_source.path == UI_ASSETS.idle_fallback
        )
        self._setup_ui()
        self._restore_history_messages()
        self._voice_service.text_recognized.connect(self.submit_recognized_text)
        self._voice_service.notice.connect(self._on_voice_notice)
        self._voice_service.error.connect(self._on_voice_error)
        self._voice_service.state_changed.connect(self._on_voice_state)
        self._voice_service.activity_changed.connect(self._sync_voice_controls)
        self._sync_voice_controls()
        if profile_service is not None:
            profile_service.profile_changed.connect(self.apply_profile)
        if ai_settings_service is not None:
            ai_settings_service.settings_changed.connect(self.apply_ai_settings)
        if not self._service.is_configured:
            self._show_status(
                "未配置 DeepSeek API Key（可在“桌宠设置 → AI 接口设置”中填写）；本地待办创建仍可使用。",
                error=True,
            )

    def _setup_ui(self) -> None:
        self.setObjectName("chatWindow")
        self.setWindowTitle(f"AI 桌宠聊天 - {self._pet_name}")
        self.setWindowIcon(load_application_icon())
        self.setMinimumSize(600, 650)
        self.resize(800, 780)
        self.setStyleSheet(CHAT_WINDOW_STYLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(12)

        header = QFrame()
        header.setObjectName("chatHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 11, 13, 11)
        header_layout.setSpacing(12)
        self.header_avatar = PetAvatarWidget(
            self._avatar_pixmap,
            54,
            object_name="headerPetAvatar",
            image_object_name="chatAvatar",
            crop_fallback_character=self._avatar_uses_idle_fallback,
        )
        header_layout.addWidget(self.header_avatar)
        title_layout = QVBoxLayout()
        title_layout.setContentsMargins(0, 1, 0, 1)
        title_layout.setSpacing(3)
        self.header_name_label = QLabel(self._pet_name)
        self.header_name_label.setObjectName("chatPetName")
        title_layout.addWidget(self.header_name_label)
        self.header_status_label = QLabel("陪你聊聊天")
        self.header_status_label.setObjectName("chatPetStatus")
        title_layout.addWidget(self.header_status_label)
        header_layout.addLayout(title_layout)
        header_layout.addStretch(1)
        short_model = self._service.config.model.removeprefix("deepseek-")
        self.model_label = QLabel(f"DeepSeek · {short_model}")
        self.model_label.setObjectName("chatModelLabel")
        self.model_label.setToolTip(self._service.config.model)
        header_layout.addWidget(self.model_label)
        self.clear_button = QPushButton("⋯")
        self.clear_button.setObjectName("headerClearButton")
        self.clear_button.setToolTip("聊天菜单")
        self.header_menu = QMenu(self)
        self.header_menu.setObjectName("chatHeaderMenu")
        self.ai_settings_action = self.header_menu.addAction("AI 接口设置")
        self.ai_settings_action.triggered.connect(self.ai_settings_requested.emit)
        self.header_menu.addSeparator()
        self.clear_chat_action = self.header_menu.addAction("清空聊天")
        self.clear_chat_action.triggered.connect(self._confirm_clear)
        self.header_menu.addSeparator()
        self.change_background_action = self.header_menu.addAction("更换聊天背景")
        self.change_background_action.triggered.connect(
            self._choose_chat_background
        )
        self.restore_background_action = self.header_menu.addAction(
            "恢复默认背景"
        )
        self.restore_background_action.triggered.connect(
            self._restore_default_background
        )
        self.clear_button.setMenu(self.header_menu)
        header_layout.addWidget(self.clear_button)
        _apply_soft_shadow(header, blur=22, y_offset=3)
        root.addWidget(header)

        self.chat_stage = ChatBackdrop()
        configured_background = self._ui_settings.load_background_path()
        if configured_background is not None:
            self.chat_stage.set_background_image(configured_background)
        stage_layout = QGridLayout(self.chat_stage)
        stage_layout.setContentsMargins(1, 1, 1, 1)
        stage_layout.setSpacing(0)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("chatScrollArea")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.scroll.viewport().setAutoFillBackground(False)
        self.message_container = QWidget()
        self.message_container.setObjectName("chatMessageContainer")
        self.message_container.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground
        )
        self.message_layout = QVBoxLayout(self.message_container)
        self.message_layout.setContentsMargins(18, 18, 18, 182)
        self.message_layout.setSpacing(0)
        self.message_layout.setSizeConstraint(
            QLayout.SizeConstraint.SetMinimumSize
        )
        self.empty_state = self._build_empty_state()
        self.message_layout.addWidget(self.empty_state, 1)
        self.message_layout.addStretch(0)
        self.scroll.setWidget(self.message_container)
        stage_layout.addWidget(self.scroll, 0, 0)
        self.corner_pet_layer = self._build_corner_pet_layer()
        stage_layout.addWidget(self.corner_pet_layer, 0, 0)
        self.corner_pet_layer.raise_()
        self._sync_corner_pet_visibility()
        root.addWidget(self.chat_stage, 1)

        input_panel = QFrame()
        input_panel.setObjectName("chatInputPanel")
        input_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        input_layout = QVBoxLayout(input_panel)
        input_layout.setContentsMargins(11, 9, 11, 10)
        input_layout.setSpacing(7)

        self.status_label = QLabel("")
        self.status_label.setObjectName("chatStatusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumHeight(16)
        self.status_label.hide()
        input_layout.addWidget(self.status_label)

        self.input = ChatInput()
        self.input.setObjectName("chatInput")
        self.input.setPlaceholderText("和桌宠说点什么吧……")
        self.input.setMinimumHeight(74)
        self.input.setMaximumHeight(102)
        self.input.send_requested.connect(self.send_message)
        input_layout.addWidget(self.input)

        voice_controls = QHBoxLayout()
        voice_controls.setSpacing(7)
        self.voice_toggle_button = QPushButton("语音回复：关")
        self.voice_toggle_button.setObjectName("voiceToggleButton")
        self.voice_toggle_button.setCheckable(True)
        self.voice_toggle_button.setToolTip("使用本地声音按句朗读；生成中开启将在下一轮生效")
        self.voice_toggle_button.toggled.connect(self._toggle_voice_reply)
        voice_controls.addWidget(self.voice_toggle_button)
        self.voice_preview_button = QPushButton("试听")
        self.voice_preview_button.setObjectName("voicePreviewButton")
        self.voice_preview_button.setToolTip("试听所选声音；首次使用本地声音需要加载，不需要 API，不写入聊天或记忆")
        self.voice_preview_button.clicked.connect(self._preview_voice)
        voice_controls.addWidget(self.voice_preview_button)
        self.voice_stop_button = QPushButton("停止朗读")
        self.voice_stop_button.setObjectName("voiceStopButton")
        self.voice_stop_button.setToolTip("只停止当前语音，文字回答继续生成")
        self.voice_stop_button.clicked.connect(self._stop_voice_reply)
        voice_controls.addWidget(self.voice_stop_button)
        self.voice_status_label = QLabel("无 API 可试听 · 本地声音首次加载较慢")
        self.voice_status_label.setObjectName("voiceStatusLabel")
        self.voice_status_label.setWordWrap(True)
        voice_controls.addWidget(self.voice_status_label, 1)
        input_layout.addLayout(voice_controls)

        voice_options = QHBoxLayout()
        voice_options.addWidget(QLabel("声音来源"))
        self.voice_backend_combo = QComboBox()
        for label, value in (("自动", "auto"), ("本地声音", "local_neural"), ("系统声音", "system")):
            self.voice_backend_combo.addItem(label, value)
        self.voice_backend_combo.setCurrentIndex(self.voice_backend_combo.findData(self._voice_service.settings.tts_backend))
        self.voice_backend_combo.currentIndexChanged.connect(self._change_voice_backend)
        self.voice_backend_combo.setToolTip("自动：本地声音失败时使用系统声音。切换后停止当前朗读，下一轮生效。")
        voice_options.addWidget(self.voice_backend_combo)
        self.voice_name_label = QLabel("Serena · 中文女声")
        voice_options.addWidget(self.voice_name_label)
        voice_options.addStretch(1)
        input_layout.addLayout(voice_options)

        controls = QHBoxLayout()
        hint = QLabel("Enter 发送 · Shift+Enter 换行")
        hint.setObjectName("chatInputHint")
        controls.addWidget(hint)
        controls.addStretch(1)
        self.stop_button = QPushButton("停止")
        self.stop_button.setObjectName("stopChatButton")
        self.stop_button.setEnabled(False)
        self.stop_button.hide()
        self.stop_button.clicked.connect(self.stop_generation)
        controls.addWidget(self.stop_button)
        self.send_button = QPushButton("发送")
        self.send_button.setObjectName("sendChatButton")
        self.send_button.setMinimumWidth(86)
        self.send_button.clicked.connect(self.send_message)
        controls.addWidget(self.send_button)
        input_layout.addLayout(controls)
        _apply_soft_shadow(input_panel, blur=20, y_offset=3)
        root.addWidget(input_panel)

    def _build_empty_state(self) -> QWidget:
        empty = QWidget()
        empty.setObjectName("chatEmptyState")
        empty.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        layout = QVBoxLayout(empty)
        layout.setContentsMargins(34, 8, 280, 8)
        layout.addStretch(2)

        row = QHBoxLayout()
        row.setSpacing(10)
        sparkle = QLabel("✦")
        sparkle.setObjectName("chatEmptySparkle")
        sparkle.setAlignment(Qt.AlignmentFlag.AlignTop)
        row.addWidget(sparkle)
        copy = QVBoxLayout()
        copy.setSpacing(6)
        title = QLabel("今天想聊点什么？")
        title.setObjectName("chatEmptyTitle")
        subtitle = QLabel("也可以让我帮你设置一个待办。")
        subtitle.setObjectName("chatEmptySubtitle")
        subtitle.setWordWrap(True)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        row.addLayout(copy)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(3)
        return empty

    def _build_corner_pet_layer(self) -> QWidget:
        layer = QWidget()
        layer.setObjectName("chatCornerPetLayer")
        layer.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents,
            True,
        )

        layer_layout = QVBoxLayout(layer)
        layer_layout.setContentsMargins(0, 0, 7, 3)
        layer_layout.addStretch(1)
        bottom = QHBoxLayout()
        bottom.addStretch(1)

        glow = QFrame()
        glow.setObjectName("chatMascotGlow")
        glow.setFixedSize(260, 260)
        glow_layout = QVBoxLayout(glow)
        glow_layout.setContentsMargins(3, 3, 3, 3)
        self.corner_pet_label = OriginalPixmapLabel(
            self._character_pixmap,
            254,
            target_size=252,
        )
        self.corner_pet_label.setObjectName("chatCornerPet")
        glow_layout.addWidget(self.corner_pet_label)
        bottom.addWidget(glow)
        layer_layout.addLayout(bottom)
        return layer

    def send_message(self) -> None:
        if self._worker is not None or self._service.is_busy:
            return
        text = self.input.toPlainText().strip()
        if not text:
            self._show_status("消息不能为空。", error=True)
            return

        self._voice_service.stop()
        self._accept_voice_reply = False
        self._voice_streaming_turn = False

        action = self._action_router.route(text)
        if action.is_local:
            self.input.clear()
            self._add_message("user", text, force_scroll=True)
            self._handle_local_action(action)
            return

        try:
            messages = self._service.begin_turn(text)
        except (EmptyChatMessageError, ChatBusyError, DeepSeekClientError) as exc:
            self._show_status(str(exc), error=True)
            return

        self.input.clear()
        self._add_message("user", text, force_scroll=True)
        self._assistant_label = self._add_message(
            "assistant",
            "桌宠正在想……",
            force_scroll=True,
        )
        self._assistant_text = ""
        self._accept_chunks = True
        self._accept_voice_reply = True
        if self._voice_service.settings.stream_sentences:
            self._voice_streaming_turn = self._voice_service.begin_reply()
        self._outcome_received = False
        self._set_generating(True)
        self._show_status("正在生成……", error=False)

        worker = ChatStreamWorker(self._service, messages)
        self._worker = worker
        worker.signals.chunk.connect(self._on_chunk)
        worker.signals.completed.connect(self._on_completed)
        worker.signals.error.connect(self._on_error)
        worker.signals.stopped.connect(self._on_stopped)
        worker.signals.finished.connect(self._on_finished)
        self._thread_pool.start(worker)

    def submit_recognized_text(self, text: str) -> bool:
        """Voice enters exactly the typed-send route, without replacing a draft."""
        text = text.strip()
        if not text:
            self._on_voice_notice("没有听清，再说一次吧。")
            return False
        if self.is_generating or self._service.is_busy:
            self._on_voice_notice("正在回答，请稍后再使用语音输入。")
            return False
        if self.input.toPlainText().strip():
            self._on_voice_notice("输入框已有草稿，请先发送或清空，再使用语音。")
            return False
        self.input.setPlainText(text)
        self.send_message()
        return not self.input.toPlainText().strip()

    def _on_voice_notice(self, message: str) -> None:
        self.voice_status_label.setText(message)
        self.voice_status_label.setToolTip(message)

    def _on_voice_error(self, message: str) -> None:
        # Keep completed text/history and ChatService turn state untouched.
        self._accept_voice_reply = False
        self.voice_status_label.setText(f"语音：{message}")
        self.voice_status_label.setToolTip(message)
        self._sync_voice_controls()

    def _on_voice_state(self, state: VoiceState) -> None:
        descriptions = {
            VoiceState.SYNTHESIZING: "正在准备语音……",
            VoiceState.PLAYING: "正在朗读……",
            VoiceState.PROCESSING: "正在识别……",
            VoiceState.CLOSED: "语音已关闭",
        }
        if state in descriptions:
            self.voice_status_label.setText(descriptions[state])
        self._sync_voice_controls()

    def _sync_voice_controls(self) -> None:
        if not hasattr(self, "voice_toggle_button"):
            return
        enabled = self._voice_service.settings.enable_voice_reply
        with QSignalBlocker(self.voice_toggle_button):
            self.voice_toggle_button.setChecked(enabled)
        self.voice_toggle_button.setText("语音回复：开" if enabled else "语音回复：关")
        closed = self._voice_service.state == VoiceState.CLOSED
        self.voice_toggle_button.setEnabled(not closed)
        self.voice_preview_button.setEnabled(not closed and not self.is_generating)
        self.voice_stop_button.setEnabled(self._voice_service.is_speaking)
        self.voice_backend_combo.setEnabled(not closed)
        self.voice_name_label.setText("Windows 已安装音色" if self._voice_service.settings.tts_backend == "system" else "Serena · 中文女声")

    def _toggle_voice_reply(self, enabled: bool) -> None:
        self._accept_voice_reply = False
        updated = replace(
            self._voice_service.settings,
            enable_voice_reply=enabled,
            stream_sentences=True,
        )
        self._voice_service.configure(updated)
        if self._voice_settings_store is not None:
            self._voice_settings_store.save_from(self._voice_service.settings)
        if enabled:
            message = "从下一轮回复开始按句朗读。" if self.is_generating else "已开启：回答生成一句，朗读一句。"
        else:
            message = "已关闭语音回复，文字聊天不受影响。"
        self._on_voice_notice(message)

    def _change_voice_backend(self, _index: int) -> None:
        choice = self.voice_backend_combo.currentData()
        self._accept_voice_reply = False
        self._voice_streaming_turn = False
        self._voice_service.configure(replace(self._voice_service.settings, tts_backend=choice))
        if self._voice_settings_store is not None:
            self._voice_settings_store.save_from(self._voice_service.settings)
        self.voice_name_label.setText("Windows 已安装音色" if choice == "system" else "Serena · 中文女声")
        self._on_voice_notice("已切换声音来源；点击试听，或从下一轮语音回复开始使用。")

    def _stop_voice_reply(self) -> None:
        self._accept_voice_reply = False
        self._voice_service.stop()
        self._on_voice_notice("本轮朗读已停止，文字回答会继续。")

    def _preview_voice(self) -> None:
        if self.is_generating:
            return
        self._accept_voice_reply = False
        self._voice_service.preview()

    def _handle_local_action(self, action: ChatActionResult) -> None:
        if action.kind == ChatActionKind.TODO_DRAFT and action.draft is not None:
            self._add_message("assistant", action.message, force_scroll=True)
            self._add_todo_card(action.draft)
            self._show_status("待办草案尚未保存，请确认或取消。", error=False)
            self._set_header_status("等你确认待办")
            return
        self._add_message("assistant", action.message, force_scroll=True)
        self._show_status(action.message, error=False)
        self._set_header_status("陪你聊聊天")

    def _add_todo_card(self, draft: TaskDraft) -> TodoConfirmationCard:
        card = TodoConfirmationCard(draft)
        card.confirm_requested.connect(self._confirm_todo_card)
        card.cancel_requested.connect(self._cancel_todo_card)
        self._todo_cards.append(card)
        self._insert_message_widget(card, "assistant", force_scroll=True)
        self._refresh_message_widths()
        return card

    def _confirm_todo_card(self, card: TodoConfirmationCard) -> None:
        if card.state != "pending":
            return
        if self._todo_manager is None:
            card.mark_failed("待办服务不可用，没有写入任何数据")
            self._add_message(
                "assistant",
                "这次没有创建成功，待办服务暂时不可用。",
                force_scroll=True,
            )
            self._show_status("待办服务不可用。", error=True)
            return

        current = self._action_router.now()
        if card.draft.is_expired(current):
            card.mark_failed("草案时间已经过去，没有创建待办")
            self._add_message(
                "assistant",
                "这个时间已经过去了，请重新输入一个未来时间。",
                force_scroll=True,
            )
            self._show_status("草案时间已经过去。", error=True)
            return

        try:
            task = self._todo_manager.add_task(
                card.draft.title,
                card.draft.due_at_iso,
                card.draft.advance_minutes,
                card.draft.repeat_type,
                card.draft.repeat_weekdays,
                now=current,
            )
        except (TaskDataError, TodoStorageError) as exc:
            card.mark_failed("保存失败，没有创建待办")
            self._add_message(
                "assistant",
                "这次没有创建成功，请检查待办存储后再试。",
                force_scroll=True,
            )
            self._show_status(str(exc), error=True)
            return

        card.mark_created(task.id)
        controlled_result = card.draft.controlled_success_message()
        try:
            self._service.record_local_exchange(
                card.draft.original_text,
                controlled_result,
            )
        except (ChatBusyError, ValueError):
            pass
        self._add_message(
            "assistant",
            "设置好了，到时候我会提醒你。",
            force_scroll=True,
        )
        self._show_status("待办已保存。", error=False)
        self._set_header_status("待办设置好了")

    def _cancel_todo_card(self, card: TodoConfirmationCard) -> None:
        if card.state != "pending":
            return
        card.mark_cancelled()
        self._add_message(
            "assistant",
            "好，那我先不创建。",
            force_scroll=True,
        )
        self._show_status("待办草案已取消。", error=False)
        self._set_header_status("陪你聊聊天")

    def stop_generation(self) -> None:
        self._accept_voice_reply = False
        self._voice_service.stop()
        if self._worker is None:
            return
        self._accept_chunks = False
        self.stop_button.setEnabled(False)
        self._show_status("正在停止生成……", error=False)
        self._set_header_status("正在停止……")
        self._worker.request_stop()

    def _on_chunk(self, chunk: str) -> None:
        if not self._accept_chunks or self._assistant_label is None:
            return
        follow = self._is_near_bottom()
        if not self._assistant_text:
            self._set_header_status("正在回复……")
        self._assistant_text += chunk
        self._assistant_label.setText(clean_chat_markdown(self._assistant_text))
        self._scroll_to_bottom(force=follow)

        if self._accept_voice_reply and self._voice_streaming_turn:
            self._voice_service.append_reply(chunk)

    def _on_completed(self, full_text: str) -> None:
        if self._outcome_received:
            return
        self._outcome_received = True
        self._accept_chunks = False
        self._assistant_text = full_text
        if self._assistant_label is not None:
            self._assistant_label.setText(
                clean_chat_markdown(full_text) or "（本次没有返回文字）"
            )
        self._service.complete_turn(full_text)
        self._show_status("回答完成。", error=False)
        if self._accept_voice_reply:
            if self._voice_streaming_turn:
                self._voice_service.end_reply()
            elif not self._voice_service.settings.stream_sentences:
                self._voice_service.speak(full_text)

    def _on_stopped(self, _worker_partial: str) -> None:
        if self._outcome_received:
            return
        self._outcome_received = True
        self._accept_voice_reply = False
        self._voice_service.stop()
        self._accept_chunks = False
        if self._assistant_label is not None and not self._assistant_text:
            self._assistant_label.setText("（已停止）")
        self._service.stop_turn(self._assistant_text)
        self._show_status("已停止生成，已生成内容会保留。", error=False)

    def _on_error(self, message: str) -> None:
        if self._outcome_received:
            return
        self._outcome_received = True
        self._accept_voice_reply = False
        self._voice_service.stop()
        self._accept_chunks = False
        self._service.fail_turn()
        if self._assistant_label is not None:
            self._assistant_label.setText(f"请求失败：{message}")
        self._show_status(message, error=True)

    def _on_finished(self) -> None:
        self._accept_voice_reply = False
        if not self._outcome_received and self._service.is_busy:
            self._service.fail_turn()
        self._worker = None
        self._set_generating(False)
        self._scroll_to_bottom(force=self._is_near_bottom())

    def _set_generating(self, generating: bool) -> None:
        self.send_button.setEnabled(not generating)
        self.send_button.setVisible(not generating)
        self.stop_button.setEnabled(generating)
        self.stop_button.setVisible(generating)
        self.clear_button.setEnabled(not generating)
        self._set_header_status("正在想……" if generating else "陪你聊聊天")
        self.voice_preview_button.setEnabled(
            not generating and self._voice_service.state != VoiceState.CLOSED
        )

    def _create_message_avatar(self) -> PetAvatarWidget:
        return PetAvatarWidget(
            self._avatar_pixmap,
            34,
            object_name="messagePetAvatar",
            image_object_name="chatMessageAvatar",
            crop_fallback_character=self._avatar_uses_idle_fallback,
        )

    def _add_message(
        self,
        role: str,
        text: str,
        *,
        force_scroll: bool = False,
    ) -> QLabel:
        bubble = QFrame()
        bubble.setObjectName("userMessage" if role == "user" else "assistantMessage")
        bubble.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        bubble_layout = QVBoxLayout(bubble)
        bubble_layout.setContentsMargins(13, 9, 13, 10)
        bubble_layout.setSpacing(3)
        author = QLabel("你" if role == "user" else self._pet_name)
        author.setObjectName("messageAuthor")
        bubble_layout.addWidget(author)
        content = QLabel(clean_chat_markdown(text) if role == "assistant" else text)
        content.setObjectName(
            "userMessageText" if role == "user" else "assistantMessageText"
        )
        content.setWordWrap(True)
        content.setTextFormat(Qt.TextFormat.PlainText)
        content.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        content.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        bubble_layout.addWidget(content)
        self._message_records.append((role, content))
        self._message_bubbles.append(bubble)
        self._insert_message_widget(bubble, role, force_scroll=force_scroll)
        self._refresh_message_widths()
        return content

    def _insert_message_widget(
        self,
        widget: QWidget,
        role: str,
        *,
        force_scroll: bool,
    ) -> None:
        follow = force_scroll or self._is_near_bottom()
        self.empty_state.hide()
        row_widget = QWidget()
        row_widget.setObjectName("chatMessageRow")
        row_widget.setProperty("messageRole", role)
        row = QHBoxLayout(row_widget)
        top_spacing = 3 if role == self._last_row_role else 10
        row.setContentsMargins(0, top_spacing, 0, 0)
        row.setSpacing(8)
        if role == "user":
            row.addStretch(1)
            row.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight)
        else:
            row.addWidget(
                self._create_message_avatar(),
                0,
                Qt.AlignmentFlag.AlignTop,
            )
            row.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft)
            row.addStretch(1)
        insert_at = max(0, self.message_layout.count() - 1)
        self.message_layout.insertWidget(insert_at, row_widget)
        self._message_rows.append(row_widget)
        self._last_row_role = role
        self._scroll_to_bottom(force=follow)

    def _is_near_bottom(self) -> bool:
        if not hasattr(self, "scroll"):
            return True
        bar = self.scroll.verticalScrollBar()
        return bar.maximum() - bar.value() <= 90

    def _scroll_to_bottom(self, *, force: bool = True) -> None:
        if not force:
            return
        QTimer.singleShot(
            0,
            lambda: self.scroll.verticalScrollBar().setValue(
                self.scroll.verticalScrollBar().maximum()
            ),
        )

    def _restore_history_messages(self) -> None:
        history = self._service.history
        if not history:
            return
        for entry in history:
            role = entry.get("role")
            content = entry.get("content", "")
            if role in ("user", "assistant") and content:
                self._add_message(role, content, force_scroll=False)
        self._scroll_to_bottom(force=True)

    def _refresh_message_widths(self) -> None:
        if not hasattr(self, "scroll"):
            return
        available = max(320, self.scroll.viewport().width())
        maximum = max(280, min(520, int(available * 0.70)))
        for bubble in self._message_bubbles:
            bubble.setMaximumWidth(maximum)
        for card in self._todo_cards:
            card.setMaximumWidth(maximum)

    def _set_header_status(self, message: str) -> None:
        self.header_status_label.setText(message)

    def apply_profile(self, profile: PetProfile) -> None:
        """Apply a saved profile to the header and all future AI requests."""

        self._pet_name = profile.pet_name
        self._service.refresh_system_prompt()
        self.setWindowTitle(f"AI 桌宠聊天 - {self._pet_name}")
        if hasattr(self, "header_name_label"):
            self.header_name_label.setText(self._pet_name)

    def apply_ai_settings(self, settings: AiConnectionSettings) -> None:
        """Apply updated AI API key / base URL / model live without restart."""

        config = settings.resolve_config()
        self._service.reconfigure(config, api_key=settings.resolve_api_key())
        if hasattr(self, "model_label"):
            short_model = config.model.removeprefix("deepseek-")
            self.model_label.setText(f"DeepSeek · {short_model}")
            self.model_label.setToolTip(config.model)
        if self._service.is_configured:
            self._show_status("AI 接口设置已更新，下一次聊天生效。", error=False)
        else:
            self._show_status(
                "未配置 DeepSeek API Key（可在“桌宠设置 → AI 接口设置”中填写）；本地待办创建仍可使用。",
                error=True,
            )

    def _show_status(self, message: str, *, error: bool) -> None:
        self.status_label.setProperty("error", error)
        style = self.status_label.style()
        style.unpolish(self.status_label)
        style.polish(self.status_label)
        self.status_label.setText(message)
        self.status_label.setVisible(bool(message))

    def _choose_chat_background(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "选择聊天背景",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.webp)",
        )
        if selected:
            self._apply_chat_background(Path(selected))

    def _apply_chat_background(self, background_path: Path) -> bool:
        previous = self.chat_stage.background_path
        if not self.chat_stage.set_background_image(background_path):
            self._show_status("无法读取所选背景图片，已保留原背景。", error=True)
            return False
        try:
            self._ui_settings.save_background_path(background_path)
        except ChatUiSettingsError as exc:
            if previous is None or not self.chat_stage.set_background_image(previous):
                self.chat_stage.restore_default_background()
            self._sync_corner_pet_visibility()
            self._show_status(str(exc), error=True)
            return False
        self._sync_corner_pet_visibility()
        self._show_status("聊天背景已更换。", error=False)
        return True

    def _restore_default_background(self) -> None:
        self.chat_stage.restore_default_background()
        self._sync_corner_pet_visibility()
        try:
            self._ui_settings.restore_default_background()
        except ChatUiSettingsError as exc:
            self._show_status(str(exc), error=True)
            return
        self._show_status("已恢复默认蓝紫渐变背景。", error=False)

    def _sync_corner_pet_visibility(self) -> None:
        if hasattr(self, "corner_pet_layer"):
            self.corner_pet_layer.setVisible(
                not self.chat_stage.has_custom_background
            )

    def _confirm_clear(self) -> None:
        if self.is_generating:
            return
        reply = QMessageBox.question(
            self,
            "清空聊天",
            "确定清空当前聊天和上下文吗？已创建的待办不会删除。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.clear_chat()

    def clear_chat(self) -> None:
        if self.is_generating:
            return
        self._accept_voice_reply = False
        self._voice_service.stop()
        try:
            self._service.clear()
        except ChatBusyError as exc:
            self._show_status(str(exc), error=True)
            return
        for card in self._todo_cards:
            card.invalidate()
        for row_widget in self._message_rows:
            self.message_layout.removeWidget(row_widget)
            row_widget.deleteLater()
        self._message_records.clear()
        self._message_bubbles.clear()
        self._message_rows.clear()
        self._todo_cards.clear()
        self._last_row_role = None
        self._assistant_label = None
        self._assistant_text = ""
        self.empty_state.show()
        self._set_header_status("陪你聊聊天")
        self._show_status("聊天和上下文已清空，已有待办保持不变。", error=False)

    def shutdown(self) -> None:
        self._accept_voice_reply = False
        self._voice_service.shutdown()
        if self._worker is not None:
            self._accept_chunks = False
            self._worker.request_stop()
        self.hide()

    @property
    def voice_service(self) -> VoiceService:
        return self._voice_service

    @property
    def is_generating(self) -> bool:
        return self._worker is not None

    @property
    def message_texts(self) -> tuple[tuple[str, str], ...]:
        return tuple((role, label.text()) for role, label in self._message_records)

    @property
    def todo_cards(self) -> tuple[TodoConfirmationCard, ...]:
        return tuple(self._todo_cards)

    @property
    def service(self) -> ChatService:
        return self._service

    @property
    def action_router(self) -> ChatActionRouter:
        return self._action_router

    @property
    def todo_manager(self) -> TodoManager | None:
        return self._todo_manager

    @property
    def avatar_source_path(self) -> Path:
        return self._avatar_source_path

    @property
    def character_source_path(self) -> Path:
        return self._character_source_path

    @property
    def ui_settings(self) -> ChatUiSettings:
        return self._ui_settings

    @property
    def profile_service(self) -> ProfileService | None:
        return self._profile_service

    @property
    def memory_service(self) -> MemoryService | None:
        return self._memory_service

    @property
    def pet_name(self) -> str:
        return self._pet_name

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_message_widths()

    def closeEvent(self, event: QCloseEvent) -> None:
        self._accept_voice_reply = False
        self._voice_service.stop()
        if self._worker is not None:
            self.stop_generation()
        self.hide()
        event.ignore()
