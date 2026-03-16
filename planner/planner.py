"""
Planner: convert user instruction into a sequence of atomic steps (EDIT, SEARCH, EXPLAIN, INFRA).
Uses reasoning model only; output is always valid JSON-compatible structure.
"""

import json
import os
import re

from agent.models.model_client import call_reasoning_model
from agent.models.model_config import get_model_call_params
from planner.planner_prompts import PLANNER_SYSTEM_PROMPT
from planner.planner_utils import normalize_actions, validate_plan

# Env override when config has no max_tokens. Config (task_params.planner) takes precedence.
_PLANNER_MAX_ENV = int(os.environ.get("PLANNER_MAX_TOKENS", "4096"))


def _extract_json(text: str) -> str | None:
    """Strip markdown code fences and return the first JSON object string, or None."""
    if not text or not text.strip():
        return None
    text = text.strip()
    # Remove optional ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if match:
        text = match.group(1).strip()
    # Try to find {...}
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def plan(instruction: str, retry_context: dict | None = None) -> dict:
    """
    Convert instruction into a structured plan: list of steps with action, description, reason.
    Each step action is one of EDIT, SEARCH, EXPLAIN, INFRA.
    On parse/validation failure returns {"steps": [...], "error": "..."} with a safe default.

    Phase 5: retry_context may contain previous_attempts and critic_feedback; these are
    included in the prompt so the planner can produce a better plan on retry.
    """
    print("[workflow] planner")
    prompt = instruction
    if retry_context:
        previous_attempts = retry_context.get("previous_attempts") or []
        feedback = retry_context.get("critic_feedback") or {}
        strategy_hint = retry_context.get("strategy_hint") or ""

        # [Previous Attempts]: plan structure summary (diversity guard)
        previous_attempts_lines = ["Previous attempt plans:"]
        for i, att in enumerate(previous_attempts, 1):
            plan_att = att.get("plan") or {}
            steps_att = plan_att.get("steps") or []
            actions = [str(s.get("action", "?")) for s in steps_att if isinstance(s, dict)]
            arrow = " → "
            previous_attempts_lines.append(f"- Plan {i}: {arrow.join(actions) or '(no steps)'}")
        previous_attempts_summary = "\n".join(previous_attempts_lines)

        planning_guidance = """Avoid repeating the same plan structure as previous attempts.
Generate a different strategy if the previous attempt failed.
Focus on actions that address the failure reason."""

        parts = [
            "[Strategy Hint]",
            strategy_hint,
            "",
            "[Previous Attempts]",
            previous_attempts_summary,
            "",
            "[Planning Guidance]",
            planning_guidance,
            "",
            "[Instruction]",
            instruction,
        ]
        if feedback:
            parts.append("")
            parts.append("Critic feedback:")
            parts.append(f"  failure_reason: {feedback.get('failure_reason', '')}")
            parts.append(f"  analysis: {feedback.get('analysis', '')}")
            parts.append(f"  recommendation: {feedback.get('recommendation', '')}")
        prompt = "\n".join(parts)
    params = get_model_call_params("planner")
    max_tokens = params.get("max_tokens") or _PLANNER_MAX_ENV
    try:
        response = call_reasoning_model(
            prompt,
            system_prompt=PLANNER_SYSTEM_PROMPT,
            max_tokens=max_tokens,
            task_name="planner",
            prompt_name="planner",
        )
    except Exception as e:
        return {
            "steps": [
                {
                    "id": 1,
                    "action": "EXPLAIN",
                    "description": "Handle instruction (LLM call failed)",
                    "reason": str(e),
                }
            ],
            "error": str(e),
        }

    raw_json = _extract_json(response)
    if not raw_json:
        return {
            "steps": [
                {
                    "id": 1,
                    "action": "SEARCH",
                    "description": f"Locate items mentioned in: {instruction[:200]}{'...' if len(instruction) > 200 else ''}",
                    "reason": "Parse failed; fallback to search",
                }
            ],
            "error": "No JSON found in response",
        }

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        return {
            "steps": [
                {
                    "id": 1,
                    "action": "EXPLAIN",
                    "description": "Handle instruction (invalid JSON)",
                    "reason": str(e),
                }
            ],
            "error": str(e),
        }

    if not isinstance(data, dict) or "steps" not in data:
        data = {"steps": []}
    if not isinstance(data["steps"], list):
        data["steps"] = []

    # Ensure each step has id, action, description, reason
    for i, step in enumerate(data["steps"]):
        if not isinstance(step, dict):
            data["steps"][i] = {
                "id": i + 1,
                "action": "EXPLAIN",
                "description": "Invalid step",
                "reason": "Malformed",
            }
            continue
        step.setdefault("id", i + 1)
        step.setdefault("action", "EXPLAIN")
        step.setdefault("description", "")
        step.setdefault("reason", "")

    data = normalize_actions(data)
    if not validate_plan(data):
        data["error"] = "Validation failed: invalid or missing actions"
    return data
