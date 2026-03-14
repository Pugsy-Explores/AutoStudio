"""
Planner: convert user instruction into a sequence of atomic steps (EDIT, SEARCH, EXPLAIN, INFRA).
Uses reasoning model only; output is always valid JSON-compatible structure.
"""

import json
import os
import re

from agent.models.model_client import call_reasoning_model
from planner.planner_prompts import PLANNER_SYSTEM_PROMPT
from planner.planner_utils import normalize_actions, validate_plan

# Planner needs more tokens for multi-step JSON.
PLANNER_MAX_TOKENS = int(os.environ.get("PLANNER_MAX_TOKENS", "1024"))


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


def plan(instruction: str) -> dict:
    """
    Convert instruction into a structured plan: list of steps with action, description, reason.
    Each step action is one of EDIT, SEARCH, EXPLAIN, INFRA.
    On parse/validation failure returns {"steps": [...], "error": "..."} with a safe default.
    """
    print("[workflow] planner")
    try:
        response = call_reasoning_model(
            instruction,
            system_prompt=PLANNER_SYSTEM_PROMPT,
            max_tokens=PLANNER_MAX_TOKENS,
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
                    "action": "EXPLAIN",
                    "description": "Handle instruction (no JSON in response)",
                    "reason": "Parse failed",
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
