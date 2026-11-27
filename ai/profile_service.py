"""Safe user-directory persistence for the independent desktop-pet profile."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sys

from PySide6.QtCore import QObject, Signal

from ai.profile import PetProfile
from app_paths import profile_config_path


class ProfileStorageError(RuntimeError):
    """Raised when a valid profile cannot be persisted."""


def default_profile_config_path() -> Path:
    return profile_config_path()


@dataclass(frozen=True, slots=True)
class ProfileLoadResult:
    profile: PetProfile
    used_default: bool = False


class ProfileService(QObject):
    """Own the current profile and notify open UI surfaces after a real save."""

    profile_changed = Signal(object)

    def __init__(
        self,
        config_path: Path | str | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_path = (
            Path(config_path)
            if config_path is not None
            else default_profile_config_path()
        )
        self._profile = self._load().profile

    @property
    def profile(self) -> PetProfile:
        return self._profile

    def reload(self) -> PetProfile:
        self._profile = self._load().profile
        return self._profile

    def save(self, profile: PetProfile) -> PetProfile:
        if not isinstance(profile, PetProfile):
            raise TypeError("profile 必须是 PetProfile。")
        payload = {
            "version": 1,
            **profile.to_dict(),
        }
        temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.config_path)
        except OSError as exc:
            raise ProfileStorageError(f"无法保存桌宠设置：{exc}") from exc
        self._profile = profile
        self.profile_changed.emit(profile)
        return profile

    def _load(self) -> ProfileLoadResult:
        if not self.config_path.is_file():
            return ProfileLoadResult(PetProfile(), used_default=True)
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"[桌宠设置] 配置读取失败，使用默认值：{exc}", file=sys.stderr)
            return ProfileLoadResult(PetProfile(), used_default=True)
        if not isinstance(raw, dict):
            print("[桌宠设置] 配置顶层不是对象，使用默认值。", file=sys.stderr)
            return ProfileLoadResult(PetProfile(), used_default=True)
        return ProfileLoadResult(PetProfile.from_dict(raw))

