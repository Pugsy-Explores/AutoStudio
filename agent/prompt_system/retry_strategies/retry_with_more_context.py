"""Retry with widened retrieval budget and re-rank."""

from agent.prompt_system.retry_strategies.retry_with_stricter_prompt import RetryContext


class RetryWithMoreContextStrategy:
    """Widen context budget (e.g. 1.5x) for next attempt."""

    def __init__(self, multiplier: float = 1.5) -> None:
        self.multiplier = multiplier

    def apply(self, context: RetryContext) -> RetryContext:
        new_budget = int(context.context_budget * self.multiplier)
        return RetryContext(
            prompt_name=context.prompt_name,
            version=context.version,
            user_input=context.user_input,
            model_type=context.model_type,
            context_budget=new_budget,
            diagnosis=context.diagnosis,
            extra={**context.extra, "context_expanded": True},
        )
