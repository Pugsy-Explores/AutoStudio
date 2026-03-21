"""Replanner: on failure, use LLM to produce revised plan. Fallback to remaining steps.

Phase 4: Every replanned plan gets a new plan_id so step identity is plan-scoped.
"""

import concurrent.futures
import json
import logging
import os
import re
import time

from agent.memory.state import AgentState
from agent.orchestrator.plan_resolver import new_plan_id
from agent.orchestrator.replan_recovery import (
    build_replan_failure_context,
    format_failure_context_json,
    record_replan_recovery_event,
    repair_replan_steps_for_recovery,
)
from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from agent.prompt_system import get_registry
from agent.core.actions import Action
from planner.planner_utils import DOCS_COMPATIBLE_ACTIONS, is_explicit_docs_lane_by_structure, normalize_actions, validate_plan

logger = logging.getLogger(__name__)

REPLANNER_SYSTEM_PROMPT = get_registry().get_instructions("replanner")

_DOCS_PRESERVE_ACTIONS = (
    Action.SEARCH_CANDIDATES.value,
    Action.BUILD_CONTEXT.value,
    Action.EXPLAIN.value,
)


def _is_explicit_docs_lane_plan_by_structure(plan: dict | None) -> bool:
    # Kept for backward compatibility: delegate to shared planner_utils semantics.
    return is_explicit_docs_lane_by_structure(plan)


def _should_preserve_docs_mode(state: AgentState, failed_step: dict | None) -> bool:
    """Explicit lineage rule for docs-mode preservation across replans."""
    if isinstance(failed_step, dict) and failed_step.get("artifact_mode") == "docs":
        return True
    return _is_explicit_docs_lane_plan_by_structure(state.current_plan)


def _extract_json(text: str) -> str | None:
    """Extract first valid JSON object from LLM output. Handles markdown fences, reasoning-before-JSON."""
    if not text or not text.strip():
        return None
    text = text.strip()
    # Try markdown code fence first
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        try:
            json.loads(match.group(1).strip())
            return match.group(1).strip()
        except json.JSONDecodeError:
            pass
    # Find first {...} (outermost JSON object)
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


def _fallback_remaining(state: AgentState) -> dict:
    """Return plan with only remaining (not yet completed) steps. New plan_id (Phase 4)."""
    steps = state.current_plan.get("steps") or []
    current_plan_id = state.current_plan.get("plan_id")
    completed_ids = {
        step_id
        for (plan_id, step_id) in state.completed_steps
        if plan_id == current_plan_id
    }
    remaining = [s for s in steps if isinstance(s, dict) and s.get("id") not in completed_ids]
    return {"plan_id": new_plan_id(), "steps": remaining}


def _fallback_docs_lane(state: AgentState) -> dict:
    """Lane-consistent fallback plan for dominant docs lane."""
    instruction = (getattr(state, "instruction", "") or "")[:200]
    return {
        "plan_id": new_plan_id(),
        "steps": [
            {
                "id": 1,
                "action": Action.SEARCH_CANDIDATES.value,
                "artifact_mode": "docs",
                "description": "Find README/docs artifacts",
                "query": "readme docs",
                "reason": f"Dominant docs lane fallback for: {instruction}",
            },
            {
                "id": 2,
                "action": Action.BUILD_CONTEXT.value,
                "artifact_mode": "docs",
                "description": "Build docs context from candidates",
                "reason": "Read top docs files",
            },
            {
                "id": 3,
                "action": Action.EXPLAIN.value,
                "artifact_mode": "docs",
                "description": "Answer using docs context",
                "reason": "Complete docs fallback plan",
            },
        ],
    }


def _dominant_lane(state: AgentState) -> str:
    """Dominant artifact mode lock for this task/attempt."""
    am = (state.context or {}).get("dominant_artifact_mode") if hasattr(state, "context") else None
    return am if am in ("code", "docs") else "code"


