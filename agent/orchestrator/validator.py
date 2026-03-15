"""Step validation: SEARCH/EDIT/INFRA/EXPLAIN. Loop-aware rules + optional LLM validation."""

import logging
import os

from agent.execution.policy_engine import _is_valid_search_result
from agent.memory.state import AgentState
from agent.memory.step_result import StepResult
from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from agent.prompts import get_prompt

logger = logging.getLogger(__name__)

_VALIDATE_PROMPT = get_prompt("validate_step", "prompt")
ENABLE_LLM_VALIDATION = os.environ.get("ENABLE_LLM_VALIDATION", "0").lower() in ("1", "true", "yes")


def _is_test_file(path: str) -> bool:
    """True if path is under tests/ or filename starts with test_."""
    if not path:
        return False
    p = (path or "").replace("\\", "/").lower()
    return (
        p.startswith("tests/")
        or "/tests/" in p
        or p.endswith("/tests")
        or p.split("/")[-1].startswith("test_")
    )


def _search_results_only_tests(results: list) -> bool:
    """True if all results are from test files."""
    if not results:
        return False
    for r in results:
        f = r.get("file") or ""
        if f and not _is_test_file(f):
            return False
    return True


def _is_valid_explain(result: StepResult) -> bool:
    """Rule-based EXPLAIN validation. No LLM. Reject short outputs; allow if long or references code."""
    out_str = (result.output or "") if isinstance(result.output, str) else str(result.output or "")
    if len(out_str) < 40:
        return False
    # Optional: check if output references code
    if any(x in out_str for x in (".py", "class ", "function", "def ")):
        return True
    return True  # Allow if long enough


def _instruction_suggests_implementation(instruction: str) -> bool:
    """True if user likely wants implementation code, not tests."""
    if not instruction:
        return False
    i = instruction.lower()
    return any(
        x in i
        for x in ("how does", "how do", "routes", "handles", "implementation", "explain how", "step_", "dispatch")
    )


def _validate_step_rules(
    step: dict, result: StepResult, state: AgentState | None = None
) -> tuple[bool, str]:
    """
    Rule-based validation. Returns (valid, feedback).
    When valid=False, feedback is the reason for the replanner.
    """
    action = (step.get("action") or "EXPLAIN").upper()
    if not result.success:
        return False, result.error or "Step execution failed"

    if action == "SEARCH":
        out = result.output
        if not isinstance(out, dict):
            return False, "SEARCH returned no results"
        results = out.get("results") or []
        if not _is_valid_search_result(results):
            return False, "SEARCH returned empty or invalid results (no file/snippet)"
        # Loop-aware: next step is EXPLAIN and user wants implementation, but results are only tests
        if state:
            next_step = state.next_step()
            if next_step and (next_step.get("action") or "").upper() == "EXPLAIN":
                instruction = (getattr(state, "instruction", "") or "")[:200]
                if _instruction_suggests_implementation(instruction) and _search_results_only_tests(results):
                    return False, (
                        "SEARCH returned only test files; next step is EXPLAIN. "
                        "Add a SEARCH step targeting implementation code (e.g. step_dispatcher, agent/execution)."
                    )
        return True, ""

    if action == "EDIT":
        return True, ""

    if action == "INFRA":
        out = result.output
        if isinstance(out, dict) and out.get("returncode", -1) != 0:
            return False, f"INFRA failed with returncode {out.get('returncode', -1)}"
        return True, ""

    if action == "EXPLAIN":
        out_str = (result.output or "") if isinstance(result.output, str) else str(result.output or "")
        if "I cannot answer without relevant code context" in out_str:
            return False, "EXPLAIN received empty context. Add SEARCH step before EXPLAIN."
        if not _is_valid_explain(result):
            return False, (
                "EXPLAIN failed: output too short or lacks code references. "
                "Add a SEARCH step before EXPLAIN to locate implementation code."
            )
        return True, ""

    return True, ""


def validate_step(
    step: dict,
    result: StepResult,
    state: AgentState | None = None,
    use_llm: bool | None = None,
) -> tuple[bool, str]:
    """
    Return (valid, feedback). When valid=False, feedback is the reason for the replanner.
    If state is provided, uses loop-aware rules (e.g. SEARCH tests-only + next EXPLAIN → invalid).
    If use_llm=True (or ENABLE_LLM_VALIDATION=1 when use_llm is None), uses LLM for EXPLAIN/SEARCH when rules pass.
    """
    print("[workflow] validator")
    use_llm_val = use_llm if use_llm is not None else ENABLE_LLM_VALIDATION
    valid, feedback = _validate_step_rules(step, result, state)
    if not valid:
        return False, feedback
    if not use_llm_val:
        return True, ""
    # LLM validation for ambiguous cases (rules passed but may be insufficient)
    action = (step.get("action") or "EXPLAIN").upper()
    if action not in ("SEARCH", "EXPLAIN"):
        return True, ""
    try:
        model_type = get_model_for_task("validation")
        output_summary = str(result.output)[:600] if result.output else ""
        instruction = (getattr(state, "instruction", "") or "")[:300] if state else ""
        next_step = state.next_step() if state else None
        next_desc = next_step.get("description", "")[:100] if next_step else "none"
        prompt = _VALIDATE_PROMPT.format(
            step=step,
            success=result.success,
            output_summary=output_summary,
            instruction=instruction,
            next_step_description=next_desc,
        )
        if model_type == ModelType.REASONING:
            out = call_reasoning_model(prompt, task_name="validation")
        else:
            out = call_small_model(prompt, task_name="validation")
        llm_valid = "yes" in (out or "").strip().lower()
        if not llm_valid:
            return False, f"Validation: step output does not sufficiently address the task. LLM: {out[:150]}"
        return True, ""
    except Exception as e:
        logger.warning("LLM validation failed, using rules: %s", e)
        return _validate_step_rules(step, result, state)
