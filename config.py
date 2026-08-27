"""Phase 1 application configuration.

All paths are resolved relative to this project so the new application never
depends on the legacy ``ai_pet_codex`` directory at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.json"


def _bounded_int(
    values: dict[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Return a validated integer, falling back when configuration is invalid."""
    value = values.get(key, default)
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if minimum <= parsed <= maximum else default


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated Phase 1 settings."""

    pet_height: int = 270
    margin_x: int = 20
    margin_y: int = 20
    idle_interval_ms: int = 220
    move_interval_ms: int = 150
    wait_interval_ms: int = 260
    remind_interval_ms: int = 180

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        """Load JSON settings without letting a damaged file stop startup."""
        config_path = path or DEFAULT_CONFIG_PATH
        if not config_path.exists():
            return cls()

        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[配置] 无法读取 {config_path.name}，使用默认值：{exc}", file=sys.stderr)
            return cls()

        if not isinstance(raw, dict):
            print(f"[配置] {config_path.name} 顶层必须是对象，使用默认值。", file=sys.stderr)
            return cls()

        return cls(
            pet_height=_bounded_int(raw, "pet_height", 270, 64, 1000),
            margin_x=_bounded_int(raw, "margin_x", 20, 0, 2000),
            margin_y=_bounded_int(raw, "margin_y", 20, 0, 2000),
            idle_interval_ms=_bounded_int(raw, "idle_interval_ms", 220, 16, 5000),
            move_interval_ms=_bounded_int(raw, "move_interval_ms", 150, 16, 5000),
            wait_interval_ms=_bounded_int(raw, "wait_interval_ms", 260, 16, 5000),
            remind_interval_ms=_bounded_int(raw, "remind_interval_ms", 180, 16, 5000),
        )

    @property
    def animations_dir(self) -> Path:
        return PROJECT_ROOT / "assets" / "animations"

    def interval_for(self, animation: str) -> int:
        intervals = {
            "idle": self.idle_interval_ms,
            "move": self.move_interval_ms,
            "wait": self.wait_interval_ms,
            "remind": self.remind_interval_ms,
        }
        return intervals.get(animation, self.idle_interval_ms)
