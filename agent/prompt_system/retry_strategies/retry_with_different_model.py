"""Retry with different model (SMALL <-> REASONING)."""

from agent.models.model_types import ModelType

from agent.prompt_system.retry_strategies.retry_with_stricter_prompt import RetryContext


class RetryWithDifferentModelStrategy:
    """Flip model type: SMALL -> REASONING or REASONING -> SMALL."""

    def apply(self, context: RetryContext) -> RetryContext:
        current = context.model_type.upper() if context.model_type else "REASONING"
        if current == "SMALL":
            new_type = ModelType.REASONING
        else:
            new_type = ModelType.SMALL
        return RetryContext(
            prompt_name=context.prompt_name,
            version=context.version,
            user_input=context.user_input,
            model_type=new_type.value,
            context_budget=context.context_budget,
            diagnosis=context.diagnosis,
            extra=context.extra,
        )
