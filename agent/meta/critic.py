"""
Critic: analyzes failure trace to produce a diagnosis.

Inputs: goal, trace (tool_memories), retrieval results, execution outputs, patch results.
Outputs: Diagnosis(failure_type, affected_step, suggestion).
"""

import json
import logging
from dataclasses import dataclass

from agent.memory.state import AgentState

logger = logging.getLogger(__name__)

FAILURE_TYPES = frozenset({
    "retrieval_miss",
    "bad_plan",
    "bad_patch",
    "missing_dependency",
    "timeout",
    "unknown",
})


@dataclass
class Diagnosis:
    """Diagnosis of why an autonomous run failed."""

    failure_type: str
    affected_step: int | None
    suggestion: str

    def to_dict(self) -> dict:
        return {
            "failure_type": self.failure_type,
            "affected_step": self.affected_step,
            "suggestion": self.suggestion,
        }


def _build_critic_prompt(state: AgentState, evaluation_reason: str) -> str:
    """Build user prompt for critic from state and evaluation."""
    goal = state.instruction or ""
    step_results = state.step_results or []
    completed_steps = state.completed_steps or []
    context = state.context or {}

    tool_memories = context.get("tool_memories") or []
    retrieved_files = context.get("retrieved_files") or []
    retrieved_symbols = context.get("retrieved_symbols") or []

    lines = [
        f"Goal: {goal}",
        f"Evaluation reason: {evaluation_reason}",
        "",
        "Step results:",
    ]
    for i, (step, sr) in enumerate(zip(completed_steps, step_results)):
        if not isinstance(step, dict):
            continue
        action = step.get("action", "?")
        desc = (step.get("description") or "")[:150]
        success = getattr(sr, "success", False)
        err = getattr(sr, "error", None) or ""
        classification = getattr(sr, "classification", None)
        files_mod = getattr(sr, "files_modified", None)
        lines.append(f"  Step {i+1}: {action} - success={success} classification={classification}")
        lines.append(f"    description: {desc}")
        if err:
            lines.append(f"    error: {err[:300]}")
        if files_mod:
            lines.append(f"    files_modified: {files_mod}")
    if not step_results:
        lines.append("  (no steps executed)")

    lines.extend([
        "",
        "Retrieval:",
        f"  retrieved_files: {retrieved_files[:10]}{'...' if len(retrieved_files) > 10 else ''}",
        f"  retrieved_symbols count: {len(retrieved_symbols)}",
        "",
        "Trace (tool_memories):",
    ])
    for m in tool_memories[-8:]:
        if isinstance(m, dict):
            lines.append(f"  {m.get('tool', '?')}: {str(m.get('query', ''))[:80]} -> {m.get('result_count', '?')}")

    return "\n".join(lines)


def diagnose(
    state: AgentState,
    evaluation_result: "EvaluationResult",
) -> Diagnosis:
    """
    Diagnose why the run failed. Uses call_small_model with critic_system.yaml.

    Args:
        state: AgentState after the failed run
        evaluation_result: EvaluationResult from evaluator (reason, status)

    Returns:
        Diagnosis with failure_type, affected_step, suggestion
    """
    prompt = _build_critic_prompt(state, evaluation_result.reason)

    try:
        from agent.models.model_client import call_small_model
        from agent.prompt_system import get_registry

        system = get_registry().get_instructions("critic")
        out = call_small_model(
            prompt,
            task_name="critique",
            system_prompt=system,
            max_tokens=512,
        )
        out = (out or "").strip()
        # Parse JSON from output
        idx = out.find("{")
        if idx >= 0:
            end = out.rfind("}")
            if end > idx:
                obj = json.loads(out[idx : end + 1])
                ft = str(obj.get("failure_type", "unknown")).strip().lower()
                if ft not in FAILURE_TYPES:
                    ft = "unknown"
                return Diagnosis(
                    failure_type=ft,
                    affected_step=obj.get("affected_step") if obj.get("affected_step") is not None else None,
                    suggestion=str(obj.get("suggestion", ""))[:500],
                )
    except Exception as e:
        logger.warning("[critic] diagnose failed: %s", e)

    # Fallback: infer from evaluation reason
    reason = evaluation_result.reason or ""
    if "fatal_failure" in reason or "retry" in reason.lower():
        return Diagnosis(
            failure_type="bad_patch",
            affected_step=None,
            suggestion="Retry with different patch or search for correct symbol first.",
        )
    if "limits" in reason or "timeout" in reason.lower():
        return Diagnosis(
            failure_type="timeout",
            affected_step=None,
            suggestion="Simplify plan or expand search scope to find relevant code faster.",
        )
    return Diagnosis(
        failure_type="unknown",
        affected_step=None,
        suggestion="Retry with expanded search scope and revised plan.",
    )


