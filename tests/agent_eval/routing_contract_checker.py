"""
Post-hoc routing contract checks for live-model agent_eval runs.

Reads plan_resolution_telemetry (from outcome.json _audit) and applies broad-gate
assertions from the Stage 39/40 routing contract eval design. Soft checks are
informational; strict and anti checks are intended to fail the checker CLI.
"""

from __future__ import annotations

from typing import Any

# Mirrors agent/orchestrator/plan_resolver.py and agent/routing/intent.py
VALID_RESOLVER_CONSUMPTION = frozenset(
    {"docs_seed", "short_search", "short_explain", "short_infra", "planner"}
)

INTENT_VALIDATE = "VALIDATE"
INTENT_COMPOUND = "COMPOUND"
INTENT_EDIT = "EDIT"
INTENT_AMBIGUOUS = "AMBIGUOUS"
PLAN_SHAPE_TWO_PHASE_DOCS_CODE = "two_phase_docs_code"

SIGNAL_CONFIDENCE_BELOW = "confidence_below_threshold"
HANDOFF_CONFIDENCE_BELOW = "confidence_below_threshold"
HANDOFF_UNCLEAR = "unclear_intent"

ROUTING_CONTRACT_TASK_IDS: frozenset[str] = frozenset(
    {
        "rc_doc",
        "rc_search",
        "rc_explain",
        "rc_edit",
        "rc_two_phase",
        "rc_not_compound",
        "rc_not_validate",
        "rc_vague",
        "rc_low_conf",
    }
)


def _primary(tele: dict[str, Any]) -> str | None:
    v = tele.get("routed_intent_primary")
    return str(v) if v is not None else None


def _handoff(tele: dict[str, Any]) -> str:
    return str(tele.get("routed_intent_planner_handoff_reason") or "")


def _shape(tele: dict[str, Any]) -> str | None:
    v = tele.get("routed_intent_suggested_plan_shape")
    if v is None or v == "":
        return None
    return str(v)


def _signals(tele: dict[str, Any]) -> list[str]:
    s = tele.get("routed_intent_matched_signals")
    if not isinstance(s, (list, tuple)):
        return []
    return [str(x) for x in s]


def check_task_specific_strict(task_id: str, tele: dict[str, Any] | None) -> list[str]:
    """Per-task strict rules from the routing contract matrix."""
    violations: list[str] = []
    if not tele or not isinstance(tele, dict):
        return violations
    primary = _primary(tele)
    if task_id == "rc_edit" and primary != INTENT_EDIT:
        violations.append("strict[rc_edit]: routed_intent_primary must be EDIT")
    return violations


def check_strict_and_anti(tele: dict[str, Any] | None) -> list[str]:
    """Global strict + anti assertions; must pass for every task."""
    violations: list[str] = []
    if not tele or not isinstance(tele, dict):
        return ["missing or invalid plan_resolution_telemetry"]

    primary = _primary(tele)
    if primary is None:
        violations.append("routed_intent_primary missing")
        return violations

    if primary == INTENT_VALIDATE:
        violations.append("strict: routed_intent_primary must not be VALIDATE")

    if primary == INTENT_COMPOUND:
        if _shape(tele) != PLAN_SHAPE_TWO_PHASE_DOCS_CODE:
            violations.append(
                "strict: COMPOUND requires routed_intent_suggested_plan_shape=two_phase_docs_code"
            )

    if primary == INTENT_EDIT and _handoff(tele) != "":
        violations.append("strict: EDIT requires routed_intent_planner_handoff_reason==''")

    sigs = _signals(tele)
    if primary == INTENT_AMBIGUOUS and SIGNAL_CONFIDENCE_BELOW in sigs:
        if _handoff(tele) != HANDOFF_CONFIDENCE_BELOW:
            violations.append(
                "strict: AMBIGUOUS with confidence_below_threshold signal requires "
                "planner_handoff_reason=confidence_below_threshold"
            )

    rc = tele.get("resolver_consumption")
    if rc is not None and str(rc) not in VALID_RESOLVER_CONSUMPTION:
        violations.append(f"strict: invalid resolver_consumption={rc!r}")

    # Anti-assertions (overlap with strict; explicit for clarity)
    if primary == INTENT_EDIT and _handoff(tele) == HANDOFF_UNCLEAR:
        violations.append("anti: EDIT must not use planner_handoff_reason=unclear_intent")

    if SIGNAL_CONFIDENCE_BELOW in sigs and _handoff(tele) == HANDOFF_UNCLEAR:
        violations.append(
            "anti: confidence_below_threshold in matched_signals must not pair with unclear_intent handoff"
        )

    return violations


