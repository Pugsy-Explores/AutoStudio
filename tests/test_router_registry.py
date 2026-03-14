"""Tests for agent/routing/router_registry."""

from unittest.mock import patch

import pytest

from agent.routing.router_registry import (
    get_router,
    get_router_raw,
    list_routers,
)


def test_list_routers_returns_names():
    """list_routers returns available router names."""
    names = list_routers()
    assert isinstance(names, list)
    assert "baseline" in names
    assert "final" in names


def test_get_router_returns_callable():
    """get_router returns a callable for known router names."""
    fn = get_router("baseline")
    assert fn is not None
    assert callable(fn)


def test_get_router_returns_none_for_unknown():
    """get_router returns None for unknown router name."""
    assert get_router("unknown_router") is None
    assert get_router("") is None


def test_get_router_returns_router_decision():
    """get_router-wrapped router returns RouterDecision with valid category."""
    from agent.routing.instruction_router import ROUTER_CATEGORIES

    with patch("router_eval.routers.baseline_router.llama_chat") as mock:
        mock.return_value = "SEARCH"
        fn = get_router("baseline")
        assert fn is not None
        decision = fn("Find the login handler")
    assert decision.category in ROUTER_CATEGORIES
    assert decision.category == "CODE_SEARCH"
    assert 0.0 <= decision.confidence <= 1.0


def test_get_router_normalizes_dict_output():
    """Router returning dict with category and confidence is normalized."""
    mock_route = lambda _: {"category": "EDIT", "confidence": 0.9, "routers_agree": True}
    with patch.dict("agent.routing.router_registry._REGISTRY", {"final": mock_route}, clear=False):
        fn = get_router("final")
        assert fn is not None
        decision = fn("Add retry logic")
    assert decision.category == "CODE_EDIT"
    assert decision.confidence == 0.9


def test_get_router_raw_returns_raw_function():
    """get_router_raw returns the unwrapped route function."""
    fn = get_router_raw("baseline")
    assert fn is not None
    assert callable(fn)
    with patch("router_eval.routers.baseline_router.llama_chat") as mock:
        mock.return_value = "EDIT"
        result = fn("Add retry logic")
    assert result == "EDIT"
