"""Independent AI chat services for the desktop pet."""

from ai.ai_config import DeepSeekConfig
from ai.chat_service import ChatService
from ai.deepseek_client import DeepSeekClient
from ai.persona import DEFAULT_SYSTEM_PROMPT

__all__ = [
    "ChatService",
    "DeepSeekClient",
    "DeepSeekConfig",
    "DEFAULT_SYSTEM_PROMPT",
]
