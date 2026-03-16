"""
Planner utilities: validation, action normalization, step sequence extraction.
Actions match router v2: EDIT, SEARCH, EXPLAIN, INFRA.
"""

ALLOWED_ACTIONS = ("EDIT", "SEARCH", "SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN", "INFRA")
_ALLOWED_SET = set(ALLOWED_ACTIONS)


def validate_plan(plan_dict: dict) -> bool:
    """
    Check that plan_dict has "steps" (list) and each step has "action" in ALLOWED_ACTIONS.
    Expects actions to already be normalized (e.g. after normalize_actions).
    """
    if not isinstance(plan_dict, dict):
        return False
    steps = plan_dict.get("steps")
    if not isinstance(steps, list):
        return False
    for step in steps:
        if not isinstance(step, dict):
            return False
        action = step.get("action")
        if action not in _ALLOWED_SET:
            return False
    return True


def normalize_actions(plan_dict: dict) -> dict:
    """
    Uppercase step "action" and map to allowed set; unknown actions become "EXPLAIN".
    Returns the same structure with actions normalized (mutates steps in place).
    """
    if not isinstance(plan_dict, dict):
        return plan_dict
    steps = plan_dict.get("steps")
    if not isinstance(steps, list):
        return plan_dict
    for step in steps:
        if not isinstance(step, dict):
            continue
        raw = step.get("action")
        if raw is None:
            step["action"] = "EXPLAIN"
            continue
        normalized = str(raw).strip().upper()
        step["action"] = normalized if normalized in _ALLOWED_SET else "EXPLAIN"
    return plan_dict


def extract_step_sequence(plan_dict: dict) -> list[str]:
    """
    Return the ordered list of action strings, e.g. ["SEARCH", "EDIT"].
    Uses normalized actions if present; invalid steps are skipped (not included).
    """
    if not isinstance(plan_dict, dict):
        return []
    steps = plan_dict.get("steps")
    if not isinstance(steps, list):
        return []
    out = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        action = step.get("action")
        if action in _ALLOWED_SET:
            out.append(action)
    return out
