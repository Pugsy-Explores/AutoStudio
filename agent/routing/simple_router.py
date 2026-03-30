"""
Stage 37: Simple keyword-based intent router for tests and fallback.

Deterministic, no model calls. Used for regression tests and when model is unavailable.
Explicit patterns only; no benchmark-specific logic.

Design rules
------------
1. Collect all intent signals that fire for a lowercased instruction.
2. Apply suppression rules before counting:
   - SEARCH is suppressed when DOC fires and no non-doc intent fires.
     Rationale: "Where is the README?" — "where" is a docs-discovery verb, not a
     general code-search verb. Classifying it COMPOUND adds noise.
3. If exactly one intent remains → single-intent result (confidence 0.85).
4. If two or more remain → COMPOUND with secondary_intents listing them.
5. If none → AMBIGUOUS.
6. Populate matched_signals (which markers fired) and suggested_plan_shape.
"""

from __future__ import annotations

from agent.routing.intent import (
    INTENT_AMBIGUOUS,
    INTENT_COMPOUND,
    INTENT_DOC,
    INTENT_EDIT,
    INTENT_EXPLAIN,
    INTENT_INFRA,
    INTENT_SEARCH,
    INTENT_VALIDATE,
    RoutedIntent,
    default_plan_shape,
)

# ---------------------------------------------------------------------------
# Marker tables — each entry is a substring checked against the lowercased
# instruction. Keep tables small and high-precision; no repo-specific strings.
# ---------------------------------------------------------------------------

_SEARCH_MARKERS = (
    "find",
    "locate",
    "where",
    "show me",
    "list",
    "grep",
    "search for",
)

# DOC markers: documentation artifact names and lifecycle verbs
_DOC_MARKERS = (
    "readme",
    "docs",
    "documentation",
    "install",
    "installation",
    "setup guide",
    "guide",
    "changelog",
    "contributing",
)

_EXPLAIN_MARKERS = (
    "explain",
    "describe",
    "how does",
    "what does",
    "walk through",
    "walk me through",
)

_EDIT_MARKERS = (
    "fix",
    "add",
    "change",
    "modify",
    "patch",
    "implement",
    "edit",
    "update",
    "refactor",
    "rename",
    "delete",
    "remove",
    "rewrite",
)

_VALIDATE_MARKERS = (
    "run test",
    "run tests",
    "pytest",
    "validate",
    "check that",
    "verify that",
)

_INFRA_MARKERS = (
    "docker",
    "dockerfile",
    "ci ",
    "build pipeline",
    "deploy",
    "infra",
    "kubernetes",
    "helm",
)


def _fired_markers(low: str, markers: tuple[str, ...]) -> list[str]:
    """Return markers from the table that appear in the lowercased instruction."""
    return [m for m in markers if m in low]


def route_intent_simple(instruction: str) -> RoutedIntent:
    """
    Deterministic keyword-based intent routing. No model calls.

    Returns RoutedIntent with primary_intent, secondary_intents,
    decomposition_needed, confidence, rationale, matched_signals,
    and suggested_plan_shape.
    """
    if not instruction or not instruction.strip():
        return RoutedIntent(
            primary_intent=INTENT_AMBIGUOUS,
            decomposition_needed=False,
            clarification_needed=True,
            confidence=0.0,
            rationale="empty instruction",
            matched_signals=(),
            suggested_plan_shape=default_plan_shape(INTENT_AMBIGUOUS),
        )

    low = instruction.strip().lower()

    # Collect signals per intent
    search_signals = _fired_markers(low, _SEARCH_MARKERS)
    doc_signals = _fired_markers(low, _DOC_MARKERS)
    explain_signals = _fired_markers(low, _EXPLAIN_MARKERS)
    edit_signals = _fired_markers(low, _EDIT_MARKERS)
    validate_signals = _fired_markers(low, _VALIDATE_MARKERS)
    infra_signals = _fired_markers(low, _INFRA_MARKERS)

    # Build intent → fired-signals map for all intents that matched
    raw: dict[str, list[str]] = {}
    if search_signals:
        raw[INTENT_SEARCH] = search_signals
    if doc_signals:
        raw[INTENT_DOC] = doc_signals
    if explain_signals:
        raw[INTENT_EXPLAIN] = explain_signals
    if edit_signals:
        raw[INTENT_EDIT] = edit_signals
    if validate_signals:
        raw[INTENT_VALIDATE] = validate_signals
    if infra_signals:
        raw[INTENT_INFRA] = infra_signals

    if not raw:
        return RoutedIntent(
            primary_intent=INTENT_AMBIGUOUS,
            decomposition_needed=False,
            clarification_needed=True,
            confidence=0.0,
            rationale="no intent markers matched",
            matched_signals=(),
            suggested_plan_shape=default_plan_shape(INTENT_AMBIGUOUS),
        )

    # ------------------------------------------------------------------
    # Suppression rule: SEARCH + DOC with no other intents.
    # "Where is the README?" — "where" is a docs-discovery verb, not a
    # general code search. Suppress SEARCH so the result is DOC, not
    # COMPOUND. Only applies when SEARCH and DOC both fire AND no other
    # intent fires alongside them.
    # ------------------------------------------------------------------
    active = dict(raw)
    other_intents = {k for k in active if k not in (INTENT_SEARCH, INTENT_DOC)}
    if INTENT_SEARCH in active and INTENT_DOC in active and not other_intents:
        del active[INTENT_SEARCH]

    # Collect all signals for observability
    all_signals: list[str] = []
    for signals in active.values():
        all_signals.extend(signals)

    ordered = list(active.keys())

    if len(ordered) == 0:
        # Suppression left nothing (shouldn't happen, but be defensive)
        return RoutedIntent(
            primary_intent=INTENT_AMBIGUOUS,
            decomposition_needed=False,
            clarification_needed=True,
            confidence=0.0,
            rationale="all signals suppressed",
            matched_signals=tuple(all_signals),
            suggested_plan_shape=default_plan_shape(INTENT_AMBIGUOUS),
        )

    if len(ordered) == 1:
        primary = ordered[0]
        return RoutedIntent(
            primary_intent=primary,
            secondary_intents=(),
            decomposition_needed=False,
            clarification_needed=False,
            confidence=0.85,
            rationale=f"matched {primary} markers: {', '.join(active[primary])}",
            matched_signals=tuple(active[primary]),
            suggested_plan_shape=default_plan_shape(primary),
        )

    # Multiple intents → COMPOUND
    return RoutedIntent(
        primary_intent=INTENT_COMPOUND,
        secondary_intents=tuple(ordered),
        decomposition_needed=True,
        clarification_needed=False,
        confidence=0.6,
        rationale=f"multiple intents: {', '.join(ordered)}",
        matched_signals=tuple(all_signals),
        suggested_plan_shape=default_plan_shape(INTENT_COMPOUND),
    )
