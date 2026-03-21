"""Safety policy: allowed tools, forbidden operations."""

from dataclasses import dataclass, field
import re

from agent.core.actions import all_action_values


@dataclass
class SafetyPolicy:
    """Defines allowed tools and forbidden operations for a prompt."""

    allowed_tools: list[str] = field(default_factory=all_action_values)
    forbidden_operations: list[str] = field(default_factory=list)
    # Regex patterns that indicate unsafe content in response
    forbidden_patterns: list[str] = field(default_factory=list)


def _extract_actions(text: str) -> list[str]:
    """Extract action names from response (e.g. SEARCH, EDIT from JSON steps)."""
    import json

    valid = set(all_action_values())
    actions: list[str] = []
    upper = text.upper()
    for action in valid:
        if action in upper:
            actions.append(action)
    try:
        obj = json.loads(text) if "{" in text else None
        if obj is None:
            start = text.find("{")
            if start >= 0:
                depth = 0
                for i in range(start, len(text)):
                    if text[i] == "{":
                        depth += 1
                    elif text[i] == "}":
                        depth -= 1
                        if depth == 0:
                            obj = json.loads(text[start : i + 1])
                            break
        if isinstance(obj, dict) and "steps" in obj:
            for s in obj.get("steps", []):
                if isinstance(s, dict) and "action" in s:
                    a = str(s["action"]).upper()
                    if a not in actions:
                        actions.append(a)
    except (json.JSONDecodeError, TypeError):
        pass
    return actions


def check_safety(response: str, policy: SafetyPolicy, *, relax_actions: bool = False) -> tuple[bool, str]:
    """
    Check response against safety policy.
    Returns (is_safe, error_message). When safe, error_message is empty.

    relax_actions: When True (planner-only recovery path), skip action validation only.
    All other checks (forbidden_patterns, forbidden_operations, JSON structure) remain enforced.
    """
    if not response or not policy:
        return (True, "")

    # Check forbidden patterns (always enforced)
    for pattern in policy.forbidden_patterns:
        if re.search(pattern, response, re.IGNORECASE):
            return (False, "forbidden pattern matched")

    # Check forbidden operations (always enforced)
    for op in policy.forbidden_operations:
        if op.lower() in response.lower():
            return (False, f"forbidden operation: {op}")

    # Check allowed tools — skipped when relax_actions=True (planner recovery)
    if not relax_actions:
        actions = _extract_actions(response)
        if actions:
            for a in actions:
                if a not in policy.allowed_tools:
                    return (False, f"invalid action: {a}")

    return (True, "")
