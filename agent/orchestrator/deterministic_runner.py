"""Deterministic execution loop: plan -> execution_loop (goal evaluator, no step retries). Single source of truth for Mode 1."""

import logging
import time

from config.agent_config import MAX_CONTEXT_CHARS
from agent.memory.state import AgentState
from agent.observability.trace_logger import log_event
from agent.orchestrator.execution_loop import ExecutionLoopMode, execution_loop
from agent.orchestrator.goal_evaluator import GoalEvaluator
from agent.orchestrator.plan_resolver import get_parent_plan, get_plan
from planner.planner_utils import is_explicit_docs_lane_by_structure

logger = logging.getLogger(__name__)


def _build_phase_agent_state(
    phase_plan: dict,
    project_root: str,
    parent_instruction: str,
    trace_id: str | None,
    similar_tasks: list[dict] | None,
    context_handoff: dict | None,
    parent_plan_id: str,
) -> AgentState:
    """Build a fresh AgentState for a phase with phase-scoped instruction and context."""
    subgoal = phase_plan.get("subgoal", "")
    steps = phase_plan.get("steps", [])
    plan_id = phase_plan.get("plan_id", "")
    lane = phase_plan.get("lane", "code")

    context = {
        "tool_node": "START",
        "retrieved_files": [],
        "retrieved_symbols": [],
        "retrieved_references": [],
        "context_snippets": [],
        "ranked_context": [],
        "context_candidates": [],
        "ranking_scores": [],
        "project_root": project_root,
        "instruction": subgoal,
        "trace_id": trace_id,
        "similar_past_tasks": similar_tasks or [],
        "parent_instruction": parent_instruction,
        "dominant_artifact_mode": lane,
        "parent_plan_id": parent_plan_id,
        "current_phase_index": phase_plan.get("phase_index", 0),
        "phase_results": [],
        "parent_policy_history": [],
    }
    if context_handoff:
        for k, v in context_handoff.items():
            context[k] = v

    return AgentState(
        instruction=subgoal,
        current_plan={"steps": steps, "plan_id": plan_id},
        context=context,
    )


def _extract_phase_context_output(phase_state: AgentState) -> dict:
    """Extract context output from a phase's final state."""
    ctx = phase_state.context or {}
    ranked = list(ctx.get("ranked_context") or [])
    symbols = list(ctx.get("retrieved_symbols") or [])
    files = list(ctx.get("retrieved_files") or [])

    files_modified = []
    patch_count = 0
    for sr in (phase_state.step_results or []):
        fm = getattr(sr, "files_modified", None) or []
        if isinstance(fm, list):
            files_modified.extend(fm)
        pm = getattr(sr, "patch_size", None)
        if isinstance(pm, int):
            patch_count += pm
        elif isinstance(pm, list):
            patch_count += len(pm)

    return {
        "ranked_context": ranked,
        "retrieved_symbols": symbols,
        "retrieved_files": files,
        "files_modified": files_modified,
        "patches_applied": patch_count,
    }


def _build_phase_context_handoff(phase_result: dict) -> tuple[dict, bool]:
    """Build handoff dict from phase_result; prune ranked_context if too large. Returns (handoff, pruned)."""
    co = phase_result.get("context_output") or {}
    ranked = list(co.get("ranked_context") or [])
    symbols = list(co.get("retrieved_symbols") or [])
    files = list(co.get("retrieved_files") or [])

    budget = MAX_CONTEXT_CHARS // 2
    est = 0
    pruned = False
    for i, item in enumerate(ranked):
        est += len(str(item))
        if est > budget:
            ranked = ranked[:i]
            pruned = True
            break

    handoff = {
        "prior_phase_ranked_context": ranked,
        "prior_phase_retrieved_symbols": symbols,
        "prior_phase_files": files,
    }
    return handoff, pruned


def _apply_parent_stage2_policy(phase_result: dict, phase_index: int) -> str:
    """Return CONTINUE only if phase succeeded; else STOP."""
    if not phase_result:
        return "STOP"
    if phase_result.get("success") is True and phase_result.get("goal_met") is True:
        return "CONTINUE"
    return "STOP"


def _aggregate_parent_goal(phase_results: list) -> tuple[bool, str]:
    """Return (True, 'all_phases_succeeded') iff all goal_met; else (False, 'phase_N_failed')."""
    if not phase_results:
        return (False, "no_phases_executed")
    for i, pr in enumerate(phase_results):
        if not pr.get("goal_met", False):
            return (False, f"phase_{i}_failed")
    return (True, "all_phases_succeeded")


def _derive_phase_failure_class(loop_result, goal_met: bool) -> str | None:
    """Derive failure class from loop result when goal_met is False."""
    if goal_met:
        return None
    state = getattr(loop_result, "state", None)
    if not state:
        return "goal_not_satisfied"
    ctx = getattr(state, "context", None) or {}
    if ctx.get("lane_violations"):
        return "lane_violation"
    if ctx.get("termination_reason") == "stall_detected":
        return "stall_detected"
    lo = getattr(loop_result, "loop_output", None) or {}
    errors = lo.get("errors_encountered", [])
    for e in errors:
        if "max_task_runtime_exceeded" in str(e):
            return "timeout"
        if "max_steps" in str(e) or "max_tool_calls" in str(e):
            return "limit_exceeded"
    return "goal_not_satisfied"


