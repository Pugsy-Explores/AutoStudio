"""
Stage 37: Regression tests for intent routing.
Stage 39: Production contract assertions; simple_router is test-only, not production parity.

Uses simple_router (deterministic, no model) to verify intent taxonomy,
routing contract, and suppression rules. simple_router is richer than production
(emits VALIDATE, general COMPOUND) and is used for regression only. Production
behavior is exercised via route_production_instruction in test_plan_resolver_routing.

Test groups
-----------
A. Simple single-intent (8 cases)   — one clear intent fires, others silent
B. Compound multi-intent (8 cases)  — two or more distinct intents fire
C. Ambiguous / borderline (8 cases) — no markers, vague phrasing, or edge cases
D. Contract correctness (4 cases)   — schema fields, serialisation, legacy adapter

Confusion summary (see bottom of file) documents which pairs are
historically difficult to separate.
"""

import pytest

from agent.routing.intent import (
    DEFERRED_PRIMARY_INTENTS,
    INTENT_AMBIGUOUS,
    INTENT_COMPOUND,
    INTENT_DOC,
    INTENT_EDIT,
    INTENT_EXPLAIN,
    INTENT_INFRA,
    INTENT_SEARCH,
    INTENT_VALIDATE,
    PLAN_SHAPE_DECOMPOSE_THEN_ROUTE,
    PLAN_SHAPE_DEFER_TO_PLANNER,
    PLAN_SHAPE_DOCS_SEED_LANE,
    PLAN_SHAPE_PLANNER_MULTI_STEP,
    PLAN_SHAPE_SINGLE_STEP_SEARCH,
    PLAN_SHAPE_SINGLE_STEP_VALIDATE,
    PLANNER_HANDOFF_UNCLEAR_INTENT,
    PRODUCTION_EMITTABLE_PRIMARY_INTENTS,
    RoutedIntent,
    default_plan_shape,
    routed_intent_from_dict,
    routed_intent_from_router_decision,
)
from agent.routing.simple_router import route_intent_simple


# ===========================================================================
# A. Simple single-intent (8 cases)
# ===========================================================================


def test_simple_search_where():
    r = route_intent_simple("Where is the login function defined?")
    assert r.primary_intent == INTENT_SEARCH
    assert r.confidence >= 0.5
    assert not r.decomposition_needed
    assert r.suggested_plan_shape == PLAN_SHAPE_SINGLE_STEP_SEARCH


def test_simple_search_find():
    r = route_intent_simple("Find all usages of fetch_user")
    assert r.primary_intent == INTENT_SEARCH
    assert not r.decomposition_needed


def test_simple_search_list():
    r = route_intent_simple("List all API endpoints in the codebase")
    assert r.primary_intent == INTENT_SEARCH


def test_simple_doc_readme():
    # "What's in the README?" — only DOC fires, no SEARCH marker present
    r = route_intent_simple("What's in the README?")
    assert r.primary_intent == INTENT_DOC
    assert not r.decomposition_needed
    assert r.suggested_plan_shape == PLAN_SHAPE_DOCS_SEED_LANE


def test_simple_doc_installation():
    # "Show the installation docs" — "installation" fires DOC; "show me" not present
    r = route_intent_simple("Show the installation docs")
    assert r.primary_intent == INTENT_DOC


def test_simple_doc_where_readme_suppressed_to_doc():
    # "Where is the README?" — SEARCH("where") + DOC("readme") fire together with no
    # other intents. Suppression rule converts this to DOC, not COMPOUND.
    r = route_intent_simple("Where is the README?")
    assert r.primary_intent == INTENT_DOC
    assert r.secondary_intents == ()
    assert not r.decomposition_needed


def test_simple_explain_how_does():
    r = route_intent_simple("How does the auth flow work?")
    assert r.primary_intent == INTENT_EXPLAIN
    assert not r.decomposition_needed


def test_simple_validate_pytest():
    r = route_intent_simple("Run pytest on tests/unit")
    assert r.primary_intent == INTENT_VALIDATE
    assert r.suggested_plan_shape == PLAN_SHAPE_SINGLE_STEP_VALIDATE


# ===========================================================================
# B. Compound multi-intent (8 cases)
# ===========================================================================


def test_compound_find_and_explain():
    r = route_intent_simple("Find the auth module and explain how it works")
    assert r.primary_intent == INTENT_COMPOUND
    assert INTENT_SEARCH in r.secondary_intents
    assert INTENT_EXPLAIN in r.secondary_intents
    assert r.decomposition_needed
    assert r.suggested_plan_shape == PLAN_SHAPE_DECOMPOSE_THEN_ROUTE


