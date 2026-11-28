"""Persistent desktop pet window geometry and behavior preferences."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any

from app_paths import pet_state_path


def default_pet_state_path() -> Path:
    return pet_state_path()


DEFAULT_GEMINI_URL = "https://gemini.google.com/"


@dataclass(frozen=True, slots=True)
class PetWindowState:
    """Validated window position, topmost, wandering, fullscreen yield, character pack, and Phase 14/15 HUD prefs."""

    x: int | None = None
    y: int | None = None
    always_on_top: bool = True
    wandering_enabled: bool = True
    fullscreen_yield_enabled: bool = True
    character_pack: str = "default"
    status_pill_enabled: bool = True
    companion_bubble_enabled: bool = True
    window_awareness_enabled: bool = True
    panel_detached: bool = False
    panel_x: int | None = None
    panel_y: int | None = None
    character_visible: bool = True
    mouse_through: bool = False
    position_locked: bool = False
    gemini_url: str = DEFAULT_GEMINI_URL

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PetWindowState":
        raw_x = raw.get("x")
        raw_y = raw.get("y")
        x = raw_x if isinstance(raw_x, int) and not isinstance(raw_x, bool) else None
        y = raw_y if isinstance(raw_y, int) and not isinstance(raw_y, bool) else None
        if x is None or y is None:
            x, y = None, None
        raw_top = raw.get("always_on_top", True)
        always_on_top = raw_top if isinstance(raw_top, bool) else True
        raw_wander = raw.get("wandering_enabled", True)
        wandering_enabled = raw_wander if isinstance(raw_wander, bool) else True
        raw_yield = raw.get("fullscreen_yield_enabled", True)
        fullscreen_yield_enabled = raw_yield if isinstance(raw_yield, bool) else True
        raw_pack = raw.get("character_pack", "default")
        character_pack = (
            raw_pack.strip()
            if isinstance(raw_pack, str) and raw_pack.strip()
            else "default"
        )
        raw_pill = raw.get("status_pill_enabled", True)
        status_pill_enabled = raw_pill if isinstance(raw_pill, bool) else True
        raw_bubble = raw.get("companion_bubble_enabled", True)
        companion_bubble_enabled = raw_bubble if isinstance(raw_bubble, bool) else True
        raw_aware = raw.get("window_awareness_enabled", True)
        window_awareness_enabled = raw_aware if isinstance(raw_aware, bool) else True
        raw_detached = raw.get("panel_detached", False)
        panel_detached = raw_detached if isinstance(raw_detached, bool) else False
        raw_px = raw.get("panel_x")
        raw_py = raw.get("panel_y")
        panel_x = raw_px if isinstance(raw_px, int) and not isinstance(raw_px, bool) else None
        panel_y = raw_py if isinstance(raw_py, int) and not isinstance(raw_py, bool) else None
        if panel_x is None or panel_y is None:
            panel_x, panel_y = None, None
        raw_char_vis = raw.get("character_visible", True)
        character_visible = raw_char_vis if isinstance(raw_char_vis, bool) else True
        raw_through = raw.get("mouse_through", False)
        mouse_through = raw_through if isinstance(raw_through, bool) else False
        raw_locked = raw.get("position_locked", False)
        position_locked = raw_locked if isinstance(raw_locked, bool) else False
        raw_gemini = raw.get("gemini_url", DEFAULT_GEMINI_URL)
        gemini_url = (
            raw_gemini.strip()
            if isinstance(raw_gemini, str)
            and (
                raw_gemini.strip().startswith("https://")
                or raw_gemini.strip().startswith("http://")
            )
            else DEFAULT_GEMINI_URL
        )
        return cls(
            x=x,
            y=y,
            always_on_top=always_on_top,
            wandering_enabled=wandering_enabled,
            fullscreen_yield_enabled=fullscreen_yield_enabled,
            character_pack=character_pack,
            status_pill_enabled=status_pill_enabled,
            companion_bubble_enabled=companion_bubble_enabled,
            window_awareness_enabled=window_awareness_enabled,
            panel_detached=panel_detached,
            panel_x=panel_x,
            panel_y=panel_y,
            character_visible=character_visible,
            mouse_through=mouse_through,
            position_locked=position_locked,
            gemini_url=gemini_url,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 4,
            "x": self.x,
            "y": self.y,
            "always_on_top": self.always_on_top,
            "wandering_enabled": self.wandering_enabled,
            "fullscreen_yield_enabled": self.fullscreen_yield_enabled,
            "character_pack": self.character_pack,
            "status_pill_enabled": self.status_pill_enabled,
            "companion_bubble_enabled": self.companion_bubble_enabled,
            "window_awareness_enabled": self.window_awareness_enabled,
            "panel_detached": self.panel_detached,
            "panel_x": self.panel_x,
            "panel_y": self.panel_y,
            "character_visible": self.character_visible,
            "mouse_through": self.mouse_through,
            "position_locked": self.position_locked,
            "gemini_url": self.gemini_url,
        }


class PetStateStore:
    """Atomically load and save PetWindowState under the user data directory."""

    def __init__(self, config_path: Path | str | None = None) -> None:
        self.config_path = (
            Path(config_path) if config_path is not None else default_pet_state_path()
        )

    def load(self) -> PetWindowState:
        if not self.config_path.is_file():
            return PetWindowState()
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[桌宠状态] 读取失败，使用默认值：{exc}", file=sys.stderr)
            return PetWindowState()
        if not isinstance(raw, dict):
            return PetWindowState()
        return PetWindowState.from_dict(raw)

    def save(self, state: PetWindowState) -> bool:
        temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.config_path)
            return True
        except OSError as exc:
            print(f"[桌宠状态] 保存失败：{exc}", file=sys.stderr)
            return False
