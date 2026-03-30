"""Constraint evaluation — structure and metrics checks, invariants, no string matching."""

from typing import Any, Dict, List


def check_invariants(result: Dict[str, Any]) -> List[str]:
    """Always-on invariant checks. Returns failure codes (empty if pass)."""
    failures: List[str] = []
    structure = result.get("structure") or {}
    metrics = result.get("metrics") or {}

    if structure.get("has_loop"):
        failures.append("loop_detected")

    if metrics.get("termination_reason") == "LOOP_PROTECTION":
        failures.append("unsafe_loop_termination")

    return failures


def evaluate_constraints(
    result: Dict[str, Any], expected: Dict[str, Any], *, strict: bool = False
) -> List[str]:
    """
    Returns list of failure messages (empty if pass).
    Only compares keys present in expected. Does not assume result has any fields.
    strict=True: fail if expected keys missing in result (explicit missing-key failures).
    strict=False: current behavior.
    """
    failures: List[str] = []

    if strict:
        for key in expected:
            if key in ("structure", "metrics"):
                val = result.get(key)
                if val is None or not isinstance(val, dict):
                    failures.append(f"strict: result missing required key '{key}'")

    failures.extend(check_invariants(result))

    if "structure" in expected:
        failures.extend(_check_structure(result, expected["structure"]))

    if "metrics" in expected:
        failures.extend(_check_metrics(result.get("metrics"), expected["metrics"]))

    return failures


def _check_structure(result: Dict[str, Any], expected_structure: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    result_structure = result.get("structure") if isinstance(result, dict) else None
    if result_structure is None or not isinstance(result_structure, dict):
        return [f"structure: missing or not a dict (got {type(result_structure).__name__})"]

    for key, expected_val in expected_structure.items():
        if key == "no_loops":
            has_loop = result_structure.get("has_loop")
            term = (result.get("metrics") or {}).get("termination_reason")
            is_loop = has_loop is True or term == "LOOP_PROTECTION"
            if expected_val is True and is_loop:
                failures.append(
                    "structure.no_loops: expected no loop, got has_loop=True or termination_reason=LOOP_PROTECTION"
                )
            continue
        if key == "max_steps":
            raw = result_structure.get("steps", result_structure.get("max_steps"))
            actual = len(raw) if isinstance(raw, list) else raw
        else:
            actual = result_structure.get(key)
        if actual is None and key not in result_structure and key != "max_steps":
            failures.append(f"structure.{key}: missing")
            continue

        if expected_val is None:
            continue

        expected_type = type(expected_val)

        if expected_type is int:
            if not isinstance(actual, int):
                failures.append(f"structure.{key}: expected int, got {type(actual).__name__}")
            elif key == "max_steps":
                if actual > expected_val:
                    failures.append(f"structure.max_steps: {actual} exceeds max {expected_val}")
            elif key == "min_search_steps":
                if actual < expected_val:
                    failures.append(
                        f"structure.min_search_steps: {actual} below min {expected_val}"
                    )
            else:
                if actual != expected_val:
                    failures.append(f"structure.{key}: expected {expected_val}, got {actual}")

        elif expected_type is bool:
            if actual is not expected_val:
                failures.append(
                    f"structure.{key}: expected {expected_val}, got {actual}"
                )

    return failures


def _check_metrics(result_metrics: Any, expected_metrics: Dict[str, Any]) -> List[str]:
    failures: List[str] = []
    if result_metrics is None or not isinstance(result_metrics, dict):
        return [f"metrics: missing or not a dict (got {type(result_metrics).__name__})"]

    for key, expected_val in expected_metrics.items():
        if key not in result_metrics:
            failures.append(f"metrics.{key}: missing")
            continue

        actual = result_metrics[key]
        if actual != expected_val:
            failures.append(f"metrics.{key}: expected {expected_val!r}, got {actual!r}")

    return failures
