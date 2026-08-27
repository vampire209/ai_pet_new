"""Qt thread-pool worker for non-blocking streamed chat requests."""

from __future__ import annotations

from threading import Event

from PySide6.QtCore import QObject, QRunnable, Signal

from ai.chat_service import ChatService
from ai.deepseek_client import user_facing_error


class ChatWorkerSignals(QObject):
    chunk = Signal(str)
    completed = Signal(str)
    error = Signal(str)
    stopped = Signal(str)
    finished = Signal()


class ChatStreamWorker(QRunnable):
    """Run one stream in a background pool thread and emit UI-safe signals."""

    def __init__(
        self,
        service: ChatService,
        messages: tuple[dict[str, str], ...],
    ) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.service = service
        self.messages = messages
        self.signals = ChatWorkerSignals()
        self._stop_event = Event()

    def request_stop(self) -> None:
        self._stop_event.set()
        self.service.cancel_active_stream()

    def run(self) -> None:
        parts: list[str] = []
        try:
            for chunk in self.service.stream_response(
                self.messages,
                self._stop_event,
            ):
                if self._stop_event.is_set():
                    break
                parts.append(chunk)
                self.signals.chunk.emit(chunk)
            combined = "".join(parts)
            if self._stop_event.is_set():
                self.signals.stopped.emit(combined)
            else:
                self.signals.completed.emit(combined)
        except Exception as exc:
            if self._stop_event.is_set():
                self.signals.stopped.emit("".join(parts))
            else:
                self.signals.error.emit(user_facing_error(exc))
        finally:
            self.signals.finished.emit()
