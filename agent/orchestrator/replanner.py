"""Replanner: on failure, use LLM to produce revised plan. Fallback to remaining steps.

Phase 4: Every replanned plan gets a new plan_id so step identity is plan-scoped.
"""

import json
import logging
import re

from agent.memory.state import AgentState
from agent.orchestrator.plan_resolver import new_plan_id
from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from agent.prompt_system import get_registry
from planner.planner_utils import DOCS_COMPATIBLE_ACTIONS, is_explicit_docs_lane_by_structure, normalize_actions, validate_plan

logger = logging.getLogger(__name__)

REPLANNER_SYSTEM_PROMPT = get_registry().get_instructions("replanner")

_DOCS_PRESERVE_ACTIONS = ("SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN")


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
                "action": "SEARCH_CANDIDATES",
                "artifact_mode": "docs",
                "description": "Find README/docs artifacts",
                "query": "readme docs",
                "reason": f"Dominant docs lane fallback for: {instruction}",
            },
            {
                "id": 2,
                "action": "BUILD_CONTEXT",
                "artifact_mode": "docs",
                "description": "Build docs context from candidates",
                "reason": "Read top docs files",
            },
            {
                "id": 3,
                "action": "EXPLAIN",
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

    user_prompt = get_registry().get_instructions(
        "replanner_user",
        variables={
            "instruction": instruction,
            "steps_json": steps_json,
            "failed_desc": failed_desc,
            "error_msg": error_msg,
        },
    )

    try:
        model_type = get_model_for_task("replanner")
        if model_type == ModelType.SMALL:
            full_prompt = f"{REPLANNER_SYSTEM_PROMPT}\n\n{user_prompt}"
            response = call_small_model(
                full_prompt, task_name="replanner", max_tokens=2048, prompt_name="replanner"
            )
        else:
            response = call_reasoning_model(
                user_prompt,
                system_prompt=REPLANNER_SYSTEM_PROMPT,
                max_tokens=2048,
                task_name="replanner",
                prompt_name="replanner",
            )
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

    # Phase 6A: dominant lane lock is the source of truth.
    # Do not silently coerce missing artifact_mode for docs-compatible actions; reject and fallback.
    if not _enforce_replan_lane_contract(state, data):
        logger.warning("[replanner] Lane contract violation in replanned plan, using lane-consistent fallback")
        return _fallback_docs_lane(state) if _dominant_lane(state) == "docs" else _fallback_remaining(state)

    # Phase 4: replanned plan always gets a new plan_id (do not reuse previous).
    data["plan_id"] = new_plan_id()
    return data
