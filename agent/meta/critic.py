"""
Critic: analyzes failure trace to produce a diagnosis.

Inputs: goal, trace (tool_memories), retrieval results, execution outputs, patch results.
Outputs: Diagnosis(failure_type, affected_step, suggestion).

Phase 5: Critic.analyze(instruction, attempt_data) returns structured analysis.
Deterministic rules set failure_reason and recommendation; optional LLM generates
analysis and strategy_hint (Phase 5 extension).
"""

import json
import logging
from dataclasses import dataclass

from agent.memory.state import AgentState

logger = logging.getLogger(__name__)

STRATEGY_HINT_PROMPT = """You are a critic for a coding agent.

Analyze the previous attempt and suggest a better strategy.

Instruction:
{instruction}

Execution summary:
{trajectory_summary}

Failure reason:
{failure_reason}

Return JSON:
{{
 "analysis": "...",
 "strategy_hint": "..."
}}
"""

MAX_TRAJECTORY_SUMMARY_CHARS = 1000


def _summarize_trajectory(plan: dict, step_results: list) -> str:
    """
    Convert plan + step_results into a short structured summary for the LLM critic.
    Max length <= MAX_TRAJECTORY_SUMMARY_CHARS. No raw Python objects.
    """
    lines = ["Attempt summary:", "", "Plan:"]
    steps = plan.get("steps") or []
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            continue
        action = (step.get("action") or "?").upper()
        desc = (step.get("description") or "")[:80]
        if desc:
            lines.append(f"{i}. {action} {desc}")
        else:
            lines.append(f"{i}. {action}")
    lines.append("")
    lines.append("Execution results:")
    any_modified = False
    for i, sr in enumerate(step_results, 1):
        if hasattr(sr, "action"):
            action = getattr(sr, "action", "?")
            success = getattr(sr, "success", False)
            status = "success" if success else "failed"
            line = f"{i}. {action} → {status}"
            files_mod = getattr(sr, "files_modified", None) or []
            if files_mod and isinstance(files_mod, list):
                names = [str(f) for f in files_mod[:5]]
                line += f" (files: {', '.join(names)}{'...' if len(files_mod) > 5 else ''})"
                any_modified = True
            patch_size = getattr(sr, "patch_size", None)
            if patch_size and (isinstance(patch_size, int) and patch_size > 0 or isinstance(patch_size, list) and len(patch_size) > 0):
                n = patch_size if isinstance(patch_size, int) else len(patch_size)
                line += f" ({n} patch(es) applied)"
                any_modified = True
            lines.append(line)
        elif isinstance(sr, dict):
            lines.append(f"{i}. {sr.get('action', '?')} → {sr.get('success', False)}")
    if step_results and not any_modified:
        lines.append("No files modified")
    out = "\n".join(lines)
    if len(out) > MAX_TRAJECTORY_SUMMARY_CHARS:
        out = out[: MAX_TRAJECTORY_SUMMARY_CHARS - 3] + "..."
    return out


def _serialize_step_results(step_results: list) -> str:
    """Serialize step_results (StepResult or dict) for LLM prompt."""
    lines = []
    for i, sr in enumerate(step_results, 1):
        if hasattr(sr, "action"):
            action = getattr(sr, "action", "?")
            success = getattr(sr, "success", False)
            err = getattr(sr, "error", None) or ""
            out_preview = ""
            if getattr(sr, "output", None):
                o = sr.output
                out_preview = (o[:200] + "...") if isinstance(o, str) and len(o) > 200 else str(o)[:200]
            lines.append(f"  Step {i}: action={action} success={success} error={err!r} output={out_preview}")
        elif isinstance(sr, dict):
            lines.append(f"  Step {i}: {sr}")
    return "\n".join(lines) if lines else "  (no steps)"


def _generate_strategy_hint_llm(
    instruction: str,
    trajectory_summary: str,
    failure_reason: str,
) -> dict:
    """
    Call reasoning model to produce analysis and strategy_hint.
    Uses trajectory summary (not raw step_results). Returns {"analysis": str, "strategy_hint": str} or empty dict on failure.
    """
    prompt = STRATEGY_HINT_PROMPT.format(
        instruction=(instruction or "")[:1500],
        trajectory_summary=trajectory_summary,
        failure_reason=failure_reason or "unknown",
    )
    try:
        from agent.models.model_client import call_reasoning_model

        out = call_reasoning_model(
            prompt,
            task_name="critic_strategy_hint",
            max_tokens=512,
        )
    except Exception as e:
        logger.warning("[critic] _generate_strategy_hint_llm failed: %s", e)
        return {}

    out = (out or "").strip()
    idx = out.find("{")
    if idx < 0:
        return {}
    end = out.rfind("}")
    if end <= idx:
        return {}
    try:
        obj = json.loads(out[idx : end + 1])
        analysis = str(obj.get("analysis", ""))[:1000]
        strategy_hint = str(obj.get("strategy_hint", ""))[:1000]
        return {"analysis": analysis, "strategy_hint": strategy_hint}
    except json.JSONDecodeError as e:
        logger.warning("[critic] strategy hint JSON parse failed: %s", e)
        return {}


