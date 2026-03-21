"""Focused unit tests for first EXPLAIN retrieval query shaping (replan_count 2->1).

When code-lane EXPLAIN has no prior context, the retrieval query is shaped from
compound instructions to extract a focused code-explanation target.
"""

import unittest
from unittest.mock import patch

from agent.execution.step_dispatcher import _shape_query_for_explain_retrieval
from agent.execution.step_dispatcher import dispatch
from agent.memory.state import AgentState


class TestShapeQueryForExplainRetrieval(unittest.TestCase):
    """Tests for _shape_query_for_explain_retrieval."""

    def test_compound_explain_instruction_shaped(self):
        """Compound code-explanation instruction gets shaped to focused target."""
        result = _shape_query_for_explain_retrieval("show architecture docs and explain replanner flow")
        self.assertEqual(result, "replanner")

    def test_explain_target_flow_shaped(self):
        """'explain X flow' extracts X."""
        result = _shape_query_for_explain_retrieval("explain replanner flow")
        self.assertEqual(result, "replanner")

    def test_explain_target_only_shaped(self):
        """'explain X' extracts X."""
        result = _shape_query_for_explain_retrieval("explain the plan_resolver")
        self.assertEqual(result, "plan_resolver")

    def test_simple_code_search_not_shaped(self):
        """Simple CODE_SEARCH instruction does not get incorrectly shaped."""
        result = _shape_query_for_explain_retrieval("where is StepExecutor implemented")
        self.assertIsNone(result)

    def test_symbol_lookup_not_shaped(self):
        """Symbol lookup / non-explain instruction unchanged."""
        result = _shape_query_for_explain_retrieval("find retrieve_graph")
        self.assertIsNone(result)

    def test_fallback_when_no_target_extractable(self):
        """Fallback to original when no target is extractable."""
        result = _shape_query_for_explain_retrieval("list all files in the project")
        self.assertIsNone(result)

    def test_explain_how_x_shaped(self):
        """'explain how X ...' extracts X (Scenario 5 style)."""
        result = _shape_query_for_explain_retrieval("explain how replanner preserves dominant lane")
        self.assertEqual(result, "replanner")

    def test_how_x_works_shaped(self):
        """'how X works' extracts X."""
        result = _shape_query_for_explain_retrieval("how does the replanner work")
        self.assertIsNone(result)  # "how X work" not "how X works"
        result = _shape_query_for_explain_retrieval("how replanner works")
        self.assertEqual(result, "replanner")

    def test_empty_or_invalid_returns_none(self):
        """Empty or invalid input returns None."""
        self.assertIsNone(_shape_query_for_explain_retrieval(""))
        self.assertIsNone(_shape_query_for_explain_retrieval("   "))
        self.assertIsNone(_shape_query_for_explain_retrieval(None))


def test_explain_inject_uses_query_when_present():
    """EXPLAIN injected search uses step.query when present, does not re-shape description."""
    captured = {}

    def capture_search(q, state):
        captured["query"] = q
        return {"results": [{"file": "x.py", "snippet": "x"}], "query": q}

    state = AgentState(
        instruction="explain the plan_resolver",
        current_plan={"plan_id": "p", "steps": []},
        context={
            "project_root": "/tmp",
            "dominant_artifact_mode": "code",
            "lane_violations": [],
            "ranked_context": [],
        },
    )
    step = {
        "id": 1,
        "action": "EXPLAIN",
        "description": "explain the plan_resolver",
        "query": "explicit_query",
    }
    with patch("agent.execution.step_dispatcher._search_fn", side_effect=capture_search):
        dispatch(step, state)
    assert captured.get("query") == "explicit_query"


def test_explain_inject_shapes_when_query_absent():
    """EXPLAIN injected search applies shaping to description when query is absent."""
    captured = {}

    def capture_search(q, state):
        captured["query"] = q
        return {"results": [{"file": "x.py", "snippet": "x"}], "query": q}

    state = AgentState(
        instruction="explain replanner flow",
        current_plan={"plan_id": "p", "steps": []},
        context={
            "project_root": "/tmp",
            "dominant_artifact_mode": "code",
            "lane_violations": [],
            "ranked_context": [],
        },
    )
    step = {
        "id": 1,
        "action": "EXPLAIN",
        "description": "explain replanner flow",
    }
    with patch("agent.execution.step_dispatcher._search_fn", side_effect=capture_search):
        dispatch(step, state)
    assert captured.get("query") == "replanner"
