"""Unit tests for EngineDecisionMapper.decide_control (exploration_refactor_plan §1)."""

from agent_v2.exploration.decision_mapper import EngineDecisionMapper
from agent_v2.schemas.exploration import SelectorBatchResult, UnderstandingResult


def _u(**kwargs):
    base = dict(
        relevance="medium",
        confidence=0.5,
        sufficient=False,
        is_sufficient=False,
        evidence_sufficiency="partial",
        knowledge_gaps=[],
        summary="",
    )
    base.update(kwargs)
    return UnderstandingResult(**base)


def test_stop_when_effective_sufficient():
    u = _u(is_sufficient=True)
    sel = SelectorBatchResult(selected_candidates=[], coverage_signal="empty", selection_confidence="low")
    c = EngineDecisionMapper.decide_control(
        u,
        sel,
        "none",
        expanded_once=False,
        refine_count=0,
        refine_limit=2,
    )
    assert c.action == "stop"
    assert c.reason == "analyzer_sufficient"


def test_expand_when_relationship_hint_and_not_expanded():
    u = _u()
    sel = SelectorBatchResult(coverage_signal="good", selection_confidence="medium")
    c = EngineDecisionMapper.decide_control(
        u,
        sel,
        "callers",
        expanded_once=False,
        refine_count=0,
        refine_limit=2,
    )
    assert c.action == "expand"
    assert "relationship_hint" in c.reason


def test_refine_when_weak_coverage_and_budget():
    u = _u()
    sel = SelectorBatchResult(coverage_signal="weak", selection_confidence="low")
    c = EngineDecisionMapper.decide_control(
        u,
        sel,
        "none",
        expanded_once=True,
        refine_count=0,
        refine_limit=2,
    )
    assert c.action == "refine"
    assert "coverage_signal" in c.reason


def test_mapper_default_stop_not_sufficient_good_coverage():
    """§1.1 intentional STOP."""
    u = _u(is_sufficient=False)
    sel = SelectorBatchResult(coverage_signal="good", selection_confidence="high")
    c = EngineDecisionMapper.decide_control(
        u,
        sel,
        "none",
        expanded_once=True,
        refine_count=2,
        refine_limit=2,
    )
    assert c.action == "stop"
    assert c.reason == "mapper_default_stop"
