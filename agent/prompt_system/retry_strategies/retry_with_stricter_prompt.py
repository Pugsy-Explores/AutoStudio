"""Retry with stricter prompt variant (v_strict)."""

from dataclasses import dataclass
from typing import Any, Protocol

from agent.prompt_system.versioning import list_versions


@dataclass
class RetryContext:
    """Context passed through retry strategies."""

    prompt_name: str
    version: str
    user_input: str
    model_type: str
    context_budget: int
    diagnosis: str | None
    extra: dict[str, Any]


class RetryStrategy(Protocol):
    """Protocol for retry strategies."""

    def apply(self, context: RetryContext) -> RetryContext:
        ...


class RetryWithStricterPromptStrategy:
    """Load v_strict variant of the prompt if available."""

    def apply(self, context: RetryContext) -> RetryContext:
        available = list_versions(context.prompt_name)
        if "v_strict" in available:
            return RetryContext(
                prompt_name=context.prompt_name,
                version="v_strict",
                user_input=context.user_input,
                model_type=context.model_type,
                context_budget=context.context_budget,
                diagnosis=context.diagnosis,
                extra=context.extra,
            )
        return context
