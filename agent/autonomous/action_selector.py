"""Action selector: small-model structured action selection. Outputs validated structured actions."""

import json
import logging
import re

from agent.autonomous.state_observer import ObservationBundle
from agent.models.model_client import call_small_model
from agent.prompt_system import get_registry
from agent.models.model_router import get_model_for_task
from agent.models.model_types import ModelType
from planner.planner_utils import ALLOWED_ACTIONS

logger = logging.getLogger(__name__)

STRUCTURED_ACTIONS = set(ALLOWED_ACTIONS)  # SEARCH, EDIT, EXPLAIN, INFRA


def select_next_action(observation: ObservationBundle) -> dict | None:
    """
    Call small model with ObservationBundle. Parse JSON output into structured action.
    Returns step dict {"action": str, "description": str, "id": int} or None on failure.
    Validates output is a recognized structured action. Dispatcher executes it (Rule 4).
    """
    prompt = _format_observation_prompt(observation)
    try:
        model_type = get_model_for_task("action_selection")
        action_system = get_registry().get_instructions("action_selector")
        if model_type == ModelType.SMALL:
            raw = call_small_model(
                prompt,
                task_name="action_selection",
                system_prompt=action_system,
                prompt_name="action_selector",
            )
        else:
            from agent.models.model_client import call_reasoning_model
            raw = call_reasoning_model(
                prompt,
                system_prompt=action_system,
                task_name="action_selection",
                prompt_name="action_selector",
            )
    except Exception as e:
        logger.warning("[action_selector] model call failed: %s", e)
        return None

    step = _parse_action_output(raw)
    if step and _is_valid_structured_action(step):
        return step
    logger.warning("[action_selector] invalid or unparseable output: %s", (raw or "")[:200])
    return None


def _format_observation_prompt(obs: ObservationBundle) -> str:
    """Format ObservationBundle for model input."""
    parts = [f"Goal: {obs.goal}"]
    if obs.recent_steps:
        parts.append("\nRecent steps:")
        for i, s in enumerate(obs.recent_steps, 1):
            status = "ok" if s.get("success") else "fail"
            parts.append(f"  {i}. {s.get('action', '?')}: {s.get('description', '')[:100]}... [{status}]")
    if obs.repo_context_summary:
        parts.append(f"\nRepo context:\n{obs.repo_context_summary[:800]}")
    if obs.ranked_context_preview:
        parts.append(f"\nContext preview:\n{obs.ranked_context_preview[:600]}")
    if obs.trace_summary:
        parts.append(f"\nTrace:\n{obs.trace_summary[:400]}")
    parts.append("\nNext action (JSON only):")
    return "\n".join(parts)


def _parse_action_output(raw: str) -> dict | None:
    """Extract JSON object from model output. Handles markdown code blocks."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    # Try to find JSON in markdown block
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        text = match.group(1)
    else:
        match = re.search(r"\{[^{}]*\"action\"[^{}]*\}", text)
        if match:
            text = match.group(0)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and obj.get("action"):
            return obj
    except json.JSONDecodeError:
        pass
    return None


def _is_valid_structured_action(step: dict) -> bool:
    """Ensure action is in STRUCTURED_ACTIONS and has required description for SEARCH/EDIT/EXPLAIN."""
    action = (step.get("action") or "").upper()
    if action not in STRUCTURED_ACTIONS:
        return False
    desc = step.get("description") or step.get("query") or ""
    if action in ("SEARCH", "EDIT", "EXPLAIN") and not isinstance(desc, str):
        return False
    return True
