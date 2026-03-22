"""Deterministic execution loop: plan -> execution_loop (goal evaluator, no step retries). Single source of truth for Mode 1."""

import logging
import time

from config.agent_config import MAX_CONTEXT_CHARS
from agent.memory.state import AgentState
from agent.observability.trace_logger import log_event
from agent.orchestrator.execution_loop import ExecutionLoopMode, execution_loop
from agent.orchestrator.goal_evaluator import GoalEvaluator
from agent.orchestrator.plan_resolver import _build_replan_phase, get_parent_plan, get_plan
from planner.planner_utils import is_explicit_docs_lane_by_structure

logger = logging.getLogger(__name__)


def _dedupe_preserve_order(items: list) -> list:
    """De-duplicate a sequence while preserving first-seen order. Items must be hashable."""
    seen: set = set()
    out: list = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def _coerce_context_list(val) -> list:
    """Normalize context list fields to a list; non-list / missing -> []."""
    if val is None:
        return []
    if isinstance(val, list):
        return list(val)
    return []


def _completed_steps_count(state: AgentState) -> int:
    """Safe length of state.completed_steps when missing or non-list-like."""
    cs = getattr(state, "completed_steps", None)
    if cs is None:
        return 0
    try:
        return len(cs)
    except TypeError:
        return 0


def _get_max_parent_retries(phase_plan: dict) -> int:
    """Read retry_policy.max_parent_retries from a phase plan; default 0 if missing/invalid."""
    if not isinstance(phase_plan, dict):
        return 0
    rp = phase_plan.get("retry_policy")
    if not isinstance(rp, dict):
        return 0
    v = rp.get("max_parent_retries", 0)
    if isinstance(v, bool) or not isinstance(v, int):
        return 0
    return v


def _compute_parent_retry_eligibility(phase_result: dict, phase_plan: dict) -> tuple[bool, str]:
    """
    Report-only: whether a phase would be eligible for a future parent retry, and why not.
    Does not alter execution flow.
    """
    if not isinstance(phase_result, dict) or not isinstance(phase_plan, dict):
        return (False, "retry_metadata_unavailable")
    if phase_result.get("success") is True and phase_result.get("goal_met") is True:
        return (False, "phase_succeeded")
    ac_raw = phase_result.get("attempt_count", 1)
    if isinstance(ac_raw, bool) or not isinstance(ac_raw, int):
        return (False, "retry_metadata_unavailable")
    attempt_count = ac_raw
    max_parent_retries = _get_max_parent_retries(phase_plan)
    retries_remaining = max_parent_retries - max(0, attempt_count - 1)
    if retries_remaining > 0:
        return (True, "retry_available")
    return (False, "max_parent_retries_exhausted")


def _summarize_hierarchical_parent_retry(phase_results: list) -> tuple[bool, str]:
    """Top-level parent retry summary from executed phase_results (report-only)."""
    if not phase_results:
        return (False, "max_parent_retries_exhausted")
    all_succeeded = True
    for pr in phase_results:
        if not isinstance(pr, dict):
            all_succeeded = False
            break
        if pr.get("success") is not True or pr.get("goal_met") is not True:
            all_succeeded = False
            break
    if all_succeeded:
        return (False, "all_phases_succeeded")
    for pr in phase_results:
        if isinstance(pr, dict) and pr.get("parent_retry_eligible") is True:
            return (True, "retry_available")
    return (False, "max_parent_retries_exhausted")


