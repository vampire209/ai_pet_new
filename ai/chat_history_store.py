"""Atomic JSON persistence for multi-turn chat history."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Iterable

from app_paths import chat_history_path


_VALID_ROLES = frozenset({"user", "assistant"})


def default_chat_history_path() -> Path:
    return chat_history_path()


class ChatHistoryStore:
    """Persist recent chat messages cleanly in the user data directory."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else default_chat_history_path()

    def load(self, *, max_messages: int | None = None) -> list[dict[str, str]]:
        if not self.path.is_file():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[聊天记录] 读取失败，使用空历史：{exc}", file=sys.stderr)
            return []
        if not isinstance(raw, dict):
            return []
        entries = raw.get("messages")
        if not isinstance(entries, list):
            return []
        messages: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            role = entry.get("role")
            content = entry.get("content")
            if role in _VALID_ROLES and isinstance(content, str) and content.strip():
                messages.append({"role": role, "content": content})
        if max_messages is not None and max_messages > 0 and len(messages) > max_messages:
            messages = messages[-max_messages:]
        return messages

    def save(self, messages: Iterable[dict[str, str]]) -> bool:
        cleaned = [
            {"role": item["role"], "content": item["content"]}
            for item in messages
            if item.get("role") in _VALID_ROLES
            and isinstance(item.get("content"), str)
            and item["content"].strip()
        ]
        payload = {"version": 1, "messages": cleaned}
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
            return True
        except OSError as exc:
            print(f"[聊天记录] 保存失败：{exc}", file=sys.stderr)
            return False

    def clear(self) -> bool:
        return self.save(())
