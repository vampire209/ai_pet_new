"""Single authority for pet animation state and priority."""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import QObject, Signal

from animation_manager import AnimationManager


class AnimationStateController(QObject):
    """Resolve remind > move > wait > idle without competing direct writes."""

    PRIORITY: Final[tuple[str, ...]] = ("remind", "move", "wait")
    VALID_STATES: Final[frozenset[str]] = frozenset((*PRIORITY, "idle"))

    state_changed = Signal(str)

    def __init__(self, manager: AnimationManager, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self._active = {state: False for state in self.PRIORITY}
        self._current_state = ""
        self._refresh()

    def set_active(self, state: str, active: bool) -> None:
        if state not in self.VALID_STATES:
            raise ValueError(f"Unsupported animation state: {state}")
        if state == "idle":
            return
        normalized = bool(active)
        if self._active[state] == normalized:
            return
        self._active[state] = normalized
        self._refresh()

    def set_moving(self, active: bool) -> None:
        self.set_active("move", active)

    def set_waiting(self, active: bool) -> None:
        self.set_active("wait", active)

    def set_reminding(self, active: bool) -> None:
        self.set_active("remind", active)

    def _refresh(self) -> None:
        desired = next(
            (state for state in self.PRIORITY if self._active[state]),
            "idle",
        )
        if desired == self._current_state and self._manager.current_animation is not None:
            return
        self._current_state = desired
        self._manager.play_animation(desired)
        self.state_changed.emit(desired)

    @property
    def current_state(self) -> str:
        """The requested logical state; the rendered animation may be idle fallback."""
        return self._current_state
