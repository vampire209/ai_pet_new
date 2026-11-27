"""Transparent desktop pet with 24fps WebP engine, 60fps physics, wander, tray, and fullscreen yield."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import time

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QContextMenuEvent,
    QDesktopServices,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPixmap,
    QScreen,
    QShowEvent,
    QTransform,
)
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
)

from ai.ai_settings_service import AiSettingsService
from ai.chat_history_store import ChatHistoryStore
from ai.chat_service import ChatService
from ai.deepseek_client import DeepSeekClient
from ai.profile_service import ProfileService
from animation_manager import AnimationManager
from animation_state import AnimationStateController
from app_paths import characters_dir
from character_pack import (
    CharacterPack,
    DEFAULT_PACK_ID,
    discover_character_packs,
)
from chat_window import ChatWindow
from config import AppConfig
from memory.memory_service import MemoryService
from memory_window import MemoryManagerWindow
from models.reminder import Reminder
from models.task import Task, TaskDataError
from pet_motion import PetMotionEngine, WanderController
from pet_state import PetStateStore, PetWindowState
from profile_window import ProfileWindow
from reminder_bubble import ReminderBubble
from reminder_manager import ReminderManager
from todo_manager import TodoManager, TodoStorageError
from todo_window import TodoWindow
from ui_resources import load_application_icon
from video_yield import FullscreenYieldWatcher
from voice.settings_store import VoiceSettingsStore


class PetWindow(QLabel):
    """Frameless pet window with smooth 60fps micro-motion, wandering, tray, and character packs."""

    pet_poked = Signal()

    def __init__(
        self,
        config: AppConfig,
        todo_manager: TodoManager,
        reminder_manager: ReminderManager | None = None,
        parent=None,
        chat_service: ChatService | None = None,
        profile_service: ProfileService | None = None,
        memory_service: MemoryService | None = None,
        ai_settings_service: AiSettingsService | None = None,
        chat_history_store: ChatHistoryStore | None = None,
        voice_settings_store: VoiceSettingsStore | None = None,
        state_store: PetStateStore | None = None,
        character_packs: tuple[CharacterPack, ...] | None = None,
        enable_fullscreen_watcher: bool | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._todo_manager = todo_manager
        self._reminder_manager = reminder_manager or ReminderManager(todo_manager)
        self._todo_window: TodoWindow | None = None
        self._chat_service = chat_service
        self._chat_window: ChatWindow | None = None
        self._profile_service = profile_service or ProfileService()
        self._profile_window: ProfileWindow | None = None
        self._memory_service = memory_service
        self._memory_window: MemoryManagerWindow | None = None
        self._ai_settings_service = ai_settings_service
        self._chat_history_store = chat_history_store
        self._voice_settings_store = voice_settings_store
        self._state_store = state_store
        self._window_state = (
            state_store.load() if state_store is not None else PetWindowState()
        )
        self._fullscreen_yielded = False
        self._tray_icon: QSystemTrayIcon | None = None
        self._tray_menu: QMenu | None = None
        self._tray_toggle_action: QAction | None = None
        self._tray_topmost_action: QAction | None = None
        self._tray_wander_action: QAction | None = None
        self._tray_yield_action: QAction | None = None
        self._reminder_bubbles: dict[str, ReminderBubble] = {}
        self._press_global: QPoint | None = None
        self._press_time: float = 0.0
        self._last_drag_global: QPoint | None = None
        self._last_drag_time: float = 0.0
        self._drag_offset = QPoint()
        self._dragging = False
        self._screen_signal_connected = False
        self._current_pixmap: QPixmap = QPixmap()
        self._missing_asset_text: str = ""

        # Character packs
        self._character_packs: tuple[CharacterPack, ...] = (
            character_packs
            if character_packs is not None
            else discover_character_packs(animations_dir=config.animations_dir)
        )
        initial_pack = self._find_pack(self._window_state.character_pack)

        # 60fps micro-motion & autonomous wandering
        now = time.perf_counter()
        self._motion = PetMotionEngine(start_time=now)
        self._wander = WanderController(
            enabled=self._window_state.wandering_enabled and (state_store is not None),
        )
        self._motion_timer = QTimer(self)
        self._motion_timer.setInterval(33)
        self._motion_timer.timeout.connect(self._on_motion_tick)

        # Poke transient timer (ensures poke animation returns even on looped clips)
        self._poke_clear_timer = QTimer(self)
        self._poke_clear_timer.setSingleShot(True)
        self._poke_clear_timer.timeout.connect(
            lambda: self._state_controller.set_poking(False)
        )

        self._setup_window()
        self._setup_tray()

        screen = QApplication.primaryScreen()
        initial_dpr = screen.devicePixelRatio() if screen is not None else 1.0
        self._animation_manager = AnimationManager(
            config,
            device_pixel_ratio=initial_dpr,
            parent=self,
            character_pack=initial_pack,
        )
        self._animation_manager.frame_changed.connect(self._show_frame)
        self._state_controller = AnimationStateController(
            self._animation_manager,
            parent=self,
        )
        self._reminder_manager.reminder_ready.connect(self._on_reminder_due)
        self._reminder_manager.scan_failed.connect(self._on_reminder_scan_failed)
        self._reminder_manager.waiting_changed.connect(self.set_waiting)
        self._reminder_manager.active_invalidated.connect(
            self._close_invalidated_bubble
        )
        self._state_controller.set_waiting(self._reminder_manager.waiting_state)

        if self._animation_manager.current_animation is None:
            self._show_missing_asset_message()
        self._set_initial_position()

        # Fullscreen auto-yield watcher (enabled in live app runs or when explicitly requested)
        use_watcher = (
            enable_fullscreen_watcher
            if enable_fullscreen_watcher is not None
            else (state_store is not None and sys.platform == "win32")
        )
        self._fullscreen_watcher = FullscreenYieldWatcher(
            hwnd_provider=self._safe_hwnd,
            parent=self,
        )
        self._fullscreen_watcher.set_enabled(self._window_state.fullscreen_yield_enabled)
        self._fullscreen_watcher.fullscreen_changed.connect(self._on_fullscreen_changed)
        if use_watcher:
            self._fullscreen_watcher.start()

        self._motion_timer.start()

    def _safe_hwnd(self) -> int | None:
        try:
            return int(self.winId())
        except Exception:
            return None

    def _find_pack(self, pack_id: str) -> CharacterPack:
        for pack in self._character_packs:
            if pack.pack_id == pack_id:
                return pack
        for pack in self._character_packs:
            if pack.pack_id == DEFAULT_PACK_ID:
                return pack
        return self._character_packs[0]

    def refresh_character_packs(self) -> tuple[CharacterPack, ...]:
        self._character_packs = discover_character_packs(
            animations_dir=self._config.animations_dir
        )
        return self._character_packs

    def select_character_pack(self, pack_id: str) -> bool:
        pack = self._find_pack(pack_id)
        if pack.pack_id != pack_id and pack_id != DEFAULT_PACK_ID:
            return False
        ok = self._animation_manager.load_character_pack(pack)
        self._window_state = replace(self._window_state, character_pack=pack.pack_id)
        self._save_window_state()
        self.update()
        return ok

    def _open_characters_folder(self) -> None:
        target = characters_dir()
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _setup_window(self) -> None:
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self._effective_topmost():
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: transparent;")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("轻点戳一戳，按住左键拖动，双击打开待办，右键打开功能菜单")

    def _effective_topmost(self) -> bool:
        if not self._window_state.always_on_top:
            return False
        if self._fullscreen_yielded and not self._reminder_bubbles:
            return False
        return True

    def _apply_effective_topmost(self) -> None:
        target = self._effective_topmost()
        current = bool(self.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        if target == current:
            return
        was_visible = self.isVisible()
        position = self.pos()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, target)
        if was_visible:
            self.show()
            self.move(position)
            if target:
                self.raise_()

    def _on_fullscreen_changed(self, is_fullscreen: bool) -> None:
        self._fullscreen_yielded = bool(
            is_fullscreen and self._window_state.fullscreen_yield_enabled
        )
        self._apply_effective_topmost()

    def _setup_tray(self) -> None:
        menu = QMenu()
        toggle_action = QAction("隐藏桌宠", menu)
        toggle_action.triggered.connect(self.toggle_pet_visibility)
        menu.addAction(toggle_action)

        topmost_action = QAction("始终置顶", menu)
        topmost_action.setCheckable(True)
        topmost_action.setChecked(self._window_state.always_on_top)
        topmost_action.triggered.connect(self.set_always_on_top)
        menu.addAction(topmost_action)

        wander_action = QAction("自主漫步", menu)
        wander_action.setCheckable(True)
        wander_action.setChecked(self._window_state.wandering_enabled)
        wander_action.triggered.connect(self.set_wandering_enabled)
        menu.addAction(wander_action)

        yield_action = QAction("全屏自动让位", menu)
        yield_action.setCheckable(True)
        yield_action.setChecked(self._window_state.fullscreen_yield_enabled)
        yield_action.triggered.connect(self.set_fullscreen_yield_enabled)
        menu.addAction(yield_action)
        menu.addSeparator()

        todo_action = QAction("打开待办", menu)
        todo_action.triggered.connect(lambda _checked=False: self._open_todo_window())
        menu.addAction(todo_action)
        chat_action = QAction("AI聊天", menu)
        chat_action.triggered.connect(lambda _checked=False: self._open_chat_window())
        menu.addAction(chat_action)
        profile_action = QAction("桌宠设置", menu)
        profile_action.triggered.connect(
            lambda _checked=False: self._open_profile_window()
        )
        menu.addAction(profile_action)
        memory_action = QAction("记忆管理", menu)
        memory_action.triggered.connect(
            lambda _checked=False: self._open_memory_window()
        )
        menu.addAction(memory_action)
        menu.addSeparator()

        chars_menu = menu.addMenu("角色形象")
        chars_menu.aboutToShow.connect(
            lambda m=chars_menu: self._populate_character_menu(m)
        )
        menu.addSeparator()

        exit_action = QAction("退出", menu)
        exit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(exit_action)

        self._tray_menu = menu
        self._tray_toggle_action = toggle_action
        self._tray_topmost_action = topmost_action
        self._tray_wander_action = wander_action
        self._tray_yield_action = yield_action

        if QSystemTrayIcon.isSystemTrayAvailable():
            tray = QSystemTrayIcon(load_application_icon(), self)
            tray.setToolTip("AI 桌宠")
            tray.setContextMenu(menu)
            tray.activated.connect(self._on_tray_activated)
            tray.show()
            self._tray_icon = tray

    def _populate_character_menu(self, menu: QMenu) -> None:
        menu.clear()
        packs = self.refresh_character_packs()
        active_id = self._animation_manager.character_pack.pack_id
        for pack in packs:
            action = QAction(pack.name, menu)
            action.setCheckable(True)
            action.setChecked(pack.pack_id == active_id)
            action.triggered.connect(
                lambda _checked=False, pid=pack.pack_id: self.select_character_pack(pid)
            )
            menu.addAction(action)
        menu.addSeparator()
        open_dir = QAction("打开角色包目录…", menu)
        open_dir.triggered.connect(self._open_characters_folder)
        menu.addAction(open_dir)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            if self.isVisible():
                self.raise_()
                self.activateWindow()
            else:
                self.show_from_tray()

    def _sync_tray_state(self) -> None:
        if self._tray_toggle_action is not None:
            self._tray_toggle_action.setText(
                "隐藏桌宠" if self.isVisible() else "显示桌宠"
            )
        if self._tray_topmost_action is not None:
            self._tray_topmost_action.setChecked(self._window_state.always_on_top)
        if self._tray_wander_action is not None:
            self._tray_wander_action.setChecked(self._window_state.wandering_enabled)
        if self._tray_yield_action is not None:
            self._tray_yield_action.setChecked(
                self._window_state.fullscreen_yield_enabled
            )

    def _save_window_state(self) -> None:
        if self._state_store is not None:
            self._state_store.save(self._window_state)

    def set_always_on_top(self, enabled: bool) -> None:
        target = bool(enabled)
        self._window_state = replace(self._window_state, always_on_top=target)
        self._apply_effective_topmost()
        self._save_window_state()
        self._sync_tray_state()

    def set_wandering_enabled(self, enabled: bool) -> None:
        target = bool(enabled)
        self._window_state = replace(self._window_state, wandering_enabled=target)
        self._wander.set_enabled(target)
        if not target:
            self._motion.set_walking(False, time.perf_counter())
            self._state_controller.set_walking(False)
        self._save_window_state()
        self._sync_tray_state()

    def set_fullscreen_yield_enabled(self, enabled: bool) -> None:
        target = bool(enabled)
        self._window_state = replace(
            self._window_state, fullscreen_yield_enabled=target
        )
        self._fullscreen_watcher.set_enabled(target)
        if not target:
            self._fullscreen_yielded = False
        self._apply_effective_topmost()
        self._save_window_state()
        self._sync_tray_state()

    def hide_to_tray(self) -> None:
        self._wander.cancel()
        self._motion.set_walking(False, time.perf_counter())
        self._state_controller.set_walking(False)
        self.hide()
        self._sync_tray_state()

    def show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self._sync_tray_state()

    def toggle_pet_visibility(self) -> None:
        if self.isVisible():
            self.hide_to_tray()
        else:
            self.show_from_tray()

    def trigger_poke(self) -> None:
        """Play 60fps elastic Q-bounce and optional poke action clip."""
        now = time.perf_counter()
        self._motion.trigger_poke(now)
        self._motion_timer.setInterval(16)
        if self._animation_manager.character_pack.clips_for("poke"):
            self._state_controller.trigger_oneshot("poke")
            self._poke_clear_timer.start(1600)
        self.pet_poked.emit()
        self.update()

    def _on_motion_tick(self) -> None:
        now = time.perf_counter()

        # Autonomous wandering step
        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            min_x = area.left() + 8
            max_x = max(min_x, area.right() - self.width() - 8)
            allowed = (
                self.isVisible()
                and not self._dragging
                and not self._reminder_bubbles
                and self._state_controller.current_state in ("idle", "wait", "walk")
            )
            step = self._wander.tick(
                now=now,
                current_x=self.x(),
                min_x=min_x,
                max_x=max_x,
                allowed=allowed,
            )
            if step is not None:
                self._motion.set_facing(step.facing)
                self._motion.set_walking(step.walking, now)
                if step.walking:
                    self._state_controller.set_walking(True)
                    if step.x != self.x():
                        self.move(step.x, self.y())
                else:
                    self._state_controller.set_walking(False)
                    if step.arrived and step.x != self.x():
                        self.move(step.x, self.y())
                    if step.trigger_variant and self._animation_manager.character_pack.clips_for("variant"):
                        self._state_controller.trigger_oneshot("variant")

        # Adaptive timer rate: 60fps during active motion, gentle 30fps breathing otherwise
        desired_interval = 16 if self._motion.needs_high_fps(now) else 33
        if self._motion_timer.interval() != desired_interval:
            self._motion_timer.setInterval(desired_interval)
        if self.isVisible() and not self._current_pixmap.isNull():
            self.update()

    def _show_frame(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self._missing_asset_text = ""
        self._current_pixmap = pixmap
        logical_size = pixmap.deviceIndependentSize().toSize()
        if not logical_size.isEmpty():
            self.setFixedSize(logical_size)
        self.update()

    def _show_missing_asset_message(self) -> None:
        self._current_pixmap = QPixmap()
        self._missing_asset_text = "桌宠 idle 素材缺失"
        self.setText(self._missing_asset_text)
        self.setStyleSheet(
            "background: rgba(40, 40, 40, 190); color: white; "
            "padding: 12px; border-radius: 8px;"
        )
        self.adjustSize()

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._current_pixmap.isNull():
            super().paintEvent(event)
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        state = self._motion.sample(time.perf_counter())
        rect = self.rect()
        anchor_x = rect.width() * 0.5
        anchor_y = float(rect.height())

        transform = QTransform()
        transform.translate(anchor_x, anchor_y + state.offset_y)
        if abs(state.rotation_deg) >= 0.05:
            transform.rotate(state.rotation_deg)
        sx = state.scale_x * (1.0 if state.facing >= 0 else -1.0)
        transform.scale(sx, state.scale_y)
        transform.translate(-anchor_x, -anchor_y)

        painter.setTransform(transform, True)
        logical = self._current_pixmap.deviceIndependentSize()
        draw_x = (rect.width() - logical.width()) * 0.5
        draw_y = rect.height() - logical.height()
        painter.drawPixmap(QPoint(int(round(draw_x)), int(round(draw_y))), self._current_pixmap)
        painter.end()

    def _is_position_visible_on_any_screen(self, x: int, y: int) -> bool:
        width = max(1, self.width())
        height = max(1, self.height())
        pet_rect = QRect(x, y, width, height)
        for screen in QApplication.screens():
            area = screen.availableGeometry()
            overlap = area.intersected(pet_rect)
            if overlap.width() >= min(48, width) and overlap.height() >= min(48, height):
                return True
        return False

    def _set_initial_position(self) -> None:
        if (
            self._window_state.x is not None
            and self._window_state.y is not None
            and self._is_position_visible_on_any_screen(
                self._window_state.x, self._window_state.y
            )
        ):
            self.move(self._window_state.x, self._window_state.y)
            return

        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = available.x() + available.width() - self.width() - self._config.margin_x
        y = available.y() + available.height() - self.height() - self._config.margin_y
        self.move(max(available.x(), x), max(available.y(), y))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and not self._screen_signal_connected:
            handle.screenChanged.connect(self._on_screen_changed)
            self._screen_signal_connected = True
            self._on_screen_changed(handle.screen())
        self._sync_tray_state()

    def _on_screen_changed(self, screen: QScreen | None) -> None:
        if screen is not None:
            self._animation_manager.set_device_pixel_ratio(screen.devicePixelRatio())
        for bubble in self._reminder_bubbles.values():
            self._position_bubble(bubble)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        now = time.perf_counter()
        self._press_global = event.globalPosition().toPoint()
        self._press_time = now
        self._last_drag_global = self._press_global
        self._last_drag_time = now
        self._drag_offset = self._press_global - self.pos()
        self._dragging = False
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (
            self._press_global is None
            or not (event.buttons() & Qt.MouseButton.LeftButton)
        ):
            super().mouseMoveEvent(event)
            return

        now = time.perf_counter()
        current_global = event.globalPosition().toPoint()
        if not self._dragging:
            distance = (current_global - self._press_global).manhattanLength()
            threshold = QApplication.styleHints().startDragDistance()
            if distance < threshold:
                event.accept()
                return
            self._dragging = True
            self._wander.cancel(now=now)
            self._state_controller.set_walking(False)
            self._motion.begin_drag(now)
            self._motion_timer.setInterval(16)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._state_controller.set_moving(True)

        if self._last_drag_global is not None:
            dx = float(current_global.x() - self._last_drag_global.x())
            dt = max(0.008, now - self._last_drag_time)
            self._motion.update_drag(dx, dt)
        self._last_drag_global = current_global
        self._last_drag_time = now

        self.move(current_global - self._drag_offset)
        for bubble in self._reminder_bubbles.values():
            self._position_bubble(bubble)
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        now = time.perf_counter()
        was_dragging = self._dragging
        press_global = self._press_global
        press_time = self._press_time
        if was_dragging:
            self._state_controller.set_moving(False)
            self._motion.end_drag(now)
            self._window_state = replace(
                self._window_state,
                x=self.x(),
                y=self.y(),
            )
            self._save_window_state()
        elif press_global is not None and (now - press_time) < 0.35:
            self.trigger_poke()
        self._dragging = False
        self._press_global = None
        self._last_drag_global = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self._dragging:
            super().mouseDoubleClickEvent(event)
            return
        self._press_global = None
        self._open_todo_window()
        event.accept()

    def _open_todo_window(self, task_id: str | None = None) -> None:
        if self._todo_window is None:
            self._todo_window = TodoWindow(self._todo_manager)
        else:
            self._todo_window.refresh_tasks()

        if self._todo_window.isMinimized():
            state = self._todo_window.windowState() & ~Qt.WindowState.WindowMinimized
            self._todo_window.setWindowState(state | Qt.WindowState.WindowActive)
        self._todo_window.show()
        self._todo_window.raise_()
        self._todo_window.activateWindow()

    def _create_default_chat_service(self) -> ChatService:
        if self._ai_settings_service is not None:
            settings = self._ai_settings_service.settings
            config = settings.resolve_config()
            client = DeepSeekClient(config, api_key=settings.resolve_api_key())
            return ChatService(
                client=client,
                config=config,
                memory_service=self._memory_service,
                history_store=self._chat_history_store,
            )
        return ChatService(
            memory_service=self._memory_service,
            history_store=self._chat_history_store,
        )

    def _open_chat_window(self) -> None:
        if self._chat_window is None:
            service = self._chat_service or self._create_default_chat_service()
            self._chat_window = ChatWindow(
                service,
                todo_manager=self._todo_manager,
                profile_service=self._profile_service,
                memory_service=self._memory_service,
                ai_settings_service=self._ai_settings_service,
                voice_settings_store=self._voice_settings_store,
            )
            self._chat_window.ai_settings_requested.connect(
                lambda: self._open_profile_window(tab="ai")
            )
        if self._chat_window.isMinimized():
            state = self._chat_window.windowState() & ~Qt.WindowState.WindowMinimized
            self._chat_window.setWindowState(state | Qt.WindowState.WindowActive)
        self._chat_window.show()
        self._chat_window.raise_()
        self._chat_window.activateWindow()

    def _open_profile_window(self, tab: str = "profile") -> None:
        if self._profile_window is None:
            self._profile_window = ProfileWindow(
                self._profile_service,
                ai_settings_service=self._ai_settings_service,
            )
        self._profile_window.open_tab(tab)
        if self._profile_window.isMinimized():
            state = self._profile_window.windowState() & ~Qt.WindowState.WindowMinimized
            self._profile_window.setWindowState(state | Qt.WindowState.WindowActive)
        self._profile_window.show()
        self._profile_window.raise_()
        self._profile_window.activateWindow()

    def _open_memory_window(self) -> None:
        if self._memory_service is None:
            self._memory_service = MemoryService()
        if self._memory_window is None:
            self._memory_window = MemoryManagerWindow(self._memory_service)
        if self._memory_window.isMinimized():
            state = self._memory_window.windowState() & ~Qt.WindowState.WindowMinimized
            self._memory_window.setWindowState(state | Qt.WindowState.WindowActive)
        self._memory_window.show()
        self._memory_window.raise_()
        self._memory_window.activateWindow()

    def _on_reminder_due(self, task: Task, reminder: Reminder) -> None:
        existing = self._reminder_bubbles.get(reminder.id)
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return

        bubble = ReminderBubble(task, reminder)
        bubble.view_requested.connect(self._view_reminder_task)
        bubble.close_requested.connect(self._acknowledge_reminder)
        bubble.complete_requested.connect(self._complete_reminder_task)
        bubble.snooze_requested.connect(self._snooze_reminder)
        bubble.dismissed.connect(self._reminder_bubble_finished)
        self._reminder_bubbles[reminder.id] = bubble
        self._wander.cancel()
        self._state_controller.set_walking(False)
        self._state_controller.set_reminding(True)
        # Unread reminder temporarily takes priority over fullscreen yield.
        self._apply_effective_topmost()
        bubble.adjustSize()
        self._position_bubble(bubble)
        bubble.show()
        bubble.raise_()

    def _view_reminder_task(self, task_id: str, reminder_id: str) -> None:
        self._open_todo_window(task_id)
        self._close_bubble(reminder_id)

    def _acknowledge_reminder(self, task_id: str, reminder_id: str) -> None:
        try:
            self._todo_manager.acknowledge_reminder(task_id, reminder_id)
        except TodoStorageError as exc:
            QMessageBox.critical(None, "提醒保存失败", str(exc))
            return
        if self._todo_window is not None:
            self._todo_window.refresh_tasks()
        self._close_bubble(reminder_id)

    def _complete_reminder_task(self, task_id: str, reminder_id: str) -> None:
        try:
            self._todo_manager.complete_task(
                task_id,
                reminder_id,
                now=self._reminder_manager.now(),
            )
        except TodoStorageError as exc:
            QMessageBox.critical(None, "待办保存失败", str(exc))
            return
        if self._todo_window is not None:
            self._todo_window.refresh_tasks()
        self._close_bubble(reminder_id)

    def _snooze_reminder(
        self,
        task_id: str,
        reminder_id: str,
        minutes: int,
    ) -> None:
        try:
            self._todo_manager.snooze_reminder(
                task_id,
                reminder_id,
                minutes,
                now=self._reminder_manager.now(),
            )
        except (TaskDataError, TodoStorageError) as exc:
            QMessageBox.critical(None, "提醒保存失败", str(exc))
            return
        if self._todo_window is not None:
            self._todo_window.refresh_tasks()
        self._close_bubble(reminder_id)

    def _close_bubble(self, reminder_id: str) -> None:
        bubble = self._reminder_bubbles.get(reminder_id)
        if bubble is not None:
            bubble.close()

    def _close_invalidated_bubble(self, reminder_id: str) -> None:
        self._close_bubble(reminder_id)

    def _reminder_bubble_finished(self, reminder_id: str) -> None:
        bubble = self._reminder_bubbles.pop(reminder_id, None)
        self._reminder_manager.reminder_finished(reminder_id)
        self._state_controller.set_reminding(bool(self._reminder_bubbles))
        self._apply_effective_topmost()
        if bubble is not None:
            bubble.deleteLater()

    def _position_bubble(self, bubble: ReminderBubble) -> None:
        screen = QApplication.screenAt(self.geometry().center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        bubble.adjustSize()
        gap = 12
        left_x = self.x() - bubble.width() - gap
        right_x = self.x() + self.width() + gap
        x = left_x if left_x >= area.left() else right_x
        max_x = area.right() - bubble.width() + 1
        x = max(area.left(), min(x, max_x))
        centered_y = self.y() + (self.height() - bubble.height()) // 2
        max_y = area.bottom() - bubble.height() + 1
        y = max(area.top(), min(centered_y, max_y))
        bubble.move(x, y)

    @staticmethod
    def _on_reminder_scan_failed(message: str) -> None:
        print(f"[提醒扫描失败] {message}", file=sys.stderr)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = self._create_context_menu()
        menu.exec(event.globalPos())
        event.accept()

    def _create_context_menu(self) -> QMenu:
        menu = QMenu(self)
        todo_action = QAction("打开待办", menu)
        todo_action.triggered.connect(lambda _checked=False: self._open_todo_window())
        menu.addAction(todo_action)
        chat_action = QAction("AI聊天", menu)
        chat_action.triggered.connect(lambda _checked=False: self._open_chat_window())
        menu.addAction(chat_action)
        profile_action = QAction("桌宠设置", menu)
        profile_action.triggered.connect(
            lambda _checked=False: self._open_profile_window()
        )
        menu.addAction(profile_action)
        memory_action = QAction("记忆管理", menu)
        memory_action.triggered.connect(
            lambda _checked=False: self._open_memory_window()
        )
        menu.addAction(memory_action)
        menu.addSeparator()

        appearance_menu = menu.addMenu("外观与行为")
        chars_menu = appearance_menu.addMenu("角色形象")
        self._populate_character_menu(chars_menu)
        appearance_menu.addSeparator()

        wander_action = QAction("自主漫步", appearance_menu)
        wander_action.setCheckable(True)
        wander_action.setChecked(self._window_state.wandering_enabled)
        wander_action.triggered.connect(self.set_wandering_enabled)
        appearance_menu.addAction(wander_action)

        yield_action = QAction("全屏自动让位", appearance_menu)
        yield_action.setCheckable(True)
        yield_action.setChecked(self._window_state.fullscreen_yield_enabled)
        yield_action.triggered.connect(self.set_fullscreen_yield_enabled)
        appearance_menu.addAction(yield_action)

        topmost_action = QAction("始终置顶", menu)
        topmost_action.setCheckable(True)
        topmost_action.setChecked(self._window_state.always_on_top)
        topmost_action.triggered.connect(self.set_always_on_top)
        menu.addAction(topmost_action)

        hide_action = QAction("隐藏到托盘", menu)
        hide_action.triggered.connect(self.hide_to_tray)
        menu.addAction(hide_action)
        menu.addSeparator()

        exit_action = QAction("退出", menu)
        exit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(exit_action)
        return menu

    def set_waiting(self, active: bool) -> None:
        """Apply the centralized future-reminder wait state."""
        self._state_controller.set_waiting(active)

    def set_reminding(self, active: bool) -> None:
        """Reserved integration point for a future reminder manager."""
        self._state_controller.set_reminding(active)

    @property
    def always_on_top(self) -> bool:
        return self._window_state.always_on_top

    @property
    def wandering_enabled(self) -> bool:
        return self._window_state.wandering_enabled

    @property
    def fullscreen_yield_enabled(self) -> bool:
        return self._window_state.fullscreen_yield_enabled

    @property
    def fullscreen_yielded(self) -> bool:
        return self._fullscreen_yielded

    @property
    def motion_engine(self) -> PetMotionEngine:
        return self._motion

    @property
    def active_character_pack(self) -> CharacterPack:
        return self._animation_manager.character_pack

    @property
    def tray_icon(self) -> QSystemTrayIcon | None:
        return self._tray_icon

    @property
    def tray_menu(self) -> QMenu | None:
        return self._tray_menu

    @property
    def animation_state(self) -> str:
        return self._state_controller.current_state

    @property
    def current_animation(self) -> str | None:
        return self._animation_manager.current_animation

    @property
    def current_frame_index(self) -> int:
        return self._animation_manager.current_frame_index

    @property
    def todo_window(self) -> TodoWindow | None:
        return self._todo_window

    @property
    def chat_window(self) -> ChatWindow | None:
        return self._chat_window

    @property
    def profile_window(self) -> ProfileWindow | None:
        return self._profile_window

    @property
    def profile_service(self) -> ProfileService:
        return self._profile_service

    @property
    def memory_window(self) -> MemoryManagerWindow | None:
        return self._memory_window

    @property
    def memory_service(self) -> MemoryService | None:
        return self._memory_service

    @property
    def reminder_dialogs(self) -> tuple[ReminderBubble, ...]:
        """Phase 3/4 compatibility alias for the active reminder surface."""
        return tuple(self._reminder_bubbles.values())

    @property
    def reminder_bubbles(self) -> tuple[ReminderBubble, ...]:
        return tuple(self._reminder_bubbles.values())

    def closeEvent(self, event: QCloseEvent) -> None:
        self._motion_timer.stop()
        self._poke_clear_timer.stop()
        self._fullscreen_watcher.stop()
        self._animation_manager.stop()
        self._reminder_manager.stop()
        self._reminder_manager.clear_queue()
        if self._tray_icon is not None:
            self._tray_icon.hide()
        if self._todo_window is not None:
            self._todo_window.hide()
        if self._chat_window is not None:
            self._chat_window.shutdown()
        if self._profile_window is not None:
            self._profile_window.hide()
        if self._memory_window is not None:
            self._memory_window.hide()
        if self._memory_service is not None:
            self._memory_service.close()
        for bubble in list(self._reminder_bubbles.values()):
            bubble.close()
        self._reminder_bubbles.clear()
        super().closeEvent(event)