def _enforce_replan_lane_contract(state: AgentState, plan_dict: dict) -> bool:
    """
    Enforce Phase 6A single-lane contract on replanned output.
    Returns True if plan_dict is lane-consistent; False otherwise.
    """
    dom = _dominant_lane(state)
    steps = plan_dict.get("steps") or []
    if not isinstance(steps, list):
        return False
    if dom == "docs":
        # Only docs-compatible actions allowed; require explicit artifact_mode="docs".
        for s in steps:
            if not isinstance(s, dict):
                return False
            a = (s.get("action") or "").upper()
            if a not in DOCS_COMPATIBLE_ACTIONS:
                return False
            if s.get("artifact_mode") != "docs":
                return False
        return True
    # dom == "code": no docs steps allowed.
    for s in steps:
        if isinstance(s, dict) and s.get("artifact_mode") == "docs":
            return False
    return True


def replan(
    state: AgentState,
    failed_step: dict | None = None,
    error: str | None = None,
) -> dict:
    """
    On failure, use LLM to produce a revised plan. Fallback to remaining steps if LLM fails.
    """
    print("[workflow] replanner")
    last = state.step_results[-1] if state.step_results else None
    if last:
        logger.warning(
            "Replan triggered: step_id=%s action=%s success=%s error=%s",
            last.step_id,
            last.action,
            last.success,
            last.error,
        )

    if not failed_step and not error:
        # Keep lane lock: if dominant lane is docs, remaining mixed plans are not allowed.
        dom = _dominant_lane(state)
        fb = _fallback_remaining(state)
        if dom == "docs":
            return _fallback_docs_lane(state)
        return fb

    instruction = (getattr(state, "instruction", "") or "")[:1500]
    current_plan = state.current_plan
    steps_json = json.dumps(current_plan.get("steps") or [], indent=2)
    failed_desc = json.dumps(failed_step, indent=2) if failed_step else "{}"
    error_msg = ((error or "").strip() or "Unknown error")[:500]

    failure_context = build_replan_failure_context(state, failed_step, error)

    # Phase 1: Persist failure state in state.context before NOT_FOUND gate and LLM call
    ctx = state.context if isinstance(getattr(state, "context", None), dict) else {}
    curr_reason = failure_context.get("reason_code")
    curr_mode = failure_context.get("recovery_mode")
    prev_reason = ctx.get("last_failure_reason_code")
    ctx["same_failure_count"] = (ctx.get("same_failure_count", 0) + 1) if prev_reason == curr_reason else 1
    ctx["last_failure_reason_code"] = curr_reason
    prev_mode = ctx.get("last_recovery_mode")
    ctx["same_recovery_mode_count"] = (ctx.get("same_recovery_mode_count", 0) + 1) if prev_mode == curr_mode else 1
    ctx["last_recovery_mode"] = curr_mode
    failure_context["same_failure_count"] = ctx["same_failure_count"]
    failure_context["same_recovery_mode_count"] = ctx["same_recovery_mode_count"]

    # Phase 2D: Loop safety cap (non-heuristic) — always evaluate first
    same_failure_count = ctx.get("same_failure_count", 0)
    if same_failure_count >= 4:
        logger.info("[replanner] terminating: LOOP_PROTECTION")
        return {
            "success": True,
            "output": "Unable to complete the request due to repeated planning failures.",
            "terminal": "LOOP_PROTECTION",
        }

    # Phase 2/2B/2C: NOT_FOUND termination gate (all paths, before replanning)
    eval_res = ctx.get("answer_grounding_eval") or {}
    supported = eval_res.get("supported")
    records = ctx.get("search_debug_records") or []
    final_has_signal = any(r.get("final_has_signal") for r in records if isinstance(r, dict))
    count_threshold = 2 if supported is False else 3  # None -> 3 (evaluator absence)
    if (
        (supported is False or supported is None)
        and final_has_signal is True
        and same_failure_count >= count_threshold
    ):
        logger.info(
            "[control] same_failure_count=%d supported=%s final_has_signal=%s termination=NOT_FOUND",
            same_failure_count,
            supported,
            final_has_signal,
        )
        logger.info("[replanner] terminating: NOT_FOUND (all paths)")
        return {
            "success": True,
            "output": "The requested behavior or structure could not be found in the codebase based on available context.",
            "terminal": "NOT_FOUND",
        }
    logger.info(
        "[control] same_failure_count=%d supported=%s final_has_signal=%s termination=none",
        same_failure_count,
        supported,
        final_has_signal,
    )

    failure_context_json = format_failure_context_json(failure_context)

    _rs = failure_context.get("recent_searches") or []
    if _rs:
        recent_searches_block = "\n".join(f"    - {s}" for s in _rs)
    else:
        recent_searches_block = "    (none recorded)"

    recovery_hint = failure_context.get("recovery_hint") or ""
    if recovery_hint:
        recovery_hint_block = """Recovery hint (optional, low priority):

The following hint may contain generic patterns or examples.
- It is NOT tailored to the current instruction.
- You MUST ignore any part of it that is not directly relevant to the instruction.
- Do NOT reuse generic terms (e.g., main, cli, entrypoint) unless they are explicitly required by the instruction.
- You must NOT copy phrases from the recovery hint. You must rewrite searches using the language of the instruction.

[OPTIONAL RECOVERY HINT — MAY BE IRRELEVANT]
""" + recovery_hint + """

Use it only if it helps refine a search grounded in the instruction."""
    else:
        recovery_hint_block = ""

    user_prompt = get_registry().get_instructions(
        "replanner_user",
        version="v2",  # Frozen contract; do not change without passing replanner_regression_suite
        variables={
            "instruction": instruction,
            "plan": steps_json,
            "failed_step": failed_desc,
            "error": error_msg,
            "failure_context": failure_context_json,
            "recovery_hint_block": recovery_hint_block,
            "recent_searches": recent_searches_block,
        },
    )

    ctx = state.context if isinstance(getattr(state, "context", None), dict) else {}
    debug_replanner = bool(ctx.get("debug_replanner"))
    if debug_replanner:
        recovery_mode = failure_context.get("recovery_mode")
        recovery_hint = failure_context.get("recovery_hint")
        debug_payload = {
            "failed_step": failed_step,
            "error": error,
            "failure_context": failure_context,
            "recovery_mode": recovery_mode,
            "recovery_hint": recovery_hint,
            "search_quality": ctx.get("search_quality"),
            "retrieval_intent": ctx.get("retrieval_intent"),
            "recent_steps": [
                {
                    "action": r.action,
                    "success": r.success,
                    "description": (getattr(r, "description", None) or str(r.output or ""))[:120],
                    "reason_code": getattr(r, "reason_code", None),
                }
                for r in state.step_results[-5:]
            ],
        }
        os.makedirs("artifacts/replanner_debug", exist_ok=True)
        path = f"artifacts/replanner_debug/replanner_{int(time.time() * 1000)}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(debug_payload, f, indent=2, default=str)
        print(f"[DEBUG] replanner payload dumped to {path}")

    # Hard wall-clock cap on the replanner LLM call (streaming HTTP timeouts do not bound total time).
    # Set REPLANNER_LLM_DEADLINE_SECONDS (e.g. 30) to enable; unset = no extra deadline.
    _deadline_sec: float | None = None
    _raw_deadline = (os.environ.get("REPLANNER_LLM_DEADLINE_SECONDS") or "").strip()
    if _raw_deadline:
        try:
            _v = float(_raw_deadline)
            if _v > 0:
                _deadline_sec = _v
        except ValueError:
            pass

    def _call_replanner_llm() -> str:
        model_type = get_model_for_task("replanner")
        if model_type == ModelType.SMALL:
            full_prompt = f"{REPLANNER_SYSTEM_PROMPT}\n\n{user_prompt}"
            return call_small_model(
                full_prompt,
                task_name="replanner",
                max_tokens=2048,
                prompt_name="replanner",
                debug_replanner=debug_replanner,
            )
        return call_reasoning_model(
            user_prompt,
            system_prompt=REPLANNER_SYSTEM_PROMPT,
            max_tokens=2048,
            task_name="replanner",
            prompt_name="replanner",
            debug_replanner=debug_replanner,
        )

    try:
        if _deadline_sec is not None:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(_call_replanner_llm)
                response = fut.result(timeout=_deadline_sec)
        else:
            response = _call_replanner_llm()
    except concurrent.futures.TimeoutError:
        logger.warning(
            "[replanner] LLM exceeded hard deadline (%.2fs), using fallback",
            _deadline_sec,
        )
        return _fallback_docs_lane(state) if _dominant_lane(state) == "docs" else _fallback_remaining(state)
    except Exception as e:
        logger.warning("[replanner] LLM call failed: %s, using fallback", e)
        return _fallback_docs_lane(state) if _dominant_lane(state) == "docs" else _fallback_remaining(state)

    raw_json = _extract_json(response)
    if not raw_json:
        logger.warning("[replanner] No JSON in response, using fallback")
        return _fallback_docs_lane(state) if _dominant_lane(state) == "docs" else _fallback_remaining(state)

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.warning("[replanner] Invalid JSON: %s, using fallback", e)
        return _fallback_docs_lane(state) if _dominant_lane(state) == "docs" else _fallback_remaining(state)

    if not isinstance(data, dict) or "steps" not in data:
        logger.warning("[replanner] Missing steps in response, using fallback")
        return _fallback_docs_lane(state) if _dominant_lane(state) == "docs" else _fallback_remaining(state)

    steps = data.get("steps")
    if not isinstance(steps, list):
        return _fallback_docs_lane(state) if _dominant_lane(state) == "docs" else _fallback_remaining(state)

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            steps[i] = {"id": i + 1, "action": "EXPLAIN", "description": "Invalid", "reason": "Malformed"}
            continue
        step.setdefault("id", i + 1)
        step.setdefault("action", "EXPLAIN")
        step.setdefault("description", "")
        step.setdefault("reason", "")

    data = normalize_actions(data)
    if not validate_plan(data):
        logger.warning("[replanner] Validation failed, using fallback")
        return _fallback_docs_lane(state) if _dominant_lane(state) == "docs" else _fallback_remaining(state)

    recovery_mode = failure_context.get("recovery_mode") or "generic_failure"
    repaired_steps, repair_mutated = repair_replan_steps_for_recovery(
        data.get("steps") or [],
        failure_context,
        recovery_mode,
    )
    data["steps"] = repaired_steps
    if repair_mutated:
        logger.info(
            "[replanner] recovery repair applied mode=%s steps=%s",
            recovery_mode,
            len(repaired_steps),
        )
    record_replan_recovery_event(state, failure_context)

    if repair_mutated:
        data = normalize_actions(data)
        if not validate_plan(data):
            logger.warning("[replanner] Invalid plan after recovery repair, using fallback")
            return _fallback_docs_lane(state) if _dominant_lane(state) == "docs" else _fallback_remaining(state)

    # Phase 6A: dominant lane lock is the source of truth.
    # Do not silently coerce missing artifact_mode for docs-compatible actions; reject and fallback.
    if not _enforce_replan_lane_contract(state, data):
        logger.warning("[replanner] Lane contract violation in replanned plan, using lane-consistent fallback")
        return _fallback_docs_lane(state) if _dominant_lane(state) == "docs" else _fallback_remaining(state)

    # Phase 4: replanned plan always gets a new plan_id (do not reuse previous).
    data["plan_id"] = new_plan_id()
    return data
