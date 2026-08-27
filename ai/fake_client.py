"""Deterministic fake streaming client for tests and zero-cost live demos."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from threading import Event, Lock
import time

from ai.deepseek_client import DeepSeekClientError


class FakeDeepSeekClient:
    """Return scripted chunks without importing the SDK or using the network."""

    is_configured = True

    def __init__(
        self,
        scripted_responses: Iterable[Iterable[str] | Exception] | None = None,
        *,
        chunk_delay_seconds: float = 0.0,
    ) -> None:
        self._responses = list(
            scripted_responses
            if scripted_responses is not None
            else (("你", "好", "呀"),)
        )
        self._response_index = 0
        self._lock = Lock()
        self.chunk_delay_seconds = max(0.0, float(chunk_delay_seconds))
        self.requests: list[tuple[dict[str, str], ...]] = []

    def stream_chat(
        self,
        messages: Iterable[dict[str, str]],
        stop_event: Event | None = None,
    ) -> Iterator[str]:
        request = tuple(dict(message) for message in messages)
        self.requests.append(request)
        with self._lock:
            if self._response_index >= len(self._responses):
                response: Iterable[str] | Exception = ("好的。",)
            else:
                response = self._responses[self._response_index]
                self._response_index += 1
        if isinstance(response, Exception):
            raise response
        for chunk in response:
            if stop_event is not None and stop_event.is_set():
                break
            if self.chunk_delay_seconds:
                time.sleep(self.chunk_delay_seconds)
            if stop_event is not None and stop_event.is_set():
                break
            yield str(chunk)

    @staticmethod
    def cancel_active_stream() -> None:
        return


def fake_api_error(message: str = "模拟 API 异常") -> DeepSeekClientError:
    return DeepSeekClientError(message)
