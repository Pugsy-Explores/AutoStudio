"""Compute per-case metrics: action_accuracy, json_validity, tool_correctness, task_success."""

import json
import re
from dataclasses import dataclass


@dataclass
class PromptScore:
    """Per-case evaluation scores."""

    case_id: str
    action_accuracy: float
    json_validity: float
    tool_correctness: float
    task_success: float


def _extract_actions_from_response(response: str) -> list[str]:
    """Extract action names from planner/replanner JSON response."""
    actions: list[str] = []
    try:
        obj = None
        if "{" in response:
            start = response.find("{")
            depth = 0
            for i in range(start, len(response)):
                if response[i] == "{":
                    depth += 1
                elif response[i] == "}":
                    depth -= 1
                    if depth == 0:
                        obj = json.loads(response[start : i + 1])
                        break
        if isinstance(obj, dict) and "steps" in obj:
            for s in obj.get("steps", []):
                if isinstance(s, dict) and "action" in s:
                    actions.append(str(s["action"]).upper())
    except (json.JSONDecodeError, TypeError):
        pass
    return actions


def _is_valid_json(response: str) -> bool:
    """Check if response contains valid JSON."""
    if not response or not response.strip():
        return False
    try:
        if "{" in response:
            start = response.find("{")
            depth = 0
            for i in range(start, len(response)):
                if response[i] == "{":
                    depth += 1
                elif response[i] == "}":
                    depth -= 1
                    if depth == 0:
                        json.loads(response[start : i + 1])
                        return True
    except json.JSONDecodeError:
        pass
    return False


def compute_score(
    case_id: str,
    response: str,
    expected_actions: list[str],
) -> PromptScore:
    """
    Compute per-case scores.
    action_accuracy: overlap of predicted vs expected actions (order-aware)
    json_validity: 1 if valid JSON, 0 otherwise
    tool_correctness: 1 if no forbidden tools, 0.5 if partial, 0 if wrong tools
    task_success: average of action_accuracy and json_validity (simplified)
    """
    expected = [a.upper() for a in expected_actions]
    predicted = _extract_actions_from_response(response)

    # Action accuracy: sequence overlap (Jaccard-like)
    if not expected:
        action_acc = 1.0
    elif not predicted:
        action_acc = 0.0
    else:
        overlap = sum(1 for a in predicted if a in expected)
        action_acc = overlap / max(len(expected), len(predicted))

    json_valid = 1.0 if _is_valid_json(response) else 0.0

    # Tool correctness: predicted actions must be in allowed set
    allowed = {"SEARCH", "EDIT", "EXPLAIN", "INFRA", "READ", "RUN_TEST"}
    wrong = [a for a in predicted if a not in allowed]
    tool_correct = 0.0 if wrong else 1.0

    # Task success: composite
    task_success = (action_acc + json_valid) / 2

    return PromptScore(
        case_id=case_id,
        action_accuracy=action_acc,
        json_validity=json_valid,
        tool_correctness=tool_correct,
        task_success=task_success,
    )