def _snapshot_phase_attempt_for_history(
    attempt_number: int,
    phase_success: bool,
    goal_met: bool,
    goal_reason: str,
    failure_class: str | None,
    errors_encountered: list,
    phase_validation: dict,
    parent_retry: dict,
    plan_id: str = "",
) -> dict:
    """One attempt row for phase_result['attempt_history'] (Stage 5+). Stage 10: plan_id per attempt."""
    pv = dict(phase_validation) if isinstance(phase_validation, dict) else phase_validation
    pr = dict(parent_retry) if isinstance(parent_retry, dict) else parent_retry
    if isinstance(errors_encountered, list):
        errs = list(errors_encountered)
    elif isinstance(errors_encountered, tuple):
        errs = list(errors_encountered)
    elif errors_encountered is None:
        errs = []
    else:
        errs = [errors_encountered]
    row = {
        "attempt_count": attempt_number,
        "success": phase_success,
        "goal_met": goal_met,
        "goal_reason": goal_reason,
        "failure_class": failure_class,
        "errors_encountered": errs,
        "phase_validation": pv,
        "parent_retry": pr,
    }
    if plan_id:
        row["plan_id"] = plan_id
    return row


def _build_parent_retry_metadata(
    eligible,
    reason,
    attempt_count=None,
    max_parent_retries=None,
    phase_count=None,
) -> dict:
    """Normalized parent retry metadata dict for observability (report-only)."""
    r = reason if reason is not None and reason != "" else "retry_metadata_unavailable"
    out: dict = {
        "eligible": bool(eligible),
        "reason": r,
    }
    if attempt_count is not None and isinstance(attempt_count, int) and not isinstance(attempt_count, bool):
        out["attempt_count"] = attempt_count
    if max_parent_retries is not None and isinstance(max_parent_retries, int) and not isinstance(max_parent_retries, bool):
        out["max_parent_retries"] = max_parent_retries
    if phase_count is not None and isinstance(phase_count, int) and not isinstance(phase_count, bool):
        out["phase_count"] = phase_count
    return out


def _build_phase_validation_metadata(
    passed: bool,
    failure_reasons=None,
    goal_met=None,
    goal_reason=None,
) -> dict:
    """Normalized phase validation contract metadata for observability (report-only)."""
    raw: list = []
    if failure_reasons is not None and isinstance(failure_reasons, list):
        raw = [str(x) for x in failure_reasons]
    reasons = _dedupe_preserve_order(raw)
    gm = bool(goal_met) if goal_met is not None else False
    if goal_reason is None:
        gr = ""
    elif isinstance(goal_reason, str):
        gr = goal_reason
    else:
        gr = str(goal_reason)
    return {
        "passed": bool(passed),
        "failure_reasons": reasons,
        "goal_met": gm,
        "goal_reason": gr,
    }


def _summarize_phase_validation_metadata(phase_results: list, phase_count: int) -> dict:
    """Aggregate phase_validation across executed phases (report-only)."""
    if isinstance(phase_count, int) and not isinstance(phase_count, bool) and phase_count >= 0:
        pc = phase_count
    else:
        try:
            pc = int(phase_count) if phase_count is not None else 0
        except (TypeError, ValueError):
            pc = 0
        if pc < 0:
            pc = 0

    if not phase_results:
        return {
            "all_passed": True,
            "failed_phase_indexes": [],
            "failure_reason_counts": {},
            "phase_count": pc,
        }

    all_passed = True
    failed_indexes: list[int] = []
    reason_counts: dict[str, int] = {}

    for pr in phase_results:
        if not isinstance(pr, dict):
            all_passed = False
            continue
        idx_raw = pr.get("phase_index", 0)
        if isinstance(idx_raw, bool) or not isinstance(idx_raw, int):
            try:
                idx = int(idx_raw)
            except (TypeError, ValueError):
                idx = 0
        else:
            idx = idx_raw
        pv = pr.get("phase_validation")
        if not isinstance(pv, dict):
            passed = True
            failures: list = []
        else:
            passed = pv.get("passed") is True
            failures = pv.get("failure_reasons") or []
            if not isinstance(failures, list):
                failures = []
        if not passed:
            all_passed = False
            failed_indexes.append(idx)
            for r in failures:
                rs = str(r)
                reason_counts[rs] = reason_counts.get(rs, 0) + 1

    failed_indexes = _dedupe_preserve_order(failed_indexes)

    return {
        "all_passed": all_passed,
        "failed_phase_indexes": failed_indexes,
        "failure_reason_counts": reason_counts,
        "phase_count": pc,
    }


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
    ranked = _coerce_context_list(ctx.get("ranked_context"))
    symbols = _coerce_context_list(ctx.get("retrieved_symbols"))
    files = _coerce_context_list(ctx.get("retrieved_files"))

    files_modified: list = []
    patches_applied_count = 0
    for sr in (phase_state.step_results or []):
        fm = getattr(sr, "files_modified", None)
        if fm is None:
            fm = []
        if isinstance(fm, list):
            files_modified.extend(fm)
        pm = getattr(sr, "patch_size", None)
        if isinstance(pm, list):
            patches_applied_count += len(pm)
        elif isinstance(pm, int) and not isinstance(pm, bool) and pm > 0:
            patches_applied_count += pm

    out = {
        "ranked_context": ranked,
        "retrieved_symbols": symbols,
        "retrieved_files": files,
        "files_modified": _dedupe_preserve_order(files_modified),
        "patches_applied": patches_applied_count,
    }
    # Stage 15: additive retrieval telemetry for hierarchical explain/docs
    rt = ctx.get("retrieval_telemetry")
    if isinstance(rt, dict):
        out["retrieval_telemetry"] = rt
    return out