def check_soft_for_task(task_id: str, tele: dict[str, Any] | None) -> list[str]:
    """Per-task soft checks (model variance); violations are warnings only."""
    if not tele or not isinstance(tele, dict):
        return []
    primary = _primary(tele)
    handoff = _handoff(tele)
    warnings: list[str] = []

    if task_id == "rc_doc":
        if primary not in ("DOC", "SEARCH", "EXPLAIN", "AMBIGUOUS"):
            warnings.append(
                f"soft[rc_doc]: primary {primary!r} not in {{DOC, SEARCH, EXPLAIN, AMBIGUOUS}}"
            )

    elif task_id == "rc_search":
        if primary not in ("SEARCH", "AMBIGUOUS"):
            warnings.append(f"soft[rc_search]: primary {primary!r} not in {{SEARCH, AMBIGUOUS}}")

    elif task_id == "rc_explain":
        if primary not in ("EXPLAIN", "AMBIGUOUS"):
            warnings.append(f"soft[rc_explain]: primary {primary!r} not in {{EXPLAIN, AMBIGUOUS}}")

    elif task_id == "rc_two_phase":
        # Two-phase may resolve as COMPOUND+two_phase, or defer to planner / other intents.
        if primary not in (
            "COMPOUND",
            "EXPLAIN",
            "SEARCH",
            "DOC",
            "EDIT",
            "AMBIGUOUS",
            "INFRA",
        ):
            warnings.append(f"soft[rc_two_phase]: primary {primary!r} unexpected")

    elif task_id == "rc_vague":
        if primary != INTENT_AMBIGUOUS:
            warnings.append(f"soft[rc_vague]: expected primary AMBIGUOUS, got {primary!r}")
        if handoff not in (HANDOFF_UNCLEAR, ""):
            warnings.append(
                f"soft[rc_vague]: planner_handoff_reason expected unclear_intent or '', got {handoff!r}"
            )

    elif task_id == "rc_low_conf":
        if primary not in ("SEARCH", "AMBIGUOUS", "EDIT", "EXPLAIN", "DOC"):
            warnings.append(f"soft[rc_low_conf]: primary {primary!r} unexpected for lookup instruction")

    return warnings


def check_routing_contract_task(
    task_id: str,
    tele: dict[str, Any] | None,
    *,
    include_soft: bool = True,
) -> tuple[list[str], list[str]]:
    """
    Run all checks for one task. Returns (strict_violations, soft_warnings).
    """
    strict = check_strict_and_anti(tele)
    strict.extend(check_task_specific_strict(task_id, tele))
    soft: list[str] = []
    if include_soft and task_id in ROUTING_CONTRACT_TASK_IDS:
        soft = check_soft_for_task(task_id, tele)
    return strict, soft


def check_outcome_audit(audit: dict[str, Any] | None, task_id: str) -> tuple[list[str], list[str]]:
    """Extract telemetry from a task _audit dict and check."""
    if not audit:
        return (["missing _audit"], [])
    tele = audit.get("plan_resolution_telemetry")
    return check_routing_contract_task(task_id, tele if isinstance(tele, dict) else None)