class Critic:
    """
    Phase 5: hybrid critic for attempt-level analysis.
    Deterministic rules set failure_reason (and recommendation); LLM generates
    analysis and strategy_hint when available.
    """

    def analyze(self, instruction: str, attempt_data: dict) -> dict:
        """
        Analyze a failed attempt. Returns:
        {
          "failure_reason": "...",   # from deterministic rules
          "analysis": "...",         # from LLM, fallback to deterministic
          "recommendation": "...",   # from deterministic rules
          "strategy_hint": "..."     # from LLM, fallback to ""
        }
        """
        step_results = attempt_data.get("step_results") or []
        patches_applied = attempt_data.get("patches_applied") or 0
        files_modified = attempt_data.get("files_modified") or []
        plan = attempt_data.get("plan") or {}
        steps = plan.get("steps") or []

        # 1. Deterministic analysis (unchanged)
        has_edit_step = any(
            (s.get("action") or "").upper() == "EDIT" for s in steps if isinstance(s, dict)
        )
        edits_done = (patches_applied and patches_applied > 0) or (
            files_modified and len(files_modified) > 0
        )
        only_search_explain = (
            all(
                (s.get("action") or "").upper() in ("SEARCH", "EXPLAIN")
                for s in steps if isinstance(s, dict)
            )
            if steps
            else True
        )

        if only_search_explain and not edits_done:
            deterministic = {
                "failure_reason": "insufficient_action",
                "analysis": "Attempt was search-only or explain-only with no edits.",
                "recommendation": "Include an EDIT step to implement the user's request.",
            }
        elif not has_edit_step:
            deterministic = {
                "failure_reason": "missing_edit",
                "analysis": "Plan had no EDIT step; task may require code changes.",
                "recommendation": "Add an EDIT step to the plan to apply the requested changes.",
            }
        elif has_edit_step and not edits_done:
            deterministic = {
                "failure_reason": "missing_write",
                "analysis": "EDIT step was planned but no patches were applied or files modified.",
                "recommendation": "Ensure retrieval finds the right locations; retry with expanded search or clearer edit target.",
            }
        else:
            errors = attempt_data.get("errors") or []
            err_preview = "; ".join(str(e)[:100] for e in errors[:3]) if errors else "unknown"
            deterministic = {
                "failure_reason": "goal_not_met",
                "analysis": f"Goal not satisfied. Errors: {err_preview}.",
                "recommendation": "Revise plan using previous attempt context; consider different approach or broader search.",
            }

        # 2. Trajectory summary for LLM (no raw StepResult objects)
        trajectory_summary = _summarize_trajectory(plan, step_results)

        # 3. LLM strategy hint (Phase 5 extension)
        llm_result = _generate_strategy_hint_llm(
            instruction=instruction,
            trajectory_summary=trajectory_summary,
            failure_reason=deterministic["failure_reason"],
        )

        # 4. Merge: deterministic sets failure_reason and recommendation; LLM sets analysis and strategy_hint
        analysis = (llm_result.get("analysis") or "").strip() or deterministic["analysis"]
        strategy_hint = (llm_result.get("strategy_hint") or "").strip()

        return {
            "failure_reason": deterministic["failure_reason"],
            "analysis": analysis,
            "recommendation": deterministic["recommendation"],
            "strategy_hint": strategy_hint,
            "summary_length": len(trajectory_summary),
        }

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
    evidence: str = ""
    suggested_strategy: str = ""

    def to_dict(self) -> dict:
        return {
            "failure_type": self.failure_type,
            "affected_step": self.affected_step,
            "suggestion": self.suggestion,
            "evidence": self.evidence,
            "suggested_strategy": self.suggested_strategy,
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
                    evidence=str(obj.get("evidence", ""))[:1000],
                    suggested_strategy=str(obj.get("suggested_strategy", ""))[:200],
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
            evidence=reason,
            suggested_strategy="retry_edit_with_different_patch",
        )
    if "limits" in reason or "timeout" in reason.lower():
        return Diagnosis(
            failure_type="timeout",
            affected_step=None,
            suggestion="Simplify plan or expand search scope to find relevant code faster.",
            evidence=reason,
            suggested_strategy="expand_search_scope",
        )
    return Diagnosis(
        failure_type="unknown",
        affected_step=None,
        suggestion="Retry with expanded search scope and revised plan.",
        evidence=reason,
        suggested_strategy="rewrite_retrieval_query",
    )