def _build_phase_context_handoff(phase_result: dict) -> tuple[dict, bool]:
    """Build handoff dict from phase_result; prune ranked_context if too large. Returns (handoff, pruned)."""
    co = phase_result.get("context_output") if isinstance(phase_result, dict) else None
    if not isinstance(co, dict):
        co = {}
    ranked = co.get("ranked_context")
    ranked = list(ranked) if isinstance(ranked, list) else []
    symbols = co.get("retrieved_symbols")
    symbols = list(symbols) if isinstance(symbols, list) else []
    files = co.get("retrieved_files")
    files = list(files) if isinstance(files, list) else []

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


def _evaluate_phase_validation_contract(
    phase_plan: dict,
    phase_state: AgentState,
    goal_met: bool,
    goal_reason: str,
) -> tuple[bool, list[str]]:
    """
    Evaluate PhaseValidationContract for a phase.
    Returns (passed, reasons). reasons is a list of machine-readable failure strings.
    For phases with no validation object, returns (True, []).
    """
    validation = phase_plan.get("validation")
    if not validation or not isinstance(validation, dict):
        return (True, [])

    reasons = []
    ctx = phase_state.context or {}

    if validation.get("require_ranked_context") is True:
        ranked = ctx.get("ranked_context")
        if not isinstance(ranked, list) or len(ranked) == 0:
            reasons.append("missing_ranked_context")

    min_candidates = validation.get("min_candidates", 0)
    if min_candidates > 0:
        ranked = ctx.get("ranked_context") or []
        count = len(ranked) if isinstance(ranked, list) else 0
        if count < min_candidates:
            reasons.append("min_candidates_not_met")

    if validation.get("require_explain_success") is True:
        explain_ok = (
            goal_reason == "docs_lane_explain_succeeded"
            or ctx.get("explain_success") is True
        )
        if not explain_ok:
            reasons.append("missing_explain_success")

    return (len(reasons) == 0, reasons)


def _parent_policy_decision_with_reason(phase_result: dict, phase_index: int) -> tuple[str, str]:
    """
    Return (decision, decision_reason).
    - Malformed input -> ("STOP", "malformed_phase_result")
    - success=True and goal_met=True -> ("CONTINUE", "phase_succeeded")
    - goal_met=False -> ("STOP", "goal_not_met")
    - Else (e.g. validation failed) -> ("STOP", "phase_failed")
    """
    if not phase_result or not isinstance(phase_result, dict):
        return ("STOP", "malformed_phase_result")
    if phase_result.get("success") is True and phase_result.get("goal_met") is True:
        return ("CONTINUE", "phase_succeeded")
    if phase_result.get("goal_met") is False:
        return ("STOP", "goal_not_met")
    return ("STOP", "phase_failed")


