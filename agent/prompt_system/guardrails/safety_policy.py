"""Safety policy: allowed tools, forbidden operations."""

from dataclasses import dataclass, field
import re


@dataclass
class SafetyPolicy:
    """Defines allowed tools and forbidden operations for a prompt."""

    allowed_tools: list[str] = field(default_factory=lambda: ["SEARCH", "READ", "EDIT", "EXPLAIN", "INFRA", "RUN_TEST"])
    forbidden_operations: list[str] = field(default_factory=list)
    # Regex patterns that indicate unsafe content in response
    forbidden_patterns: list[str] = field(default_factory=list)


def _extract_actions(text: str) -> list[str]:
    """Extract action names from response (e.g. SEARCH, EDIT from JSON steps)."""
    import json

    actions: list[str] = []
    upper = text.upper()
    for action in ["SEARCH", "EDIT", "EXPLAIN", "INFRA", "READ", "RUN_TEST"]:
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


def check_safety(response: str, policy: SafetyPolicy) -> bool:
    """
    Check response against safety policy.
    Returns True if safe, False if policy violated.
    """
    if not response or not policy:
        return True

    # Check forbidden patterns
    for pattern in policy.forbidden_patterns:
        if re.search(pattern, response, re.IGNORECASE):
            return False

    # Check forbidden operations (keywords)
    for op in policy.forbidden_operations:
        if op.lower() in response.lower():
            return False

    # Check allowed tools (if response contains structured steps)
    actions = _extract_actions(response)
    if actions:
        for a in actions:
            if a not in policy.allowed_tools:
                return False

    return True
