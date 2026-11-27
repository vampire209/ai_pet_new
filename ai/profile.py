"""Validated desktop-pet profile data, separate from chat and Todo state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ai.ability_modes import ABILITY_DEFAULT, normalize_ability_mode


DEFAULT_PET_NAME = "桌宠"
PET_NAME_MAX_LENGTH = 24

RELATIONSHIP_NORMAL = "普通桌宠"
RELATIONSHIP_FRIEND = "好朋友"
RELATIONSHIP_GIRLFRIEND = "女友"
RELATIONSHIP_CLINGY_GIRLFRIEND = "黏人女友"
RELATIONSHIP_YANDERE = "病娇风"
RELATIONSHIP_CUSTOM = "自定义"

RELATIONSHIP_MODES = (
    RELATIONSHIP_NORMAL,
    RELATIONSHIP_FRIEND,
    RELATIONSHIP_GIRLFRIEND,
    RELATIONSHIP_CLINGY_GIRLFRIEND,
    RELATIONSHIP_YANDERE,
    RELATIONSHIP_CUSTOM,
)

TRAIT_FIELDS = (
    "gentleness",
    "clinginess",
    "coquetry",
    "jealousy",
    "possessiveness",
    "liveliness",
)

TRAIT_LABELS = {
    "gentleness": "温柔度",
    "clinginess": "黏人度",
    "coquetry": "撒娇度",
    "jealousy": "吃醋度",
    "possessiveness": "占有欲",
    "liveliness": "活泼度",
}

PROFILE_PRESETS: dict[str, dict[str, int]] = {
    RELATIONSHIP_NORMAL: {
        "gentleness": 65,
        "clinginess": 20,
        "coquetry": 20,
        "jealousy": 0,
        "possessiveness": 0,
        "liveliness": 55,
    },
    RELATIONSHIP_FRIEND: {
        "gentleness": 70,
        "clinginess": 35,
        "coquetry": 25,
        "jealousy": 5,
        "possessiveness": 0,
        "liveliness": 70,
    },
    RELATIONSHIP_GIRLFRIEND: {
        "gentleness": 85,
        "clinginess": 55,
        "coquetry": 60,
        "jealousy": 25,
        "possessiveness": 20,
        "liveliness": 55,
    },
    RELATIONSHIP_CLINGY_GIRLFRIEND: {
        "gentleness": 90,
        "clinginess": 90,
        "coquetry": 80,
        "jealousy": 45,
        "possessiveness": 35,
        "liveliness": 60,
    },
    RELATIONSHIP_YANDERE: {
        "gentleness": 88,
        "clinginess": 92,
        "coquetry": 72,
        "jealousy": 58,
        "possessiveness": 52,
        "liveliness": 48,
    },
}


class ProfileValidationError(ValueError):
    """Raised when an edited profile cannot be accepted."""


def validate_pet_name(value: object) -> str:
    if not isinstance(value, str):
        raise ProfileValidationError("桌宠名字必须是文字。")
    normalized = value.strip()
    if not normalized:
        raise ProfileValidationError("桌宠名字不能为空。")
    if len(normalized) > PET_NAME_MAX_LENGTH:
        raise ProfileValidationError(
            f"桌宠名字最多 {PET_NAME_MAX_LENGTH} 个字符。"
        )
    return normalized


def _validate_trait(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProfileValidationError(f"{TRAIT_LABELS[name]}必须是整数。")
    if not 0 <= value <= 100:
        raise ProfileValidationError(f"{TRAIT_LABELS[name]}必须在 0～100 之间。")
    return value


def preset_values(mode: str) -> dict[str, int]:
    """Return an independent copy of one relationship preset."""

    if mode == RELATIONSHIP_CUSTOM:
        mode = RELATIONSHIP_NORMAL
    try:
        return dict(PROFILE_PRESETS[mode])
    except KeyError as exc:
        raise ProfileValidationError("未知的关系模式。") from exc


@dataclass(frozen=True, slots=True)
class PetProfile:
    pet_name: str = DEFAULT_PET_NAME
    relationship_mode: str = RELATIONSHIP_NORMAL
    ability_mode: str = ABILITY_DEFAULT
    gentleness: int = 65
    clinginess: int = 20
    coquetry: int = 20
    jealousy: int = 0
    possessiveness: int = 0
    liveliness: int = 55

    def __post_init__(self) -> None:
        object.__setattr__(self, "pet_name", validate_pet_name(self.pet_name))
        if self.relationship_mode not in RELATIONSHIP_MODES:
            raise ProfileValidationError("未知的关系模式。")
        object.__setattr__(
            self,
            "ability_mode",
            normalize_ability_mode(self.ability_mode),
        )
        for field in TRAIT_FIELDS:
            object.__setattr__(
                self,
                field,
                _validate_trait(field, getattr(self, field)),
            )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PetProfile":
        """Load tolerant legacy/damaged values without failing application startup."""

        mode_value = raw.get("relationship_mode", RELATIONSHIP_NORMAL)
        mode = mode_value if mode_value in RELATIONSHIP_MODES else RELATIONSHIP_NORMAL
        defaults = preset_values(mode)

        name_value = raw.get("pet_name", DEFAULT_PET_NAME)
        try:
            name = validate_pet_name(name_value)
        except ProfileValidationError:
            name = DEFAULT_PET_NAME

        values: dict[str, int] = {}
        for field in TRAIT_FIELDS:
            candidate = raw.get(field, defaults[field])
            try:
                values[field] = _validate_trait(field, candidate)
            except ProfileValidationError:
                values[field] = defaults[field]
        return cls(
            pet_name=name,
            relationship_mode=mode,
            ability_mode=normalize_ability_mode(raw.get("ability_mode")),
            **values,
        )

    @classmethod
    def for_mode(
        cls,
        relationship_mode: str,
        *,
        pet_name: str = DEFAULT_PET_NAME,
        ability_mode: str = ABILITY_DEFAULT,
    ) -> "PetProfile":
        return cls(
            pet_name=pet_name,
            relationship_mode=relationship_mode,
            ability_mode=ability_mode,
            **preset_values(relationship_mode),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "pet_name": self.pet_name,
            "relationship_mode": self.relationship_mode,
            "ability_mode": self.ability_mode,
            **{field: getattr(self, field) for field in TRAIT_FIELDS},
        }
