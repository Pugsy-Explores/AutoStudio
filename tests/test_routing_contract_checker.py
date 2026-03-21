"""Unit tests for routing_contract_checker (synthetic telemetry)."""

from __future__ import annotations

import pytest

from tests.agent_eval.routing_contract_checker import (
    check_routing_contract_task,
    check_strict_and_anti,
)


def _tele(**kwargs: object) -> dict:
    base = {
        "routed_intent_primary": "SEARCH",
        "routed_intent_planner_handoff_reason": "",
        "routed_intent_matched_signals": [],
        "routed_intent_suggested_plan_shape": None,
        "resolver_consumption": "short_search",
    }
    base.update(kwargs)
    return base


def test_strict_rejects_validate():
    t = _tele(routed_intent_primary="VALIDATE", resolver_consumption="planner")
    assert any("VALIDATE" in v for v in check_strict_and_anti(t))


def test_strict_rejects_compound_without_two_phase_shape():
    t = _tele(
        routed_intent_primary="COMPOUND",
        routed_intent_suggested_plan_shape="decompose_then_route",
        resolver_consumption="planner",
    )
    assert any("COMPOUND" in v for v in check_strict_and_anti(t))


def test_strict_allows_compound_with_two_phase_shape():
    t = _tele(
        routed_intent_primary="COMPOUND",
        routed_intent_suggested_plan_shape="two_phase_docs_code",
        resolver_consumption="planner",
    )
    assert check_strict_and_anti(t) == []


def test_strict_edit_requires_empty_handoff():
    t = _tele(routed_intent_primary="EDIT", routed_intent_planner_handoff_reason="unclear_intent", resolver_consumption="planner")
    assert check_strict_and_anti(t)


def test_anti_edit_unclear_handoff():
    t = _tele(routed_intent_primary="EDIT", routed_intent_planner_handoff_reason="unclear_intent", resolver_consumption="planner")
    v = check_strict_and_anti(t)
    assert any("anti" in x.lower() or "EDIT" in x for x in v)


def test_rc_edit_task_strict_requires_edit_primary():
    tele = _tele(routed_intent_primary="SEARCH")
    strict, _ = check_routing_contract_task("rc_edit", tele)
    assert any("rc_edit" in x and "EDIT" in x for x in strict)


def test_rc_edit_passes_with_edit():
    tele = _tele(routed_intent_primary="EDIT", resolver_consumption="planner")
    strict, soft = check_routing_contract_task("rc_edit", tele)
    assert strict == []
    assert soft == []


def test_soft_rc_doc_warns():
    tele = _tele(routed_intent_primary="EDIT")
    _, soft = check_routing_contract_task("rc_doc", tele)
    assert soft and "rc_doc" in soft[0]


@pytest.mark.parametrize(
    "primary,handoff,expect_issue",
    [
        ("AMBIGUOUS", "confidence_below_threshold", False),
        ("AMBIGUOUS", "unclear_intent", True),
    ],
)
def test_ambiguous_confidence_signal_handoff(primary, handoff, expect_issue):
    tele = _tele(
        routed_intent_primary=primary,
        routed_intent_planner_handoff_reason=handoff,
        routed_intent_matched_signals=["confidence_below_threshold"],
        resolver_consumption="planner",
    )
    v = check_strict_and_anti(tele)
    assert bool(v) == expect_issue
