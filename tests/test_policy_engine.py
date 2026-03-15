"""Unit tests for ExecutionPolicyEngine: retry and mutation for SEARCH."""

import unittest
from unittest.mock import MagicMock

from agent.execution.mutation_strategies import generate_query_variants
from agent.execution.policy_engine import ExecutionPolicyEngine, POLICIES
from agent.memory.state import AgentState


class TestGenerateQueryVariants(unittest.TestCase):
    """Test Phase 1 identifier variants."""

    def test_router_eval2_produces_expected_variants(self):
        variants = generate_query_variants("router eval2")
        self.assertIn("router_eval_v2", variants)
        self.assertIn("router_eval2", variants)
        self.assertIn("router_eval", variants)
        self.assertIn("router", variants)

    def test_empty_string_returns_empty_list(self):
        self.assertEqual(generate_query_variants(""), [])
        self.assertEqual(generate_query_variants("   "), [])


def _identity_rewrite(description: str, user_request: str, attempt_history: list) -> str:
    """Rewriter that passes through description (new context-aware signature)."""
    return (description or "").strip() or description


class TestExecutionPolicyEngineSearch(unittest.TestCase):
    """Policy engine SEARCH: retries with context-aware rewrite until success or exhausted."""

    def test_search_retries_then_succeeds_on_third_query(self):
        # Mock: first two queries empty, third returns results
        call_count = 0

        def mock_search(query: str, state=None):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return {"results": [], "query": query}
            return {"results": [{"file": "x.py", "snippet": "y"}], "query": query}

        # Rewriter returns different query per attempt so we get 3 distinct searches
        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> str:
            n = len(attempt_history)
            if n == 0:
                return "router_eval2"
            if n == 1:
                return "router_eval_v2"
            return "router_eval"

        engine = ExecutionPolicyEngine(
            search_fn=mock_search,
            edit_fn=MagicMock(return_value={"success": True, "output": {}}),
            infra_fn=MagicMock(return_value={"success": True, "output": {"returncode": 0}}),
            rewrite_query_fn=mock_rewrite,
            max_total_attempts=10,
        )
        step = {"id": 1, "action": "SEARCH", "description": "router eval2"}
        state = AgentState(instruction="test", current_plan={"steps": [step]})

        result = engine.execute_with_policy(step, state)

        self.assertTrue(result["success"], result)
        self.assertIn("output", result)
        out = result["output"]
        self.assertIn("results", out)
        self.assertEqual(len(out["results"]), 1)
        self.assertIn("attempt_history", out)
        history = out["attempt_history"]
        self.assertGreaterEqual(len(history), 3, "should have at least 3 attempts")
        self.assertTrue(any(h.get("result_count", 0) > 0 for h in history), history)
        self.assertEqual(call_count, len(history))

    def test_search_rewriter_receives_planner_step_user_request_and_attempt_history(self):
        """Rewriter is called with (planner step, user request, previous attempts); attempt_history grows each attempt."""
        rewrite_calls = []

        def mock_rewrite(description: str, user_request: str, attempt_history: list, state=None) -> str:
            rewrite_calls.append({
                "description": description,
                "user_request": user_request,
                "attempt_history_len": len(attempt_history),
            })
            # Return a new query each time so we get multiple attempts
            n = len(rewrite_calls)
            return f"StepExecutor_v{n}"

        search_count = 0

        def mock_search(query: str, state=None):
            nonlocal search_count
            search_count += 1
            # Succeed only on 3rd attempt
            if search_count >= 3:
                return {"results": [{"file": "executor.py", "snippet": "class StepExecutor"}], "query": query}
            return {"results": [], "query": query}

        engine = ExecutionPolicyEngine(
            search_fn=mock_search,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=mock_rewrite,
            max_total_attempts=5,
        )
        step = {"id": 1, "action": "SEARCH", "description": "Find where the Step Executor class is"}
        state = AgentState(
            instruction="Find where the Step Executor class is",
            current_plan={"steps": [step]},
        )

        result = engine.execute_with_policy(step, state)

        self.assertTrue(result["success"], result)
        self.assertEqual(len(rewrite_calls), 3, "rewriter should be called once per attempt until success")
        self.assertEqual(rewrite_calls[0]["description"], "Find where the Step Executor class is")
        self.assertEqual(rewrite_calls[0]["user_request"], "Find where the Step Executor class is")
        self.assertEqual(rewrite_calls[0]["attempt_history_len"], 0)
        self.assertEqual(rewrite_calls[1]["attempt_history_len"], 1, "second call sees first attempt")
        self.assertEqual(rewrite_calls[2]["attempt_history_len"], 2, "third call sees two previous attempts")

    def test_search_returns_failure_with_only_attempt_history_when_exhausted(self):
        def mock_search_empty(_query: str, state=None):
            return {"results": [], "query": _query}

        engine = ExecutionPolicyEngine(
            search_fn=mock_search_empty,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=_identity_rewrite,
            max_total_attempts=3,
        )
        step = {"id": 1, "action": "SEARCH", "description": "nonexistent symbol xyz"}
        state = AgentState(instruction="test", current_plan={"steps": [step]})

        result = engine.execute_with_policy(step, state)

        self.assertFalse(result["success"])
        self.assertIn("error", result)
        self.assertNotIn("results", result["output"])
        self.assertIn("attempt_history", result["output"])
        self.assertGreater(len(result["output"]["attempt_history"]), 0)


class TestPolicies(unittest.TestCase):
    """Policy table has expected structure."""

    def test_search_policy_has_retry_on_and_max_attempts(self):
        self.assertIn("SEARCH", POLICIES)
        p = POLICIES["SEARCH"]
        self.assertEqual(p["retry_on"], ["empty_results"])
        self.assertGreaterEqual(p["max_attempts"], 1)

    def test_edit_and_infra_policies_defined(self):
        self.assertIn("EDIT", POLICIES)
        self.assertIn("INFRA", POLICIES)
        self.assertIn("retry_on", POLICIES["EDIT"])
        self.assertIn("retry_on", POLICIES["INFRA"])


if __name__ == "__main__":
    unittest.main()
