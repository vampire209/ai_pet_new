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
from app_paths import characters_dir, user_data_dir
from character_pack import (
    CharacterPack,
    DEFAULT_PACK_ID,
    discover_character_packs,
)
from chat_window import ChatWindow
from claude_control_panel import ClaudeControlPanel
from companion_bubble import (
    CompanionBubble,
    QuickAskPopup,
    apply_context_menu_style,
)
from companion_service import CompanionController, CompanionReply
from config import AppConfig
from memory.memory_service import MemoryService
from memory_window import MemoryManagerWindow
from models.reminder import Reminder
from models.task import Task, TaskDataError
from pet_motion import PetMotionEngine, WanderController
from pet_state import DEFAULT_GEMINI_URL, PetStateStore, PetWindowState
from pet_status_pill import PetStatusPill, compute_todo_status_snapshot
from profile_window import ProfileWindow
from reminder_bubble import ReminderBubble
from reminder_manager import ReminderManager
from todo_manager import TodoManager, TodoStorageError
from todo_window import TodoWindow
from ui_resources import load_application_icon
from video_yield import FullscreenYieldWatcher
from voice.settings_store import VoiceSettingsStore
from window_observer import ForegroundWindowInfo, ForegroundWindowObserver


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
        companion_controller: CompanionController | None = None,
        enable_window_observer: bool | None = None,
        url_opener=None,
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
        self._url_opener = url_opener
        self._desktop_hidden = False
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
        self._tray_pill_action: QAction | None = None
        self._tray_bubble_action: QAction | None = None
        self._tray_aware_action: QAction | None = None
        self._tray_detach_action: QAction | None = None
        self._tray_char_action: QAction | None = None
        self._tray_through_action: QAction | None = None
        self._tray_lock_action: QAction | None = None
        self._reminder_bubbles: dict[str, ReminderBubble] = {}
        self._transient_activity: str = ""
        self._press_global: QPoint | None = None
        self._press_time: float = 0.0
        self._last_drag_global: QPoint | None = None
        self._last_drag_time: float = 0.0
        self._drag_offset = QPoint()
        self._dragging = False
        self._screen_signal_connected = False
        self._current_pixmap: QPixmap = QPixmap()
        self._missing_asset_text: str = ""

        # Phase 14/15 overlays, unified Claude Control Panel & companion engine
        self._status_pill = PetStatusPill()
        self._status_pill.gemini_requested.connect(self.open_gemini)
        self._status_pill.panel_requested.connect(
            lambda: self.toggle_control_panel("status")
        )
        self._status_pill.quick_ask_requested.connect(self.open_quick_ask)
        self._status_pill.drag_moved.connect(self._on_pill_drag_moved)
        self._status_pill.drag_finished.connect(self._on_pill_drag_finished)

        self._control_panel = ClaudeControlPanel(
            ai_settings_service=self._ai_settings_service,
        )
        self._control_panel.open_gemini_requested.connect(self.open_gemini)
        self._control_panel.open_todo_requested.connect(self._open_todo_window)
        self._control_panel.open_memory_requested.connect(self._open_memory_window)
        self._control_panel.open_full_chat_requested.connect(self._open_chat_window)
        self._control_panel.open_profile_requested.connect(self._open_profile_window)
        self._control_panel.open_characters_dir_requested.connect(
            self._open_characters_folder
        )
        self._control_panel.open_data_dir_requested.connect(self._open_user_data_folder)
        self._control_panel.reset_position_requested.connect(self.reset_position)
        self._control_panel.recover_requested.connect(self.recover_desktop_components)
        self._control_panel.clear_companion_session_requested.connect(
            self._clear_companion_session
        )
        self._control_panel.quick_chat_submitted.connect(self._on_quick_ask_submitted)
        self._control_panel.complete_task_requested.connect(
            self._complete_task_from_panel
        )
        self._control_panel.state_applied.connect(self.apply_panel_state)
        self._control_panel.topmost_toggled.connect(self.set_always_on_top)
        self._control_panel.detach_toggled.connect(self.set_panel_detached)
        self._control_panel.drag_moved.connect(self._on_panel_drag_moved)
        self._control_panel.drag_finished.connect(self._on_panel_drag_finished)

        self._companion_bubble = CompanionBubble()
        self._companion_bubble.open_chat_requested.connect(self._open_chat_window)

        self._quick_ask_popup = QuickAskPopup()
        self._quick_ask_popup.question_submitted.connect(self._on_quick_ask_submitted)

        self._companion_controller = companion_controller or CompanionController(
            profile_service=self._profile_service,
            ai_settings_service=self._ai_settings_service,
            parent=self,
        )
        self._companion_controller.set_enabled(
            self._window_state.companion_bubble_enabled
        )
        self._companion_controller.reply_ready.connect(self._on_companion_reply)
        self._companion_controller.activity_changed.connect(
            self._on_companion_activity_changed
        )
        self.pet_poked.connect(self._companion_controller.handle_poke)
        self._profile_service.profile_changed.connect(
            lambda _profile: self._refresh_status_pill()
        )

        self._pill_refresh_timer = QTimer(self)
        self._pill_refresh_timer.setInterval(30_000)
        self._pill_refresh_timer.timeout.connect(self._refresh_status_pill)
        self._todo_manager.subscribe(self._refresh_status_pill)

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

        # Respectful foreground window awareness (active in live runs or when requested)
        use_observer = (
            enable_window_observer
            if enable_window_observer is not None
            else (state_store is not None and sys.platform == "win32")
        )
        self._window_observer = ForegroundWindowObserver(parent=self)
        self._window_observer.set_enabled(
            self._window_state.window_awareness_enabled
            and self._window_state.companion_bubble_enabled
        )
        self._window_observer.context_noticed.connect(
            self._on_foreground_context_noticed
        )
        if use_observer:
            self._window_observer.start()

        self._refresh_status_pill()
        self._pill_refresh_timer.start()
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

    def _open_user_data_folder(self) -> None:
        target = user_data_dir()
        target.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def open_gemini(self, url: str | None = None) -> bool:
        """Open Gemini web interface (replaces White Dragon's GPT/Codex shortcut)."""
        target = (url or self._window_state.gemini_url or DEFAULT_GEMINI_URL).strip()
        if not (target.startswith("https://") or target.startswith("http://")):
            target = DEFAULT_GEMINI_URL
        if self._url_opener is not None:
            return bool(self._url_opener(target))
        return bool(QDesktopServices.openUrl(QUrl(target)))

    def _setup_window(self) -> None:
        self.setObjectName("petWindow")
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self._effective_topmost():
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if self._window_state.mouse_through:
            flags |= Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAutoFillBackground(False)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Scope transparent rule strictly to the pet QLabel so child QMenus never turn black on Win11.
        self.setStyleSheet("QLabel#petWindow { background: transparent; }")
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
        self._status_pill.set_topmost(target)
        self._control_panel.set_topmost(target)
        self._companion_bubble.set_topmost(target)
        self._quick_ask_popup.set_topmost(target)
        if hasattr(self, "_window_observer"):
            self._update_observer_suppression()
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
            self._reposition_overlays()

    def _apply_mouse_through_flag(self) -> None:
        enabled = self._window_state.mouse_through
        self._status_pill.set_mouse_through(enabled and not self._window_state.panel_detached)
        current = bool(self.windowFlags() & Qt.WindowType.WindowTransparentForInput)
        if enabled == current:
            return
        was_visible = self.isVisible()
        position = self.pos()
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, enabled)
        if was_visible:
            self.show()
            self.move(position)

    def _on_fullscreen_changed(self, is_fullscreen: bool) -> None:
        self._fullscreen_yielded = bool(
            is_fullscreen and self._window_state.fullscreen_yield_enabled
        )
        self._apply_effective_topmost()

    def _setup_tray(self) -> None:
        menu = QMenu()
        recover_action = QAction("找回组件并关闭穿透", menu)
        recover_action.triggered.connect(self.recover_desktop_components)
        menu.addAction(recover_action)

        toggle_action = QAction("隐藏桌宠", menu)
        toggle_action.triggered.connect(self.toggle_pet_visibility)
        menu.addAction(toggle_action)

        panel_action = QAction("打开控制面板", menu)
        panel_action.triggered.connect(
            lambda _checked=False: self.open_control_panel("status")
        )
        menu.addAction(panel_action)

        gemini_action = QAction("打开 Gemini", menu)
        gemini_action.triggered.connect(lambda _checked=False: self.open_gemini())
        menu.addAction(gemini_action)
        menu.addSeparator()

        topmost_action = QAction("始终置顶", menu)
        topmost_action.setCheckable(True)
        topmost_action.setChecked(self._window_state.always_on_top)
        topmost_action.triggered.connect(self.set_always_on_top)
        menu.addAction(topmost_action)

        detach_action = QAction("面板与人物分离", menu)
        detach_action.setCheckable(True)
        detach_action.setChecked(self._window_state.panel_detached)
        detach_action.triggered.connect(self.set_panel_detached)
        menu.addAction(detach_action)

        char_action = QAction("显示人物", menu)
        char_action.setCheckable(True)
        char_action.setChecked(self._window_state.character_visible)
        char_action.triggered.connect(self.set_character_visible)
        menu.addAction(char_action)

        through_action = QAction("鼠标穿透（从托盘恢复）", menu)
        through_action.setCheckable(True)
        through_action.setChecked(self._window_state.mouse_through)
        through_action.triggered.connect(self.set_mouse_through)
        menu.addAction(through_action)

        lock_action = QAction("锁定人物位置", menu)
        lock_action.setCheckable(True)
        lock_action.setChecked(self._window_state.position_locked)
        lock_action.triggered.connect(self.set_position_locked)
        menu.addAction(lock_action)

        pill_action = QAction("显示灵动胶囊", menu)
        pill_action.setCheckable(True)
        pill_action.setChecked(self._window_state.status_pill_enabled)
        pill_action.triggered.connect(self.set_status_pill_enabled)
        menu.addAction(pill_action)

        bubble_action = QAction("主动陪聊气泡", menu)
        bubble_action.setCheckable(True)
        bubble_action.setChecked(self._window_state.companion_bubble_enabled)
        bubble_action.triggered.connect(self.set_companion_bubble_enabled)
        menu.addAction(bubble_action)

        aware_action = QAction("前台窗口感知", menu)
        aware_action.setCheckable(True)
        aware_action.setChecked(self._window_state.window_awareness_enabled)
        aware_action.triggered.connect(self.set_window_awareness_enabled)
        menu.addAction(aware_action)

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
        ask_action = QAction("问她一句", menu)
        ask_action.triggered.connect(lambda _checked=False: self.open_quick_ask())
        menu.addAction(ask_action)
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

        apply_context_menu_style(menu)
        self._tray_menu = menu
        self._tray_toggle_action = toggle_action
        self._tray_topmost_action = topmost_action
        self._tray_detach_action = detach_action
        self._tray_char_action = char_action
        self._tray_through_action = through_action
        self._tray_lock_action = lock_action
        self._tray_pill_action = pill_action
        self._tray_bubble_action = bubble_action
        self._tray_aware_action = aware_action
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
        apply_context_menu_style(menu)

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
                "显示桌宠" if self._desktop_hidden else "隐藏桌宠"
            )
        if self._tray_topmost_action is not None:
            self._tray_topmost_action.setChecked(self._window_state.always_on_top)
        if self._tray_detach_action is not None:
            self._tray_detach_action.setChecked(self._window_state.panel_detached)
        if self._tray_char_action is not None:
            self._tray_char_action.setChecked(self._window_state.character_visible)
        if self._tray_through_action is not None:
            self._tray_through_action.setChecked(self._window_state.mouse_through)
        if self._tray_lock_action is not None:
            self._tray_lock_action.setChecked(self._window_state.position_locked)
        if self._tray_pill_action is not None:
            self._tray_pill_action.setChecked(self._window_state.status_pill_enabled)
        if self._tray_bubble_action is not None:
            self._tray_bubble_action.setChecked(
                self._window_state.companion_bubble_enabled
            )
        if self._tray_aware_action is not None:
            self._tray_aware_action.setChecked(
                self._window_state.window_awareness_enabled
            )
        if self._tray_wander_action is not None:
            self._tray_wander_action.setChecked(self._window_state.wandering_enabled)
        if self._tray_yield_action is not None:
            self._tray_yield_action.setChecked(
                self._window_state.fullscreen_yield_enabled
            )

    def _save_window_state(self) -> None:
        if self._state_store is not None:
            self._state_store.save(self._window_state)

    def _screen_available_area(self) -> QRect | None:
        screen = (
            QApplication.screenAt(self.geometry().center())
            or QApplication.primaryScreen()
        )
        return screen.availableGeometry() if screen is not None else None

    def _reposition_overlays(self) -> None:
        pet_rect = self.geometry()
        area = self._screen_available_area()
        if self._window_state.panel_detached:
            if (
                self._window_state.panel_x is not None
                and self._window_state.panel_y is not None
            ):
                px, py = self._window_state.panel_x, self._window_state.panel_y
                if area is not None:
                    px = max(area.left() + 4, min(px, area.right() - self._status_pill.width() - 4))
                    py = max(area.top() + 4, min(py, area.bottom() - self._status_pill.height() - 4))
                self._status_pill.move(px, py)
            else:
                self._status_pill.reposition_above(pet_rect, area)
            if self._control_panel.isVisible():
                self._control_panel.position_near(self._status_pill.geometry(), area)
        else:
            self._status_pill.reposition_above(pet_rect, area)
            if self._control_panel.isVisible():
                self._control_panel.position_near(pet_rect, area)

        # Quick-ask box and companion bubble ALWAYS move smoothly with Claude-chan!
        if self._quick_ask_popup.isVisible():
            anchor = (
                self._status_pill.geometry()
                if self._status_pill.isVisible() and not self._window_state.panel_detached
                else pet_rect
            )
            self._quick_ask_popup.position_near(anchor, area)
        if self._companion_bubble.isVisible():
            self._companion_bubble.position_near(pet_rect, area)

    def _sync_status_pill_visibility(self) -> None:
        should_show = (
            not self._desktop_hidden
            and self._window_state.status_pill_enabled
            and (self.isVisible() or not self._window_state.character_visible)
        )
        if should_show:
            self._reposition_overlays()
            if not self._status_pill.isVisible():
                self._status_pill.show()
                self._status_pill.raise_()
        else:
            self._status_pill.hide()

    def _memory_count(self) -> int | None:
        if self._memory_service is None:
            return None
        try:
            return len(self._memory_service.list_memories())
        except Exception:
            return None

    def _sync_control_panel_data(self) -> None:
        pet_name = self._profile_service.profile.pet_name.strip() or "克劳德娘"
        self._control_panel.sync_from_pet(
            state=self._window_state,
            packs=self._character_packs,
            tasks=self._todo_manager.get_tasks(),
            pet_name=pet_name,
            memory_count=self._memory_count(),
            active_reminder_count=len(self._reminder_bubbles),
        )

    def _refresh_status_pill(self) -> None:
        pet_name = self._profile_service.profile.pet_name.strip() or "克劳德娘"
        snapshot = compute_todo_status_snapshot(
            self._todo_manager.get_tasks(),
            active_reminder_count=len(self._reminder_bubbles),
            activity_label=self._transient_activity or None,
            pet_name=pet_name,
        )
        self._status_pill.set_snapshot(snapshot)
        if self._control_panel.isVisible():
            self._sync_control_panel_data()
        if (self.isVisible() or not self._window_state.character_visible) and self._window_state.status_pill_enabled:
            self._reposition_overlays()

    def _update_observer_suppression(self) -> None:
        suppressed = (
            self._desktop_hidden
            or not self.isVisible()
            or self._fullscreen_yielded
            or bool(self._reminder_bubbles)
        )
        self._window_observer.set_suppressed(suppressed)

    def open_control_panel(self, page: str = "status") -> None:
        """Open the Claude-themed unified floating Control Panel on the requested tab."""
        if self._desktop_hidden:
            self.show_from_tray()
        self.refresh_character_packs()
        self._sync_control_panel_data()
        self._control_panel.sync_chat_history(self._companion_controller.session.messages())
        self._control_panel.select_page(page)
        anchor = (
            self._status_pill.geometry()
            if self._window_state.panel_detached and self._status_pill.isVisible()
            else self.geometry()
        )
        self._control_panel.position_near(anchor, self._screen_available_area())
        self._control_panel.show()
        self._control_panel.raise_()
        self._control_panel.activateWindow()

    def toggle_control_panel(self, page: str = "status") -> None:
        if self._control_panel.isVisible():
            self._control_panel.collapse_panel()
        else:
            self.open_control_panel(page)

    def _on_pill_drag_moved(self, dx: int, dy: int) -> None:
        if self._window_state.panel_detached:
            self._status_pill.move(self._status_pill.x() + dx, self._status_pill.y() + dy)
            if self._control_panel.isVisible():
                self._control_panel.position_near(
                    self._status_pill.geometry(), self._screen_available_area()
                )
        elif not self._window_state.position_locked:
            self._wander.cancel()
            self.move(self.x() + dx, self.y() + dy)
            self._reposition_overlays()

    def _on_pill_drag_finished(self) -> None:
        if self._window_state.panel_detached:
            self._window_state = replace(
                self._window_state,
                panel_x=self._status_pill.x(),
                panel_y=self._status_pill.y(),
            )
            self._save_window_state()
        elif not self._window_state.position_locked:
            self._window_state = replace(
                self._window_state,
                x=self.x(),
                y=self.y(),
            )
            self._save_window_state()

    def _on_panel_drag_moved(self, dx: int, dy: int) -> None:
        if self._window_state.panel_detached:
            self._control_panel.move(
                self._control_panel.x() + dx, self._control_panel.y() + dy
            )
            self._status_pill.move(
                self._status_pill.x() + dx, self._status_pill.y() + dy
            )
        elif not self._window_state.position_locked:
            self._wander.cancel()
            self.move(self.x() + dx, self.y() + dy)
            self._reposition_overlays()

    def _on_panel_drag_finished(self) -> None:
        self._on_pill_drag_finished()

    def _complete_task_from_panel(self, task_id: str) -> None:
        try:
            self._todo_manager.complete_task(
                task_id, now=self._reminder_manager.now()
            )
        except Exception:
            return
        if self._todo_window is not None:
            self._todo_window.refresh_tasks()
        self._refresh_status_pill()

    def _clear_companion_session(self) -> None:
        self._companion_controller.session.clear()

    def apply_panel_state(self, new_state: PetWindowState) -> None:
        """Apply all settings saved from ClaudeControlPanel atomically."""
        old_pack = self._window_state.character_pack
        old_detached = self._window_state.panel_detached
        if new_state.panel_detached and not old_detached and new_state.panel_x is None:
            new_state = replace(
                new_state,
                panel_x=self._status_pill.x(),
                panel_y=self._status_pill.y(),
            )
        self._window_state = new_state

        if new_state.character_pack != old_pack:
            pack = self._find_pack(new_state.character_pack)
            self._animation_manager.load_character_pack(pack)

        self._wander.set_enabled(
            new_state.wandering_enabled
            and not new_state.position_locked
            and (self._state_store is not None)
        )
        if not new_state.wandering_enabled or new_state.position_locked:
            self._motion.set_walking(False, time.perf_counter())
            self._state_controller.set_walking(False)

        self._fullscreen_watcher.set_enabled(new_state.fullscreen_yield_enabled)
        if not new_state.fullscreen_yield_enabled:
            self._fullscreen_yielded = False

        self._companion_controller.set_enabled(new_state.companion_bubble_enabled)
        self._window_observer.set_enabled(
            new_state.window_awareness_enabled and new_state.companion_bubble_enabled
        )
        if not new_state.companion_bubble_enabled:
            self._companion_bubble.dismiss()

        if not self._desktop_hidden:
            self.setVisible(new_state.character_visible)
        self._apply_effective_topmost()
        self._apply_mouse_through_flag()
        self._sync_status_pill_visibility()
        self._reposition_overlays()
        self._save_window_state()
        self._sync_tray_state()
        if self._control_panel.isVisible():
            self._sync_control_panel_data()

    def set_panel_detached(self, detached: bool) -> None:
        target = bool(detached)
        updated = replace(
            self._window_state,
            panel_detached=target,
            panel_x=self._status_pill.x() if target else self._window_state.panel_x,
            panel_y=self._status_pill.y() if target else self._window_state.panel_y,
        )
        self.apply_panel_state(updated)

    def set_character_visible(self, visible: bool) -> None:
        target = bool(visible)
        pill_enabled = self._window_state.status_pill_enabled or (not target)
        updated = replace(
            self._window_state,
            character_visible=target,
            status_pill_enabled=pill_enabled,
        )
        self.apply_panel_state(updated)

    def set_mouse_through(self, enabled: bool) -> None:
        updated = replace(self._window_state, mouse_through=bool(enabled))
        self.apply_panel_state(updated)

    def set_position_locked(self, locked: bool) -> None:
        updated = replace(self._window_state, position_locked=bool(locked))
        self.apply_panel_state(updated)

    def reset_position(self) -> None:
        """Move Claude-chan back to the primary screen bottom-right corner."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        x = max(available.x(), available.x() + available.width() - self.width() - self._config.margin_x)
        y = max(available.y(), available.y() + available.height() - self.height() - self._config.margin_y)
        self.move(x, y)
        self._window_state = replace(
            self._window_state,
            x=x,
            y=y,
            panel_x=None,
            panel_y=None,
        )
        self._reposition_overlays()
        self._save_window_state()

    def recover_desktop_components(self) -> None:
        """One-click safety recovery from tray: restore character, disable click-through, bring on screen."""
        self._desktop_hidden = False
        updated = replace(
            self._window_state,
            character_visible=True,
            mouse_through=False,
            status_pill_enabled=True,
        )
        self.apply_panel_state(updated)
        if not self._is_position_visible_on_any_screen(self.x(), self.y()):
            self.reset_position()
        self.show()
        self.raise_()
        self.activateWindow()
        self._sync_status_pill_visibility()
        self._sync_tray_state()

    def set_status_pill_enabled(self, enabled: bool) -> None:
        target = bool(enabled)
        if not target and not self._window_state.character_visible:
            return
        self._window_state = replace(self._window_state, status_pill_enabled=target)
        self._sync_status_pill_visibility()
        self._save_window_state()
        self._sync_tray_state()

    def set_companion_bubble_enabled(self, enabled: bool) -> None:
        target = bool(enabled)
        self._window_state = replace(
            self._window_state, companion_bubble_enabled=target
        )
        self._companion_controller.set_enabled(target)
        self._window_observer.set_enabled(
            target and self._window_state.window_awareness_enabled
        )
        if not target:
            self._companion_bubble.dismiss()
        self._save_window_state()
        self._sync_tray_state()

    def set_window_awareness_enabled(self, enabled: bool) -> None:
        target = bool(enabled)
        self._window_state = replace(
            self._window_state, window_awareness_enabled=target
        )
        self._window_observer.set_enabled(
            target and self._window_state.companion_bubble_enabled
        )
        self._save_window_state()
        self._sync_tray_state()

    def open_quick_ask(self) -> None:
        """Open the Claude-themed floating 1-line prompt box anchored near the pet."""
        if self._desktop_hidden:
            self.show_from_tray()
        anchor = (
            self._status_pill.geometry()
            if self._status_pill.isVisible() and not self._window_state.panel_detached
            else self.geometry()
        )
        self._quick_ask_popup.open_near(anchor, self._screen_available_area())

    def _on_quick_ask_submitted(self, question: str) -> None:
        self._companion_controller.ask_quick(question)

    def _on_foreground_context_noticed(self, info: ForegroundWindowInfo) -> None:
        if not self._window_state.companion_bubble_enabled or self._reminder_bubbles:
            return
        self._companion_controller.handle_foreground_context(info)

    def _on_companion_activity_changed(self, label: str) -> None:
        self._transient_activity = label.strip()
        self._refresh_status_pill()

    def _on_companion_reply(self, reply: CompanionReply) -> None:
        if self._control_panel.isVisible():
            self._control_panel.append_chat_message("assistant", reply.text)
            if self._control_panel._pages.currentIndex() == 1:
                return
        if self._desktop_hidden or not self.isVisible() or self._reminder_bubbles:
            return
        if reply.kind != "quick_ask" and not self._window_state.companion_bubble_enabled:
            return
        duration = 8000 if reply.kind == "quick_ask" else 6200
        self._companion_bubble.show_message(
            reply.text,
            speaker=reply.speaker,
            duration_ms=duration,
            show_chat_button=True,
        )
        self._companion_bubble.position_near(
            self.geometry(), self._screen_available_area()
        )

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
        self._desktop_hidden = True
        self._wander.cancel()
        self._motion.set_walking(False, time.perf_counter())
        self._state_controller.set_walking(False)
        self._status_pill.hide()
        self._control_panel.hide()
        self._companion_bubble.dismiss()
        self._quick_ask_popup.hide()
        self.hide()
        self._update_observer_suppression()
        self._sync_tray_state()

    def show_from_tray(self) -> None:
        self._desktop_hidden = False
        if self._window_state.character_visible:
            self.show()
            self.raise_()
            self.activateWindow()
        self._sync_status_pill_visibility()
        self._update_observer_suppression()
        self._sync_tray_state()

    def toggle_pet_visibility(self) -> None:
        if not self._desktop_hidden and (self.isVisible() or self._status_pill.isVisible()):
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
                and not self._window_state.position_locked
                and not self._reminder_bubbles
                and not self._quick_ask_popup.isVisible()
                and not (self._control_panel.isVisible() and not self._window_state.panel_detached)
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
                        self._reposition_overlays()
                else:
                    self._state_controller.set_walking(False)
                    if step.arrived and step.x != self.x():
                        self.move(step.x, self.y())
                        self._reposition_overlays()
                    if step.trigger_variant and self._animation_manager.character_pack.clips_for("variant"):
                        self._state_controller.trigger_oneshot("variant")

        # Disable artificial breathing scale on native 24fps animated clips so Qt renders
        # every frame 1:1 pixel-sharp without continuous non-integer resampling blur.
        self._motion.set_breathing_enabled(not self._animation_manager.is_streaming_high_fps)

        # Adaptive timer rate: 60fps during active motion, gentle 30fps breathing otherwise
        desired_interval = 16 if self._motion.needs_high_fps(now) else (
            120 if self._animation_manager.is_streaming_high_fps else 33
        )
        if self._motion_timer.interval() != desired_interval:
            self._motion_timer.setInterval(desired_interval)
        if (
            self.isVisible()
            and not self._current_pixmap.isNull()
            and (self._motion.needs_high_fps(now) or not self._animation_manager.is_streaming_high_fps)
        ):
            self.update()

    def _show_frame(self, pixmap: QPixmap) -> None:
        if pixmap.isNull():
            return
        self._missing_asset_text = ""
        self._current_pixmap = pixmap
        logical_size = pixmap.deviceIndependentSize().toSize()
        if not logical_size.isEmpty() and logical_size != self.size():
            self.setFixedSize(logical_size)
            if self.isVisible():
                self._reposition_overlays()
        self.update()

    def _show_missing_asset_message(self) -> None:
        self._current_pixmap = QPixmap()
        self._missing_asset_text = "桌宠 idle 素材缺失"
        self.setText(self._missing_asset_text)
        self.setStyleSheet(
            "QLabel#petWindow { background: rgba(40, 40, 40, 190); color: white; "
            "padding: 12px; border-radius: 8px; }"
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
        logical = self._current_pixmap.deviceIndependentSize()
        draw_x = int(round((rect.width() - logical.width()) * 0.5))
        draw_y = int(round(rect.height() - logical.height()))

        if not state.is_identity:
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

        painter.drawPixmap(QPoint(draw_x, draw_y), self._current_pixmap)
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
        self._refresh_status_pill()
        self._sync_status_pill_visibility()
        self._update_observer_suppression()
        self._sync_tray_state()

    def _on_screen_changed(self, screen: QScreen | None) -> None:
        if screen is not None:
            self._animation_manager.set_device_pixel_ratio(screen.devicePixelRatio())
        self._reposition_overlays()
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
        if self._window_state.position_locked:
            event.accept()
            return
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
        self._reposition_overlays()
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
        self._companion_bubble.dismiss()
        self._wander.cancel()
        self._state_controller.set_walking(False)
        self._state_controller.set_reminding(True)
        # Unread reminder temporarily takes priority over fullscreen yield.
        self._apply_effective_topmost()
        self._refresh_status_pill()
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
        self._refresh_status_pill()
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
        ask_action = QAction("问她一句", menu)
        ask_action.triggered.connect(lambda _checked=False: self.open_quick_ask())
        menu.addAction(ask_action)
        menu.addSeparator()

        appearance_menu = menu.addMenu("外观与行为")
        open_panel_action = QAction("克劳德控制面板…", appearance_menu)
        open_panel_action.triggered.connect(
            lambda _checked=False: self.open_control_panel("status")
        )
        appearance_menu.addAction(open_panel_action)

        open_gemini_action = QAction("打开 Gemini", appearance_menu)
        open_gemini_action.triggered.connect(lambda _checked=False: self.open_gemini())
        appearance_menu.addAction(open_gemini_action)
        appearance_menu.addSeparator()

        chars_menu = appearance_menu.addMenu("角色形象")
        self._populate_character_menu(chars_menu)
        appearance_menu.addSeparator()

        detach_action = QAction("面板与人物分离", appearance_menu)
        detach_action.setCheckable(True)
        detach_action.setChecked(self._window_state.panel_detached)
        detach_action.triggered.connect(self.set_panel_detached)
        appearance_menu.addAction(detach_action)

        char_vis_action = QAction("显示人物", appearance_menu)
        char_vis_action.setCheckable(True)
        char_vis_action.setChecked(self._window_state.character_visible)
        char_vis_action.triggered.connect(self.set_character_visible)
        appearance_menu.addAction(char_vis_action)

        pill_action = QAction("显示灵动胶囊", appearance_menu)
        pill_action.setCheckable(True)
        pill_action.setChecked(self._window_state.status_pill_enabled)
        pill_action.triggered.connect(self.set_status_pill_enabled)
        appearance_menu.addAction(pill_action)

        bubble_action = QAction("主动陪聊气泡", appearance_menu)
        bubble_action.setCheckable(True)
        bubble_action.setChecked(self._window_state.companion_bubble_enabled)
        bubble_action.triggered.connect(self.set_companion_bubble_enabled)
        appearance_menu.addAction(bubble_action)

        aware_action = QAction("前台窗口感知", appearance_menu)
        aware_action.setCheckable(True)
        aware_action.setChecked(self._window_state.window_awareness_enabled)
        aware_action.triggered.connect(self.set_window_awareness_enabled)
        appearance_menu.addAction(aware_action)
        appearance_menu.addSeparator()

        wander_action = QAction("自主漫步", appearance_menu)
        wander_action.setCheckable(True)
        wander_action.setChecked(self._window_state.wandering_enabled)
        wander_action.triggered.connect(self.set_wandering_enabled)
        appearance_menu.addAction(wander_action)

        lock_action = QAction("锁定人物位置", appearance_menu)
        lock_action.setCheckable(True)
        lock_action.setChecked(self._window_state.position_locked)
        lock_action.triggered.connect(self.set_position_locked)
        appearance_menu.addAction(lock_action)

        through_action = QAction("鼠标穿透（从托盘恢复）", appearance_menu)
        through_action.setCheckable(True)
        through_action.setChecked(self._window_state.mouse_through)
        through_action.triggered.connect(self.set_mouse_through)
        appearance_menu.addAction(through_action)

        yield_action = QAction("全屏自动让位", appearance_menu)
        yield_action.setCheckable(True)
        yield_action.setChecked(self._window_state.fullscreen_yield_enabled)
        yield_action.triggered.connect(self.set_fullscreen_yield_enabled)
        appearance_menu.addAction(yield_action)
        appearance_menu.addSeparator()

        reset_pos_action = QAction("回到屏幕右下角", appearance_menu)
        reset_pos_action.triggered.connect(self.reset_position)
        appearance_menu.addAction(reset_pos_action)

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
        apply_context_menu_style(menu)
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
    def status_pill_enabled(self) -> bool:
        return self._window_state.status_pill_enabled

    @property
    def companion_bubble_enabled(self) -> bool:
        return self._window_state.companion_bubble_enabled

    @property
    def window_awareness_enabled(self) -> bool:
        return self._window_state.window_awareness_enabled

    @property
    def status_pill(self) -> PetStatusPill:
        return self._status_pill

    @property
    def control_panel(self) -> ClaudeControlPanel:
        return self._control_panel

    @property
    def panel_detached(self) -> bool:
        return self._window_state.panel_detached

    @property
    def mouse_through(self) -> bool:
        return self._window_state.mouse_through

    @property
    def position_locked(self) -> bool:
        return self._window_state.position_locked

    @property
    def gemini_url(self) -> str:
        return self._window_state.gemini_url

    @property
    def companion_bubble(self) -> CompanionBubble:
        return self._companion_bubble

    @property
    def quick_ask_popup(self) -> QuickAskPopup:
        return self._quick_ask_popup

    @property
    def companion_controller(self) -> CompanionController:
        return self._companion_controller

    @property
    def window_observer(self) -> ForegroundWindowObserver:
        return self._window_observer

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
        self._pill_refresh_timer.stop()
        self._fullscreen_watcher.stop()
        self._window_observer.stop()
        self._companion_controller.shutdown()
        self._todo_manager.unsubscribe(self._refresh_status_pill)
        self._animation_manager.stop()
        self._reminder_manager.stop()
        self._reminder_manager.clear_queue()
        self._status_pill.close()
        self._control_panel.close()
        self._companion_bubble.dismiss()
        self._companion_bubble.close()
        self._quick_ask_popup.close()
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
