"""Phase 12 UX, unified storage, persistence, tray, and GUI API settings tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from ai.ai_config import DEFAULT_BASE_URL, DEFAULT_MODEL
from ai.ai_settings_service import (
    AiConnectionSettings,
    AiSettingsService,
    AiSettingsValidationError,
)
from ai.chat_history_store import ChatHistoryStore
from ai.chat_service import ChatService
from ai.chat_ui_config import ChatUiSettings, default_chat_ui_config_path
from ai.deepseek_client import DeepSeekClient
from ai.fake_client import FakeDeepSeekClient
from ai.profile import PetProfile
from ai.profile_service import ProfileService, default_profile_config_path
from chat_window import ChatWindow
from config import AppConfig
from memory.memory_store import default_memory_db_path
from pet_state import PetStateStore, PetWindowState
from pet_window import PetWindow
from profile_window import ProfileWindow
from todo_manager import TodoManager, default_tasks_data_path
from voice.fake_backends import FakeAudioPlayer, FakeTTSBackend
from voice.settings_store import VoiceSettingsStore
from voice.tts_service import TTSService
from voice.voice_models import VoiceSettings
from voice.voice_service import VoiceService


class Phase12UxPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase12-tests"])
        cls.config = AppConfig.load()

    def test_01_all_default_user_paths_share_one_directory(self) -> None:
        with patch(
            "app_paths.QStandardPaths.writableLocation",
            return_value=r"C:\Users\Demo\AppData\Local",
        ):
            expected_dir = Path(r"C:\Users\Demo\AppData\Local\ai_pet_new")
            self.assertEqual(default_tasks_data_path().parent, expected_dir)
            self.assertEqual(default_profile_config_path().parent, expected_dir)
            self.assertEqual(default_chat_ui_config_path().parent, expected_dir)
            self.assertEqual(default_memory_db_path().parent, expected_dir)

    def test_02_legacy_tasks_json_migrates_automatically_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy_path = root / "legacy" / "tasks.json"
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_text(
                json.dumps(
                    [{"id": "t1", "text": "旧目录里的待办", "completed": False}],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            new_path = root / "unified" / "tasks.json"
            with (
                patch("todo_manager.default_tasks_data_path", return_value=new_path),
                patch("todo_manager.legacy_tasks_data_path", return_value=legacy_path),
            ):
                manager = TodoManager()
            self.assertTrue(new_path.is_file())
            self.assertEqual([t.text for t in manager.get_tasks()], ["旧目录里的待办"])

    def test_03_legacy_profile_with_ability_mode_loads_safely(self) -> None:
        profile = PetProfile.from_dict(
            {
                "pet_name": "小蓝",
                "relationship_mode": "好朋友",
                "ability_mode": "english_teacher",
                "gentleness": 80,
            }
        )
        self.assertEqual(profile.pet_name, "小蓝")
        self.assertEqual(profile.relationship_mode, "好朋友")
        self.assertEqual(profile.gentleness, 80)
        self.assertNotIn("ability_mode", profile.to_dict())

    def test_04_pet_remembers_position_and_always_on_top_and_tray_menu(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = PetStateStore(root / "pet_state.json")
            manager = TodoManager(root / "tasks.json")
            profiles = ProfileService(root / "profile.json")
            pet = PetWindow(
                self.config,
                manager,
                profile_service=profiles,
                state_store=store,
            )
            pet.show()
            self.app.processEvents()
            self.assertTrue(pet.always_on_top)
            self.assertIsNotNone(pet.tray_menu)
            menu_texts = [a.text() for a in pet._create_context_menu().actions() if a.text()]
            self.assertIn("始终置顶", menu_texts)
            self.assertIn("隐藏到托盘", menu_texts)

            pet.set_always_on_top(False)
            self.assertFalse(pet.always_on_top)
            self.assertFalse(bool(pet.windowFlags() & Qt.WindowType.WindowStaysOnTopHint))
            self.assertFalse(store.load().always_on_top)

            pet.hide_to_tray()
            self.assertFalse(pet.isVisible())
            pet.show_from_tray()
            self.assertTrue(pet.isVisible())

            # Drag to a visible location and confirm saved coordinates restore on restart
            start_pos = pet.pos()
            press = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(10, 10),
                QPointF(start_pos.x() + 10, start_pos.y() + 10),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            pet.mousePressEvent(press)
            threshold = QApplication.styleHints().startDragDistance()
            moved_global = QPointF(start_pos.x() + 10 - threshold - 30, start_pos.y() + 10 - 20)
            move = QMouseEvent(
                QEvent.Type.MouseMove,
                QPointF(10, 10),
                moved_global,
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            pet.mouseMoveEvent(move)
            release = QMouseEvent(
                QEvent.Type.MouseButtonRelease,
                QPointF(10, 10),
                moved_global,
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            pet.mouseReleaseEvent(release)
            saved = store.load()
            self.assertEqual((saved.x, saved.y), (pet.x(), pet.y()))
            pet.close()

            reopened = PetWindow(
                self.config,
                manager,
                profile_service=profiles,
                state_store=store,
            )
            self.assertFalse(reopened.always_on_top)
            self.assertEqual((reopened.x(), reopened.y()), (saved.x, saved.y))
            reopened.close()

            # Off-screen coordinates fall back safely to screen bounds
            store.save(PetWindowState(x=999999, y=999999, always_on_top=True))
            recovered = PetWindow(
                self.config,
                manager,
                profile_service=profiles,
                state_store=store,
            )
            self.assertNotEqual((recovered.x(), recovered.y()), (999999, 999999))
            recovered.close()

    def test_05_chat_history_persists_restores_in_ui_and_clears(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            history_store = ChatHistoryStore(root / "chat_history.json")
            service = ChatService(
                client=FakeDeepSeekClient(),
                history_store=history_store,
            )
            service.begin_turn("你好呀")
            service.complete_turn("我一直在这里陪你。")
            self.assertEqual(len(history_store.load()), 2)

            restarted_service = ChatService(
                client=FakeDeepSeekClient(),
                history_store=history_store,
            )
            self.assertEqual(len(restarted_service.history), 2)
            window = ChatWindow(
                restarted_service,
                ui_settings=ChatUiSettings(root / "chat_ui.json"),
                voice_service=VoiceService(
                    tts_service=TTSService(FakeTTSBackend),
                    audio_player=FakeAudioPlayer(),
                ),
            )
            self.assertEqual(
                window.message_texts,
                (("user", "你好呀"), ("assistant", "我一直在这里陪你。")),
            )
            window.clear_chat()
            self.assertEqual(history_store.load(), [])
            window.shutdown()

    def test_06_voice_settings_persist_across_chat_window_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            voice_store = VoiceSettingsStore(root / "voice_settings.json")
            voice_1 = VoiceService(
                settings=VoiceSettings(stream_sentences=True, tts_backend="auto"),
                tts_service=TTSService(FakeTTSBackend),
                audio_player=FakeAudioPlayer(),
            )
            w1 = ChatWindow(
                ChatService(client=FakeDeepSeekClient()),
                ui_settings=ChatUiSettings(root / "chat_ui.json"),
                voice_service=voice_1,
                voice_settings_store=voice_store,
            )
            w1.voice_toggle_button.setChecked(True)
            system_idx = w1.voice_backend_combo.findData("system")
            w1.voice_backend_combo.setCurrentIndex(system_idx)
            w1.shutdown()

            voice_2 = VoiceService(
                settings=VoiceSettings(stream_sentences=True, tts_backend="auto"),
                tts_service=TTSService(FakeTTSBackend),
                audio_player=FakeAudioPlayer(),
            )
            w2 = ChatWindow(
                ChatService(client=FakeDeepSeekClient()),
                ui_settings=ChatUiSettings(root / "chat_ui.json"),
                voice_service=voice_2,
                voice_settings_store=voice_store,
            )
            self.assertTrue(w2.voice_service.settings.enable_voice_reply)
            self.assertEqual(w2.voice_service.settings.tts_backend, "system")
            self.assertTrue(w2.voice_toggle_button.isChecked())
            self.assertEqual(w2.voice_backend_combo.currentData(), "system")
            w2.shutdown()

    def test_07_gui_ai_settings_save_validate_and_live_reconfigure_chat(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            ai_service = AiSettingsService(root / "ai_settings.json")
            profiles = ProfileService(root / "profile.json")
            profile_win = ProfileWindow(profiles, ai_settings_service=ai_service)

            with patch.dict(os.environ, {}, clear=True):
                chat_service = ChatService(client=DeepSeekClient())
                self.assertFalse(chat_service.is_configured)
                chat_win = ChatWindow(
                    chat_service,
                    profile_service=profiles,
                    ai_settings_service=ai_service,
                    ui_settings=ChatUiSettings(root / "chat_ui.json"),
                    voice_service=VoiceService(
                        tts_service=TTSService(FakeTTSBackend),
                        audio_player=FakeAudioPlayer(),
                    ),
                )

                # Invalid URL rejected
                profile_win.open_tab("ai")
                profile_win.base_url_edit.setText("ftp://invalid")
                self.assertFalse(profile_win.save_ai_settings())

                # Valid settings saved and applied live without restart
                profile_win.api_key_edit.setText("sk-gui-test-key-123456")
                profile_win.base_url_edit.setText(DEFAULT_BASE_URL)
                profile_win.model_combo.setCurrentText("deepseek-chat")
                self.assertTrue(profile_win.save_ai_settings())
                self.app.processEvents()

                self.assertTrue(chat_service.is_configured)
                self.assertEqual(chat_service.config.model, "deepseek-chat")
                self.assertEqual(chat_win.model_label.text(), "DeepSeek · chat")

                # Persists across restart
                reloaded = AiSettingsService(root / "ai_settings.json")
                self.assertEqual(reloaded.settings.api_key, "sk-gui-test-key-123456")
                self.assertEqual(reloaded.settings.model, "deepseek-chat")
                chat_win.shutdown()
            profile_win.close()

        with self.assertRaises(AiSettingsValidationError):
            AiConnectionSettings(model="   ")


if __name__ == "__main__":
    unittest.main()
