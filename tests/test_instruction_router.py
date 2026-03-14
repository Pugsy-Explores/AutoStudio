"""Tests for agent/routing/instruction_router."""

from unittest.mock import patch

import pytest

from agent.routing.instruction_router import (
    ROUTER_CATEGORIES,
    RouterDecision,
    route_instruction,
)


def test_route_instruction_returns_valid_structure():
    """route_instruction returns RouterDecision with category and confidence."""
    with patch("agent.routing.instruction_router.call_small_model") as mock:
        mock.return_value = '{"category": "CODE_SEARCH", "confidence": 0.92}'
        decision = route_instruction("Find where password hashing is implemented")
    assert isinstance(decision, RouterDecision)
    assert decision.category in ROUTER_CATEGORIES
    assert 0.0 <= decision.confidence <= 1.0


def test_route_instruction_search_query():
    """Search queries are classified as CODE_SEARCH."""
    with patch("agent.routing.instruction_router.call_small_model") as mock:
        mock.return_value = '{"category": "CODE_SEARCH", "confidence": 0.95}'
        decision = route_instruction("Locate where JWT tokens are generated")
    assert decision.category == "CODE_SEARCH"


def test_route_instruction_edit_query():
    """Edit queries are classified as CODE_EDIT."""
    with patch("agent.routing.instruction_router.call_small_model") as mock:
        mock.return_value = '{"category": "CODE_EDIT", "confidence": 0.88}'
        decision = route_instruction("Add bcrypt password hashing to the auth module")
    assert decision.category == "CODE_EDIT"


def test_route_instruction_explain_query():
    """Explain queries are classified as CODE_EXPLAIN."""
    with patch("agent.routing.instruction_router.call_small_model") as mock:
        mock.return_value = '{"category": "CODE_EXPLAIN", "confidence": 0.9}'
        decision = route_instruction("What does the auth module export?")
    assert decision.category == "CODE_EXPLAIN"


def test_route_instruction_infra_query():
    """Infra queries are classified as INFRA."""
    with patch("agent.routing.instruction_router.call_small_model") as mock:
        mock.return_value = '{"category": "INFRA", "confidence": 0.93}'
        decision = route_instruction("Create a Dockerfile for the FastAPI backend")
    assert decision.category == "INFRA"


def test_route_instruction_handles_markdown_fence():
    """JSON inside markdown code fence is extracted."""
    with patch("agent.routing.instruction_router.call_small_model") as mock:
        mock.return_value = '```json\n{"category": "CODE_SEARCH", "confidence": 0.8}\n```'
        decision = route_instruction("Find foo")
    assert decision.category == "CODE_SEARCH"
    assert decision.confidence == 0.8


def test_route_instruction_fallback_on_model_error():
    """On model failure, defaults to GENERAL with 0 confidence."""
    with patch("agent.routing.instruction_router.call_small_model") as mock:
        mock.side_effect = RuntimeError("Connection refused")
        decision = route_instruction("Some query")
    assert decision.category == "GENERAL"
    assert decision.confidence == 0.0


def test_route_instruction_fallback_on_invalid_json():
    """On invalid JSON, defaults to GENERAL."""
    with patch("agent.routing.instruction_router.call_small_model") as mock:
        mock.return_value = "not valid json at all"
        decision = route_instruction("Some query")
    assert decision.category == "GENERAL"


def test_route_instruction_normalizes_unknown_category():
    """Unknown category from model is normalized to GENERAL."""
    with patch("agent.routing.instruction_router.call_small_model") as mock:
        mock.return_value = '{"category": "UNKNOWN_CAT", "confidence": 0.5}'
        decision = route_instruction("Some query")
    assert decision.category == "GENERAL"


def test_route_instruction_uses_registry_when_router_type_set():
    """When ROUTER_TYPE is set, uses router from registry."""
    mock_route = lambda _: "SEARCH"
    with patch("agent.routing.instruction_router.ROUTER_TYPE", "baseline"):
        with patch.dict("agent.routing.router_registry._REGISTRY", {"baseline": mock_route}, clear=False):
            decision = route_instruction("Find the login handler")
    assert decision.category == "CODE_SEARCH"
