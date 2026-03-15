"""Retry with critic diagnosis appended to next attempt."""

from agent.prompt_system.retry_strategies.retry_with_stricter_prompt import RetryContext


class RetryWithCriticFeedbackStrategy:
    """
    Append diagnosis to user_input for next attempt.
    Caller should run critic first and pass diagnosis in context.diagnosis.
    """
    def apply(self, context: RetryContext) -> RetryContext:
        if not context.diagnosis:
            return context
        new_input = f"{context.user_input}\n\n[Critic feedback: {context.diagnosis}]"
        return RetryContext(
            prompt_name=context.prompt_name,
            version=context.version,
            user_input=new_input,
            model_type=context.model_type,
            context_budget=context.context_budget,
            diagnosis=context.diagnosis,
            extra=context.extra,
        )
