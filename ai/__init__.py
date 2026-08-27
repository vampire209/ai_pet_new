"""Independent AI chat services for the desktop pet."""

from ai.ai_config import DeepSeekConfig
from ai.chat_action_router import ChatActionKind, ChatActionRouter, TaskDraft
from ai.chat_service import ChatService
from ai.deepseek_client import DeepSeekClient
from ai.persona import BASE_PERSONA_RULES, DEFAULT_SYSTEM_PROMPT
from ai.persona_builder import PersonaBuilder, build_system_persona
from ai.profile import PetProfile
from ai.profile_service import ProfileService

__all__ = [
    "ChatActionKind",
    "ChatActionRouter",
    "ChatService",
    "DeepSeekClient",
    "DeepSeekConfig",
    "BASE_PERSONA_RULES",
    "DEFAULT_SYSTEM_PROMPT",
    "PersonaBuilder",
    "PetProfile",
    "ProfileService",
    "TaskDraft",
    "build_system_persona",
]
