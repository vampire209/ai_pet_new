"""Phase 10A profile, settings UI, and Persona Builder tests."""

from __future__ import annotations

from threading import Event
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

from PySide6.QtWidgets import QApplication, QLabel

from ai.chat_service import ChatService
from ai.fake_client import FakeDeepSeekClient
from ai.persona_builder import PersonaBuilder
from ai.profile import (
    DEFAULT_PET_NAME,
    PET_NAME_MAX_LENGTH,
    PROFILE_PRESETS,
    RELATIONSHIP_CLINGY_GIRLFRIEND,
    RELATIONSHIP_CUSTOM,
    RELATIONSHIP_FRIEND,
    RELATIONSHIP_GIRLFRIEND,
    RELATIONSHIP_NORMAL,
    RELATIONSHIP_YANDERE,
    TRAIT_FIELDS,
    TRAIT_LABELS,
    PetProfile,
    ProfileValidationError,
)
from ai.profile_service import ProfileService, default_profile_config_path
from chat_window import ChatWindow
from config import AppConfig, PROJECT_ROOT as CONFIG_PROJECT_ROOT
from pet_window import PetWindow
from profile_window import ProfileWindow
from reminder_manager import ReminderManager
from todo_manager import TodoManager


class Phase10AProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication(["phase10a-tests"])
        cls.builder = PersonaBuilder()

    def test_01_default_profile_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ProfileService(Path(temp_dir) / "profile.json")
            self.assertEqual(service.profile, PetProfile())
            self.assertFalse(service.config_path.exists())

    def test_02_default_name_and_user_config_location(self) -> None:
        self.assertEqual(PetProfile().pet_name, "桌宠")
        path = default_profile_config_path()
        self.assertEqual(path.name, "profile.json")
        self.assertEqual(path.parent.name, "ai_pet_new")
        self.assertFalse(path.is_relative_to(CONFIG_PROJECT_ROOT))

    def test_03_name_can_be_changed_and_trimmed(self) -> None:
        profile = PetProfile(pet_name="  小蓝  ")
        self.assertEqual(profile.pet_name, "小蓝")

    def test_04_name_persists_after_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            ProfileService(path).save(PetProfile(pet_name="小蓝"))
            self.assertEqual(ProfileService(path).profile.pet_name, "小蓝")

    def test_05_empty_and_overlong_names_cannot_be_saved(self) -> None:
        for value in ("", "   ", "蓝" * (PET_NAME_MAX_LENGTH + 1)):
            with self.subTest(value=value):
                with self.assertRaises(ProfileValidationError):
                    PetProfile(pet_name=value)

    def test_06_chinese_and_emoji_names_are_supported(self) -> None:
        profile = PetProfile(pet_name="小蓝兔✨")
        self.assertEqual(profile.pet_name, "小蓝兔✨")

    def test_07_normal_pet_preset_matches_defaults(self) -> None:
        profile = PetProfile.for_mode(RELATIONSHIP_NORMAL)
        self.assertEqual(
            {field: getattr(profile, field) for field in TRAIT_FIELDS},
            PROFILE_PRESETS[RELATIONSHIP_NORMAL],
        )

    def test_08_friend_preset_matches_defaults(self) -> None:
        profile = PetProfile.for_mode(RELATIONSHIP_FRIEND)
        self.assertEqual(profile.liveliness, 70)
        self.assertEqual(profile.clinginess, 35)

    def test_09_girlfriend_preset_matches_defaults(self) -> None:
        profile = PetProfile.for_mode(RELATIONSHIP_GIRLFRIEND)
        self.assertEqual(profile.gentleness, 85)
        self.assertEqual(profile.coquetry, 60)

    def test_10_clingy_girlfriend_preset_matches_defaults(self) -> None:
        profile = PetProfile.for_mode(RELATIONSHIP_CLINGY_GIRLFRIEND)
        self.assertEqual(profile.clinginess, 90)
        self.assertEqual(profile.coquetry, 80)

    def test_11_yandere_mode_becomes_safe_behavior_not_a_label(self) -> None:
        prompt = self.builder.build(PetProfile.for_mode(RELATIONSHIP_YANDERE))
        self.assertNotIn("病娇", prompt)
        self.assertIn("高亲密", prompt)
        self.assertIn("吃醋", prompt)
        self.assertIn("占有感", prompt)
        self.assertIn("不威胁", prompt)
        self.assertIn("不强迫", prompt)
        self.assertIn("不以自伤施压", prompt)
        self.assertIn("不要求用户切断现实关系", prompt)

    def test_12_custom_personality_values_are_valid(self) -> None:
        profile = PetProfile(
            relationship_mode=RELATIONSHIP_CUSTOM,
            gentleness=11,
            clinginess=22,
            coquetry=33,
            jealousy=44,
            possessiveness=55,
            liveliness=66,
        )
        self.assertEqual(profile.relationship_mode, RELATIONSHIP_CUSTOM)
        self.assertEqual(profile.jealousy, 44)

    def test_13_trait_model_and_sliders_are_limited_to_zero_through_100(self) -> None:
        for value in (-1, 101):
            with self.subTest(value=value):
                with self.assertRaises(ProfileValidationError):
                    PetProfile(gentleness=value)
        with tempfile.TemporaryDirectory() as temp_dir:
            window = ProfileWindow(ProfileService(Path(temp_dir) / "profile.json"))
            for slider in window.sliders.values():
                self.assertEqual(slider.minimum(), 0)
                self.assertEqual(slider.maximum(), 100)
            window.close()

    def test_14_all_parameters_persist_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            profile = PetProfile(
                pet_name="团子",
                relationship_mode=RELATIONSHIP_CUSTOM,
                gentleness=12,
                clinginess=23,
                coquetry=34,
                jealousy=45,
                possessiveness=56,
                liveliness=67,
            )
            ProfileService(path).save(profile)
            self.assertEqual(ProfileService(path).profile, profile)

    def test_15_clear_chat_does_not_clear_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            profiles = ProfileService(path)
            profiles.save(PetProfile(pet_name="小蓝"))
            service = ChatService(client=FakeDeepSeekClient())
            window = ChatWindow(service, profile_service=profiles)
            service.begin_turn("你好")
            service.complete_turn("你好呀")
            window.clear_chat()
            self.assertEqual(service.history, ())
            self.assertEqual(profiles.profile.pet_name, "小蓝")
            self.assertEqual(ProfileService(path).profile.pet_name, "小蓝")
            window.shutdown()

    def test_16_profile_data_never_enters_todo_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = ProfileService(root / "profile.json")
            profiles.save(PetProfile(pet_name="小蓝"))
            manager = TodoManager(root / "tasks.json")
            manager.add_task("写作业")
            raw = (root / "tasks.json").read_text(encoding="utf-8")
            self.assertNotIn("小蓝", raw)
            self.assertNotIn("relationship_mode", raw)
            self.assertEqual(json.loads(raw)[0]["text"], "写作业")

    def test_17_profile_data_is_system_context_not_chat_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = ProfileService(Path(temp_dir) / "profile.json")
            profiles.save(
                PetProfile.for_mode(
                    RELATIONSHIP_GIRLFRIEND,
                    pet_name="小蓝",
                )
            )
            service = ChatService(client=FakeDeepSeekClient())
            window = ChatWindow(service, profile_service=profiles)
            service.begin_turn("你好")
            service.complete_turn("你好呀")
            self.assertNotIn("小蓝", repr(service.history))
            self.assertNotIn("relationship_mode", repr(service.history))
            self.assertIn("小蓝", service.messages[0]["content"])
            window.shutdown()

    def test_18_persona_builder_uses_pet_name(self) -> None:
        prompt = self.builder.build(PetProfile(pet_name="小蓝"))
        self.assertIn("你名叫“小蓝”", prompt)

    def test_19_persona_builder_uses_relationship_mode(self) -> None:
        prompt = self.builder.build(PetProfile.for_mode(RELATIONSHIP_GIRLFRIEND))
        self.assertIn("角色关系设定为恋人", prompt)
        self.assertIn("你是用户的女友", prompt)
        self.assertIn("尊重现实边界", prompt)

    def test_20_personality_parameters_change_natural_behavior(self) -> None:
        low = self.builder.build(
            PetProfile(
                relationship_mode=RELATIONSHIP_CUSTOM,
                gentleness=10,
                clinginess=10,
                coquetry=10,
                jealousy=10,
                possessiveness=10,
                liveliness=10,
            )
        )
        high = self.builder.build(
            PetProfile(
                relationship_mode=RELATIONSHIP_CUSTOM,
                gentleness=90,
                clinginess=90,
                coquetry=90,
                jealousy=90,
                possessiveness=90,
                liveliness=90,
            )
        )
        self.assertIn("直接利落", low)
        self.assertIn("沉静克制", low)
        self.assertIn("很柔和", high)
        self.assertIn("等用户回来", high)
        self.assertIn("活跃开朗", high)

    def test_21_persona_never_contains_raw_trait_configuration(self) -> None:
        profile = PetProfile(
            relationship_mode=RELATIONSHIP_CUSTOM,
            gentleness=11,
            clinginess=22,
            coquetry=33,
            jealousy=44,
            possessiveness=55,
            liveliness=66,
        )
        prompt = self.builder.build(profile)
        for field, value in profile.to_dict().items():
            if field in TRAIT_FIELDS:
                self.assertNotIn(f"{field}={value}", prompt)
                self.assertNotIn(f"{TRAIT_LABELS[field]}={value}", prompt)
                self.assertNotIn(f"{TRAIT_LABELS[field]}：{value}", prompt)
                self.assertNotIn(str(value), prompt)

    def test_22_persona_preserves_todo_creation_capability_boundary(self) -> None:
        prompt = self.builder.build(PetProfile())
        self.assertIn("创建自己的新待办", prompt)
        self.assertIn("一次性、每日、每周和提前提醒", prompt)
        self.assertIn("只有用户点击确认", prompt)
        self.assertIn("绝不能跳过确认卡", prompt)

    def test_23_persona_forbids_fake_existing_todo_operations(self) -> None:
        prompt = self.builder.build(PetProfile())
        self.assertIn("不能修改、删除或完成已有待办", prompt)
        self.assertIn("不能操作 Windows 系统提醒", prompt)
        self.assertIn("不能操作", prompt)
        self.assertIn("其他应用", prompt)

    def test_24_chat_header_and_future_prompt_update_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = ProfileService(Path(temp_dir) / "profile.json")
            service = ChatService(client=FakeDeepSeekClient())
            window = ChatWindow(service, profile_service=profiles)
            header = window.findChild(QLabel, "chatPetName")
            self.assertEqual(header.text(), DEFAULT_PET_NAME)
            profiles.save(PetProfile(pet_name="小蓝"))
            self.app.processEvents()
            self.assertEqual(header.text(), "小蓝")
            self.assertIn("小蓝", window.windowTitle())
            self.assertIn("小蓝", service.messages[0]["content"])
            next_messages = service.begin_turn("下一条消息")
            self.assertIn("小蓝", next_messages[0]["content"])
            service.fail_turn()
            window.shutdown()

    def test_25_settings_window_opens_closes_and_remains_single_instance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manager = TodoManager(root / "tasks.json")
            scanner = ReminderManager(manager)
            profiles = ProfileService(root / "profile.json")
            pet = PetWindow(
                AppConfig.load(),
                manager,
                scanner,
                profile_service=profiles,
            )
            pet._open_profile_window()
            first = pet.profile_window
            self.assertIsNotNone(first)
            self.assertTrue(first.isVisible())
            first.close()
            self.app.processEvents()
            self.assertFalse(first.isVisible())
            pet._open_profile_window()
            self.assertIs(pet.profile_window, first)
            pet.close()

    def test_26_preset_selection_and_manual_edit_do_not_loop(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            window = ProfileWindow(ProfileService(Path(temp_dir) / "profile.json"))
            index = window.relationship_combo.findData(RELATIONSHIP_GIRLFRIEND)
            window.relationship_combo.setCurrentIndex(index)
            self.assertEqual(window.sliders["gentleness"].value(), 85)
            self.assertEqual(window.relationship_combo.currentData(), RELATIONSHIP_GIRLFRIEND)
            window.sliders["gentleness"].setValue(84)
            self.assertEqual(window.relationship_combo.currentData(), RELATIONSHIP_CUSTOM)
            self.assertEqual(window.value_labels["gentleness"].text(), "84")
            window.close()

    def test_27_restore_preset_keeps_name_and_save_uses_current_values(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = ProfileService(Path(temp_dir) / "profile.json")
            window = ProfileWindow(service)
            window.name_edit.setText("小蓝")
            index = window.relationship_combo.findData(RELATIONSHIP_GIRLFRIEND)
            window.relationship_combo.setCurrentIndex(index)
            window.sliders["gentleness"].setValue(10)
            self.assertEqual(window.relationship_combo.currentData(), RELATIONSHIP_CUSTOM)
            window.restore_selected_preset()
            self.assertEqual(window.name_edit.text(), "小蓝")
            self.assertEqual(window.sliders["gentleness"].value(), 85)
            self.assertTrue(window.save_settings())
            self.assertEqual(service.profile.pet_name, "小蓝")
            self.assertEqual(service.profile.relationship_mode, RELATIONSHIP_GIRLFRIEND)
            window.close()

    def test_28_settings_window_rejects_blank_name_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            window = ProfileWindow(ProfileService(path))
            window.name_edit.setText("   ")
            self.assertFalse(window.save_settings())
            self.assertFalse(path.exists())
            self.assertIn("不能为空", window.status_label.text())
            window.close()

    def test_29_damaged_or_partial_config_recovers_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "profile.json"
            path.write_text("{bad json", encoding="utf-8")
            self.assertEqual(ProfileService(path).profile, PetProfile())
            path.write_text(
                json.dumps(
                    {
                        "pet_name": "  ",
                        "relationship_mode": RELATIONSHIP_GIRLFRIEND,
                        "gentleness": 999,
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            recovered = ProfileService(path).profile
            self.assertEqual(recovered.pet_name, DEFAULT_PET_NAME)
            self.assertEqual(recovered.relationship_mode, RELATIONSHIP_GIRLFRIEND)
            self.assertEqual(recovered.gentleness, 85)

    def test_30_user_current_instruction_has_priority_over_profile_style(self) -> None:
        prompt = self.builder.build(
            PetProfile.for_mode(RELATIONSHIP_CLINGY_GIRLFRIEND)
        )
        self.assertIn("用户当前", prompt)
        self.assertIn("明确要求优先", prompt)

    def test_31_normal_mode_has_explicit_pet_identity(self) -> None:
        prompt = self.builder.build(PetProfile.for_mode(RELATIONSHIP_NORMAL))
        self.assertIn("关系身份：你是用户的 AI 桌宠和陪伴助手", prompt)

    def test_32_friend_mode_has_explicit_friend_identity(self) -> None:
        prompt = self.builder.build(PetProfile.for_mode(RELATIONSHIP_FRIEND))
        self.assertIn("关系身份：你和用户是关系亲近的好朋友", prompt)

    def test_33_girlfriend_modes_have_explicit_romantic_identity(self) -> None:
        girlfriend = self.builder.build(
            PetProfile.for_mode(RELATIONSHIP_GIRLFRIEND)
        )
        clingy = self.builder.build(
            PetProfile.for_mode(RELATIONSHIP_CLINGY_GIRLFRIEND)
        )
        for prompt in (girlfriend, clingy):
            self.assertIn("角色关系设定为恋人", prompt)
            self.assertIn("用户", prompt)
            self.assertIn("女友", prompt)
        self.assertIn("比较黏人的女友", clingy)

    def test_34_yandere_mode_has_romantic_identity_and_safety_boundaries(self) -> None:
        prompt = self.builder.build(PetProfile.for_mode(RELATIONSHIP_YANDERE))
        self.assertIn("角色关系设定为恋人", prompt)
        self.assertIn("高亲密", prompt)
        self.assertIn("黏人", prompt)
        self.assertIn("轻微吃醋", prompt)
        self.assertIn("适度占有感", prompt)
        self.assertIn("不威胁", prompt)
        self.assertIn("不强迫", prompt)
        self.assertIn("不以自伤施压", prompt)
        self.assertIn("不控制用户现实生活", prompt)

    def test_35_relationship_identity_is_separate_from_slider_style(self) -> None:
        low = PetProfile(
            relationship_mode=RELATIONSHIP_GIRLFRIEND,
            gentleness=0,
            clinginess=0,
            coquetry=0,
            jealousy=0,
            possessiveness=0,
            liveliness=0,
        )
        high = PetProfile(
            relationship_mode=RELATIONSHIP_GIRLFRIEND,
            gentleness=100,
            clinginess=100,
            coquetry=100,
            jealousy=100,
            possessiveness=100,
            liveliness=100,
        )
        identity = "关系身份：你与用户的角色关系设定为恋人，你是用户的女友。"
        self.assertIn(identity, self.builder.build(low))
        self.assertIn(identity, self.builder.build(high))

    def test_36_chat_service_receives_explicit_identity_on_next_turn(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            profiles = ProfileService(Path(temp_dir) / "profile.json")
            service = ChatService(client=FakeDeepSeekClient())
            window = ChatWindow(service, profile_service=profiles)
            profiles.save(
                PetProfile.for_mode(
                    RELATIONSHIP_GIRLFRIEND,
                    pet_name="新泽西",
                )
            )
            messages = service.begin_turn("我们是什么关系")
            system_prompt = messages[0]["content"]
            self.assertIn("你名叫“新泽西”", system_prompt)
            self.assertIn("角色关系设定为恋人", system_prompt)
            self.assertIn("你是用户的女友", system_prompt)
            self.assertIn("必须依据上述关系身份自然、明确回答", system_prompt)
            self.assertNotIn("特别关系。", system_prompt)
            service.fail_turn()
            window.shutdown()

    def test_37_real_shared_gui_chain_hot_updates_each_fake_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profiles = ProfileService(root / "profile.json")
            manager = TodoManager(root / "tasks.json")
            scanner = ReminderManager(manager)
            fake = FakeDeepSeekClient()
            service = ChatService(client=fake)
            pet = PetWindow(
                AppConfig.load(),
                manager,
                scanner,
                chat_service=service,
                profile_service=profiles,
            )
            pet._open_chat_window()
            pet._open_profile_window()
            chat = pet.chat_window
            settings = pet.profile_window

            self.assertIs(pet.profile_service, profiles)
            self.assertIs(chat.profile_service, profiles)
            self.assertIs(settings.profile_service, profiles)
            self.assertIs(chat.service, service)

            settings.name_edit.setText("新泽西")
            settings.relationship_combo.setCurrentIndex(
                settings.relationship_combo.findData(RELATIONSHIP_GIRLFRIEND)
            )
            self.assertTrue(settings.save_settings())
            self.app.processEvents()
            self.assertEqual(chat.pet_name, "新泽西")

            service.set_system_prompt("故意写入的旧 Persona")
            first_messages = service.begin_turn("我们是什么关系")
            first_reply = "".join(service.stream_response(first_messages, Event()))
            service.complete_turn(first_reply)
            first_system = fake.requests[0][0]["content"]
            self.assertIn("新泽西", first_system)
            self.assertIn("角色关系设定为恋人", first_system)
            self.assertIn("你是用户的女友", first_system)
            self.assertNotIn("AI 桌宠和陪伴助手", first_system)
            self.assertNotIn("故意写入的旧 Persona", first_system)

            settings.relationship_combo.setCurrentIndex(
                settings.relationship_combo.findData(RELATIONSHIP_YANDERE)
            )
            self.assertTrue(settings.save_settings())
            service.set_system_prompt("再次写入旧女友 Persona")
            second_messages = service.begin_turn("你是我什么人")
            second_reply = "".join(
                service.stream_response(second_messages, Event())
            )
            service.complete_turn(second_reply)
            second_system = fake.requests[1][0]["content"]
            self.assertIn("新泽西", second_system)
            self.assertIn("角色关系设定为恋人", second_system)
            self.assertIn("高亲密", second_system)
            self.assertIn("轻微吃醋", second_system)
            self.assertIn("适度占有感", second_system)
            self.assertNotIn("再次写入旧女友 Persona", second_system)

            pet.close()


if __name__ == "__main__":
    unittest.main()
