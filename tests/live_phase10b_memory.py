"""Phase 10B Fake Live manual-memory scenarios; never calls a real API."""

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
from chat_window import ChatWindow
from memory.memory_service import MemoryService
from memory_window import MemoryManagerWindow


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    app = QApplication.instance() or QApplication(["phase10b-live"])
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "memory.db"
        first = MemoryService(path)
        window = MemoryManagerWindow(first)
        window.show()

        math = first.create_memory("用户希望数学题默认展示关键步骤")
        app.processEvents()
        require(math in first.active_memories(), "场景 A 未进入活跃记忆")
        require(math.id in window.rendered_memory_ids, "场景 A UI 未刷新")
        print("LIVE_A active_memory_created")

        first.set_core(math.id)
        require(first.get_memory(math.id).is_core, "场景 B 未设为核心")
        window.close()
        first.close()
        second = MemoryService(path)
        require(second.get_memory(math.id).is_core, "场景 B 重启后核心状态丢失")
        print("LIVE_B core_persisted_after_restart")

        window = MemoryManagerWindow(second)
        study = second.create_memory("最近正在学习信号与系统")
        second.archive_memory(study.id)
        app.processEvents()
        require(study.id not in window.rendered_memory_ids, "场景 C 主列表仍显示归档")
        window.toggle_archive_view()
        require(study.id in window.rendered_memory_ids, "场景 C 归档页找不到记录")
        print("LIVE_C archived_hidden_from_main_and_visible_in_archive")

        second.restore_memory(study.id)
        app.processEvents()
        require(study.id not in window.rendered_memory_ids, "场景 D 归档页仍显示已恢复记录")
        window.toggle_archive_view()
        require(study.id in window.rendered_memory_ids, "场景 D 未恢复到活跃列表")
        print("LIVE_D archived_memory_restored")

        second.update_memory(study.id, "最近正在复习信号与系统")
        window.close()
        second.close()
        third = MemoryService(path)
        require("复习" in third.get_memory(study.id).content, "场景 E 编辑未持久化")
        print("LIVE_E edited_content_persisted")

        before = third.all_memories()
        chat_service = ChatService(client=FakeDeepSeekClient())
        chat_window = ChatWindow(chat_service)
        chat_service.begin_turn("你好")
        chat_service.complete_turn("你好呀")
        chat_window.clear_chat()
        require(chat_service.history == (), "场景 F 聊天历史未清空")
        require(third.all_memories() == before, "场景 F Memory 被 Clear 修改")
        print("LIVE_F_clear_chat_keeps_memory")

        chat_window.shutdown()
        third.close()

    print("LIVE_PASS A_create B_core C_archive D_restore E_edit F_clear")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
