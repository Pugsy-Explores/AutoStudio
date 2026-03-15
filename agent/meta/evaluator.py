"""
Evaluator: determines whether an autonomous run succeeded.

Rule-based checks first: patch applied? no fatal errors? steps completed?
Optional small-model confirmation for goal completion when ambiguous.
Outputs: SUCCESS | FAILURE | PARTIAL.
"""

import json
import logging
from dataclasses import dataclass

from agent.memory.state import AgentState
from agent.memory.step_result import StepResult

logger = logging.getLogger(__name__)

EVALUATION_STATUS_SUCCESS = "SUCCESS"
EVALUATION_STATUS_FAILURE = "FAILURE"
EVALUATION_STATUS_PARTIAL = "PARTIAL"


@dataclass
class EvaluationResult:
    """Result of evaluating an autonomous run."""

    status: str  # SUCCESS | FAILURE | PARTIAL
    reason: str
    score: float  # 0.0 to 1.0

    def to_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason, "score": self.score}


def _has_fatal_failure(step_results: list[StepResult]) -> bool:
    """True if any step was classified as FATAL_FAILURE."""
    for r in step_results:
        if getattr(r, "classification", None) == "FATAL_FAILURE":
            return True
    return False


def _has_successful_edit(step_results: list[StepResult]) -> bool:
    """True if any EDIT step succeeded and modified files."""
    for r in step_results:
        if getattr(r, "action", "") == "EDIT" and getattr(r, "success", False):
            files = getattr(r, "files_modified", None)
            if files and len(files) > 0:
                return True
    return False


def _count_successful_steps(step_results: list[StepResult]) -> int:
    """Count steps that succeeded."""
    return sum(1 for r in step_results if getattr(r, "success", False))


def _build_rule_based_result(
    state: AgentState,
    result: dict,
    success_criteria: str | None,
) -> EvaluationResult:
    """
    Rule-based evaluation. No model calls.
    """
    step_results = state.step_results or []
    completed = len(state.completed_steps or [])

    if _has_fatal_failure(step_results):
        return EvaluationResult(
            status=EVALUATION_STATUS_FAILURE,
            reason="fatal_failure_in_step",
            score=0.0,
        )

    success_count = _count_successful_steps(step_results)
    stop_reason = result.get("stop_reason", "")
    if stop_reason in ("action_selector_failed", "max_steps", "max_tool_calls", "max_runtime"):
        # Hit limits without clear success
        if completed == 0:
            return EvaluationResult(
                status=EVALUATION_STATUS_FAILURE,
                reason=f"stopped_early:{stop_reason}",
                score=0.0,
            )
        has_edit = _has_successful_edit(step_results)
        if has_edit:
            return EvaluationResult(
                status=EVALUATION_STATUS_PARTIAL,
                reason=f"limits_hit_with_edits:{stop_reason}",
                score=0.5,
            )
        if success_count > 0:
            return EvaluationResult(
                status=EVALUATION_STATUS_PARTIAL,
                reason=f"limits_hit_partial:{success_count}/{completed}:{stop_reason}",
                score=0.3,
            )
        return EvaluationResult(
            status=EVALUATION_STATUS_FAILURE,
            reason=f"limits_hit_no_edits:{stop_reason}",
            score=0.2,
        )

    if completed > 0 and success_count == completed:
        has_edit = _has_successful_edit(step_results)
        if success_criteria == "tests_pass":
            # Cannot verify tests without running them; assume PARTIAL if edits applied
            return EvaluationResult(
                status=EVALUATION_STATUS_PARTIAL if has_edit else EVALUATION_STATUS_SUCCESS,
                reason="all_steps_succeeded" + ("_edits_applied" if has_edit else ""),
                score=0.8 if has_edit else 0.9,
            )
        if success_criteria == "no_syntax_errors":
            return EvaluationResult(
                status=EVALUATION_STATUS_SUCCESS if has_edit else EVALUATION_STATUS_PARTIAL,
                reason="all_steps_succeeded",
                score=0.9,
            )
        # Default: all steps succeeded
        return EvaluationResult(
            status=EVALUATION_STATUS_SUCCESS,
            reason="all_steps_succeeded",
            score=1.0,
        )

    if success_count > 0:
        return EvaluationResult(
            status=EVALUATION_STATUS_PARTIAL,
            reason=f"partial_success:{success_count}/{completed}_steps",
            score=0.5,
        )

    return EvaluationResult(
        status=EVALUATION_STATUS_FAILURE,
        reason="no_successful_steps",
        score=0.0,
    )


def evaluate(
    result: dict,
    state: AgentState,
    success_criteria: str | None = None,
    use_model: bool = False,
) -> EvaluationResult:
    """
    Evaluate an autonomous run.

    Args:
        result: Summary dict from run_autonomous (task_id, goal, completed_steps, stop_reason, etc.)
        state: AgentState after the run (step_results, completed_steps, context)
        success_criteria: Optional hint, e.g. "tests_pass", "no_syntax_errors"
        use_model: If True, call small model for goal completion confirmation when rule-based is ambiguous

    Returns:
        EvaluationResult with status (SUCCESS | FAILURE | PARTIAL), reason, score
    """
    rule_result = _build_rule_based_result(state, result, success_criteria)

    if not use_model:
        return rule_result

    # Optional model confirmation for PARTIAL or borderline SUCCESS
    if rule_result.status == EVALUATION_STATUS_PARTIAL and rule_result.score >= 0.5:
        try:
            from agent.models.model_client import call_small_model

            goal = state.instruction or result.get("goal", "")
            steps_summary = []
            for i, sr in enumerate(state.step_results or []):
                steps_summary.append(
                    f"  {i+1}. {getattr(sr, 'action', '?')} success={getattr(sr, 'success', False)}"
                )
            prompt = f"""Goal: {goal}
Steps completed: {len(state.completed_steps or [])}
Step results:
{chr(10).join(steps_summary[:10])}

Did the agent achieve the goal? Reply with JSON only: {{"achieved": true|false, "confidence": 0.0-1.0}}"""
            out = call_small_model(prompt, task_name="evaluation", max_tokens=128)
            out = (out or "").strip()
            # Try to parse JSON from output
            idx = out.find("{")
            if idx >= 0:
                end = out.rfind("}")
                if end > idx:
                    try:
                        obj = json.loads(out[idx : end + 1])
                        if obj.get("achieved") and obj.get("confidence", 0) >= 0.7:
                            return EvaluationResult(
                                status=EVALUATION_STATUS_SUCCESS,
                                reason="model_confirmed_goal_achieved",
                                score=float(obj.get("confidence", 0.8)),
                            )
                    except json.JSONDecodeError:
                        pass
        except Exception as e:
            logger.warning("[evaluator] model confirmation failed: %s", e)

    return rule_result
