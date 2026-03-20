"""
Stage 37: Intent routing contract for a general software AI assistant.
Stage 39: Production-emission contract; VALIDATE deferred.

Minimal intent taxonomy and stable schema for routed intent output.
No benchmark-specific logic, no task-id/suite-name/fixture-path references.

Stage 37: matched_signals + suggested_plan_shape.
Stage 38: clarification_needed; COMPOUND uses decomposition_needed, AMBIGUOUS uses
clarification_needed (not decomposition_needed for vague input).
Stage 39: PRODUCTION_EMITTABLE_PRIMARY_INTENTS; VALIDATE and non-two-phase COMPOUND
demoted from production-first-class.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Intent taxonomy (PRIMARY_INTENTS = full schema for deserialization/telemetry)
# ---------------------------------------------------------------------------

INTENT_SEARCH = "SEARCH"
INTENT_DOC = "DOC"
INTENT_EXPLAIN = "EXPLAIN"
INTENT_EDIT = "EDIT"
INTENT_VALIDATE = "VALIDATE"
INTENT_INFRA = "INFRA"
INTENT_COMPOUND = "COMPOUND"
INTENT_AMBIGUOUS = "AMBIGUOUS"

PRIMARY_INTENTS = frozenset({
    INTENT_SEARCH,
    INTENT_DOC,
    INTENT_EXPLAIN,
    INTENT_EDIT,
    INTENT_VALIDATE,
    INTENT_INFRA,
    INTENT_COMPOUND,
    INTENT_AMBIGUOUS,
})

# ---------------------------------------------------------------------------
# Production-emission contract (Stage 39)
# route_production_instruction can return these primary intents.
# VALIDATE is deferred: no emission path; remains in PRIMARY_INTENTS for
# deserialization. COMPOUND is production-real only when
# suggested_plan_shape == PLAN_SHAPE_TWO_PHASE_DOCS_CODE (two-phase docs+code).
# ---------------------------------------------------------------------------

DEFERRED_PRIMARY_INTENTS = frozenset({INTENT_VALIDATE})

PRODUCTION_EMITTABLE_PRIMARY_INTENTS = frozenset({
    INTENT_SEARCH,
    INTENT_DOC,
    INTENT_EXPLAIN,
    INTENT_EDIT,
    INTENT_INFRA,
    INTENT_COMPOUND,
    INTENT_AMBIGUOUS,
})

# ---------------------------------------------------------------------------
# Suggested plan shapes: hints to plan_resolver, not binding contracts.
# plan_resolver is free to ignore or override these.
# ---------------------------------------------------------------------------

PLAN_SHAPE_SINGLE_STEP_SEARCH = "single_step_search"
PLAN_SHAPE_DOCS_SEED_LANE = "docs_seed_lane"
PLAN_SHAPE_SINGLE_STEP_EXPLAIN = "single_step_explain"
PLAN_SHAPE_PLANNER_MULTI_STEP = "planner_multi_step"
PLAN_SHAPE_SINGLE_STEP_VALIDATE = "single_step_validate"  # Deferred: not used by production router
PLAN_SHAPE_SINGLE_STEP_INFRA = "single_step_infra"
PLAN_SHAPE_DECOMPOSE_THEN_ROUTE = "decompose_then_route"
PLAN_SHAPE_DEFER_TO_PLANNER = "defer_to_planner"
# Sub-shape for COMPOUND: docs phase then code phase (parent plan).
PLAN_SHAPE_TWO_PHASE_DOCS_CODE = "two_phase_docs_code"

# Stage 40: planner_handoff_reason values when primary_intent is AMBIGUOUS
PLANNER_HANDOFF_ROUTER_DISABLED = "router_disabled"
PLANNER_HANDOFF_CONFIDENCE_BELOW = "confidence_below_threshold"
PLANNER_HANDOFF_UNCLEAR_INTENT = "unclear_intent"

_INTENT_TO_PLAN_SHAPE: dict[str, str] = {
    INTENT_SEARCH: PLAN_SHAPE_SINGLE_STEP_SEARCH,
    INTENT_DOC: PLAN_SHAPE_DOCS_SEED_LANE,
    INTENT_EXPLAIN: PLAN_SHAPE_SINGLE_STEP_EXPLAIN,
    INTENT_EDIT: PLAN_SHAPE_PLANNER_MULTI_STEP,
    INTENT_VALIDATE: PLAN_SHAPE_SINGLE_STEP_VALIDATE,
    INTENT_INFRA: PLAN_SHAPE_SINGLE_STEP_INFRA,
    INTENT_COMPOUND: PLAN_SHAPE_DECOMPOSE_THEN_ROUTE,
    INTENT_AMBIGUOUS: PLAN_SHAPE_DEFER_TO_PLANNER,
}


def default_plan_shape(primary_intent: str) -> str:
    """Return the canonical plan shape hint for a given primary intent."""
    return _INTENT_TO_PLAN_SHAPE.get(primary_intent, PLAN_SHAPE_DEFER_TO_PLANNER)


def is_production_emittable_primary(
    primary_intent: str,
    *,
    suggested_plan_shape: str | None = None,
) -> bool:
    """True if this primary intent can be emitted by route_production_instruction.
    COMPOUND is production-real only when suggested_plan_shape is PLAN_SHAPE_TWO_PHASE_DOCS_CODE.
    """
    if primary_intent in DEFERRED_PRIMARY_INTENTS:
        return False
    if primary_intent not in PRODUCTION_EMITTABLE_PRIMARY_INTENTS:
        return False
    if primary_intent == INTENT_COMPOUND and suggested_plan_shape is not None:
        return suggested_plan_shape == PLAN_SHAPE_TWO_PHASE_DOCS_CODE
    if primary_intent == INTENT_COMPOUND and suggested_plan_shape is None:
        return False  # Cannot confirm two-phase; conservative
    return True


# ---------------------------------------------------------------------------
# Routing contract
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoutedIntent:
    """
    Stable schema for routed intent output.

    Fields
    ------
    primary_intent      : One of PRIMARY_INTENTS.
    secondary_intents   : Non-empty only when primary_intent is COMPOUND.
    decomposition_needed: True only for COMPOUND (multi-intent decomposition).
    clarification_needed: True for AMBIGUOUS when the user should clarify intent,
                          or when legacy GENERAL applies; False for COMPOUND.

    Production semantics (Stage 39): secondary_intents and decomposition_needed are
    behavior-driving only when suggested_plan_shape == PLAN_SHAPE_TWO_PHASE_DOCS_CODE
    and get_parent_plan runs. Otherwise telemetry-only (flat COMPOUND never emitted
    in production).

    confidence          : Float in [0, 1]. AMBIGUOUS should use low confidence
                          when classification is uncertain, not a forced high value.
    rationale           : Human-readable reason for the classification.
    matched_signals     : Lexical or logical signals that fired.
    suggested_plan_shape: Plan-shape hint for plan_resolver.
    planner_handoff_reason: Reason this routed instruction was deferred to planner
                            instead of taking a production short-circuit path.
                            Values: router_disabled | confidence_below_threshold |
                            unclear_intent. Empty when not deferred.
                            Narrow scope: NOT a generic explanation, failure reason,
                            or execution outcome — only planner-defer causation.
    """

    primary_intent: str
    secondary_intents: tuple[str, ...] = ()
    decomposition_needed: bool = False
    clarification_needed: bool = False
    confidence: float = 0.0
    rationale: str = ""
    matched_signals: tuple[str, ...] = ()
    suggested_plan_shape: str = ""
    planner_handoff_reason: str = ""

    def __post_init__(self) -> None:
        if self.primary_intent not in PRIMARY_INTENTS:
            raise ValueError(
                f"primary_intent must be one of {PRIMARY_INTENTS}, got {self.primary_intent!r}"
            )
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0, 1], got {self.confidence}")

    def to_dict(self) -> dict:
        return {
            "primary_intent": self.primary_intent,
            "secondary_intents": list(self.secondary_intents),
            "decomposition_needed": self.decomposition_needed,
            "clarification_needed": self.clarification_needed,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "matched_signals": list(self.matched_signals),
            "suggested_plan_shape": self.suggested_plan_shape,
            "planner_handoff_reason": self.planner_handoff_reason,
        }


def routed_intent_from_dict(d: dict) -> RoutedIntent:
    """Build RoutedIntent from dict (e.g. from JSON or telemetry)."""
    raw = str(d.get("primary_intent", "AMBIGUOUS")).strip().upper()
    primary = raw if raw in PRIMARY_INTENTS else INTENT_AMBIGUOUS
    return RoutedIntent(
        primary_intent=primary,
        secondary_intents=tuple(str(x).strip().upper() for x in (d.get("secondary_intents") or [])),
        decomposition_needed=bool(d.get("decomposition_needed", False)),
        clarification_needed=bool(d.get("clarification_needed", False)),
        confidence=float(d.get("confidence", 0.0)),
        rationale=str(d.get("rationale", "")),
        matched_signals=tuple(str(x) for x in (d.get("matched_signals") or [])),
        suggested_plan_shape=str(d.get("suggested_plan_shape", "")),
        planner_handoff_reason=str(d.get("planner_handoff_reason", "")),
    )


# ---------------------------------------------------------------------------
# Legacy adapter
# ---------------------------------------------------------------------------

# Mapping from legacy RouterDecision categories to new intent taxonomy
_LEGACY_TO_INTENT = {
    "CODE_SEARCH": INTENT_SEARCH,
    "CODE_EDIT": INTENT_EDIT,
    "CODE_EXPLAIN": INTENT_EXPLAIN,
    "INFRA": INTENT_INFRA,
    "GENERAL": INTENT_AMBIGUOUS,
}
# Unknown legacy category -> AMBIGUOUS with unclear_intent. Adapter fallback,
# not genuine semantic understanding. Slightly lossy; acceptable for Stage 40.


def routed_intent_from_router_decision(category: str, confidence: float) -> RoutedIntent:
    """Convert legacy RouterDecision (CODE_SEARCH, CODE_EDIT, etc.) to RoutedIntent.
    Unknown category is treated as unclear_intent for now — adapter fallback."""
    primary = _LEGACY_TO_INTENT.get(category, INTENT_AMBIGUOUS)
    is_ambiguous = primary == INTENT_AMBIGUOUS
    return RoutedIntent(
        primary_intent=primary,
        decomposition_needed=False,
        clarification_needed=is_ambiguous,
        confidence=confidence,
        rationale=f"legacy router: {category}",
        matched_signals=(category,),
        suggested_plan_shape=default_plan_shape(primary),
        planner_handoff_reason=PLANNER_HANDOFF_UNCLEAR_INTENT if is_ambiguous else "",
    )
