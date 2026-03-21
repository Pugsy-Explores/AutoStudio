"""Unit tests for action semantic normalization (SEARCH_CANDIDATES → SEARCH)."""

import pytest

from agent.core.actions import Action, normalize_action_for_execution, valid_action_values


def test_normalize_search_candidates_to_search_in_code_mode():
    """SEARCH_CANDIDATES in code mode normalizes to SEARCH."""
    assert normalize_action_for_execution("SEARCH_CANDIDATES", artifact_mode="code") == Action.SEARCH.value


def test_normalize_preserves_search_candidates_in_docs_mode():
    """SEARCH_CANDIDATES in docs mode stays SEARCH_CANDIDATES (docs flow uses its own path)."""
    assert (
        normalize_action_for_execution("SEARCH_CANDIDATES", artifact_mode="docs")
        == Action.SEARCH_CANDIDATES.value
    )


def test_normalize_preserves_search():
    """SEARCH stays SEARCH."""
    assert normalize_action_for_execution("SEARCH", artifact_mode="code") == Action.SEARCH.value


def test_normalize_empty_returns_explain():
    """Empty action normalizes to EXPLAIN."""
    assert normalize_action_for_execution("", artifact_mode="code") == Action.EXPLAIN.value


def test_normalize_invalid_returns_explain():
    """Invalid action normalizes to EXPLAIN."""
    assert normalize_action_for_execution("FIND", artifact_mode="code") == Action.EXPLAIN.value


def test_normalize_preserves_other_valid_actions():
    """Valid actions other than SEARCH_CANDIDATES stay unchanged."""
    for action in [Action.EDIT.value, Action.EXPLAIN.value, Action.BUILD_CONTEXT.value]:
        assert normalize_action_for_execution(action, artifact_mode="code") == action
