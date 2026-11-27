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


@dataclass(frozen=True, slots=True)
class PetWindowState:
    """Validated window position, topmost, wandering, fullscreen yield, and character pack."""

    x: int | None = None
    y: int | None = None
    always_on_top: bool = True
    wandering_enabled: bool = True
    fullscreen_yield_enabled: bool = True
    character_pack: str = "default"

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
        return cls(
            x=x,
            y=y,
            always_on_top=always_on_top,
            wandering_enabled=wandering_enabled,
            fullscreen_yield_enabled=fullscreen_yield_enabled,
            character_pack=character_pack,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 2,
            "x": self.x,
            "y": self.y,
            "always_on_top": self.always_on_top,
            "wandering_enabled": self.wandering_enabled,
            "fullscreen_yield_enabled": self.fullscreen_yield_enabled,
            "character_pack": self.character_pack,
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
