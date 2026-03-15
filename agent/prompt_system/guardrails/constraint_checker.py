"""Runs output_schema_guard, safety_policy, injection guard in sequence."""

from agent.prompt_system.guardrails.output_schema_guard import validate_output_schema
from agent.prompt_system.guardrails.prompt_injection_guard import (
    PromptInjectionError,
    check_prompt_injection,
)
from agent.prompt_system.guardrails.safety_policy import SafetyPolicy, check_safety
from agent.prompt_system.prompt_template import PromptTemplate


def check_constraints(
    user_input: str | None,
    response: str,
    template: PromptTemplate,
    safety_policy: SafetyPolicy | None = None,
) -> tuple[bool, str]:
    """
    Run all constraint checks: injection (on user_input), output schema, safety.
    Returns (is_valid, error_message).
    """
    if user_input:
        try:
            check_prompt_injection(user_input)
        except PromptInjectionError as e:
            return False, str(e)

    valid, msg = validate_output_schema(response, template.output_schema)
    if not valid:
        return False, msg

    policy = safety_policy or SafetyPolicy()
    if not check_safety(response, policy):
        return False, "Response violates safety policy"

    return True, ""
