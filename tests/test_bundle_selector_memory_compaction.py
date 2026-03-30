"""Memory compaction tests for bundle selector alpha path."""

from __future__ import annotations

import json
from unittest.mock import patch

from agent.memory.state import AgentState
from agent.retrieval.bundle_selector import run_bundle_selector


def _row(cid: str, **kw) -> dict:
    base = {"candidate_id": cid, "file": "src/a.py", "symbol": "foo", "snippet": "def foo", "candidate_kind": "symbol"}
    base.update(kw)
    return base


def test_selector_success_compacts_active_ranked_context_to_selected_rows_only():
    state = AgentState(
        instruction="how does a connect to b",
        current_plan={"plan_id": "p", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": [
                _row("rc_0001", file="src/a.py"),
                _row("rc_0002", file="src/b.py", implementation_body_present=True, relations=[{"kind": "call"}]),
                _row("rc_0003", file="tests/test_c.py"),
            ],
            "ranked_context": [{"file": "src/original.py", "snippet": "old"}],
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does a connect to b"}
    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps(
            {
                "keep_ids": ["rc_0002", "rc_0001"],
                "primary_ids": ["rc_0002"],
                "supporting_ids": ["rc_0001"],
                "reason": "impl + support",
            }
        )
        ok = run_bundle_selector(step, state)
    assert ok is True
    rc = state.context["ranked_context"]
    assert [r["candidate_id"] for r in rc] == ["rc_0002", "rc_0001"]
    assert state.context["final_answer_context_from_selected_rows_only"] is True


def test_full_pool_preserved_and_selected_dropped_ids_tracked():
    pool = [
        _row("rc_0001", file="src/a.py"),
        _row("rc_0002", file="src/b.py", implementation_body_present=True),
        _row("rc_0003", file="src/c.py"),
    ]
    state = AgentState(
        instruction="arch",
        current_plan={"plan_id": "p", "steps": []},
        context={"project_root": "/tmp", "retrieval_candidate_pool": list(pool), "ranked_context": []},
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "flow from a to c"}
    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps(
            {"keep_ids": ["rc_0002"], "primary_ids": ["rc_0002"], "supporting_ids": [], "reason": "best impl"}
        )
        ok = run_bundle_selector(step, state)
    assert ok is True
    assert state.context["retrieval_candidate_pool"] == pool
    assert [r["candidate_id"] for r in state.context["bundle_selector_selected_pool"]] == ["rc_0002"]
    assert set(state.context["bundle_selector_dropped_ids"]) == {"rc_0001", "rc_0003"}


def test_selector_off_or_failure_leaves_memory_unchanged():
    original_ranked = [{"file": "src/original.py", "snippet": "old"}]
    state = AgentState(
        instruction="arch",
        current_plan={"plan_id": "p", "steps": []},
        context={
            "project_root": "/tmp",
            "retrieval_candidate_pool": [_row("rc_0001"), _row("rc_0002")],
            "ranked_context": list(original_ranked),
        },
    )
    step = {"action": "EXPLAIN", "artifact_mode": "code", "description": "how does x connect"}
    with patch("agent.models.model_client.call_small_model") as mock_call:
        mock_call.return_value = json.dumps({"keep_ids": ["rc_9999"], "primary_ids": [], "supporting_ids": [], "reason": ""})
        ok = run_bundle_selector(step, state)
    assert ok is False
    assert state.context["ranked_context"] == original_ranked
    assert state.context.get("bundle_selector_selected_pool") in (None, [])

