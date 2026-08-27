"""Visible-free Phase 10A Fake Live profile scenarios (no real API calls)."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from ai.chat_service import ChatService
from ai.fake_client import FakeDeepSeekClient
from ai.persona_builder import PersonaBuilder
from ai.profile import (
    RELATIONSHIP_CLINGY_GIRLFRIEND,
    RELATIONSHIP_GIRLFRIEND,
    RELATIONSHIP_YANDERE,
    PetProfile,
)
from ai.profile_service import ProfileService
from chat_window import ChatWindow
from profile_window import ProfileWindow


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    app = QApplication.instance() or QApplication(["phase10a-live"])
    builder = PersonaBuilder()
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        profile_path = root / "profile.json"
        profiles = ProfileService(profile_path)
        service = ChatService(client=FakeDeepSeekClient())
        chat = ChatWindow(service, profile_service=profiles)
        settings = ProfileWindow(profiles)

        settings.name_edit.setText("小蓝")
        require(settings.save_settings(), "场景 A 保存失败")
        app.processEvents()
        require(chat.pet_name == "小蓝", "场景 A Header 未更新")
        require(ProfileService(profile_path).profile.pet_name == "小蓝", "场景 A 重启未恢复")
        print("LIVE_A name_header_and_restart_persisted")

        girlfriend = PetProfile.for_mode(
            RELATIONSHIP_GIRLFRIEND,
            pet_name="小蓝",
        )
        girlfriend_prompt = builder.build(girlfriend)
        require("恋人型桌宠" in girlfriend_prompt, "场景 B 未生成恋人型 Persona")
        require("尊重现实边界" in girlfriend_prompt, "场景 B 缺少边界")
        print("LIVE_B girlfriend_persona_natural")

        clingy_index = settings.relationship_combo.findData(
            RELATIONSHIP_CLINGY_GIRLFRIEND
        )
        settings.relationship_combo.setCurrentIndex(clingy_index)
        settings.sliders["clinginess"].setValue(84)
        settings.sliders["coquetry"].setValue(76)
        require(settings.save_settings(), "场景 C 保存失败")
        restored = ProfileService(profile_path).profile
        require(restored.clinginess == 84, "场景 C 黏人度未恢复")
        require(restored.coquetry == 76, "场景 C 撒娇度未恢复")
        print("LIVE_C customized_clingy_profile_restarted")

        safe_prompt = builder.build(PetProfile.for_mode(RELATIONSHIP_YANDERE))
        for cue in ("高亲密", "黏", "吃醋", "占有感", "不威胁", "不强迫"):
            require(cue in safe_prompt, f"场景 D 缺少行为特征：{cue}")
        require("病娇" not in safe_prompt, "场景 D 不应只发送病娇标签")
        print("LIVE_D safe_high_intimacy_behavior_persona")

        before = profiles.profile
        service.begin_turn("你好")
        service.complete_turn("你好呀")
        chat.clear_chat()
        require(service.history == (), "场景 E 聊天历史未清空")
        require(profiles.profile == before, "场景 E Profile 被清空")
        require(ProfileService(profile_path).profile == before, "场景 E 持久化 Profile 改变")
        print("LIVE_E clear_keeps_profile")

        settings.close()
        chat.shutdown()

    print("LIVE_PASS A_name B_girlfriend C_custom D_safe E_clear")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