def _build_hierarchical_loop_output(
    phase_results: list,
    start_time: float,
    last_phase_state: AgentState | None,
) -> dict:
    """Build aggregated loop_output from phase results."""
    completed_steps = sum(pr.get("completed_steps", 0) for pr in phase_results)
    files_modified = []
    patches_applied = 0
    errors_encountered = []
    tool_calls = 0

    for idx, pr in enumerate(phase_results):
        co = pr.get("context_output") or {}
        fm = co.get("files_modified") or []
        if isinstance(fm, list):
            files_modified.extend(fm)
        patches_applied += co.get("patches_applied", 0) if isinstance(co.get("patches_applied"), (int, float)) else 0
        lo = pr.get("loop_output") or {}
        errors_encountered.extend(lo.get("errors_encountered") or [])
        tool_calls += lo.get("tool_calls", 0)

        if not pr.get("success", False):
            fc = pr.get("failure_class")
            err = f"phase_{idx}_failed:{fc}" if fc else f"phase_{idx}_goal_not_met"
            if err not in errors_encountered:
                errors_encountered.append(err)

    plan_result = last_phase_state.current_plan if last_phase_state else None

    return {
        "completed_steps": completed_steps,
        "files_modified": files_modified,
        "patches_applied": patches_applied,
        "errors_encountered": errors_encountered,
        "tool_calls": tool_calls,
        "plan_result": plan_result,
        "start_time": start_time,
        "phase_results": phase_results,
    }


def run_deterministic(
    instruction: str,
    project_root: str,
    *,
    trace_id: str | None = None,
    similar_tasks: list[dict] | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
    max_runtime_seconds: int | None = None,
) -> tuple[AgentState, dict]:
    """
    Run deterministic loop: get_plan -> execution_loop (goal evaluator on plan exhaustion, no step retries).
    Returns (state, loop_output) where loop_output has completed_steps, patches_applied, files_modified,
    errors_encountered, tool_calls, plan_result, start_time.

    Phase 5: retry_context (previous_attempts, critic_feedback) is passed to get_plan when provided.
    """
    log_fn = log_event_fn or log_event
    plan_result = get_plan(
        instruction,
        trace_id=trace_id,
        log_event_fn=log_fn,
        retry_context=retry_context,
    )
    if trace_id:
        log_fn(trace_id, "planner_decision", {"plan": plan_result})

    dominant_artifact_mode = "docs" if is_explicit_docs_lane_by_structure(plan_result) else "code"

    state = AgentState(
        instruction=instruction,
        current_plan=plan_result,
        context={
            "tool_node": "START",
            "retrieved_files": [],
            "retrieved_symbols": [],
            "retrieved_references": [],
            "context_snippets": [],
            "ranked_context": [],
            "context_candidates": [],
            "ranking_scores": [],
            "project_root": project_root,
            "instruction": instruction,
            "trace_id": trace_id,
            "similar_past_tasks": similar_tasks or [],
            # Phase 6A: single-lane per task. Set once; immutable for the task/attempt.
            "dominant_artifact_mode": dominant_artifact_mode,
            "lane_violations": [],
        },
    )

    if trace_id:
        # Log once per deterministic attempt start.
        log_fn(
            trace_id,
            "dominant_artifact_mode",
            {"dominant_artifact_mode": dominant_artifact_mode, "plan_id": plan_result.get("plan_id")},
        )

    result = execution_loop(
        state,
        instruction,
        trace_id=trace_id,
        log_event_fn=log_fn,
        retry_context=retry_context,
        mode=ExecutionLoopMode.DETERMINISTIC,
        max_runtime_seconds=max_runtime_seconds,
    )

    assert result.loop_output is not None, "run_deterministic expects loop_output from execution_loop"
    return result.state, result.loop_output