def _parent_policy_decision_after_phase_attempt(
    phase_result: dict,
    phase_plan: dict,
    attempt_number: int,
    previous_attempt_failure_class: str | None = None,
) -> tuple[str, str]:
    """
    Policy after one phase execution attempt (hierarchical mode, Stage 4+).
    On success -> CONTINUE. On failure with parent attempts remaining:
    - Stage 10: REPLAN if this failure_class equals previous_attempt_failure_class (same phase).
    - Else RETRY (same plan).
    On failure with no attempts left -> same terminal STOP reasons as _parent_policy_decision_with_reason.
    attempt_number is 1-based. max_parent_retries=N allows attempts 1..(N+1). REPLAN shares this budget.
    """
    max_r = _get_max_parent_retries(phase_plan)
    max_attempts = 1 + max_r
    if phase_result.get("success") is True and phase_result.get("goal_met") is True:
        return ("CONTINUE", "phase_succeeded")
    if attempt_number < max_attempts:
        fc = phase_result.get("failure_class")
        if (
            previous_attempt_failure_class is not None
            and fc is not None
            and fc == previous_attempt_failure_class
        ):
            return ("REPLAN", "replan_scheduled")
        return ("RETRY", "parent_retry_scheduled")
    return _parent_policy_decision_with_reason(phase_result, phase_plan.get("phase_index", 0))


def _apply_parent_stage2_policy(phase_result: dict, phase_index: int) -> str:
    """Return CONTINUE only if phase succeeded; else STOP. Backward-compat wrapper."""
    decision, _ = _parent_policy_decision_with_reason(phase_result, phase_index)
    return decision


