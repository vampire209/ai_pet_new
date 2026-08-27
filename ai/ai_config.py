"""Centralized DeepSeek configuration without secrets in source code."""

from __future__ import annotations

from dataclasses import dataclass, replace
import os


DEFAULT_PROVIDER = "deepseek"
DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
ALTERNATE_MODEL = "deepseek-v4-pro"
DEFAULT_THINKING_TYPE = "disabled"
DEFAULT_MAX_CONTEXT_ROUNDS = 20
DEFAULT_TIMEOUT_SECONDS = 45.0
DEFAULT_MAX_OUTPUT_TOKENS = 2048
API_KEY_ENVIRONMENT_VARIABLE = "DEEPSEEK_API_KEY"
MODEL_ENVIRONMENT_VARIABLE = "DEEPSEEK_MODEL"


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    """Validated settings shared by the client, service, and UI."""

    provider: str = DEFAULT_PROVIDER
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    thinking_type: str = DEFAULT_THINKING_TYPE
    max_context_rounds: int = DEFAULT_MAX_CONTEXT_ROUNDS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    api_key_environment_variable: str = API_KEY_ENVIRONMENT_VARIABLE

    @classmethod
    def from_environment(cls) -> "DeepSeekConfig":
        model = os.environ.get(MODEL_ENVIRONMENT_VARIABLE, DEFAULT_MODEL).strip()
        return cls(model=model or DEFAULT_MODEL)

    def with_model(self, model: str) -> "DeepSeekConfig":
        normalized = model.strip()
        if not normalized:
            raise ValueError("模型名称不能为空")
        return replace(self, model=normalized)
