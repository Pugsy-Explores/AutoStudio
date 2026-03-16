"""Integration test: full agent loop with mocked planner and dispatch.

Step 5 — Execution Loop Integration:
- step executed, result validated, next step triggered
- ExplainGate triggers SEARCH automatically when EXPLAIN without context
"""

import unittest
from unittest.mock import patch

from agent.orchestrator.agent_loop import run_agent


# Phase 4: plans include plan_id for plan-scoped step identity
FAKE_PLAN = {
    "plan_id": "fake_plan_001",
    "steps": [
        {"id": 1, "action": "SEARCH", "description": "JWT token generation", "reason": "Locate code"},
        {"id": 2, "action": "EDIT", "description": "Change expiration to 24 hours", "reason": "User request"},
    ],
}

FAKE_PLAN_EXPLAIN_ONLY = {
    "plan_id": "fake_plan_explain",
    "steps": [
        {"id": 1, "action": "EXPLAIN", "description": "Explain how AgentState works", "reason": "User request"},
    ],
}


def _mock_dispatch_search_edit(step, state):
    """Mock dispatch: return valid SEARCH/EDIT results without real retrieval."""
    action = (step.get("action") or "EXPLAIN").upper()
    if action == "SEARCH":
        return {
            "success": True,
            "output": {
                "results": [
                    {"file": "agent/auth/jwt.py", "symbol": "generate_token", "snippet": "def generate_token(): ...", "line": 10},
                ],
                "query": step.get("description", ""),
            },
            "error": None,
        }
    if action == "EDIT":
        return {"success": True, "output": {"files_modified": []}, "error": None}
    return {"success": False, "output": "", "error": "Unknown action"}


class TestAgentLoop(unittest.TestCase):
    """Test that run_agent runs planner -> executor -> results."""

    @patch("agent.execution.executor.dispatch", side_effect=_mock_dispatch_search_edit)
    @patch("agent.orchestrator.agent_loop.get_plan")
    def test_run_agent_returns_state_with_plan_and_results(self, mock_get_plan, mock_dispatch):
        mock_get_plan.return_value = FAKE_PLAN
        instruction = "Find JWT token generation and change expiration to 24 hours"
        state = run_agent(instruction)
        self.assertIn("steps", state.current_plan)
        self.assertGreaterEqual(len(state.current_plan["steps"]), 1)
        self.assertGreaterEqual(len(state.step_results), 1)
        self.assertEqual(len(state.step_results), len(state.completed_steps))
        if len(state.step_results) >= 1:
            self.assertEqual(state.step_results[0].action, "SEARCH")
        if len(state.step_results) >= 2:
            self.assertEqual(state.step_results[1].action, "EDIT")

    @patch("agent.execution.executor.dispatch", side_effect=_mock_dispatch_search_edit)
    @patch("agent.orchestrator.agent_loop.get_plan")
    def test_planner_called_with_instruction(self, mock_get_plan, mock_dispatch):
        mock_get_plan.return_value = FAKE_PLAN
        run_agent("Find JWT and change expiry")
        mock_get_plan.assert_called_once()
        call_args = mock_get_plan.call_args
        self.assertEqual(call_args[0][0], "Find JWT and change expiry")

    @patch("agent.execution.executor.dispatch")
    @patch("agent.orchestrator.agent_loop.get_plan")
    def test_explain_step_completes_loop(self, mock_get_plan, mock_dispatch):
        """EXPLAIN step executes and loop completes. ExplainGate logic tested in test_explain_gate."""
        mock_get_plan.return_value = FAKE_PLAN_EXPLAIN_ONLY
        mock_dispatch.return_value = {
            "success": True,
            "output": "AgentState holds instruction, plan, step_results, and context.",
            "error": None,
        }
        state = run_agent("Explain how AgentState works")
        self.assertGreaterEqual(len(state.step_results), 1)
        self.assertEqual(state.step_results[0].action, "EXPLAIN")
        self.assertTrue(state.step_results[0].success)


if __name__ == "__main__":
    unittest.main()
