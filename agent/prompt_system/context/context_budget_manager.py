"""Context budget: compute token budget from model context window."""

from dataclasses import dataclass

# Approximate context windows (tokens) for common models
_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-4": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4o": 128000,
    "gpt-3.5-turbo": 16385,
    "claude-3-opus": 200000,
    "claude-3-sonnet": 200000,
    "claude-3-haiku": 200000,
    "default": 128000,
}


@dataclass
class ContextBudget:
    """Token budget for context. Computes available context from model context window."""

    max_tokens: int
    model_name: str = "default"

    @classmethod
    def for_model(cls, model_name: str) -> "ContextBudget":
        """Create budget from model name (uses known context window)."""
        key = model_name.lower() if model_name else "default"
        for k, v in _MODEL_CONTEXT_WINDOWS.items():
            if k in key or key in k:
                return cls(max_tokens=v, model_name=model_name)
        return cls(max_tokens=_MODEL_CONTEXT_WINDOWS["default"], model_name=model_name)

    def allocate(
        self,
        system_tokens: int = 0,
        history_tokens: int = 0,
        reserve_tokens: int = 500,
    ) -> int:
        """
        Compute available tokens for context.
        available = max_tokens - system_tokens - history_tokens - reserve_tokens
        """
        used = system_tokens + history_tokens + reserve_tokens
        return max(0, self.max_tokens - used)
