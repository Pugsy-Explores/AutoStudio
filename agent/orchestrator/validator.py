"""Minimal step validation: SEARCH/EDIT/INFRA/EXPLAIN; optional LLM validation via router."""

import logging

from agent.execution.policy_engine import _is_valid_search_result
from agent.memory.step_result import StepResult
from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType

logger = logging.getLogger(__name__)

_VALIDATE_PROMPT = """Did this step succeed?
Step: {step}
Result success: {success}, output (summary): {output_summary}
Answer with exactly YES or NO."""


def _validate_step_rules(step: dict, result: StepResult) -> bool:
    """Rule-based validation. SEARCH: non-empty results; EDIT: success; INFRA: returncode==0; EXPLAIN: True."""
    action = (step.get("action") or "EXPLAIN").upper()
    if not result.success:
        return False
    if action == "SEARCH":
        out = result.output
        if isinstance(out, dict):
            results = out.get("results") or []
            return _is_valid_search_result(results)
        return False
    if action == "EDIT":
        return True
    if action == "INFRA":
        out = result.output
        if isinstance(out, dict):
            return out.get("returncode", -1) == 0
        return True
    return True


def validate_step(step: dict, result: StepResult, use_llm: bool = False) -> bool:
    """
    Return True if step outcome is considered successful.
    If use_llm=True, get_model_for_task("validation") from config and ask the chosen model; fall back to rules on failure.
    """
    print("[workflow] validator")
    if not use_llm:
        return _validate_step_rules(step, result)
    try:
        model_type = get_model_for_task("validation")
        output_summary = str(result.output)[:200] if result.output else ""
        prompt = _VALIDATE_PROMPT.format(
            step=step,
            success=result.success,
            output_summary=output_summary,
        )
        if model_type == ModelType.REASONING:
            out = call_reasoning_model(prompt, max_tokens=16)
        else:
            out = call_small_model(prompt, max_tokens=16)
        return "yes" in (out or "").strip().lower()
    except Exception as e:
        logger.warning("LLM validation failed, using rules: %s", e)
        return _validate_step_rules(step, result)