def test_compound_fix_and_validate():
    r = route_intent_simple("Fix the bug and run tests to validate the fix")
    assert r.primary_intent == INTENT_COMPOUND
    assert INTENT_EDIT in r.secondary_intents
    assert INTENT_VALIDATE in r.secondary_intents
    assert r.decomposition_needed


def test_compound_docs_and_edit():
    r = route_intent_simple("Find the README and update the version number")
    assert r.primary_intent == INTENT_COMPOUND
    # "find" fires SEARCH; "readme" fires DOC; "update" fires EDIT — three intents
    assert INTENT_EDIT in r.secondary_intents
    assert r.decomposition_needed


def test_compound_explain_and_add():
    r = route_intent_simple("Explain the current flow and add logging to each step")
    assert r.primary_intent == INTENT_COMPOUND
    assert INTENT_EXPLAIN in r.secondary_intents
    assert INTENT_EDIT in r.secondary_intents


def test_compound_search_fix_validate():
    r = route_intent_simple("Find the failing test, fix it, and run pytest")
    assert r.primary_intent == INTENT_COMPOUND
    # At least two of: SEARCH, EDIT, VALIDATE
    assert len(r.secondary_intents) >= 2


def test_compound_infra_and_explain():
    r = route_intent_simple("Set up Docker and explain how the containers are configured")
    assert r.primary_intent == INTENT_COMPOUND
    assert INTENT_INFRA in r.secondary_intents
    assert INTENT_EXPLAIN in r.secondary_intents


def test_compound_edit_and_validate():
    r = route_intent_simple("Refactor the parser module and check that all tests pass")
    assert r.primary_intent == INTENT_COMPOUND
    assert INTENT_EDIT in r.secondary_intents
    assert INTENT_VALIDATE in r.secondary_intents


def test_compound_has_matched_signals():
    r = route_intent_simple("Find the auth module and explain how it works")
    assert r.primary_intent == INTENT_COMPOUND
    assert len(r.matched_signals) >= 2, "compound result must expose matched signals"


# ===========================================================================
# C. Ambiguous / borderline (8 cases)
# ===========================================================================


def test_ambiguous_empty():
    r = route_intent_simple("")
    assert r.primary_intent == INTENT_AMBIGUOUS
    assert not r.decomposition_needed
    assert r.clarification_needed
    assert r.confidence == 0.0
    assert r.suggested_plan_shape == PLAN_SHAPE_DEFER_TO_PLANNER


def test_ambiguous_whitespace_only():
    r = route_intent_simple("   ")
    assert r.primary_intent == INTENT_AMBIGUOUS
    assert not r.decomposition_needed
    assert r.clarification_needed


def test_ambiguous_no_markers():
    r = route_intent_simple("The thing that does stuff")
    assert r.primary_intent == INTENT_AMBIGUOUS


def test_ambiguous_vague():
    r = route_intent_simple("Something is wrong with the code")
    assert r.primary_intent == INTENT_AMBIGUOUS


def test_ambiguous_pronoun_only():
    r = route_intent_simple("Make it work")
    assert r.primary_intent == INTENT_AMBIGUOUS


def test_ambiguous_no_confidence_inflation():
    # Ambiguous results must not report high confidence
    r = route_intent_simple("The thing is broken")
    assert r.primary_intent == INTENT_AMBIGUOUS
    assert r.confidence < 0.5, "ambiguous classification must not report high confidence"


def test_borderline_single_word_refactor():
    # "refactor" fires EDIT via _EDIT_MARKERS; acceptable to route as EDIT
    r = route_intent_simple("refactor")
    assert r.primary_intent in (INTENT_EDIT, INTENT_AMBIGUOUS)


def test_borderline_update_without_target():
    # "update" alone fires EDIT — borderline, but single-intent is acceptable
    r = route_intent_simple("update the code")
    assert r.primary_intent in (INTENT_EDIT, INTENT_COMPOUND, INTENT_AMBIGUOUS)


# ===========================================================================
# D. Contract correctness
# ===========================================================================


def test_routed_intent_full_fields():
    r = RoutedIntent(
        primary_intent=INTENT_SEARCH,
        secondary_intents=(INTENT_EDIT,),
        decomposition_needed=False,
        clarification_needed=False,
        confidence=0.9,
        rationale="test",
        matched_signals=("find",),
        suggested_plan_shape=PLAN_SHAPE_SINGLE_STEP_SEARCH,
        planner_handoff_reason="",
    )
    d = r.to_dict()
    assert d["primary_intent"] == INTENT_SEARCH
    assert d["secondary_intents"] == [INTENT_EDIT]
    assert d["decomposition_needed"] is False
    assert d["clarification_needed"] is False
    assert d["confidence"] == 0.9
    assert d["rationale"] == "test"
    assert d["matched_signals"] == ["find"]
    assert d["suggested_plan_shape"] == PLAN_SHAPE_SINGLE_STEP_SEARCH
    assert d["planner_handoff_reason"] == ""