def _aggregate_parent_goal(phase_results: list) -> tuple[bool, str]:
    """Return (True, 'all_phases_succeeded') iff all success and goal_met; else (False, 'phase_N_failed')."""
    if not phase_results:
        return (False, "no_phases_executed")
    for i, pr in enumerate(phase_results):
        if not pr.get("success", False) or not pr.get("goal_met", False):
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
    if not isinstance(lo, dict):
        lo = {}
    errors = lo.get("errors_encountered")
    if errors is None:
        errors = []
    elif not isinstance(errors, (list, tuple)):
        errors = [errors]
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
    parent_plan_id: str = "",
    phase_count: int = 0,
    parent_goal_met: bool = False,
    parent_goal_reason: str = "",
    max_parent_retries: int = 0,
) -> dict:
    """Build aggregated loop_output from phase results."""
    completed_steps = sum(pr.get("completed_steps", 0) for pr in phase_results)
    files_modified: list = []
    patches_applied_total = 0
    errors_encountered: list = []
    tool_calls = 0

    for idx, pr in enumerate(phase_results):
        co = pr.get("context_output") or {}
        if not isinstance(co, dict):
            co = {}
        fm = co.get("files_modified")
        if fm is None:
            fm = []
        if isinstance(fm, list):
            files_modified.extend(fm)
        pa = co.get("patches_applied")
        if isinstance(pa, list):
            patches_applied_total += len(pa)
        elif isinstance(pa, (int, float)) and not isinstance(pa, bool):
            patches_applied_total += int(pa)
        merged = pr.get("errors_encountered_merged")
        if merged is not None and isinstance(merged, list):
            errs = list(merged)
        else:
            lo = pr.get("loop_output")
            if lo is None or not isinstance(lo, dict):
                lo = {}
            errs = lo.get("errors_encountered")
            if errs is None:
                errs = []
            elif not isinstance(errs, list):
                errs = list(errs) if isinstance(errs, (list, tuple)) else [errs]
        errors_encountered.extend(errs)
        lo_final = pr.get("loop_output")
        if lo_final is None or not isinstance(lo_final, dict):
            lo_final = {}
        tc = lo_final.get("tool_calls")
        if tc is None:
            tc = 0
        tool_calls += int(tc) if isinstance(tc, (int, float)) and not isinstance(tc, bool) else 0

        if not pr.get("success", False):
            fc = pr.get("failure_class")
            if fc and fc != "goal_not_satisfied":
                err = f"phase_{idx}_failed:{fc}"
            else:
                err = f"phase_{idx}_goal_not_met"
            if err not in errors_encountered:
                errors_encountered.append(err)

    files_modified = _dedupe_preserve_order(files_modified)

    plan_result = last_phase_state.current_plan if last_phase_state else None

    out = {
        "completed_steps": completed_steps,
        "files_modified": files_modified,
        "patches_applied": patches_applied_total,
        "errors_encountered": errors_encountered,
        "tool_calls": tool_calls,
        "plan_result": plan_result,
        "start_time": start_time,
        "phase_results": phase_results,
    }
    out["parent_plan_id"] = parent_plan_id
    out["phase_count"] = phase_count
    out["parent_goal_met"] = parent_goal_met
    out["parent_goal_reason"] = parent_goal_reason
    max_pr_scalar = max_parent_retries if isinstance(max_parent_retries, int) else 0
    out["max_parent_retries"] = max_pr_scalar
    parent_retry_eligible, parent_retry_reason = _summarize_hierarchical_parent_retry(phase_results)
    out["parent_retry_eligible"] = parent_retry_eligible
    out["parent_retry_reason"] = parent_retry_reason
    out["parent_retry"] = _build_parent_retry_metadata(
        parent_retry_eligible,
        parent_retry_reason,
        max_parent_retries=max_pr_scalar,
        phase_count=phase_count,
    )
    out["phase_validation"] = _summarize_phase_validation_metadata(phase_results, phase_count)

    attempts_total = 0
    retries_used = 0
    for pr in phase_results:
        if not isinstance(pr, dict):
            continue
        ac = pr.get("attempt_count", 1)
        if isinstance(ac, bool) or not isinstance(ac, int):
            try:
                ac = int(ac) if ac is not None else 1
            except (TypeError, ValueError):
                ac = 1
        if ac < 1:
            ac = 1
        attempts_total += ac
        retries_used += ac - 1
    out["attempts_total"] = attempts_total
    out["retries_used"] = retries_used

    # Surface last phase edit_telemetry so harness/benchmarks match run_deterministic shape (Stage 18).
    for pr in reversed(phase_results):
        if not isinstance(pr, dict):
            continue
        lo = pr.get("loop_output")
        if isinstance(lo, dict):
            et = lo.get("edit_telemetry")
            if isinstance(et, dict) and et:
                out["edit_telemetry"] = et
                break

    return out


