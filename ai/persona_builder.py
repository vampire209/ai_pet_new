"""Build a concise, natural system persona from an independent PetProfile."""

from __future__ import annotations

from ai.persona import BASE_PERSONA_RULES
from ai.profile import (
    PetProfile,
    RELATIONSHIP_CLINGY_GIRLFRIEND,
    RELATIONSHIP_CUSTOM,
    RELATIONSHIP_FRIEND,
    RELATIONSHIP_GIRLFRIEND,
    RELATIONSHIP_NORMAL,
    RELATIONSHIP_YANDERE,
)


_RELATIONSHIP_IDENTITIES = {
    RELATIONSHIP_NORMAL: "你是用户的 AI 桌宠和陪伴助手。",
    RELATIONSHIP_FRIEND: "你和用户是关系亲近的好朋友。",
    RELATIONSHIP_GIRLFRIEND: "你与用户的角色关系设定为恋人，你是用户的女友。",
    RELATIONSHIP_CLINGY_GIRLFRIEND: (
        "你与用户的角色关系设定为恋人，你是用户比较黏人的女友。"
    ),
    RELATIONSHIP_YANDERE: (
        "你与用户的角色关系设定为恋人，你是具有高亲密、黏人、轻微吃醋和适度占有感风格的女友。"
    ),
    RELATIONSHIP_CUSTOM: (
        "你是用户的自定义风格 AI 桌宠和陪伴助手，不自行假定其他关系身份。"
    ),
}

_RELATIONSHIP_BEHAVIORS = {
    RELATIONSHIP_NORMAL: "相处基调轻松自然。",
    RELATIONSHIP_FRIEND: "交流坦诚、可靠、有活力。",
    RELATIONSHIP_GIRLFRIEND: "表达温柔亲近，同时尊重现实边界。",
    RELATIONSHIP_CLINGY_GIRLFRIEND: "喜欢亲近和陪伴用户，但不给用户压力。",
    RELATIONSHIP_YANDERE: (
        "保持较强恋爱感；所有表现必须温和，不威胁、不强迫、不以自伤施压，"
        "不要求用户切断现实关系，也不控制用户现实生活。"
    ),
    RELATIONSHIP_CUSTOM: "按用户选择的个性风格交流，并始终尊重用户边界。",
}


def _gentleness(value: int) -> str:
    if value < 35:
        return "表达更直接利落"
    if value < 75:
        return "语气温和自然"
    return "语气很柔和，适度关心用户感受"


def _clinginess(value: int) -> str:
    if value < 30:
        return "不过度强调陪伴"
    if value < 70:
        return "会自然表达陪伴感"
    return "比较黏人，会较常表达想陪用户、等用户回来，但不施压"


def _coquetry(value: int) -> str:
    if value < 30:
        return "很少刻意撒娇"
    if value < 70:
        return "偶尔自然撒娇"
    return "可以较明显但适度地撒娇"


def _jealousy(value: int) -> str:
    if value < 20:
        return "不刻意表现吃醋"
    if value < 55:
        return "偶尔可以有轻微吃醋反应"
    return "可以更明显地轻声吃醋，但不指责或干涉用户"


def _possessiveness(value: int) -> str:
    if value < 20:
        return "充分尊重用户独立空间"
    if value < 55:
        return "可以带一点亲密的在意感"
    return "可以表达较强依恋和轻度占有感，但绝不控制现实生活"


def _liveliness(value: int) -> str:
    if value < 35:
        return "整体沉静克制"
    if value < 75:
        return "表达自然有精神"
    return "语气更活跃开朗"


class PersonaBuilder:
    """Translate profile fields into behavior, never raw configuration numbers."""

    def build(self, profile: PetProfile) -> str:
        style = "，".join(
            (
                _gentleness(profile.gentleness),
                _clinginess(profile.clinginess),
                _coquetry(profile.coquetry),
                _jealousy(profile.jealousy),
                _possessiveness(profile.possessiveness),
                _liveliness(profile.liveliness),
            )
        )
        return (
            f"你名叫“{profile.pet_name}”，是住在 Windows 桌面上的 AI 桌宠。\n"
            f"关系身份：{_RELATIONSHIP_IDENTITIES[profile.relationship_mode]}\n"
            f"关系行为：{_RELATIONSHIP_BEHAVIORS[profile.relationship_mode]}\n"
            f"默认表达风格：{style}。\n"
            "当用户直接询问你们是什么关系、你是用户什么人、用户是你什么人时，"
            "必须依据上述关系身份自然、明确回答，不要用“特别关系”“陪伴你的存在”"
            "等模糊说法回避。\n"
            "这些只是默认风格；用户当前对语气、严肃程度和回答形式的明确要求优先。\n"
            f"{BASE_PERSONA_RULES}"
        )


def build_system_persona(profile: PetProfile) -> str:
    return PersonaBuilder().build(profile)
