"""DeepSeek Chat Completions transport using the OpenAI-compatible SDK."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
import os
from threading import Event, Lock
from typing import Any

from ai.ai_config import DeepSeekConfig


class DeepSeekClientError(RuntimeError):
    """Safe user-facing API error with no secret or traceback details."""


class MissingAPIKeyError(DeepSeekClientError):
    """Raised when DEEPSEEK_API_KEY is unavailable."""


class DeepSeekClient:
    """Small synchronous streaming transport intended for a worker thread."""

    def __init__(
        self,
        config: DeepSeekConfig | None = None,
        *,
        api_key: str | None = None,
        sdk_client: Any | None = None,
        client_factory: Any | None = None,
    ) -> None:
        self.config = config or DeepSeekConfig.from_environment()
        environment_key = os.environ.get(
            self.config.api_key_environment_variable,
            "",
        )
        self._api_key = (api_key if api_key is not None else environment_key).strip()
        self._sdk_client = sdk_client
        self._client_factory = client_factory
        self._active_stream: Any | None = None
        self._stream_lock = Lock()

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key or self._sdk_client is not None)

    def reconfigure(
        self,
        config: DeepSeekConfig,
        *,
        api_key: str | None = None,
    ) -> None:
        """Apply updated GUI/environment settings without restarting the app."""
        self.config = config
        if api_key is not None:
            self._api_key = api_key.strip()
        else:
            self._api_key = os.environ.get(
                self.config.api_key_environment_variable,
                "",
            ).strip()
        # Reset lazily constructed SDK client unless an injected mock/test client is fixed.
        if self._client_factory is None:
            self._sdk_client = None

    def stream_chat(
        self,
        messages: Iterable[dict[str, str]],
        stop_event: Event | None = None,
    ) -> Iterator[str]:
        if not self.is_configured:
            raise MissingAPIKeyError(
                "未配置 DeepSeek API Key，请先设置 DEEPSEEK_API_KEY。"
            )

        client = self._ensure_client()
        try:
            stream = client.chat.completions.create(
                model=self.config.model,
                messages=[dict(message) for message in messages],
                stream=True,
                max_tokens=self.config.max_output_tokens,
                extra_body={
                    "thinking": {
                        "type": self.config.thinking_type,
                    }
                },
            )
        except Exception as exc:
            raise _map_api_error(exc) from None

        with self._stream_lock:
            self._active_stream = stream
        try:
            for chunk in stream:
                if stop_event is not None and stop_event.is_set():
                    self._close_stream(stream)
                    break
                content = _extract_chunk_content(chunk)
                if content:
                    yield content
        except Exception as exc:
            if stop_event is None or not stop_event.is_set():
                raise _map_api_error(exc) from None
        finally:
            with self._stream_lock:
                if self._active_stream is stream:
                    self._active_stream = None

    def cancel_active_stream(self) -> None:
        with self._stream_lock:
            stream = self._active_stream
        if stream is not None:
            self._close_stream(stream)

    def _ensure_client(self) -> Any:
        if self._sdk_client is not None:
            return self._sdk_client
        try:
            if self._client_factory is None:
                from openai import OpenAI

                factory = OpenAI
            else:
                factory = self._client_factory
            self._sdk_client = factory(
                api_key=self._api_key,
                base_url=self.config.base_url,
                timeout=self.config.timeout_seconds,
                max_retries=0,
            )
        except ImportError:
            raise DeepSeekClientError(
                "缺少 openai 依赖，请先安装 requirements.txt。"
            ) from None
        except Exception as exc:
            raise _map_api_error(exc) from None
        return self._sdk_client

    @staticmethod
    def _close_stream(stream: Any) -> None:
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def _extract_chunk_content(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    if delta is None:
        return ""
    content = getattr(delta, "content", None)
    return content if isinstance(content, str) else ""


def _map_api_error(exc: Exception) -> DeepSeekClientError:
    if isinstance(exc, DeepSeekClientError):
        return exc
    name = type(exc).__name__.lower()
    status_code = getattr(exc, "status_code", None)
    raw_message = str(exc).lower()

    if "authentication" in name or status_code in {401, 403}:
        message = "DeepSeek API 认证失败，请检查 API Key。"
    elif "timeout" in name or isinstance(exc, TimeoutError):
        message = "DeepSeek API 请求超时，请重试。"
    elif "connection" in name or "network" in name:
        message = "网络连接失败，请稍后重试。"
    elif "ratelimit" in name or status_code == 429:
        message = "DeepSeek API 请求过于频繁，请稍后重试。"
    elif status_code == 402 or any(
        token in raw_message
        for token in ("insufficient balance", "insufficient_quota", "余额", "额度")
    ):
        message = "DeepSeek API 余额或额度不足，请检查账户。"
    elif status_code == 404 or "model" in raw_message and "not found" in raw_message:
        message = "DeepSeek 模型不可用，请检查模型名称。"
    elif isinstance(status_code, int) and status_code >= 500:
        message = "DeepSeek 服务暂时异常，请稍后重试。"
    else:
        message = "DeepSeek 请求失败，请稍后重试。"
    return DeepSeekClientError(message)


def user_facing_error(exc: Exception) -> str:
    """Return a short safe message for UI and worker signals."""

    return str(_map_api_error(exc))
