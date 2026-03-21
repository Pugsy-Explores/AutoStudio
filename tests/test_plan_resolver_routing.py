"""
Stage 38: Production plan_resolver consumption of unified RoutedIntent.

Tests resolver branches and telemetry, not keyword-router classification alone.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent.orchestrator.plan_resolver import (
    get_plan,
    get_plan_resolution_telemetry,
    reset_plan_resolution_telemetry,
)
from agent.routing.intent import (
    INTENT_AMBIGUOUS,
    INTENT_COMPOUND,
    INTENT_DOC,
    INTENT_EDIT,
    INTENT_EXPLAIN,
    INTENT_INFRA,
    INTENT_SEARCH,
    PLAN_SHAPE_DOCS_SEED_LANE,
    PLAN_SHAPE_PLANNER_MULTI_STEP,
    PLAN_SHAPE_TWO_PHASE_DOCS_CODE,
    PLANNER_HANDOFF_UNCLEAR_INTENT,
    RoutedIntent,
)


@pytest.fixture(autouse=True)
def _reset_telemetry():
    reset_plan_resolution_telemetry()
    yield
    reset_plan_resolution_telemetry()


def test_get_plan_docs_seed_from_routed_intent():
    """DOC + docs_seed_lane yields SEARCH_CANDIDATES docs lane (no planner)."""
    ri = RoutedIntent(
        primary_intent=INTENT_DOC,
        suggested_plan_shape=PLAN_SHAPE_DOCS_SEED_LANE,
        confidence=0.95,
        rationale="test",
        matched_signals=("docs_artifact",),
    )
    with patch("agent.orchestrator.plan_resolver.ENABLE_INSTRUCTION_ROUTER", True):
        plan = get_plan("Find the README for install", routed_intent=ri)
    steps = plan.get("steps", [])
    assert len(steps) == 3
    assert steps[0].get("action") == "SEARCH_CANDIDATES"
    assert steps[0].get("artifact_mode") == "docs"
    tel = get_plan_resolution_telemetry()
    assert tel.get("docs_seed_plan_used") is True
    assert tel.get("routed_intent_primary") == INTENT_DOC
    assert tel.get("routed_intent_suggested_plan_shape") == PLAN_SHAPE_DOCS_SEED_LANE
    assert "routed_intent_matched_signals" in tel


def test_get_plan_search_short_circuit_from_routed_intent():
    ri = RoutedIntent(
        primary_intent=INTENT_SEARCH,
        confidence=0.9,
        rationale="legacy",
        matched_signals=("CODE_SEARCH",),
        suggested_plan_shape="single_step_search",
    )
    instruction = "Locate the auth module"
    with patch("agent.orchestrator.plan_resolver.ENABLE_INSTRUCTION_ROUTER", True):
        plan = get_plan(instruction, routed_intent=ri)
    assert len(plan.get("steps", [])) == 1
    step = plan["steps"][0]
    assert step.get("action") == "SEARCH"
    assert step.get("description") == instruction
    assert "query" in step
    assert step.get("query")
    tel = get_plan_resolution_telemetry()
    assert tel.get("router_short_circuit_used") is True
    assert tel.get("routed_intent_primary") == INTENT_SEARCH


def test_get_plan_router_disabled_still_merges_telemetry():
    ri = RoutedIntent(
        primary_intent=INTENT_SEARCH,
        confidence=1.0,
        rationale="x",
        matched_signals=("router_disabled",),
        suggested_plan_shape=PLAN_SHAPE_PLANNER_MULTI_STEP,
    )
    mock_plan = {"steps": [{"id": 1, "action": "EDIT", "description": "x"}]}
    with patch("agent.orchestrator.plan_resolver.ENABLE_INSTRUCTION_ROUTER", False):
        with patch("agent.orchestrator.plan_resolver.plan", return_value=mock_plan) as mp:
            out = get_plan("anything", routed_intent=ri)
    mp.assert_called_once()
    assert out.get("plan_id")
    tel = get_plan_resolution_telemetry()
    assert tel.get("planner_used") is True
    assert tel.get("routed_intent_primary") == INTENT_SEARCH


def test_get_plan_compound_defers_to_planner_with_override_flag():
    """COMPOUND on flat get_plan defers to planner; override flag set."""
    ri = RoutedIntent(
        primary_intent=INTENT_COMPOUND,
        secondary_intents=(INTENT_DOC, INTENT_EXPLAIN),
        decomposition_needed=True,
        clarification_needed=False,
        confidence=0.9,
        rationale="test",
        matched_signals=("two_phase_docs_code",),
        suggested_plan_shape=PLAN_SHAPE_TWO_PHASE_DOCS_CODE,
    )
    mock_plan = {"steps": [{"id": 1, "action": "EXPLAIN", "description": "x"}]}
    with patch("agent.orchestrator.plan_resolver.ENABLE_INSTRUCTION_ROUTER", True):
        with patch("agent.orchestrator.plan_resolver.plan", return_value=mock_plan):
            get_plan("Find docs and explain flow", routed_intent=ri)
    tel = get_plan_resolution_telemetry()
    assert tel.get("planner_used") is True
    assert tel.get("routing_overridden_downstream") is True
    assert tel.get("routing_override_reason") == "compound_intent_flat_plan_defers_to_planner"


def test_route_production_instruction_two_phase_matches_parent_plan_shape():
    """Unified router returns COMPOUND + two_phase shape for mixed docs+code."""
    from agent.routing.production_routing import route_production_instruction

    with patch("agent.routing.production_routing.ENABLE_INSTRUCTION_ROUTER", True):
        ri = route_production_instruction(
            "Find architecture docs and explain replanner flow",
        )
    assert ri.primary_intent == INTENT_COMPOUND
    assert ri.suggested_plan_shape == PLAN_SHAPE_TWO_PHASE_DOCS_CODE
    assert ri.decomposition_needed is True
    assert ri.clarification_needed is False


def test_route_production_docs_artifact_before_two_phase():
    """Docs-only instructions resolve to DOC, not two-phase COMPOUND."""
    from agent.routing.production_routing import route_production_instruction

    with patch("agent.routing.production_routing.ENABLE_INSTRUCTION_ROUTER", True):
        ri = route_production_instruction("Find the README in the repo")
    assert ri.primary_intent == INTENT_DOC
    assert ri.suggested_plan_shape == PLAN_SHAPE_DOCS_SEED_LANE


def test_ambiguous_contract_clarification_not_decomposition():
    from agent.routing.production_routing import route_production_instruction

    with patch("agent.routing.production_routing.ENABLE_INSTRUCTION_ROUTER", True):
        with patch("agent.routing.instruction_router.route_instruction") as rm:
            rm.return_value = MagicMock(category="GENERAL", confidence=0.5)
            ri = route_production_instruction("vague thing")
    assert ri.primary_intent == "AMBIGUOUS"
    assert ri.decomposition_needed is False
    assert ri.clarification_needed is True
    assert ri.planner_handoff_reason == PLANNER_HANDOFF_UNCLEAR_INTENT


def test_edit_is_planner_deferred_not_ambiguous():
    """EDIT path: primary is EDIT, planner_handoff_reason empty, planner used."""
    mock_plan = {"steps": [{"id": 1, "action": "EDIT", "description": "refactor x"}]}
    with patch("agent.routing.production_routing.ENABLE_INSTRUCTION_ROUTER", True):
        with patch("agent.routing.production_routing.route_instruction") as rm:
            rm.return_value = MagicMock(category="CODE_EDIT", confidence=0.9)
            with patch("agent.orchestrator.plan_resolver.plan", return_value=mock_plan) as mp:
                plan = get_plan("Refactor the auth module", trace_id=None)
    mp.assert_called_once()
    tel = get_plan_resolution_telemetry()
    assert tel.get("planner_used") is True
    assert tel.get("routed_intent_primary") == INTENT_EDIT
    assert tel.get("routed_intent_planner_handoff_reason") == ""
    assert plan.get("steps")


def test_router_disabled_is_operational_fallback():
    """Router disabled -> AMBIGUOUS with planner_handoff_reason router_disabled."""
    from agent.routing.production_routing import route_production_instruction

    mock_plan = {"steps": [{"id": 1, "action": "EDIT", "description": "x"}]}
    with patch("agent.routing.production_routing.ENABLE_INSTRUCTION_ROUTER", False):
        ri = route_production_instruction("anything")
        assert ri.primary_intent == INTENT_AMBIGUOUS
        assert ri.planner_handoff_reason == "router_disabled"
        assert ri.clarification_needed is False
        with patch("agent.orchestrator.plan_resolver.ENABLE_INSTRUCTION_ROUTER", False):
            with patch("agent.orchestrator.plan_resolver.plan", return_value=mock_plan) as mp:
                get_plan("anything")
                mp.assert_called_once()
    tel = get_plan_resolution_telemetry()
    assert tel.get("routed_intent_primary") == INTENT_AMBIGUOUS
    assert tel.get("routed_intent_planner_handoff_reason") == "router_disabled"


def test_true_ambiguity_has_unclear_intent():
    """GENERAL from model -> AMBIGUOUS with planner_handoff_reason unclear_intent."""
    from agent.routing.production_routing import route_production_instruction

    with patch("agent.routing.production_routing.ENABLE_INSTRUCTION_ROUTER", True):
        with patch("agent.routing.production_routing.route_instruction") as rm:
            rm.return_value = MagicMock(category="GENERAL", confidence=0.5)
            ri = route_production_instruction("vague thing")
    assert ri.primary_intent == INTENT_AMBIGUOUS
    assert ri.planner_handoff_reason == PLANNER_HANDOFF_UNCLEAR_INTENT
    assert ri.clarification_needed is True


def test_route_production_legacy_edit_calls_planner_via_get_plan():
    """Production chain: legacy CODE_EDIT -> get_plan -> planner (no short-circuit)."""
    mock_plan = {"steps": [{"id": 1, "action": "EDIT", "description": "refactor x"}]}
    with patch("agent.routing.production_routing.ENABLE_INSTRUCTION_ROUTER", True):
        with patch("agent.routing.production_routing.route_instruction") as rm:
            rm.return_value = MagicMock(category="CODE_EDIT", confidence=0.9)
            with patch("agent.orchestrator.plan_resolver.plan", return_value=mock_plan) as mp:
                plan = get_plan("Refactor the auth module", trace_id=None)
    mp.assert_called_once()
    tel = get_plan_resolution_telemetry()
    assert tel.get("planner_used") is True
    assert tel.get("router_short_circuit_used") is False
    assert tel.get("resolver_consumption") == "planner"
    assert plan.get("steps")


def test_get_plan_infra_short_circuit_from_production_chain():
    """Production chain: legacy INFRA -> get_plan -> single INFRA step (no planner)."""
    with patch("agent.routing.production_routing.ENABLE_INSTRUCTION_ROUTER", True):
        with patch("agent.routing.production_routing.route_instruction") as rm:
            rm.return_value = MagicMock(category="INFRA", confidence=0.85)
            with patch("agent.orchestrator.plan_resolver.plan") as mp:
                plan = get_plan("Update the Dockerfile")
    mp.assert_not_called()
    assert len(plan.get("steps", [])) == 1
    assert plan["steps"][0].get("action") == "INFRA"
    tel = get_plan_resolution_telemetry()
    assert tel.get("router_short_circuit_used") is True
    assert tel.get("resolver_consumption") == "short_infra"


def test_low_confidence_search_becomes_planner():
    """Production chain: CODE_SEARCH with low confidence -> AMBIGUOUS path -> planner."""
    from agent.routing.production_routing import route_production_instruction

    mock_plan = {"steps": [{"id": 1, "action": "SEARCH", "description": "x"}]}
    with patch("agent.routing.production_routing.ENABLE_INSTRUCTION_ROUTER", True):
        with patch("agent.routing.production_routing.route_instruction") as rm:
            rm.return_value = MagicMock(category="CODE_SEARCH", confidence=0.5)
            ri = route_production_instruction("Find the login function")
            assert ri.planner_handoff_reason == "confidence_below_threshold"
            assert ri.clarification_needed is False
            with patch("agent.orchestrator.plan_resolver.plan", return_value=mock_plan) as mp:
                get_plan("Find the login function")
    mp.assert_called_once()
    tel = get_plan_resolution_telemetry()
    assert tel.get("planner_used") is True
    assert tel.get("routed_intent_primary") == INTENT_AMBIGUOUS
    assert tel.get("routed_intent_planner_handoff_reason") == "confidence_below_threshold"
