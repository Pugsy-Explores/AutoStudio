"""
Stage 38: Single production entrypoint for instruction → RoutedIntent.
Stage 39: Contract-aligned; see PRODUCTION_EMITTABLE_PRIMARY_INTENTS in intent.py.

Unifies:
- deterministic docs-artifact detection (docs_intent)
- deterministic two-phase docs+code detection (docs_intent)
- legacy model router (instruction_router) when enabled

plan_resolver consumes only RoutedIntent from here; it does not re-implement
docs/two-phase string checks.
"""

from __future__ import annotations

from config.router_config import ENABLE_INSTRUCTION_ROUTER

from agent.routing.docs_intent import is_docs_artifact_intent, is_two_phase_docs_code_intent
from agent.routing.intent import (
    INTENT_AMBIGUOUS,
    INTENT_COMPOUND,
    INTENT_DOC,
    INTENT_EXPLAIN,
    PLAN_SHAPE_DOCS_SEED_LANE,
    PLAN_SHAPE_PLANNER_MULTI_STEP,
    PLAN_SHAPE_TWO_PHASE_DOCS_CODE,
    PLANNER_HANDOFF_CONFIDENCE_BELOW,
    PLANNER_HANDOFF_ROUTER_DISABLED,
    RoutedIntent,
    routed_intent_from_router_decision,
)
from agent.routing.instruction_router import route_instruction

# Legacy short-circuit categories that require confidence >= threshold for single-step plans.
_SHORT_CIRCUIT_ROUTER_CATEGORIES = frozenset({"CODE_SEARCH", "CODE_EXPLAIN", "INFRA"})


def _router_confidence_allows_short_circuit(confidence) -> bool:
    """Imported lazily to avoid circular imports with config usage in plan_resolver."""
    from config.router_config import ROUTER_CONFIDENCE_THRESHOLD

    if confidence is None:
        return False
    try:
        value = float(confidence)
    except (TypeError, ValueError):
        return False
    return value >= float(ROUTER_CONFIDENCE_THRESHOLD)


def route_production_instruction(
    instruction: str,
    *,
    ignore_two_phase: bool = False,
) -> RoutedIntent:
    """
    Single production routing entrypoint. Returns RoutedIntent.

    Evaluation order (when ENABLE_INSTRUCTION_ROUTER is true):
      1. Docs-artifact-only intent → primary DOC, docs_seed_lane
      2. Two-phase docs+code intent → primary COMPOUND, two_phase_docs_code
         (skipped if ignore_two_phase=True)
      3. Legacy model router → mapped via routed_intent_from_router_decision,
         with confidence-based effective routing for short-circuit categories

    When ENABLE_INSTRUCTION_ROUTER is false:
      Returns AMBIGUOUS with suggested_plan_shape planner_multi_step and
      rationale that the router is disabled (not user ambiguity).

    Parameters
    ----------
    ignore_two_phase
        If True, skip step 2. Used when get_parent_plan already tried two-phase
        execution and falls back to a flat plan.
    """
    if not ENABLE_INSTRUCTION_ROUTER:
        return RoutedIntent(
            primary_intent=INTENT_AMBIGUOUS,
            secondary_intents=(),
            decomposition_needed=False,
            clarification_needed=False,
            confidence=1.0,
            rationale="instruction router disabled; defer to planner",
            matched_signals=("router_disabled",),
            suggested_plan_shape=PLAN_SHAPE_PLANNER_MULTI_STEP,
            planner_handoff_reason=PLANNER_HANDOFF_ROUTER_DISABLED,
        )

    # 1) Docs lane (same predicate as pre–Stage 38 plan_resolver)
    if is_docs_artifact_intent(instruction):
        return RoutedIntent(
            primary_intent=INTENT_DOC,
            secondary_intents=(),
            decomposition_needed=False,
            clarification_needed=False,
            confidence=0.95,
            rationale="deterministic docs-artifact intent",
            matched_signals=("docs_artifact",),
            suggested_plan_shape=PLAN_SHAPE_DOCS_SEED_LANE,
        )

    # 2) Mixed docs + code → hierarchical parent plan (not flat single-step)
    if not ignore_two_phase and is_two_phase_docs_code_intent(instruction):
        return RoutedIntent(
            primary_intent=INTENT_COMPOUND,
            secondary_intents=(INTENT_DOC, INTENT_EXPLAIN),
            decomposition_needed=True,
            clarification_needed=False,
            confidence=0.9,
            rationale="deterministic two-phase docs-then-code intent",
            matched_signals=("two_phase_docs_code",),
            suggested_plan_shape=PLAN_SHAPE_TWO_PHASE_DOCS_CODE,
        )

    # 3) Legacy model router
    decision = route_instruction(instruction)
    router_category = decision.category
    raw_confidence = getattr(decision, "confidence", None)
    trust_sc = _router_confidence_allows_short_circuit(raw_confidence)
    confidence_fallback = (
        router_category in _SHORT_CIRCUIT_ROUTER_CATEGORIES and not trust_sc
    )

    if confidence_fallback:
        # Effective planning path matches legacy: treat as GENERAL → AMBIGUOUS + planner.
        # Stage 40: clarification_needed=False — we know the category, defer due to low confidence.
        return RoutedIntent(
            primary_intent=INTENT_AMBIGUOUS,
            secondary_intents=(),
            decomposition_needed=False,
            clarification_needed=False,
            confidence=float(raw_confidence) if raw_confidence is not None else 0.0,
            rationale=(
                f"legacy router category {router_category} suppressed: "
                f"confidence below threshold; defer to planner"
            ),
            matched_signals=(f"legacy:{router_category}", "confidence_below_threshold"),
            suggested_plan_shape=PLAN_SHAPE_PLANNER_MULTI_STEP,
            planner_handoff_reason=PLANNER_HANDOFF_CONFIDENCE_BELOW,
        )

    return routed_intent_from_router_decision(
        router_category,
        float(raw_confidence if raw_confidence is not None else 0.5),
    )
