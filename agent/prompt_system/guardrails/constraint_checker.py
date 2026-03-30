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
    *,
    relax_actions: bool = False,
) -> tuple[bool, str]:
    """
    Run all constraint checks: injection (on user_input), output schema, safety.
    Returns (is_valid, error_message).
    relax_actions: When True (planner-only recovery), skip action validation in safety check.
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
    safe, safety_msg = check_safety(response, policy, relax_actions=relax_actions)
    if not safe:
        return False, safety_msg or "Response violates safety policy"

    return True, ""
