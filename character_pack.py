"""Character pack manifest schema, discovery, and action fallback resolution."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any, Final

from app_paths import characters_dir
from config import PROJECT_ROOT


DEFAULT_PACK_ID: Final = "default"
ANIMATED_IMAGE_SUFFIXES: Final[frozenset[str]] = frozenset({".webp", ".gif", ".apng"})
_FRAME_NAME = re.compile(r"^frame_(\d+)\.png$", re.IGNORECASE)

# Rich action categories inspired by Bailong + Feiyu desktop pets.
ALL_ACTION_CATEGORIES: Final[tuple[str, ...]] = (
    "idle",
    "variant",
    "walk",
    "turn",
    "move",
    "drag",
    "poke",
    "wait",
    "remind",
    "think",
    "sleep",
)

ACTION_FALLBACKS: Final[dict[str, tuple[str, ...]]] = {
    "idle": (),
    "variant": ("idle",),
    "walk": ("move", "idle"),
    "turn": ("idle",),
    "move": ("drag", "idle"),
    "drag": ("move", "idle"),
    "poke": ("variant", "idle"),
    "wait": ("idle",),
    "remind": ("idle",),
    "think": ("idle",),
    "sleep": ("idle",),
}


@dataclass(frozen=True, slots=True)
class AnimationClip:
    """Single playable clip: either an animated WebP/GIF file or a PNG sequence folder."""

    action: str
    kind: str  # "animated" or "sequence"
    path: Path
    fps: float = 24.0
    loop: bool = True
    clip_id: str = ""

    @property
    def frame_interval_ms(self) -> int:
        return max(16, int(round(1000.0 / max(1.0, self.fps))))


@dataclass(frozen=True, slots=True)
class CharacterPack:
    """Validated character pack containing one or more animation action categories."""

    pack_id: str
    name: str
    root: Path
    actions: dict[str, tuple[AnimationClip, ...]]
    author: str = ""
    default_height: int | None = None
    avatar_path: Path | None = None
    character_path: Path | None = None

    def clips_for(self, action: str) -> tuple[AnimationClip, ...]:
        return self.actions.get(action, ())

    def resolve_action(self, requested: str) -> tuple[str | None, bool]:
        """Return (resolved_action, matched_exact) using safe fallback chains."""
        if self.actions.get(requested):
            return requested, True
        for fallback in ACTION_FALLBACKS.get(requested, ("idle",)):
            if self.actions.get(fallback):
                return fallback, False
        if self.actions.get("idle"):
            return "idle", False
        return None, False

    @property
    def available_actions(self) -> tuple[str, ...]:
        return tuple(name for name in ALL_ACTION_CATEGORIES if self.actions.get(name))


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _has_png_frames(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    return any(
        entry.is_file() and _FRAME_NAME.fullmatch(entry.name)
        for entry in directory.iterdir()
    )


def _parse_clip_entry(
    action: str,
    entry: Any,
    root: Path,
    default_loop: bool,
) -> AnimationClip | None:
    if isinstance(entry, str):
        raw_path = entry
        fps = 24.0
        loop = default_loop
        clip_id = ""
    elif isinstance(entry, dict):
        raw_path = entry.get("file") or entry.get("path") or ""
        raw_fps = entry.get("fps", 24.0)
        fps = (
            float(raw_fps)
            if isinstance(raw_fps, (int, float))
            and not isinstance(raw_fps, bool)
            and 1.0 <= float(raw_fps) <= 120.0
            else 24.0
        )
        raw_loop = entry.get("loop", default_loop)
        loop = raw_loop if isinstance(raw_loop, bool) else default_loop
        clip_id = str(entry.get("id", "")).strip()
    else:
        return None

    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    candidate = (root / raw_path).resolve()
    if not _is_within(root, candidate):
        return None

    if candidate.is_file() and candidate.suffix.lower() in ANIMATED_IMAGE_SUFFIXES:
        return AnimationClip(
            action=action,
            kind="animated",
            path=candidate,
            fps=fps,
            loop=loop,
            clip_id=clip_id or candidate.stem,
        )
    if candidate.is_dir() and _has_png_frames(candidate):
        return AnimationClip(
            action=action,
            kind="sequence",
            path=candidate,
            fps=fps,
            loop=loop,
            clip_id=clip_id or candidate.name,
        )
    return None


def load_character_pack(pack_dir: Path) -> CharacterPack | None:
    """Load one character pack from ``pack_dir/manifest.json`` safely."""
    manifest_path = pack_dir / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[角色包] 无法读取 {manifest_path}：{exc}", file=sys.stderr)
        return None
    if not isinstance(raw, dict):
        return None

    pack_id = str(raw.get("id") or pack_dir.name).strip() or pack_dir.name
    name = str(raw.get("name") or pack_id).strip() or pack_id
    author = str(raw.get("author") or "").strip()
    raw_height = raw.get("default_height")
    default_height = (
        int(raw_height)
        if isinstance(raw_height, int)
        and not isinstance(raw_height, bool)
        and 64 <= raw_height <= 1000
        else None
    )

    actions_raw = raw.get("actions")
    actions: dict[str, tuple[AnimationClip, ...]] = {}
    if isinstance(actions_raw, dict):
        for action_name, spec in actions_raw.items():
            if not isinstance(action_name, str):
                continue
            normalized_action = action_name.strip().lower()
            if normalized_action not in ALL_ACTION_CATEGORIES:
                continue
            default_loop = normalized_action not in ("poke", "variant", "turn")
            entries = spec if isinstance(spec, list) else [spec]
            parsed = [
                clip
                for item in entries
                if (
                    clip := _parse_clip_entry(
                        normalized_action, item, pack_dir, default_loop
                    )
                )
                is not None
            ]
            if parsed:
                actions[normalized_action] = tuple(parsed)

    if not actions:
        return None

    def _optional_image(key: str) -> Path | None:
        val = raw.get(key)
        if not isinstance(val, str) or not val.strip():
            return None
        candidate = (pack_dir / val).resolve()
        return candidate if _is_within(pack_dir, candidate) and candidate.is_file() else None

    return CharacterPack(
        pack_id=pack_id,
        name=name,
        root=pack_dir.resolve(),
        actions=actions,
        author=author,
        default_height=default_height,
        avatar_path=_optional_image("avatar"),
        character_path=_optional_image("character"),
    )


def builtin_default_pack(animations_dir: Path | None = None) -> CharacterPack:
    """Wrap the project's built-in PNG sequences as the default CharacterPack."""
    root = (animations_dir or (PROJECT_ROOT / "assets" / "animations")).resolve()
    actions: dict[str, tuple[AnimationClip, ...]] = {}
    for action in ("idle", "move", "wait", "remind"):
        folder = root / action
        if _has_png_frames(folder):
            actions[action] = (
                AnimationClip(
                    action=action,
                    kind="sequence",
                    path=folder,
                    fps=5.0,
                    loop=True,
                    clip_id=action,
                ),
            )
    return CharacterPack(
        pack_id=DEFAULT_PACK_ID,
        name="默认桌宠",
        root=root,
        actions=actions,
    )


def discover_character_packs(
    *,
    animations_dir: Path | None = None,
    builtin_characters_dir: Path | None = None,
    user_characters_dir: Path | None = None,
) -> tuple[CharacterPack, ...]:
    """Discover built-in and user-installed character packs without duplicates."""
    packs: dict[str, CharacterPack] = {
        DEFAULT_PACK_ID: builtin_default_pack(animations_dir),
    }
    search_roots = (
        builtin_characters_dir or (PROJECT_ROOT / "assets" / "characters"),
        user_characters_dir or characters_dir(),
    )
    for base in search_roots:
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir(), key=lambda p: p.name.lower()):
            if not child.is_dir():
                continue
            loaded = load_character_pack(child)
            if loaded is not None:
                packs[loaded.pack_id] = loaded
    return tuple(packs.values())
