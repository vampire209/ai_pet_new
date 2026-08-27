"""Multi-turn context management and request organization for AI chat."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import logging
from threading import Event
from typing import Any

from ai.ai_config import DeepSeekConfig
from ai.deepseek_client import DeepSeekClient, MissingAPIKeyError
from ai.persona import DEFAULT_SYSTEM_PROMPT
from memory.memory_models import MemoryDataError, MemoryRecord
from memory.memory_retriever import MemoryContext, MemoryRetriever
from memory.memory_service import MemoryService
from memory.memory_store import MemoryStorageError


class ChatBusyError(RuntimeError):
    """Raised when a second request is started before the first finishes."""


class EmptyChatMessageError(ValueError):
    """Raised for empty or whitespace-only user input."""


LOGGER = logging.getLogger(__name__)


class ChatService:
    """Own session history while delegating network transport to a client."""

    def __init__(
        self,
        client: Any | None = None,
        *,
        config: DeepSeekConfig | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        system_prompt_provider: Callable[[], str] | None = None,
        max_context_rounds: int | None = None,
        memory_service: MemoryService | None = None,
        memory_retriever: MemoryRetriever | None = None,
    ) -> None:
        self.config = config or DeepSeekConfig.from_environment()
        self.client = client or DeepSeekClient(self.config)
        self.system_prompt = system_prompt.strip() or DEFAULT_SYSTEM_PROMPT
        self._system_prompt_provider = system_prompt_provider
        configured_rounds = (
            self.config.max_context_rounds
            if max_context_rounds is None
            else int(max_context_rounds)
        )
        self.max_context_rounds = max(1, configured_rounds)
        self._history: list[dict[str, str]] = []
        self._pending_user: str | None = None
        self._memory_service: MemoryService | None = None
        self._memory_retriever: MemoryRetriever | None = None
        self._last_memory_context = MemoryContext((), (), "")
        self._last_memory_write: MemoryRecord | None = None
        self._last_memory_error: str | None = None
        if memory_service is not None:
            self.set_memory_service(memory_service, retriever=memory_retriever)

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

    @property
    def memory_service(self) -> MemoryService | None:
        return self._memory_service

    @property
    def last_memory_context(self) -> MemoryContext:
        return self._last_memory_context

    @property
    def last_memory_write(self) -> MemoryRecord | None:
        return self._last_memory_write

    @property
    def last_memory_error(self) -> str | None:
        return self._last_memory_error

    def set_system_prompt(self, system_prompt: str) -> None:
        """Update only future requests; session history remains untouched."""

        self.system_prompt = system_prompt.strip() or DEFAULT_SYSTEM_PROMPT

    def set_system_prompt_provider(
        self,
        provider: Callable[[], str] | None,
    ) -> None:
        """Use one fresh Persona snapshot at the start of every future turn."""

        self._system_prompt_provider = provider
        self.refresh_system_prompt()

    def refresh_system_prompt(self) -> str:
        if self._system_prompt_provider is not None:
            self.set_system_prompt(self._system_prompt_provider())
        return self.system_prompt

    def set_memory_service(
        self,
        memory_service: MemoryService | None,
        *,
        retriever: MemoryRetriever | None = None,
    ) -> None:
        """Use one shared application MemoryService for all future turns."""

        self._memory_service = memory_service
        self._memory_retriever = (
            None
            if memory_service is None
            else retriever or MemoryRetriever(memory_service)
        )
        self._last_memory_context = MemoryContext((), (), "")
        self._last_memory_write = None
        self._last_memory_error = None

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
        self.refresh_system_prompt()
        self._process_user_memory(normalized)
        request_system_prompt = self.system_prompt
        memory_context = self._prepare_memory_context(normalized)
        if memory_context.prompt:
            request_system_prompt = (
                f"{request_system_prompt}\n\n{memory_context.prompt}"
            )
        self._pending_user = normalized
        return (
            {"role": "system", "content": request_system_prompt},
            *self.history,
            {"role": "user", "content": normalized},
        )

    def _process_user_memory(self, user_text: str) -> None:
        """Run exactly once per accepted ordinary user turn, before streaming."""

        self._last_memory_write = None
        self._last_memory_error = None
        if self._memory_service is None:
            return
        try:
            self._last_memory_write = (
                self._memory_service.capture_user_message(user_text)
            )
        except (MemoryDataError, MemoryStorageError) as exc:
            self._last_memory_error = str(exc)
            LOGGER.exception(
                "自动记忆写入失败，数据库=%s：%s",
                self._memory_service.db_path,
                exc,
            )

    def _prepare_memory_context(self, user_text: str) -> MemoryContext:
        if self._memory_service is None or self._memory_retriever is None:
            self._last_memory_context = MemoryContext((), (), "")
            return self._last_memory_context
        try:
            self._memory_service.run_maintenance()
            self._last_memory_context = self._memory_retriever.retrieve(user_text)
        except (MemoryDataError, MemoryStorageError) as exc:
            LOGGER.exception(
                "长期记忆上下文读取失败，数据库=%s：%s",
                self._memory_service.db_path,
                exc,
            )
            self._last_memory_context = MemoryContext((), (), "")
        return self._last_memory_context

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