def run_hierarchical(
    instruction: str,
    project_root: str,
    *,
    trace_id: str | None = None,
    similar_tasks: list[dict] | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
    max_runtime_seconds: int | None = None,
) -> tuple[AgentState, dict]:
    """
    Hierarchical orchestrator. Stage 1: delegates to run_deterministic() for all
    compatibility-mode plans. Stage 2+: iterates phases for non-compatibility plans.

    Interface is identical to run_deterministic(). Drop-in replacement.
    """
    log_fn = log_event_fn or log_event
    parent_plan = get_parent_plan(
        instruction,
        trace_id=trace_id,
        log_event_fn=log_fn,
        retry_context=retry_context,
    )
    if trace_id:
        log_fn(trace_id, "run_hierarchical_start", {
            "parent_plan_id": parent_plan["parent_plan_id"],
            "compatibility_mode": parent_plan["compatibility_mode"],
            "phase_count": len(parent_plan["phases"]),
        })

    if parent_plan["compatibility_mode"]:
        return run_deterministic(
            instruction,
            project_root,
            trace_id=trace_id,
            similar_tasks=similar_tasks,
            log_event_fn=log_fn,
            retry_context=retry_context,
            max_runtime_seconds=max_runtime_seconds,
        )

    phases = parent_plan.get("phases", [])
    if len(phases) != 2:
        raise NotImplementedError(
            "Multi-phase execution supports exactly 2 phases. "
            f"Got {len(phases)} phases."
        )

    start_time = time.time()
    phase_results = []
    last_phase_state = None
    context_handoff = {}
    parent_plan_id = parent_plan.get("parent_plan_id", "")
    parent_instruction = parent_plan.get("instruction", instruction)

    for phase_plan in phases:
        phase_state = _build_phase_agent_state(
            phase_plan,
            project_root,
            parent_instruction,
            trace_id,
            similar_tasks,
            context_handoff,
            parent_plan_id,
        )

        if trace_id:
            try:
                log_fn(trace_id, "phase_started", {
                    "parent_plan_id": parent_plan_id,
                    "phase_id": phase_plan.get("phase_id", ""),
                    "phase_index": phase_plan.get("phase_index", 0),
                    "lane": phase_plan.get("lane", ""),
                    "step_count": len(phase_plan.get("steps", [])),
                    "subgoal_preview": (phase_plan.get("subgoal", "") or "")[:200],
                })
            except Exception:
                pass

        loop_result = execution_loop(
            phase_state,
            phase_plan.get("subgoal", ""),
            trace_id=trace_id,
            log_event_fn=log_fn,
            mode=ExecutionLoopMode.DETERMINISTIC,
            max_runtime_seconds=max_runtime_seconds,
        )

        goal_evaluator = GoalEvaluator()
        goal_met, goal_reason, _ = goal_evaluator.evaluate_with_reason(
            phase_plan.get("subgoal", ""),
            loop_result.state,
            phase_subgoal=phase_plan.get("subgoal", ""),
        )

        context_output = _extract_phase_context_output(loop_result.state)
        failure_class = _derive_phase_failure_class(loop_result, goal_met)

        phase_result = {
            "phase_id": phase_plan.get("phase_id", ""),
            "phase_index": phase_plan.get("phase_index", 0),
            "success": goal_met,
            "failure_class": failure_class,
            "goal_met": goal_met,
            "goal_reason": goal_reason,
            "completed_steps": len(loop_result.state.completed_steps),
            "context_output": context_output,
            "attempt_count": 1,
            "loop_output": loop_result.loop_output or {},
        }
        phase_results.append(phase_result)
        last_phase_state = loop_result.state

        if trace_id:
            try:
                log_fn(trace_id, "phase_completed", {
                    "parent_plan_id": parent_plan_id,
                    "phase_id": phase_result["phase_id"],
                    "phase_index": phase_result["phase_index"],
                    "success": phase_result["success"],
                    "goal_met": phase_result["goal_met"],
                    "goal_reason": phase_result["goal_reason"],
                    "failure_class": phase_result["failure_class"],
                    "completed_steps": phase_result["completed_steps"],
                    "attempt_count": phase_result["attempt_count"],
                })
            except Exception:
                pass

        decision = _apply_parent_stage2_policy(phase_result, phase_plan.get("phase_index", 0))
        reason = "phase_succeeded" if decision == "CONTINUE" else (failure_class or goal_reason)
        if trace_id:
            try:
                log_fn(trace_id, "parent_policy_decision", {
                    "parent_plan_id": parent_plan_id,
                    "phase_index": phase_plan.get("phase_index", 0),
                    "decision": decision,
                    "reason": reason,
                })
            except Exception:
                pass

        if decision == "STOP":
            break

        context_handoff, handoff_pruned = _build_phase_context_handoff(phase_result)
        next_idx = phase_plan.get("phase_index", 0) + 1
        if trace_id and next_idx < len(phases):
            try:
                log_fn(trace_id, "phase_context_handoff", {
                    "parent_plan_id": parent_plan_id,
                    "from_phase_index": phase_plan.get("phase_index", 0),
                    "to_phase_index": next_idx,
                    "ranked_context_count": len(context_handoff.get("prior_phase_ranked_context", [])),
                    "retrieved_symbols_count": len(context_handoff.get("prior_phase_retrieved_symbols", [])),
                    "pruned": handoff_pruned,
                })
            except Exception:
                pass

    agg_ok, agg_reason = _aggregate_parent_goal(phase_results)
    if trace_id:
        try:
            log_fn(trace_id, "parent_goal_aggregation", {
                "parent_plan_id": parent_plan_id,
                "all_phases_succeeded": agg_ok,
                "reason": agg_reason,
                "phase_count": len(phase_results),
                "successful_phases": sum(1 for pr in phase_results if pr.get("goal_met")),
            })
        except Exception:
            pass

    loop_output = _build_hierarchical_loop_output(phase_results, start_time, last_phase_state)

    if last_phase_state is None:
        raise RuntimeError("no_phase_state")

    return last_phase_state, loop_output
