"""
Planner utilities: validation, action normalization, step sequence extraction.
Actions match router v2: EDIT, SEARCH, EXPLAIN, INFRA.
"""

ALLOWED_ACTIONS = ("EDIT", "SEARCH", "SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN", "INFRA", "WRITE_ARTIFACT")
_ALLOWED_SET = set(ALLOWED_ACTIONS)
_ALLOWED_ARTIFACT_MODES = ("code", "docs")
_ALLOWED_ARTIFACT_MODE_SET = set(_ALLOWED_ARTIFACT_MODES)

# Phase 6A: docs-compatible actions for single-lane contract.
DOCS_COMPATIBLE_ACTIONS = ("SEARCH_CANDIDATES", "BUILD_CONTEXT", "EXPLAIN")
_DOCS_COMPATIBLE_SET = set(DOCS_COMPATIBLE_ACTIONS)


def is_explicit_docs_lane_by_structure(plan_dict: dict | None) -> bool:
    """
    True when the plan is explicitly docs-lane by structure (explicit signal only).

    Rule (narrow, deterministic):
    - Consider only docs-compatible actions: SEARCH_CANDIDATES, BUILD_CONTEXT, EXPLAIN.
    - For every step with one of those actions, artifact_mode must be explicitly "docs".
    - At least one such step must exist.
    """
    if not plan_dict or not isinstance(plan_dict, dict):
        return False
    steps = plan_dict.get("steps") or []
    if not isinstance(steps, list):
        return False
    seen = 0
    for s in steps:
        if not isinstance(s, dict):
            continue
        action = (s.get("action") or "").upper()
        if action in _DOCS_COMPATIBLE_SET:
            seen += 1
            if s.get("artifact_mode") != "docs":
                return False
    return seen > 0


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
        # Phase 5B: optional retrieval lane selector.
        if "artifact_mode" in step:
            am = step.get("artifact_mode")
            if am not in _ALLOWED_ARTIFACT_MODE_SET:
                return False

    # Phase 6A: single-lane per task (Option A) plan-level contract.
    # Deterministic rules only; do not infer from instruction text.
    has_any_docs_step = any(
        isinstance(s, dict) and s.get("artifact_mode") == "docs" for s in (steps or [])
    )
    docs_by_structure = is_explicit_docs_lane_by_structure(plan_dict)
    if has_any_docs_step or docs_by_structure:
        # Docs-lane plan: forbid code-only actions and require explicit docs artifact_mode
        # on all docs-compatible actions present in the plan.
        for s in steps:
            if not isinstance(s, dict):
                return False
            a = (s.get("action") or "").upper()
            if a in ("SEARCH", "EDIT"):
                return False
            if a in _DOCS_COMPATIBLE_SET:
                # Must be explicitly present and set to "docs". No silent defaulting.
                if s.get("artifact_mode") != "docs":
                    return False
        return True

    # Code-lane plan: no docs steps allowed.
    for s in steps:
        if isinstance(s, dict) and s.get("artifact_mode") == "docs":
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
