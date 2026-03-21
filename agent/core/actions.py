"""Canonical action vocabulary. Single source of truth for planner, guardrail, and execution."""

from enum import Enum


class Action(str, Enum):
    """All supported step actions. Used by planner output, guardrail, and execution layer."""

    SEARCH = "SEARCH"
    SEARCH_CANDIDATES = "SEARCH_CANDIDATES"
    BUILD_CONTEXT = "BUILD_CONTEXT"
    READ = "READ"
    EDIT = "EDIT"
    EXPLAIN = "EXPLAIN"
    INFRA = "INFRA"
    RUN_TEST = "RUN_TEST"
    WRITE_ARTIFACT = "WRITE_ARTIFACT"


def all_action_values() -> list[str]:
    """Return all action string values for validation and policy."""
    return [a.value for a in Action]


def valid_action_values() -> set[str]:
    """Return set of valid action strings for membership checks."""
    return {a.value for a in Action}


def normalize_action_for_execution(action: str, *, artifact_mode: str = "code") -> str:
    """
    Map planner-level actions to canonical execution semantics.

    Multiple actions can exist in plans, but execution routing uses a canonical form.
    SEARCH_CANDIDATES and SEARCH both mean "retrieve code/files" — normalize to SEARCH
    in code mode so all layers route consistently. Docs mode keeps SEARCH_CANDIDATES
    (SEARCH with docs is deferred; docs flow uses SEARCH_CANDIDATES + BUILD_CONTEXT).
    """
    if not action:
        return Action.EXPLAIN.value
    a = action.strip().upper()
    if a == Action.SEARCH_CANDIDATES.value and artifact_mode == "code":
        return Action.SEARCH.value
    return a if a in valid_action_values() else Action.EXPLAIN.value
