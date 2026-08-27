"""Multi-turn context management and request organization for AI chat."""

from __future__ import annotations

from collections.abc import Iterator
from threading import Event
from typing import Any

from ai.ai_config import DeepSeekConfig
from ai.deepseek_client import DeepSeekClient, MissingAPIKeyError
from ai.persona import DEFAULT_SYSTEM_PROMPT


class ChatBusyError(RuntimeError):
    """Raised when a second request is started before the first finishes."""


class EmptyChatMessageError(ValueError):
    """Raised for empty or whitespace-only user input."""


class ChatService:
    """Own session history while delegating network transport to a client."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        config: DeepSeekConfig | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_context_rounds: int | None = None,
    ) -> None:
        self.config = config or DeepSeekConfig.from_environment()
        self.client = client or DeepSeekClient(self.config)
        self.system_prompt = system_prompt.strip() or DEFAULT_SYSTEM_PROMPT
        configured_rounds = (
            self.config.max_context_rounds
            if max_context_rounds is None
            else int(max_context_rounds)
        )
        self.max_context_rounds = max(1, configured_rounds)
        self._history: list[dict[str, str]] = []
        self._pending_user: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(getattr(self.client, "is_configured", True))

    @property
    def is_busy(self) -> bool:
        return self._pending_user is not None

    @property
    def history(self) -> tuple[dict[str, str], ...]:
        return tuple(dict(message) for message in self._history)

    @property
    def messages(self) -> tuple[dict[str, str], ...]:
        return (
            {"role": "system", "content": self.system_prompt},
            *self.history,
        )

    def begin_turn(self, user_text: str) -> tuple[dict[str, str], ...]:
        normalized = user_text.strip()
        if not normalized:
            raise EmptyChatMessageError("消息不能为空。")
        if self.is_busy:
            raise ChatBusyError("当前回答尚未结束，请稍候。")
        if not self.is_configured:
            raise MissingAPIKeyError(
                "未配置 DeepSeek API Key，请先设置 DEEPSEEK_API_KEY。"
            )
        self._pending_user = normalized
        return (
            *self.messages,
            {"role": "user", "content": normalized},
        )

    def stream_response(
        self,
        messages: tuple[dict[str, str], ...],
        stop_event: Event,
    ) -> Iterator[str]:
        yield from self.client.stream_chat(messages, stop_event)

    def complete_turn(self, assistant_text: str) -> None:
        user_text = self._take_pending_user()
        normalized = assistant_text.strip()
        self._history.append({"role": "user", "content": user_text})
        self._history.append(
            {
                "role": "assistant",
                "content": normalized or "（本次没有返回文字）",
            }
        )
        self._trim_history()

    def stop_turn(self, partial_assistant_text: str) -> None:
        user_text = self._take_pending_user()
        partial = partial_assistant_text.strip()
        if partial:
            self._history.append({"role": "user", "content": user_text})
            self._history.append({"role": "assistant", "content": partial})
            self._trim_history()

    def fail_turn(self) -> None:
        self._pending_user = None

    def clear(self) -> None:
        if self.is_busy:
            raise ChatBusyError("生成期间不能清空聊天，请先停止。")
        self._history.clear()

    def record_local_exchange(self, user_text: str, assistant_text: str) -> None:
        """Record a verified local action only after it really succeeds."""

        if self.is_busy:
            raise ChatBusyError("生成期间不能记录本地操作结果。")
        normalized_user = user_text.strip()
        normalized_assistant = assistant_text.strip()
        if not normalized_user or not normalized_assistant:
            raise ValueError("本地操作记录不能为空。")
        self._history.append({"role": "user", "content": normalized_user})
        self._history.append(
            {"role": "assistant", "content": normalized_assistant}
        )
        self._trim_history()

    def cancel_active_stream(self) -> None:
        cancel = getattr(self.client, "cancel_active_stream", None)
        if callable(cancel):
            cancel()

    def _take_pending_user(self) -> str:
        if self._pending_user is None:
            raise ChatBusyError("没有正在处理的聊天轮次。")
        user_text = self._pending_user
        self._pending_user = None
        return user_text

    def _trim_history(self) -> None:
        maximum_messages = self.max_context_rounds * 2
        if len(self._history) > maximum_messages:
            self._history = self._history[-maximum_messages:]
