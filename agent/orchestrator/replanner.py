"""Replanner: on failure, use LLM to produce revised plan. Fallback to remaining steps."""

import json
import logging
import re

from agent.memory.state import AgentState
from agent.models.model_client import call_reasoning_model, call_small_model
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from agent.prompt_system import get_registry
from planner.planner_utils import normalize_actions, validate_plan

logger = logging.getLogger(__name__)

REPLANNER_SYSTEM_PROMPT = get_registry().get_instructions("replanner")


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
    """Return plan with only remaining (not yet completed) steps."""
    steps = state.current_plan.get("steps") or []
    completed_ids = {s.get("id") for s in state.completed_steps}
    remaining = [s for s in steps if isinstance(s, dict) and s.get("id") not in completed_ids]
    return {"steps": remaining}


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
        return _fallback_remaining(state)

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
            response = call_small_model(full_prompt, task_name="replanner", max_tokens=2048)
        else:
            response = call_reasoning_model(
                user_prompt,
                system_prompt=REPLANNER_SYSTEM_PROMPT,
                max_tokens=2048,
                task_name="replanner",
            )
    except Exception as e:
        logger.warning("[replanner] LLM call failed: %s, using fallback", e)
        return _fallback_remaining(state)

    raw_json = _extract_json(response)
    if not raw_json:
        logger.warning("[replanner] No JSON in response, using fallback")
        return _fallback_remaining(state)

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.warning("[replanner] Invalid JSON: %s, using fallback", e)
        return _fallback_remaining(state)

    if not isinstance(data, dict) or "steps" not in data:
        logger.warning("[replanner] Missing steps in response, using fallback")
        return _fallback_remaining(state)

    steps = data.get("steps")
    if not isinstance(steps, list):
        return _fallback_remaining(state)

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
        return _fallback_remaining(state)

    return data