def run_deterministic(
    instruction: str,
    project_root: str,
    *,
    trace_id: str | None = None,
    similar_tasks: list[dict] | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
    max_runtime_seconds: int | None = None,
    plan_result: dict | None = None,
) -> tuple[AgentState, dict]:
    """
    Run deterministic loop: get_plan -> execution_loop (goal evaluator on plan exhaustion, no step retries).
    Returns (state, loop_output) where loop_output has completed_steps, patches_applied, files_modified,
    errors_encountered, tool_calls, plan_result, start_time.

    Phase 5: retry_context (previous_attempts, critic_feedback) is passed to get_plan when provided.

    When plan_result is provided (e.g. from run_hierarchical compatibility path), skip redundant get_plan
    to avoid duplicate intent + planner model calls.
    """
    log_fn = log_event_fn or log_event
    # Phase 4C: Context reset safety at request entry (no stale intent_classification)
    # Phase 4B: Create minimal state before get_plan for intent_classification memoization
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
        "instruction": instruction,
        "trace_id": trace_id,
        "similar_past_tasks": similar_tasks or [],
        "lane_violations": [],
    }
    state = AgentState(
        instruction=instruction,
        current_plan={"steps": []},
        context=context,
    )
    state.context.pop("intent_classification", None)
    if plan_result is None:
        plan_result = get_plan(
            instruction,
            trace_id=trace_id,
            log_event_fn=log_fn,
            retry_context=retry_context,
            state=state,
        )
    if trace_id:
        log_fn(trace_id, "planner_decision", {"plan": plan_result})

    dominant_artifact_mode = "docs" if is_explicit_docs_lane_by_structure(plan_result) else "code"
    state.current_plan = plan_result
    state.context["dominant_artifact_mode"] = dominant_artifact_mode

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
        phases = parent_plan.get("phases", [])
        plan_from_parent = phases[0] if phases else None
        return run_deterministic(
            instruction,
            project_root,
            trace_id=trace_id,
            similar_tasks=similar_tasks,
            log_event_fn=log_fn,
            retry_context=retry_context,
            max_runtime_seconds=max_runtime_seconds,
            plan_result=plan_from_parent,
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
        current_phase_plan = phase_plan
        max_retries_phase = _get_max_parent_retries(current_phase_plan)
        max_attempts = 1 + max_retries_phase
        errors_encountered_merged: list = []

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

        phase_result_final = None
        last_loop_state_final = None
        attempt_history: list[dict] = []
        prev_failure_class: str | None = None

        for attempt_number in range(1, max_attempts + 1):
            phase_state = _build_phase_agent_state(
                current_phase_plan,
                project_root,
                parent_instruction,
                trace_id,
                similar_tasks,
                context_handoff,
                parent_plan_id,
            )

            loop_result = execution_loop(
                phase_state,
                current_phase_plan.get("subgoal", ""),
                trace_id=trace_id,
                log_event_fn=log_fn,
                mode=ExecutionLoopMode.DETERMINISTIC,
                max_runtime_seconds=max_runtime_seconds,
            )
            loop_output_safe = getattr(loop_result, "loop_output", None)
            if loop_output_safe is None or not isinstance(loop_output_safe, dict):
                loop_output_safe = {}

            raw_errs = loop_output_safe.get("errors_encountered")
            if raw_errs is None:
                raw_errs = []
            elif not isinstance(raw_errs, list):
                raw_errs = list(raw_errs) if isinstance(raw_errs, (list, tuple)) else [raw_errs]
            errors_encountered_merged.extend(raw_errs)

            goal_evaluator = GoalEvaluator()
            goal_met, goal_reason, _ = goal_evaluator.evaluate_with_reason(
                current_phase_plan.get("subgoal", ""),
                loop_result.state,
                phase_subgoal=current_phase_plan.get("subgoal", ""),
            )

            validation_passed, validation_reasons = _evaluate_phase_validation_contract(
                current_phase_plan, loop_result.state, goal_met, goal_reason
            )
            phase_success = goal_met and validation_passed

            if goal_met and not validation_passed:
                failure_class = "phase_validation_failed"
                if trace_id:
                    try:
                        log_fn(trace_id, "phase_validation_failed", {
                            "phase_id": current_phase_plan.get("phase_id", ""),
                            "phase_index": current_phase_plan.get("phase_index", 0),
                            "lane": current_phase_plan.get("lane", ""),
                            "validation_contract": current_phase_plan.get("validation") or {},
                            "validation_failure_reasons": validation_reasons,
                        })
                    except Exception:
                        pass
            elif not goal_met:
                failure_class = _derive_phase_failure_class(loop_result, goal_met)
            else:
                failure_class = None

            context_output = _extract_phase_context_output(loop_result.state)

            phase_result = {
                "phase_id": current_phase_plan.get("phase_id", ""),
                "phase_index": current_phase_plan.get("phase_index", 0),
                "success": phase_success,
                "failure_class": failure_class,
                "goal_met": goal_met,
                "goal_reason": goal_reason,
                "completed_steps": _completed_steps_count(loop_result.state),
                "context_output": context_output,
                "attempt_count": attempt_number,
                "loop_output": loop_output_safe,
                "errors_encountered_merged": list(errors_encountered_merged),
            }
            phase_result["phase_validation"] = _build_phase_validation_metadata(
                validation_passed,
                validation_reasons,
                goal_met=goal_met,
                goal_reason=goal_reason,
            )
            pr_eligible, pr_reason = _compute_parent_retry_eligibility(phase_result, current_phase_plan)
            phase_result["parent_retry_eligible"] = pr_eligible
            phase_result["parent_retry_reason"] = pr_reason
            phase_result["parent_retry"] = _build_parent_retry_metadata(
                pr_eligible,
                pr_reason,
                attempt_count=phase_result["attempt_count"],
                max_parent_retries=max_retries_phase,
            )

            attempt_history.append(
                _snapshot_phase_attempt_for_history(
                    attempt_number,
                    phase_success,
                    goal_met,
                    goal_reason,
                    failure_class,
                    raw_errs,
                    phase_result["phase_validation"],
                    phase_result["parent_retry"],
                    plan_id=str(current_phase_plan.get("plan_id") or ""),
                )
            )

            if trace_id:
                try:
                    log_fn(trace_id, "phase_completed", {
                        "parent_plan_id": parent_plan_id,
                        "phase_id": phase_result["phase_id"],
                        "phase_index": phase_result["phase_index"],
                        "lane": current_phase_plan.get("lane", ""),
                        "subgoal": current_phase_plan.get("subgoal", ""),
                        "success": phase_result["success"],
                        "goal_met": phase_result["goal_met"],
                        "goal_reason": phase_result["goal_reason"],
                        "failure_class": phase_result["failure_class"],
                        "phase_validation": phase_result["phase_validation"],
                        "completed_steps": phase_result["completed_steps"],
                        "attempt_count": phase_result["attempt_count"],
                        "max_parent_retries": max_retries_phase,
                        "parent_retry_eligible": phase_result["parent_retry_eligible"],
                        "parent_retry_reason": phase_result["parent_retry_reason"],
                        "parent_retry": phase_result["parent_retry"],
                    })
                except Exception:
                    pass

            decision, decision_reason = _parent_policy_decision_after_phase_attempt(
                phase_result,
                current_phase_plan,
                attempt_number,
                prev_failure_class,
            )
            if trace_id:
                try:
                    log_fn(trace_id, "parent_policy_decision", {
                        "parent_plan_id": parent_plan_id,
                        "phase_index": current_phase_plan.get("phase_index", 0),
                        "decision": decision,
                        "decision_reason": decision_reason,
                        "attempt_count": phase_result["attempt_count"],
                        "max_parent_retries": max_retries_phase,
                        "parent_retry_eligible": phase_result["parent_retry_eligible"],
                        "parent_retry_reason": phase_result["parent_retry_reason"],
                        "parent_retry": phase_result["parent_retry"],
                        "phase_validation": phase_result["phase_validation"],
                    })
                except Exception:
                    pass

            if decision == "CONTINUE":
                phase_result["attempt_history"] = attempt_history
                phase_result_final = phase_result
                last_loop_state_final = loop_result.state
                break
            if decision == "RETRY":
                prev_failure_class = phase_result.get("failure_class")
                continue
            if decision == "REPLAN":
                old_plan_id = str(current_phase_plan.get("plan_id") or "")
                fc_fail = phase_result.get("failure_class")
                ctx = {
                    "parent_instruction": parent_instruction,
                    "failure_class": fc_fail,
                    "goal_reason": phase_result.get("goal_reason"),
                }
                try:
                    new_plan = _build_replan_phase(current_phase_plan, ctx)
                except Exception as e:
                    if trace_id:
                        try:
                            log_fn(trace_id, "phase_replan_failed", {
                                "parent_plan_id": parent_plan_id,
                                "phase_index": current_phase_plan.get("phase_index", 0),
                                "attempt_count": attempt_number,
                                "reason": str(e)[:200],
                                "failure_class": fc_fail,
                                "old_plan_id": old_plan_id,
                                "lane": current_phase_plan.get("lane", ""),
                            })
                        except Exception:
                            pass
                    phase_result["attempt_history"] = attempt_history
                    phase_result_final = phase_result
                    last_loop_state_final = loop_result.state
                    break
                if (
                    not isinstance(new_plan, dict)
                    or not isinstance(new_plan.get("steps"), list)
                    or len(new_plan.get("steps", [])) == 0
                ):
                    if trace_id:
                        try:
                            log_fn(trace_id, "phase_replan_failed", {
                                "parent_plan_id": parent_plan_id,
                                "phase_index": current_phase_plan.get("phase_index", 0),
                                "attempt_count": attempt_number,
                                "reason": "malformed_replan_phase_plan",
                                "failure_class": fc_fail,
                                "old_plan_id": old_plan_id,
                                "lane": current_phase_plan.get("lane", ""),
                            })
                        except Exception:
                            pass
                    phase_result["attempt_history"] = attempt_history
                    phase_result_final = phase_result
                    last_loop_state_final = loop_result.state
                    break
                new_plan_id = str(new_plan.get("plan_id") or "")
                if trace_id:
                    try:
                        log_fn(trace_id, "phase_replanned", {
                            "parent_plan_id": parent_plan_id,
                            "phase_index": current_phase_plan.get("phase_index", 0),
                            "attempt_count": attempt_number,
                            "previous_failure_class": fc_fail,
                            "old_plan_id": old_plan_id,
                            "new_plan_id": new_plan_id,
                            "lane": current_phase_plan.get("lane", ""),
                            "subgoal_preview": (current_phase_plan.get("subgoal", "") or "")[:200],
                        })
                    except Exception:
                        pass
                current_phase_plan = new_plan
                prev_failure_class = fc_fail
                continue

            phase_result["attempt_history"] = attempt_history
            phase_result_final = phase_result
            last_loop_state_final = loop_result.state
            break

        if phase_result_final is None or last_loop_state_final is None:
            raise RuntimeError("phase_execution_invariant_failed")

        phase_results.append(phase_result_final)
        last_phase_state = last_loop_state_final

        if not phase_result_final.get("success"):
            break

        context_handoff, handoff_pruned = _build_phase_context_handoff(phase_result_final)
        next_idx = phase_plan.get("phase_index", 0) + 1
        if trace_id and next_idx < len(phases):
            try:
                ranked_items = len(context_handoff.get("prior_phase_ranked_context", []))
                log_fn(trace_id, "phase_context_handoff", {
                    "parent_plan_id": parent_plan_id,
                    "from_phase_index": phase_plan.get("phase_index", 0),
                    "to_phase_index": next_idx,
                    "ranked_context_items": ranked_items,
                    "pruned": handoff_pruned,
                })
            except Exception:
                pass

    agg_ok, agg_reason = _aggregate_parent_goal(phase_results)
    max_parent_retries_executed = 0
    for i in range(len(phase_results)):
        if i < len(phases):
            max_parent_retries_executed = max(
                max_parent_retries_executed,
                _get_max_parent_retries(phases[i]),
            )
    if trace_id:
        try:
            log_fn(trace_id, "parent_goal_aggregation", {
                "parent_plan_id": parent_plan_id,
                "all_succeeded": agg_ok,
                "aggregation_reason": agg_reason,
                "phase_count": len(phase_results),
                "successful_phases": sum(1 for pr in phase_results if pr.get("goal_met")),
            })
        except Exception:
            pass

    loop_output = _build_hierarchical_loop_output(
        phase_results,
        start_time,
        last_phase_state,
        parent_plan_id=parent_plan_id,
        phase_count=len(phase_results),
        parent_goal_met=agg_ok,
        parent_goal_reason=agg_reason,
        max_parent_retries=max_parent_retries_executed,
    )

    if last_phase_state is None:
        raise RuntimeError("no_phase_state")

    return last_phase_state, loop_output
