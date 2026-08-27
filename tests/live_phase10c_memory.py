"""Zero-cost Phase 10C A-H integration probe using temporary SQLite."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import Event
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai.chat_service import ChatService
from ai.fake_client import FakeDeepSeekClient
from memory.memory_retriever import MemoryRetriever
from memory.memory_service import MemoryService


NOW = datetime(2026, 8, 28, 9, 30, 0)
OLD = datetime(2026, 1, 1, 9, 30, 0)


def run_fake_turn(chat: ChatService, text: str) -> tuple[dict[str, str], ...]:
    messages = chat.begin_turn(text)
    reply = "".join(chat.stream_response(messages, Event()))
    chat.complete_turn(reply)
    return messages


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as temp_dir:
        path = Path(temp_dir) / "memory.db"
        memories = MemoryService(path, now_provider=lambda: NOW)
        fake = FakeDeepSeekClient(
            (
                ("知道了。",),
                ("还是钢琴。",),
                ("你最近在学钢琴。",),
                ("我会按你这次的要求回答。",),
                ("以后会尽量简短。",),
            )
        )
        chat = ChatService(client=fake, memory_service=memories)
        try:
            run_fake_turn(chat, "我最近在学钢琴。")
            piano = memories.active_memories()
            require(len(piano) == 1 and "钢琴" in piano[0].content, "A 失败")
            print("LIVE_A piano_active_memory_created")

            run_fake_turn(chat, "我这段时间还在练钢琴。")
            piano_after = [
                memory
                for memory in memories.active_memories()
                if "钢琴" in memory.content
            ]
            require(len(piano_after) == 1, "B 失败：重复钢琴记忆")
            require(piano_after[0].id == piano[0].id, "B 失败：未原位更新")
            print("LIVE_B duplicate_piano_merged")

            chat.clear()
            recall_messages = run_fake_turn(
                chat,
                "你记得我最近在学什么吗？",
            )
            require("钢琴" in recall_messages[0]["content"], "C 失败：Clear 后未召回")
            print("LIVE_C clear_then_piano_recalled")

            fourier = memories.capture_user_message("我最近在学傅里叶变换。")
            game = memories.create_memory("用户最近喜欢玩星露谷物语。")
            context = MemoryRetriever(memories).retrieve(
                "傅里叶对偶性质怎么做？",
                now=NOW,
                mark_used=False,
            )
            selected = {memory.id for memory in context.active}
            require(fourier.id in selected, "D 失败：未检索傅里叶")
            require(game.id not in selected, "D 失败：注入无关游戏记忆")
            require(piano[0].id not in selected, "D 失败：注入无关钢琴记忆")
            print("LIVE_D relevant_fourier_only")

            core = memories.create_memory("用户希望数学题默认展示详细步骤。")
            memories.set_core(core.id)
            temporary_messages = run_fake_turn(chat, "这次直接告诉我答案。")
            require("详细步骤" in temporary_messages[0]["content"], "E 失败：核心未注入")
            require(
                temporary_messages[-1]["content"] == "这次直接告诉我答案。",
                "E 失败：当前要求位置错误",
            )
            require("详细步骤" in memories.get_memory(core.id).content, "E 失败：核心被改")
            print("LIVE_E temporary_request_keeps_core")

            run_fake_turn(chat, "以后数学题尽量简短。")
            updated_core = memories.get_memory(core.id)
            require(updated_core.is_core, "F 失败：核心状态丢失")
            require("简短" in updated_core.content, "F 失败：长期要求未更新")
            print("LIVE_F long_term_change_updates_core")

            low = memories.create_memory(
                "低重要度旧活跃记忆",
                now=OLD,
                importance=0.2,
            )
            archived = memories.run_maintenance(now=NOW)
            require(low.id in {memory.id for memory in archived}, "G 失败：未归档")
            require(memories.get_memory(low.id).archived, "G 失败：记录状态错误")
            require(
                any(memory.id == low.id for memory in memories.all_memories()),
                "G 失败：记忆被永久删除",
            )
            print("LIVE_G low_old_memory_archived_not_deleted")

            old_core = memories.create_memory(
                "长期不用也不能归档的核心",
                now=OLD,
                importance=0.0,
            )
            memories.set_core(old_core.id, now=OLD)
            memories.run_maintenance(now=NOW)
            old_core_after = memories.get_memory(old_core.id)
            require(old_core_after.is_core, "H 失败：核心标记丢失")
            require(not old_core_after.archived, "H 失败：核心被自动归档")
            print("LIVE_H core_never_auto_archived")

            print("LIVE_PASS A B C D E F G H")
            return 0
        finally:
            memories.close()


if __name__ == "__main__":
    raise SystemExit(main())
