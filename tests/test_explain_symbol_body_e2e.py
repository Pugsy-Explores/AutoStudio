"""End-to-end: EXPLAIN succeeds when ranked_context carries symbol-body grounding metadata."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from agent.execution.step_dispatcher import dispatch
from agent.memory.state import AgentState
from agent.retrieval.result_contract import RETRIEVAL_RESULT_TYPE_SYMBOL_BODY


def test_explain_succeeds_with_symbol_body_context():
    """Production failure mode: typed symbol body in context -> model runs -> no empty-context string."""
    st = AgentState(
        instruction="task",
        current_plan={"plan_id": "p", "steps": []},
        context={
            "project_root": "/tmp",
            "dominant_artifact_mode": "code",
            "lane_violations": [],
            "ranked_context": [
                {
                    "file": "agent/example.py",
                    "symbol": "foo",
                    "snippet": "def foo():\n    return 42\n",
                    "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
                    "implementation_body_present": True,
                    "candidate_kind": "symbol",
                },
            ],
        },
    )
    with patch("agent.execution.step_dispatcher.call_reasoning_model", return_value="y" * 50) as crm:
        with patch("agent.execution.step_dispatcher.get_model_for_task") as gmt:
            gmt.return_value = MagicMock(value="REASONING")
            out = dispatch({"id": 1, "action": "EXPLAIN", "description": "How does foo work?"}, st)
    assert out.get("success") is True
    assert "I cannot answer without relevant code context" not in (out.get("output") or "")
    assert crm.called


def test_impl_body_survives_dedupe_and_prune_distinct_lines():
    """Same snippet text at different lines must not be merged (identity key includes line)."""
    from agent.retrieval.context_pruner import prune_context
    from agent.retrieval.reranker.deduplicator import deduplicate_candidates

    rows = [
        {
            "file": "a.py",
            "symbol": "f",
            "snippet": "x = 1",
            "line": 10,
            "implementation_body_present": True,
            "candidate_kind": "symbol",
        },
        {
            "file": "a.py",
            "symbol": "f",
            "snippet": "x = 1",
            "line": 20,
            "implementation_body_present": True,
            "candidate_kind": "symbol",
        },
    ]
    d = deduplicate_candidates(rows)
    assert len(d) == 2
    p = prune_context(d, max_snippets=10, max_chars=8000)
    assert len(p) == 2
    assert any(r.get("implementation_body_present") for r in p)


def test_policy_search_memory_carries_typed_fields():
    from agent.execution.policy_engine import _build_search_memory

    raw = {
        "results": [
            {
                "file": "a.py",
                "snippet": "def a(): pass",
                "candidate_kind": "symbol",
                "retrieval_result_type": RETRIEVAL_RESULT_TYPE_SYMBOL_BODY,
                "implementation_body_present": True,
                "line": 3,
            }
        ]
    }
    mem = _build_search_memory("q", raw)
    r0 = mem["results"][0]
    assert r0.get("candidate_kind") == "symbol"
    assert r0.get("retrieval_result_type") == RETRIEVAL_RESULT_TYPE_SYMBOL_BODY
    assert r0.get("implementation_body_present") is True
    assert r0.get("line") == 3
