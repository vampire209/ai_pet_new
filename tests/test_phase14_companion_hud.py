"""Phase 14 tests: dynamic-island status pill, crisp menus, companion bubble, poke coalescing, quick-ask, and window awareness."""

from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from ai.deepseek_client import DeepSeekClient
from companion_service import CompanionController, CompanionSession
from config import AppConfig
from models.task import Task
from pet_state import PetStateStore, PetWindowState
from pet_status_pill import PetStatusPill, compute_todo_status_snapshot
from pet_window import PetWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager
from window_observer import (
    ForegroundWindowObserver,
    classify_foreground_window,
    extract_bilibili_title,
)


class _FakeStreamClient:
    """Minimal DeepSeekClient-compatible test double that yields deterministic text."""

    is_configured = True

    def __init__(self, reply: str = "我在呢，随时听候差遣！") -> None:
        self.reply = reply
        self.calls: list[list[dict[str, str]]] = []

    def stream_chat(self, messages, stop_event=None):
        self.calls.append(list(messages))
        yield self.reply


class _UnconfiguredClient:
    is_configured = False

    def stream_chat(self, messages, stop_event=None):
        raise AssertionError("Should not invoke stream_chat when unconfigured")


class Phase14CompanionHudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.config = AppConfig.load()

    def _pump_until(self, predicate, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(0.015)
        self.app.processEvents()
        return bool(predicate())

    def test_01_context_menu_explicit_style_prevents_black_menu(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = TodoManager(Path(tmp) / "tasks.json")
            pet = PetWindow(self.config, manager, ReminderManager(manager))
            try:
                self.assertIn("QLabel#petWindow", pet.styleSheet())
                menu = pet._create_context_menu()
                self.assertIn("background-color", menu.styleSheet())
                self.assertIn("color:", menu.styleSheet())

                appearance_action = next(
                    a for a in menu.actions() if a.text() == "外观与行为"
                )
                sub = appearance_action.menu()
                self.assertIsNotNone(sub)
                self.assertIn("background-color", sub.styleSheet())
                sub_texts = [a.text() for a in sub.actions() if a.text()]
                self.assertIn("显示灵动胶囊", sub_texts)
                self.assertIn("主动陪聊气泡", sub_texts)
                self.assertIn("前台窗口感知", sub_texts)

                self.assertIsNotNone(pet.tray_menu)
                self.assertIn("background-color", pet.tray_menu.styleSheet())
            finally:
                pet.close()

    def test_02_status_pill_snapshot_progress_countdown_and_reminder(self) -> None:
        now = datetime(2026, 11, 28, 10, 0, 0)
        empty = compute_todo_status_snapshot([], now=now, pet_name="克劳德娘")
        self.assertEqual(empty.progress_ratio, 1.0)
        self.assertEqual(empty.progress_label, "100%")
        self.assertIn("待命中", empty.status_text)

        t_done = Task.create("买咖啡").mark_completed()
        t_soon = Task.create(
            "开产品评审会",
            event_at=(now + timedelta(minutes=25)).isoformat(timespec="seconds"),
        )
        snap = compute_todo_status_snapshot([t_done, t_soon], now=now)
        self.assertAlmostEqual(snap.progress_ratio, 0.5)
        self.assertEqual(snap.progress_label, "50%")
        self.assertIn("25分后", snap.status_text)

        rem = compute_todo_status_snapshot(
            [t_done, t_soon], now=now, active_reminder_count=1
        )
        self.assertIn("提醒中", rem.status_text)

        busy = compute_todo_status_snapshot(
            [t_done, t_soon], now=now, activity_label="陪你看 B 站"
        )
        self.assertEqual(busy.status_text, "陪你看 B 站")

    def test_03_status_pill_widget_clicks_and_state_persistence(self) -> None:
        pill = PetStatusPill()
        try:
            self.assertTrue(
                bool(pill.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
            )
            self.assertTrue(
                pill.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            )
            hits: list[str] = []
            pill.gemini_requested.connect(lambda: hits.append("gemini"))
            pill.panel_requested.connect(lambda: hits.append("panel"))
            pill.quick_ask_requested.connect(lambda: hits.append("ask"))

            for rect in (pill._left_rect, pill._center_rect, pill._right_rect):
                center = QPointF(rect.center())
                press_ev = QMouseEvent(
                    QMouseEvent.Type.MouseButtonPress,
                    center,
                    center,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                release_ev = QMouseEvent(
                    QMouseEvent.Type.MouseButtonRelease,
                    center,
                    center,
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                pill.mousePressEvent(press_ev)
                pill.mouseReleaseEvent(release_ev)
            self.assertEqual(hits, ["gemini", "panel", "ask"])
        finally:
            pill.close()

        with tempfile.TemporaryDirectory() as tmp:
            store = PetStateStore(Path(tmp) / "pet_state.json")
            saved = PetWindowState(
                status_pill_enabled=False,
                companion_bubble_enabled=False,
                window_awareness_enabled=False,
            )
            self.assertTrue(store.save(saved))
            loaded = store.load()
            self.assertFalse(loaded.status_pill_enabled)
            self.assertFalse(loaded.companion_bubble_enabled)
            self.assertFalse(loaded.window_awareness_enabled)

    def test_04_companion_session_30_turn_cap_and_isolation(self) -> None:
        session = CompanionSession(max_turns=30)
        for i in range(45):
            session.append_turn(f"用户消息 {i}", f"桌宠回复 {i}")
        self.assertEqual(len(session), 30)
        msgs = session.messages()
        self.assertEqual(len(msgs), 60)
        self.assertEqual(msgs[0]["content"], "用户消息 15")
        self.assertEqual(msgs[-1]["content"], "桌宠回复 44")

    def test_05_poke_coalescer_merges_rapid_taps_and_fallbacks_cleanly(self) -> None:
        replies = []
        controller = CompanionController(
            client_factory=lambda: _UnconfiguredClient(),  # type: ignore[return-value]
            poke_window_ms=80,
        )
        try:
            controller.reply_ready.connect(replies.append)
            controller.handle_poke()
            controller.handle_poke()
            controller.handle_poke()
            self.assertTrue(self._pump_until(lambda: len(replies) == 1, timeout_s=1.0))
            self.assertEqual(len(replies), 1)
            self.assertEqual(replies[0].kind, "poke")
            self.assertFalse(replies[0].used_llm)
            self.assertIn("3", replies[0].text)
        finally:
            controller.shutdown()

    def test_06_quick_ask_streams_llm_reply_into_companion_bubble(self) -> None:
        fake = _FakeStreamClient("今天状态不错，记得早点休息哦~")
        controller = CompanionController(
            client_factory=lambda: fake,  # type: ignore[return-value]
            poke_window_ms=60,
        )
        with tempfile.TemporaryDirectory() as tmp:
            manager = TodoManager(Path(tmp) / "tasks.json")
            pet = PetWindow(
                self.config,
                manager,
                ReminderManager(manager),
                companion_controller=controller,
            )
            try:
                pet.show()
                pet.open_quick_ask()
                self.assertTrue(pet.quick_ask_popup.isVisible())
                pet.quick_ask_popup.input_edit.setText("今晚适合干嘛？")
                pet.quick_ask_popup._submit()
                self.assertFalse(pet.quick_ask_popup.isVisible())
                self.assertTrue(
                    self._pump_until(
                        lambda: pet.companion_bubble.isVisible()
                        and "早点休息" in pet.companion_bubble.text,
                        timeout_s=2.0,
                    )
                )
                self.assertEqual(len(fake.calls), 1)
                self.assertEqual(len(controller.session), 1)
            finally:
                pet.close()

    def test_07_foreground_window_classifier_bilibili_and_dwell_cooldown(self) -> None:
        self.assertIsNone(classify_foreground_window("explorer.exe", "桌面"))
        self.assertIsNone(classify_foreground_window("python.exe", "AI 桌宠"))

        code_info = classify_foreground_window("Code.exe", "pet_window.py - Visual Studio Code")
        self.assertIsNotNone(code_info)
        self.assertEqual(code_info.category, "coding")

        bili_info = classify_foreground_window(
            "msedge.exe",
            "【4K】超可爱桌宠制作教程_哔哩哔哩_bilibili - Microsoft Edge",
        )
        self.assertIsNotNone(bili_info)
        self.assertTrue(bili_info.is_bilibili)
        self.assertEqual(bili_info.category, "bilibili")
        self.assertIn("超可爱桌宠制作教程", bili_info.media_title)
        self.assertEqual(extract_bilibili_title("哔哩哔哩 (゜-゜)つロ 干杯~-bilibili"), "哔哩哔哩 (゜-゜)つロ 干杯~")

        samples = [
            (101, "Code.exe", "main.py - Visual Studio Code"),
            (101, "Code.exe", "main.py - Visual Studio Code"),
            (101, "Code.exe", "main.py - Visual Studio Code"),
            (102, "msedge.exe", "测试视频 - 哔哩哔哩 - Microsoft Edge"),
            (102, "msedge.exe", "测试视频 - 哔哩哔哩 - Microsoft Edge"),
        ]
        idx = {"i": 0}

        def provider():
            val = samples[min(idx["i"], len(samples) - 1)]
            idx["i"] += 1
            return val

        noticed = []
        observer = ForegroundWindowObserver(
            provider=provider,
            dwell_seconds=10.0,
            cooldown_seconds=60.0,
        )
        observer.context_noticed.connect(noticed.append)

        # t=0: first sight -> dwell not met
        self.assertIsNone(observer.poll_once(now=100.0))
        # t=12: dwell met -> triggers
        self.assertIsNotNone(observer.poll_once(now=112.0))
        self.assertEqual(len(noticed), 1)
        # t=25: same context -> deduped
        self.assertIsNone(observer.poll_once(now=125.0))
        # t=30: switch to Bilibili -> dwell starts
        self.assertIsNone(observer.poll_once(now=130.0))
        # t=45: dwell met, but cooldown (60s since t=112) still active -> suppressed
        self.assertIsNone(observer.poll_once(now=145.0))
        # t=180: cooldown expired -> triggers
        idx["i"] = 4
        self.assertIsNotNone(observer.poll_once(now=180.0))
        self.assertEqual(len(noticed), 2)
        self.assertTrue(noticed[1].is_bilibili)

    def test_08_pet_window_phase14_lifecycle_and_overlay_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PetStateStore(Path(tmp) / "pet_state.json")
            manager = TodoManager(Path(tmp) / "tasks.json")
            pet = PetWindow(
                self.config,
                manager,
                ReminderManager(manager),
                state_store=store,
                enable_fullscreen_watcher=False,
                enable_window_observer=False,
            )
            try:
                pet.show()
                self.app.processEvents()
                self.assertTrue(pet.status_pill.isVisible())

                # Adding a task immediately refreshes the pill via TodoManager subscription
                manager.add_task("写完 Phase 14 测试")
                self.app.processEvents()
                self.assertIn("剩1项", pet.status_pill.snapshot.status_text)

                # Hide to tray hides pill too; show restores it
                pet.hide_to_tray()
                self.app.processEvents()
                self.assertFalse(pet.status_pill.isVisible())
                pet.show_from_tray()
                self.app.processEvents()
                self.assertTrue(pet.status_pill.isVisible())

                # Toggle off persists and hides pill
                pet.set_status_pill_enabled(False)
                self.app.processEvents()
                self.assertFalse(pet.status_pill.isVisible())
                self.assertFalse(store.load().status_pill_enabled)
            finally:
                pet.close()

    def test_09_gemini_shortcut_and_custom_url_from_panel(self) -> None:
        opened: list[str] = []
        with tempfile.TemporaryDirectory() as tmp:
            store = PetStateStore(Path(tmp) / "pet_state.json")
            manager = TodoManager(Path(tmp) / "tasks.json")
            pet = PetWindow(
                self.config,
                manager,
                ReminderManager(manager),
                state_store=store,
                enable_fullscreen_watcher=False,
                enable_window_observer=False,
                url_opener=lambda url: opened.append(url) or True,
            )
            try:
                pet.show()
                pet.status_pill.gemini_requested.emit()
                self.assertEqual(opened, ["https://gemini.google.com/"])

                pet.open_control_panel("settings")
                pet.control_panel.select_settings_tab("gemini")
                pet.control_panel._gemini_url_edit.setText("https://aistudio.google.com/")
                pet.control_panel._apply_settings_clicked()
                self.app.processEvents()
                self.assertEqual(pet.gemini_url, "https://aistudio.google.com/")
                self.assertEqual(store.load().gemini_url, "https://aistudio.google.com/")

                pet.control_panel._gemini_btn.click()
                self.assertEqual(opened[-1], "https://aistudio.google.com/")
            finally:
                pet.close()

    def test_10_quick_ask_and_control_panel_follow_pet_and_detach_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PetStateStore(Path(tmp) / "pet_state.json")
            manager = TodoManager(Path(tmp) / "tasks.json")
            pet = PetWindow(
                self.config,
                manager,
                ReminderManager(manager),
                state_store=store,
                enable_fullscreen_watcher=False,
                enable_window_observer=False,
            )
            try:
                pet.show()
                pet.move(320, 420)
                pet._reposition_overlays()
                pet.open_quick_ask()
                pet.open_control_panel("chat")
                self.app.processEvents()

                ask_before = pet.quick_ask_popup.pos()
                panel_before = pet.control_panel.pos()

                # Attached mode: moving pet moves quick-ask & control panel together
                pet.move(460, 420)
                pet._reposition_overlays()
                self.app.processEvents()
                self.assertNotEqual(pet.quick_ask_popup.pos().x(), ask_before.x())
                self.assertNotEqual(pet.control_panel.pos().x(), panel_before.x())

                # Detached mode: moving pet keeps detached pill & panel fixed
                pet.set_panel_detached(True)
                self.app.processEvents()
                pill_detached_pos = pet.status_pill.pos()
                panel_detached_pos = pet.control_panel.pos()

                pet.move(260, 420)
                pet._reposition_overlays()
                self.app.processEvents()
                self.assertEqual(pet.status_pill.pos(), pill_detached_pos)
                self.assertEqual(pet.control_panel.pos(), panel_detached_pos)
            finally:
                pet.close()

    def test_11_claude_control_panel_todo_and_docked_chat(self) -> None:
        fake = _FakeStreamClient("收到！我已经记在羊皮纸小本本上啦~")
        controller = CompanionController(
            client_factory=lambda: fake,  # type: ignore[return-value]
            poke_window_ms=50,
        )
        with tempfile.TemporaryDirectory() as tmp:
            store = PetStateStore(Path(tmp) / "pet_state.json")
            manager = TodoManager(Path(tmp) / "tasks.json")
            task = manager.add_task("测试面板勾选待办")
            pet = PetWindow(
                self.config,
                manager,
                ReminderManager(manager),
                state_store=store,
                companion_controller=controller,
                enable_fullscreen_watcher=False,
                enable_window_observer=False,
            )
            try:
                pet.show()
                pet.open_control_panel("status")
                self.app.processEvents()
                self.assertTrue(pet.control_panel.isVisible())

                # Complete task from panel signal
                pet.control_panel.complete_task_requested.emit(task.id)
                self.app.processEvents()
                self.assertTrue(manager.get_task(task.id).completed)

                # Docked quick chat inside Claude Control Panel
                pet.control_panel.select_page("chat")
                pet.control_panel._chat_input.setText("帮我打个气")
                pet.control_panel._submit_panel_chat()
                self.assertTrue(
                    self._pump_until(lambda: len(controller.session) == 1, timeout_s=2.0)
                )
                self.assertEqual(len(fake.calls), 1)
            finally:
                pet.close()

    def test_12_mouse_through_character_hide_and_tray_recover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = PetStateStore(Path(tmp) / "pet_state.json")
            manager = TodoManager(Path(tmp) / "tasks.json")
            pet = PetWindow(
                self.config,
                manager,
                ReminderManager(manager),
                state_store=store,
                enable_fullscreen_watcher=False,
                enable_window_observer=False,
            )
            try:
                pet.show()
                self.app.processEvents()
                pet.set_mouse_through(True)
                self.assertTrue(pet.mouse_through)
                self.assertTrue(
                    bool(pet.windowFlags() & Qt.WindowType.WindowTransparentForInput)
                )

                pet.set_character_visible(False)
                self.app.processEvents()
                self.assertFalse(pet.isVisible())
                self.assertTrue(pet.status_pill.isVisible())

                # One-click tray recovery restores character and disables click-through
                pet.recover_desktop_components()
                self.app.processEvents()
                self.assertTrue(pet.isVisible())
                self.assertFalse(pet.mouse_through)
                self.assertFalse(
                    bool(pet.windowFlags() & Qt.WindowType.WindowTransparentForInput)
                )
            finally:
                pet.close()


if __name__ == "__main__":
    unittest.main()
