"""Ability mode persistence, persona composition, and quick-switch tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from ai.ability_modes import (
    ABILITY_DEFAULT,
    ABILITY_ENGLISH_TEACHER,
    ABILITY_MODE_LABELS,
    ENGLISH_TEACHER_PROMPT,
    build_ability_prompt,
)
from ai.chat_service import ChatService
from ai.fake_client import FakeDeepSeekClient
from ai.persona_builder import PersonaBuilder
from ai.profile import (
    RELATIONSHIP_CLINGY_GIRLFRIEND,
    RELATIONSHIP_GIRLFRIEND,
    PetProfile,
)
from ai.profile_service import ProfileService
from chat_window import ChatWindow
from memory.memory_service import MemoryService
from profile_window import ProfileWindow
from todo_manager import TodoManager


class AbilityModeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["ability-mode-tests"])
        cls.builder = PersonaBuilder()

    def test_01_default_value_and_prompt_are_backward_compatible(self) -> None:
        profile = PetProfile()
        self.assertEqual(profile.ability_mode, ABILITY_DEFAULT)
        self.assertEqual(build_ability_prompt(profile.ability_mode), "")
        self.assertNotIn("英语老师能力", self.builder.build(profile))

    def test_02_ability_mode_saves_and_loads_with_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            service = ProfileService(path)
            profile = PetProfile(
                pet_name="团子",
                relationship_mode=RELATIONSHIP_GIRLFRIEND,
                ability_mode=ABILITY_ENGLISH_TEACHER,
            )
            service.save(profile)

            restored = ProfileService(path).profile
            self.assertEqual(restored, profile)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["ability_mode"],
                ABILITY_ENGLISH_TEACHER,
            )

    def test_03_english_teacher_prompt_is_composed_after_relationship(self) -> None:
        profile = PetProfile.for_mode(
            RELATIONSHIP_CLINGY_GIRLFRIEND,
            ability_mode=ABILITY_ENGLISH_TEACHER,
        )
        prompt = self.builder.build(profile)

        relationship_marker = "你是用户比较黏人的女友"
        ability_marker = "你当前启用了“英语老师能力”"
        self.assertIn(relationship_marker, prompt)
        self.assertIn(ability_marker, prompt)
        self.assertLess(prompt.index(relationship_marker), prompt.index(ability_marker))
        for required in (
            "考研英语",
            "【单词】",
            "【长难句】",
            "【阅读理解】",
            "【完形填空】",
            "【新题型】",
            "【语法】",
            "不能改变正确答案",
        ):
            self.assertIn(required, prompt)
        self.assertEqual(build_ability_prompt(ABILITY_ENGLISH_TEACHER), ENGLISH_TEACHER_PROMPT)

    def test_04_invalid_ability_safely_falls_back_to_default(self) -> None:
        self.assertEqual(PetProfile(ability_mode="unknown").ability_mode, ABILITY_DEFAULT)
        self.assertEqual(
            PetProfile.from_dict({"ability_mode": "damaged"}).ability_mode,
            ABILITY_DEFAULT,
        )
        self.assertEqual(build_ability_prompt("bad-value"), "")

    def test_05_chat_quick_switch_preserves_relationship_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            profiles = ProfileService(path)
            profiles.save(PetProfile.for_mode(RELATIONSHIP_CLINGY_GIRLFRIEND))
            service = ChatService(client=FakeDeepSeekClient())
            window = ChatWindow(service, profile_service=profiles)
            service.begin_turn("之前的聊天")
            service.complete_turn("会继续保留")
            history_before = service.history

            index = window.ability_combo.findData(ABILITY_ENGLISH_TEACHER)
            window.ability_combo.setCurrentIndex(index)
            self.app.processEvents()

            self.assertEqual(profiles.profile.ability_mode, ABILITY_ENGLISH_TEACHER)
            self.assertEqual(
                profiles.profile.relationship_mode,
                RELATIONSHIP_CLINGY_GIRLFRIEND,
            )
            self.assertEqual(service.history, history_before)
            self.assertIn("英语老师能力", service.messages[0]["content"])
            self.assertEqual(
                ProfileService(path).profile.ability_mode,
                ABILITY_ENGLISH_TEACHER,
            )

            default_index = window.ability_combo.findData(ABILITY_DEFAULT)
            window.ability_combo.setCurrentIndex(default_index)
            self.app.processEvents()
            self.assertEqual(
                profiles.profile.relationship_mode,
                RELATIONSHIP_CLINGY_GIRLFRIEND,
            )
            self.assertEqual(service.history, history_before)
            self.assertNotIn("英语老师能力", service.messages[0]["content"])
            window.shutdown()

    def test_06_profile_ui_exposes_separate_relationship_and_ability(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ProfileService(Path(temp_dir) / "profile.json")
            window = ProfileWindow(service)
            relationship_index = window.relationship_combo.findData(
                RELATIONSHIP_GIRLFRIEND
            )
            ability_index = window.ability_combo.findData(
                ABILITY_ENGLISH_TEACHER
            )
            window.relationship_combo.setCurrentIndex(relationship_index)
            window.ability_combo.setCurrentIndex(ability_index)

            self.assertTrue(window.save_settings())
            self.assertEqual(
                service.profile.relationship_mode,
                RELATIONSHIP_GIRLFRIEND,
            )
            self.assertEqual(
                service.profile.ability_mode,
                ABILITY_ENGLISH_TEACHER,
            )
            self.assertEqual(window.ability_combo.count(), len(ABILITY_MODE_LABELS))
            window.close()

    def test_07_switch_affects_next_system_prompt_not_chat_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = ProfileService(Path(temp_dir) / "profile.json")
            service = ChatService(client=FakeDeepSeekClient())
            window = ChatWindow(service, profile_service=profiles)

            first = service.begin_turn("你好")
            self.assertNotIn("英语老师能力", first[0]["content"])
            service.complete_turn("你好呀")
            history_before = service.history

            profiles.save(
                PetProfile(
                    relationship_mode=RELATIONSHIP_GIRLFRIEND,
                    ability_mode=ABILITY_ENGLISH_TEACHER,
                )
            )
            next_messages = service.begin_turn("conventional 是什么意思？")
            self.assertIn("英语老师能力", next_messages[0]["content"])
            self.assertEqual(next_messages[1:3], history_before)
            self.assertEqual(service.history, history_before)
            service.fail_turn()
            window.shutdown()

    def test_08_switch_does_not_touch_todo_or_memory_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = ProfileService(root / "profile.json")
            profiles.save(PetProfile.for_mode(RELATIONSHIP_GIRLFRIEND))
            todos = TodoManager(root / "tasks.json")
            task = todos.add_task("背考研单词")
            memories = MemoryService(root / "memory.db")
            memory = memories.create_memory("用户正在准备考研英语")
            service = ChatService(client=FakeDeepSeekClient())
            window = ChatWindow(
                service,
                profile_service=profiles,
                todo_manager=todos,
                memory_service=memories,
            )

            index = window.ability_combo.findData(ABILITY_ENGLISH_TEACHER)
            window.ability_combo.setCurrentIndex(index)
            self.app.processEvents()

            self.assertEqual(todos.get_task(task.id), task)
            self.assertEqual(memories.get_memory(memory.id), memory)
            self.assertEqual(
                profiles.profile.relationship_mode,
                RELATIONSHIP_GIRLFRIEND,
            )
            window.shutdown()
            memories.close()


if __name__ == "__main__":
    unittest.main()