def test_routed_intent_from_dict_roundtrip():
    original = route_intent_simple("Find all usages of fetch_user")
    d = original.to_dict()
    restored = routed_intent_from_dict(d)
    assert restored.primary_intent == original.primary_intent
    assert restored.confidence == original.confidence
    assert restored.matched_signals == original.matched_signals
    assert restored.suggested_plan_shape == original.suggested_plan_shape
    assert restored.clarification_needed == original.clarification_needed
    assert restored.planner_handoff_reason == original.planner_handoff_reason


def test_routed_intent_planner_handoff_reason_roundtrip():
    """planner_handoff_reason roundtrips via to_dict / routed_intent_from_dict."""
    r = RoutedIntent(
        primary_intent=INTENT_AMBIGUOUS,
        clarification_needed=False,
        confidence=1.0,
        rationale="test",
        matched_signals=("router_disabled",),
        suggested_plan_shape="planner_multi_step",
        planner_handoff_reason="router_disabled",
    )
    d = r.to_dict()
    restored = routed_intent_from_dict(d)
    assert restored.planner_handoff_reason == "router_disabled"


def test_routed_intent_from_router_decision_legacy_search():
    r = routed_intent_from_router_decision("CODE_SEARCH", 0.85)
    assert r.primary_intent == INTENT_SEARCH
    assert r.confidence == 0.85
    assert r.suggested_plan_shape == PLAN_SHAPE_SINGLE_STEP_SEARCH
    assert "CODE_SEARCH" in r.matched_signals


def test_routed_intent_legacy_general_maps_to_ambiguous():
    r = routed_intent_from_router_decision("GENERAL", 0.5)
    assert r.primary_intent == INTENT_AMBIGUOUS
    assert not r.decomposition_needed
    assert r.clarification_needed
    assert r.suggested_plan_shape == PLAN_SHAPE_DEFER_TO_PLANNER


def test_routed_intent_from_router_decision_general_sets_planner_handoff_reason():
    r = routed_intent_from_router_decision("GENERAL", 0.5)
    assert r.planner_handoff_reason == PLANNER_HANDOFF_UNCLEAR_INTENT
    assert r.clarification_needed is True


def test_default_plan_shape_covers_all_intents():
    from agent.routing.intent import PRIMARY_INTENTS
    for intent in PRIMARY_INTENTS:
        shape = default_plan_shape(intent)
        assert shape, f"default_plan_shape returned empty string for {intent}"


def test_production_emittable_excludes_validate():
    """VALIDATE is deferred: not in production emission contract."""
    assert INTENT_VALIDATE not in PRODUCTION_EMITTABLE_PRIMARY_INTENTS
    assert INTENT_VALIDATE in DEFERRED_PRIMARY_INTENTS


# ===========================================================================
# Confusion summary
#
# The following pairs are historically difficult for keyword routers and
# should guide future model-based routing improvements:
#
# 1. DOC vs SEARCH
#    "Where is the README?" fires both DOC("readme") and SEARCH("where").
#    Current fix: suppression rule collapses to DOC when no other intents fire.
#    Risk: "Find the README and show the diff" should remain COMPOUND (SEARCH+EDIT);
#    suppression does not apply because EDIT also fires.
#
# 2. EXPLAIN vs EDIT
#    "Describe and refactor the auth module" fires both EXPLAIN and EDIT.
#    Correctly classified COMPOUND. Risk: instructions that use "explain" to
#    describe the _subject_ of an edit ("explain the change you just made")
#    would double-fire; acceptable for keyword router.
#
# 3. VALIDATE vs EDIT
#    "Fix the failing test" fires EDIT("fix") only, not VALIDATE, because
#    "run test" is not present. Correct. "Fix the tests and run pytest" fires
#    both → COMPOUND. Correct.
#
# 4. INFRA vs EDIT
#    "Update the Dockerfile" fires EDIT("update") + INFRA("dockerfile") → COMPOUND.
#    This is intentional: the user wants to edit an infra artifact, not just
#    describe it. No suppression rule applied here.
#
# 5. AMBIGUOUS
#    Any instruction without a marker lands AMBIGUOUS. This is correct and
#    conservative — do not add heuristics to force a label on vague input.
# ===========================================================================
