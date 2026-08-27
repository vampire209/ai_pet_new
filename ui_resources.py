"""Centralized, replaceable UI artwork and application icon loading."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtGui import QIcon, QPixmap

from config import PROJECT_ROOT


SUPPORTED_UI_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
SUPPORTED_ICON_SUFFIXES = frozenset({".ico", ".png"})


@dataclass(frozen=True, slots=True)
class LoadedPixmap:
    """An original, unscaled pixmap and the source file it came from."""

    path: Path
    pixmap: QPixmap


@dataclass(frozen=True, slots=True)
class UiAssetPaths:
    """Stable locations that can receive dedicated artwork later."""

    assets_dir: Path = PROJECT_ROOT / "assets"

    @property
    def icons_dir(self) -> Path:
        return self.assets_dir / "icons"

    @property
    def avatar_dir(self) -> Path:
        return self.assets_dir / "ui" / "avatar"

    @property
    def character_dir(self) -> Path:
        return self.assets_dir / "ui" / "character"

    @property
    def idle_fallback(self) -> Path:
        return self.assets_dir / "animations" / "idle" / "frame_01.png"


UI_ASSETS = UiAssetPaths()


def _first_valid_file(
    directory: Path,
    preferred_names: tuple[str, ...],
    suffixes: frozenset[str],
) -> Path | None:
    if not directory.is_dir():
        return None

    for name in preferred_names:
        candidate = directory / name
        if candidate.is_file() and candidate.suffix.lower() in suffixes:
            return candidate

    candidates = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix.lower() in suffixes
        ),
        key=lambda path: path.name.casefold(),
    )
    return candidates[0] if candidates else None


def _load_original_pixmap(
    directory: Path,
    preferred_names: tuple[str, ...],
) -> LoadedPixmap:
    source = _first_valid_file(
        directory,
        preferred_names,
        SUPPORTED_UI_IMAGE_SUFFIXES,
    )
    if source is not None:
        pixmap = QPixmap(str(source))
        if not pixmap.isNull():
            return LoadedPixmap(source, pixmap)

    fallback = UI_ASSETS.idle_fallback
    return LoadedPixmap(fallback, QPixmap(str(fallback)))


def load_avatar_source() -> LoadedPixmap:
    """Load a dedicated original avatar, or the original idle frame."""

    return _load_original_pixmap(
        UI_ASSETS.avatar_dir,
        ("avatar.png", "avatar.webp", "avatar.jpg", "avatar.jpeg"),
    )


def load_character_source() -> LoadedPixmap:
    """Load dedicated original character art, or the original idle frame."""

    return _load_original_pixmap(
        UI_ASSETS.character_dir,
        ("character.png", "character.webp", "character.jpg", "character.jpeg"),
    )


def application_icon_source_path() -> Path:
    """Return the replaceable application icon source with a safe fallback."""

    icon = _first_valid_file(
        UI_ASSETS.icons_dir,
        ("app.ico", "app.png", "icon.ico", "icon.png"),
        SUPPORTED_ICON_SUFFIXES,
    )
    return icon or UI_ASSETS.idle_fallback


def load_application_icon() -> QIcon:
    """Load the app icon without ever falling back to Qt's default icon."""

    source = application_icon_source_path()
    icon = QIcon(str(source))
    if not icon.isNull():
        return icon
    fallback = QPixmap(str(UI_ASSETS.idle_fallback))
    return QIcon(fallback) if not fallback.isNull() else QIcon()
