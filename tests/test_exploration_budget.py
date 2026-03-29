"""Advisory exploration budget derivation from QueryIntent."""

from agent_v2.schemas.exploration import (
    EXPLORATION_BUDGET_GLOBAL_CAP,
    QueryIntent,
    effective_exploration_budget,
)


def test_effective_budget_navigation() -> None:
    assert effective_exploration_budget(QueryIntent(intent_type="navigation")) == 1


def test_effective_budget_debugging_respects_cap() -> None:
    assert effective_exploration_budget(QueryIntent(intent_type="debugging")) == min(
        3, EXPLORATION_BUDGET_GLOBAL_CAP
    )


def test_fallback_focus_relationships() -> None:
    assert effective_exploration_budget(QueryIntent(focus="relationships")) == 2


def test_fallback_relationship_hint() -> None:
    assert effective_exploration_budget(QueryIntent(relationship_hint="callers")) == 2


def test_fallback_none_query_intent() -> None:
    assert effective_exploration_budget(None) == 2


def test_intent_type_wins_over_focus() -> None:
    assert (
        effective_exploration_budget(
            QueryIntent(intent_type="navigation", focus="relationships")
        )
        == 1
    )
