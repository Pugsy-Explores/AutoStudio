"""
Plan resolver: router decides, planner plans.

Per docs (phase.md, ROUTING_ARCHITECTURE_REPORT.md):
- Instruction router classifies before planner when ENABLE_INSTRUCTION_ROUTER=1
- CODE_SEARCH / CODE_EXPLAIN / INFRA → single-step plan, skip planner (30–60% fewer planner calls)
- CODE_EDIT / GENERAL → planner produces multi-step plan

Categories: CODE_SEARCH, CODE_EDIT, CODE_EXPLAIN, INFRA, GENERAL
Planner actions: SEARCH, EDIT, EXPLAIN, INFRA

Phase 4: Every plan has a unique plan_id so step identity is (plan_id, step_id).
"""

import logging
from uuid import uuid4

from agent.observability.trace_logger import trace_stage
from config.agent_config import (
    TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0,
    TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1,
)
from config.router_config import ENABLE_INSTRUCTION_ROUTER, ROUTER_CONFIDENCE_THRESHOLD
from planner.planner import plan

from agent.routing.docs_intent import DOCS_DISCOVERY_VERBS, DOCS_INTENT_TOKENS, is_two_phase_docs_code_intent
from agent.routing.intent import (
    INTENT_COMPOUND,
    INTENT_DOC,
    INTENT_EDIT,
    INTENT_EXPLAIN,
    INTENT_INFRA,
    INTENT_SEARCH,
    INTENT_VALIDATE,
    PLAN_SHAPE_DOCS_SEED_LANE,
    PLAN_SHAPE_TWO_PHASE_DOCS_CODE,
    RoutedIntent,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resolver consumption matrix (Stage 39, Stage 40)
# get_plan branches: DOC+docs_seed_lane -> _docs_seed_plan; SEARCH/EXPLAIN/INFRA ->
# single-step; EDIT/VALIDATE/AMBIGUOUS/COMPOUND-flat -> plan(). get_parent_plan:
# COMPOUND+two_phase_docs_code -> _build_two_phase_parent_plan or fallback.
# resolver_consumption telemetry: docs_seed | short_search | short_explain |
# short_infra | planner. planner_handoff_reason in telemetry when AMBIGUOUS.
# ---------------------------------------------------------------------------

# Stage 28 / Stage 38: plan resolution telemetry (router short-circuit, docs seed, planner, RoutedIntent)
_plan_resolution_telemetry: dict = {}


def get_plan_resolution_telemetry() -> dict:
    """Return copy of last plan resolution telemetry. For run artifacts."""
    return dict(_plan_resolution_telemetry)


def reset_plan_resolution_telemetry() -> None:
    """Reset before each task for clean audit."""
    _plan_resolution_telemetry.clear()


def _legacy_router_category_label(ri: RoutedIntent) -> str:
    """Backward-compatible label for logs (pre–RoutedIntent era)."""
    if ri.primary_intent == INTENT_SEARCH:
        return "CODE_SEARCH"
    if ri.primary_intent == INTENT_EDIT:
        return "CODE_EDIT"
    if ri.primary_intent == INTENT_EXPLAIN:
        return "CODE_EXPLAIN"
    if ri.primary_intent == INTENT_INFRA:
        return "INFRA"
    if ri.primary_intent == INTENT_DOC:
        return "DOC"
    if ri.primary_intent == INTENT_VALIDATE:
        return "VALIDATE"
    if ri.primary_intent == INTENT_COMPOUND:
        return "COMPOUND"
    return "GENERAL"


def _merge_routing_telemetry(
    ri: RoutedIntent,
    *,
    routing_overridden_downstream: bool = False,
    routing_override_reason: str | None = None,
) -> None:
    """Stage 38: unified RoutedIntent observability into plan resolution telemetry.
    Stage 40: planner_handoff_reason when primary is AMBIGUOUS."""
    _plan_resolution_telemetry.update(
        {
            "routed_intent_primary": ri.primary_intent,
            "routed_intent_secondary": list(ri.secondary_intents),
            "routed_intent_confidence": ri.confidence,
            "routed_intent_matched_signals": list(ri.matched_signals),
            "routed_intent_suggested_plan_shape": ri.suggested_plan_shape,
            "routed_intent_planner_handoff_reason": ri.planner_handoff_reason,
            "routing_overridden_downstream": routing_overridden_downstream,
            "routing_override_reason": routing_override_reason,
        }
    )


def _docs_seed_plan(instruction: str) -> dict:
    """Docs-lane seed plan: SEARCH_CANDIDATES(docs) -> BUILD_CONTEXT(docs) -> EXPLAIN(docs)."""
    i = (instruction or "").strip()
    q = "readme docs"
    il = i.lower()
    if "architecture" in il:
        q = "architecture docs"
    elif "install" in il or "setup" in il:
        q = "install docs"
    return _ensure_plan_id(
        {
            "steps": [
                {
                    "id": 1,
                    "action": "SEARCH_CANDIDATES",
                    "artifact_mode": "docs",
                    "description": "Locate README/docs artifacts",
                    "query": q,
                    "reason": "Docs-artifact intent detected; enter docs lane early",
                },
                {
                    "id": 2,
                    "action": "BUILD_CONTEXT",
                    "artifact_mode": "docs",
                    "description": "Build docs context from candidates",
                    "reason": "Read and rank docs context",
                },
                {
                    "id": 3,
                    "action": "EXPLAIN",
                    "artifact_mode": "docs",
                    "description": i or "Explain documentation content",
                    "reason": "Answer using docs context",
                },
            ]
        }
    )


def new_plan_id() -> str:
    """Return a unique plan_id with 'plan_' prefix for readable logs (e.g. plan_3f8b8a7d)."""
    return f"plan_{uuid4().hex[:8]}"


def _ensure_plan_id(plan: dict) -> dict:
    """Ensure plan has plan_id (Phase 4 — plan-scoped step identity)."""
    out = dict(plan)
    if "plan_id" not in out or not out["plan_id"]:
        out["plan_id"] = new_plan_id()
    return out


def get_plan(
    instruction: str,
    trace_id: str | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
    routed_intent: RoutedIntent | None = None,
    ignore_two_phase: bool = False,
) -> dict:
    """
    Resolve plan using Stage 38 unified production routing (RoutedIntent).

    When ENABLE_INSTRUCTION_ROUTER=1:
    - RoutedIntent from route_production_instruction() drives branches.
    - DOC + docs_seed_lane → docs seed plan (same as pre–Stage 38).
    - SEARCH / EXPLAIN / INFRA → single-step plan (legacy short-circuit).
    - Otherwise → planner.

    routed_intent: if provided, skip a second call to route_production_instruction()
    (used when get_parent_plan already classified the instruction).

    ignore_two_phase: passed to route_production_instruction when building ri
    (two-phase parent fallback to flat plan).
    """
    from agent.routing.production_routing import route_production_instruction

    ri = (
        routed_intent
        if routed_intent is not None
        else route_production_instruction(instruction, ignore_two_phase=ignore_two_phase)
    )

    routing_override = False
    routing_reason: str | None = None
    if ENABLE_INSTRUCTION_ROUTER and ri.primary_intent == INTENT_COMPOUND:
        # Flat plan cannot execute parent-level two-phase decomposition; defer to planner.
        routing_override = True
        routing_reason = "compound_intent_flat_plan_defers_to_planner"

    _merge_routing_telemetry(
        ri,
        routing_overridden_downstream=routing_override,
        routing_override_reason=routing_reason,
    )

    legacy_cat = _legacy_router_category_label(ri)

    if not ENABLE_INSTRUCTION_ROUTER:
        _plan_resolution_telemetry.update(
            {
                "planner_used": True,
                "router_short_circuit_used": False,
                "docs_seed_plan_used": False,
                "router_category": legacy_cat,
                "resolver_consumption": "planner",
            }
        )
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                plan_result = plan(instruction, retry_context=retry_context)
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = len(plan_result.get("steps", []))
                summary["actions"] = [s.get("action") for s in plan_result.get("steps", [])]
            return _ensure_plan_id(plan_result)
        return _ensure_plan_id(plan(instruction, retry_context=retry_context))

    # Docs-artifact lane (deterministic, from unified router)
    if ri.primary_intent == INTENT_DOC and ri.suggested_plan_shape == PLAN_SHAPE_DOCS_SEED_LANE:
        _plan_resolution_telemetry.update(
            {
                "docs_seed_plan_used": True,
                "router_short_circuit_used": False,
                "planner_used": False,
                "router_category": legacy_cat,
                "resolver_consumption": "docs_seed",
            }
        )
        if log_event_fn and trace_id:
            try:
                log_event_fn(
                    trace_id,
                    "docs_intent_override",
                    {"detected": True, "routed_intent": ri.to_dict()},
                )
            except Exception:
                pass
        plan_result = _docs_seed_plan(instruction)
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = len(plan_result.get("steps", []))
                summary["actions"] = [s.get("action") for s in plan_result.get("steps", [])]
        return plan_result

    if log_event_fn and trace_id:
        try:
            log_event_fn(
                trace_id,
                "instruction_router",
                {
                    "routed_intent": ri.to_dict(),
                    "legacy_router_category": legacy_cat,
                    "router_confidence_threshold": float(ROUTER_CONFIDENCE_THRESHOLD),
                },
            )
        except Exception as e:
            logger.debug("[plan_resolver] log_event skipped: %s", e)

    if ri.primary_intent == INTENT_SEARCH:
        _plan_resolution_telemetry.update(
            {
                "router_short_circuit_used": True,
                "router_category": legacy_cat,
                "docs_seed_plan_used": False,
                "planner_used": False,
                "resolver_consumption": "short_search",
            }
        )
        plan_result = _ensure_plan_id(
            {
                "steps": [
                    {
                        "id": 1,
                        "action": "SEARCH",
                        "description": instruction,
                        "reason": "Routed by unified production router",
                    }
                ],
            }
        )
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = 1
                summary["actions"] = ["SEARCH"]
        return plan_result
    if ri.primary_intent == INTENT_EXPLAIN:
        _plan_resolution_telemetry.update(
            {
                "router_short_circuit_used": True,
                "router_category": legacy_cat,
                "docs_seed_plan_used": False,
                "planner_used": False,
                "resolver_consumption": "short_explain",
            }
        )
        plan_result = _ensure_plan_id(
            {
                "steps": [
                    {
                        "id": 1,
                        "action": "EXPLAIN",
                        "description": instruction,
                        "reason": "Routed by unified production router",
                    }
                ],
            }
        )
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = 1
                summary["actions"] = ["EXPLAIN"]
        return plan_result
    if ri.primary_intent == INTENT_INFRA:
        _plan_resolution_telemetry.update(
            {
                "router_short_circuit_used": True,
                "router_category": legacy_cat,
                "docs_seed_plan_used": False,
                "planner_used": False,
                "resolver_consumption": "short_infra",
            }
        )
        plan_result = _ensure_plan_id(
            {
                "steps": [
                    {
                        "id": 1,
                        "action": "INFRA",
                        "description": instruction,
                        "reason": "Routed by unified production router",
                    }
                ],
            }
        )
        if trace_id:
            with trace_stage(trace_id, "planner") as summary:
                summary["instruction"] = (instruction or "")[:200]
                summary["number_of_steps"] = 1
                summary["actions"] = ["INFRA"]
        return plan_result

    # EDIT, VALIDATE, AMBIGUOUS, COMPOUND, etc. → planner
    _plan_resolution_telemetry.update(
        {
            "planner_used": True,
            "router_short_circuit_used": False,
            "docs_seed_plan_used": False,
            "router_category": legacy_cat,
            "resolver_consumption": "planner",
        }
    )
    if trace_id:
        with trace_stage(trace_id, "planner") as summary:
            plan_result = plan(instruction, retry_context=retry_context)
            summary["instruction"] = (instruction or "")[:200]
            summary["number_of_steps"] = len(plan_result.get("steps", []))
            summary["actions"] = [s.get("action") for s in plan_result.get("steps", [])]
        return _ensure_plan_id(plan_result)
    return _ensure_plan_id(plan(instruction, retry_context=retry_context))


def _derive_phase_subgoals(instruction: str) -> tuple[str, str]:
    """
    Return (phase_0_subgoal, phase_1_subgoal) for two-phase docs-then-code execution.
    Phase 0: docs discovery. Phase 1: code explanation/description from connector split.
    """
    source = (instruction or "").strip()
    phase0 = "Find documentation artifacts relevant to: " + source[:150]

    connectors = (
        " and explain ",
        " and describe ",
        " and show how ",
        " and summarize ",
        " and walk through ",
        ", then explain ",
        " then explain ",
        " and tell me about ",
        " and tell me how ",
        " and walk me through ",
        " and illustrate ",
        " before explaining ",
        ", explain ",
    )
    lower = source.lower()
    for connector in connectors:
        pos = lower.find(connector)
        if pos != -1:
            start = pos + len(connector)
            raw = source[start:].strip()
            if len(raw) >= 10:
                phase1 = raw[0].upper() + raw[1:]
            else:
                phase1 = source
            return (phase0, phase1)

    phase1 = source
    return (phase0, phase1)


def _coerce_max_parent_retries(value) -> int:
    """
    Sanitize config-driven retry budget for two-phase plans.
    Non-int, bool, or negative values -> 0 (Stage 7).
    """
    if isinstance(value, bool):
        return 0
    if not isinstance(value, int):
        return 0
    if value < 0:
        return 0
    return value


def _build_replan_phase(phase_plan: dict, failure_context: dict | None = None) -> dict:
    """
    Stage 10: rebuild a single phase plan after a REPLAN parent-policy decision.

    Must not call get_parent_plan() or run_hierarchical(). Returns a new phase_plan dict
    for the same phase_id / phase_index / lane / validation / retry_policy with fresh steps
    and a new plan_id where applicable.

    failure_context may include: parent_instruction, failure_class, goal_reason (for logging
    or deterministic query tweaks).
    """
    from agent.orchestrator.goal_evaluator import is_explain_like_instruction
    from planner.planner_utils import validate_plan

    if not isinstance(phase_plan, dict):
        raise ValueError("phase_plan_invalid")
    fc = None
    parent_instruction = ""
    if isinstance(failure_context, dict):
        parent_instruction = str(failure_context.get("parent_instruction") or "")
        fc = failure_context.get("failure_class")

    lane = str(phase_plan.get("lane") or "code")
    phase_id = phase_plan.get("phase_id", "")
    pidx = phase_plan.get("phase_index", 0)
    if isinstance(pidx, bool) or not isinstance(pidx, int):
        pidx = 0
    subgoal = str(phase_plan.get("subgoal") or "")
    validation = phase_plan.get("validation")
    if not isinstance(validation, dict):
        if lane == "docs":
            validation = {
                "require_ranked_context": True,
                "require_explain_success": True,
                "min_candidates": 1,
            }
        else:
            validation = {
                "require_ranked_context": True,
                "require_explain_success": is_explain_like_instruction(subgoal),
                "min_candidates": 1,
            }
    retry_policy = phase_plan.get("retry_policy")
    if not isinstance(retry_policy, dict):
        retry_policy = {"max_parent_retries": 0}

    if lane == "docs":
        seed = _docs_seed_plan(parent_instruction)
        steps: list = []
        for s in seed.get("steps", []):
            if isinstance(s, dict):
                steps.append(dict(s))
        if not steps:
            raise ValueError("replan_docs_empty_steps")
        if isinstance(steps[0], dict) and steps[0].get("action") == "SEARCH_CANDIDATES":
            q = str(steps[0].get("query", "readme docs"))
            if fc in ("phase_validation_failed", "goal_not_satisfied"):
                q = (q + " readme documentation overview").strip()
            steps[0]["query"] = q
        candidate = _ensure_plan_id({"steps": steps})
        if not validate_plan({"steps": candidate["steps"]}):
            raise ValueError("replan_docs_validate_plan_failed")
        return {
            "phase_id": phase_id,
            "phase_index": pidx,
            "subgoal": subgoal,
            "lane": "docs",
            "steps": candidate["steps"],
            "plan_id": candidate["plan_id"],
            "validation": validation,
            "retry_policy": retry_policy,
        }

    code_flat = plan(subgoal)
    code_flat = _ensure_plan_id(code_flat)
    if not validate_plan({"steps": code_flat.get("steps", [])}):
        raise ValueError("replan_code_validate_plan_failed")
    return {
        "phase_id": phase_id,
        "phase_index": pidx,
        "subgoal": subgoal,
        "lane": "code",
        "steps": code_flat["steps"],
        "plan_id": code_flat["plan_id"],
        "validation": validation,
        "retry_policy": retry_policy,
    }


def _build_two_phase_parent_plan(
    instruction: str,
    trace_id: str | None = None,
    log_event_fn=None,
) -> dict:
    """
    Build a two-phase ParentPlan: Phase 0 (docs lane) + Phase 1 (code lane).
    Phase 1 planner receives phase_1_subgoal, not the parent instruction.
    Raises ValueError if either phase's steps fail validate_plan.
    """
    from agent.orchestrator.goal_evaluator import is_explain_like_instruction
    from agent.orchestrator.parent_plan import new_parent_plan_id, new_phase_id
    from planner.planner_utils import validate_plan

    _budget_phase_0 = _coerce_max_parent_retries(TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_0)
    _budget_phase_1 = _coerce_max_parent_retries(TWO_PHASE_DOCS_CODE_MAX_PARENT_RETRIES_PHASE_1)
    phase_0_subgoal, phase_1_subgoal = _derive_phase_subgoals(instruction)

    # Phase 0 — docs lane
    docs_flat = _docs_seed_plan(instruction)
    if not validate_plan({"steps": docs_flat["steps"]}):
        raise ValueError("phase_0_validate_plan_failed")

    phase_0: dict = {
        "phase_id": new_phase_id(),
        "phase_index": 0,
        "subgoal": phase_0_subgoal,
        "lane": "docs",
        "steps": docs_flat["steps"],
        "plan_id": docs_flat["plan_id"],
        "validation": {
            "require_ranked_context": True,
            "require_explain_success": True,
            "min_candidates": 1,
        },
        "retry_policy": {"max_parent_retries": _budget_phase_0},
    }

    # Phase 1 — code lane
    code_flat = plan(phase_1_subgoal)
    code_flat = _ensure_plan_id(code_flat)
    if not validate_plan({"steps": code_flat["steps"]}):
        raise ValueError("phase_1_validate_plan_failed")

    phase_1: dict = {
        "phase_id": new_phase_id(),
        "phase_index": 1,
        "subgoal": phase_1_subgoal,
        "lane": "code",
        "steps": code_flat["steps"],
        "plan_id": code_flat["plan_id"],
        "validation": {
            "require_ranked_context": True,
            "require_explain_success": is_explain_like_instruction(phase_1_subgoal),
            "min_candidates": 1,
        },
        "retry_policy": {"max_parent_retries": _budget_phase_1},
    }

    return {
        "parent_plan_id": new_parent_plan_id(),
        "instruction": instruction,
        "decomposition_type": "two_phase_docs_code",
        "phases": [phase_0, phase_1],
        "compatibility_mode": False,
    }


def get_parent_plan(
    instruction: str,
    trace_id: str | None = None,
    log_event_fn=None,
    retry_context: dict | None = None,
) -> dict:
    """
    Stage 1: wraps get_plan() in a single-phase compatibility ParentPlan.
    Stage 2: adds mixed-intent detection before the compatibility fallback.
    Stage 38: two-phase detection uses the same RoutedIntent as flat routing (unified entrypoint).
    Never raises; propagates get_plan() behavior on failure.
    """
    from agent.orchestrator.parent_plan import make_compatibility_parent_plan
    from agent.routing.production_routing import route_production_instruction

    ri = route_production_instruction(instruction)
    _merge_routing_telemetry(ri)

    if (
        ri.primary_intent == INTENT_COMPOUND
        and ri.suggested_plan_shape == PLAN_SHAPE_TWO_PHASE_DOCS_CODE
    ):
        try:
            parent_plan = _build_two_phase_parent_plan(
                instruction,
                trace_id=trace_id,
                log_event_fn=log_event_fn,
            )
            if log_event_fn and trace_id:
                try:
                    log_event_fn(trace_id, "parent_plan_created", {
                        "parent_plan_id": parent_plan["parent_plan_id"],
                        "decomposition_type": parent_plan["decomposition_type"],
                        "compatibility_mode": parent_plan["compatibility_mode"],
                        "phase_count": len(parent_plan["phases"]),
                        "instruction_preview": (instruction or "")[:200],
                        "routed_intent": ri.to_dict(),
                    })
                except Exception:
                    pass
            return parent_plan
        except Exception as e:
            if log_event_fn and trace_id:
                try:
                    log_event_fn(trace_id, "two_phase_fallback", {
                        "reason": str(e)[:120],
                        "instruction_preview": (instruction or "")[:200],
                    })
                except Exception:
                    pass
            # Fall back to flat plan: re-resolve without two-phase branch (model + docs lane only).
            flat_plan = get_plan(
                instruction,
                trace_id=trace_id,
                log_event_fn=log_event_fn,
                retry_context=retry_context,
                ignore_two_phase=True,
            )
            parent_plan = make_compatibility_parent_plan(flat_plan, instruction)
            if log_event_fn and trace_id:
                try:
                    log_event_fn(trace_id, "parent_plan_created", {
                        "parent_plan_id": parent_plan["parent_plan_id"],
                        "decomposition_type": parent_plan["decomposition_type"],
                        "compatibility_mode": parent_plan["compatibility_mode"],
                        "phase_count": len(parent_plan["phases"]),
                        "instruction_preview": (instruction or "")[:200],
                    })
                except Exception:
                    pass
            return parent_plan

    # Near-miss observability: docs + discovery markers but two-phase detection did not fire.
    if log_event_fn and trace_id and not is_two_phase_docs_code_intent(instruction):
        il = (instruction or "").strip().lower()
        has_discovery = any(v in il for v in DOCS_DISCOVERY_VERBS)
        has_docs = any(t in il for t in DOCS_INTENT_TOKENS)
        if has_discovery and has_docs:
            try:
                log_event_fn(
                    trace_id,
                    "two_phase_near_miss",
                    {
                        "reason": "docs_and_discovery_but_no_code_marker",
                        "instruction_preview": (instruction or "")[:200],
                    },
                )
            except Exception:
                pass

    flat_plan = get_plan(
        instruction,
        trace_id=trace_id,
        log_event_fn=log_event_fn,
        retry_context=retry_context,
        routed_intent=ri,
    )
    parent_plan = make_compatibility_parent_plan(flat_plan, instruction)
    if log_event_fn and trace_id:
        try:
            log_event_fn(trace_id, "parent_plan_created", {
                "parent_plan_id": parent_plan["parent_plan_id"],
                "decomposition_type": parent_plan["decomposition_type"],
                "compatibility_mode": parent_plan["compatibility_mode"],
                "phase_count": len(parent_plan["phases"]),
                "instruction_preview": (instruction or "")[:200],
            })
        except Exception:
            pass
    return parent_plan
