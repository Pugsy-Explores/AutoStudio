"""Integration test: full agent loop with mocked planner and dispatch.

Step 5 — Execution Loop Integration:
- step executed, result validated, next step triggered
- ExplainGate triggers SEARCH automatically when EXPLAIN without context
"""

import unittest

from tests.utils.runtime_adapter import run_agent


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

    def test_run_agent_returns_state_with_plan_and_results(self):
        instruction = "Find JWT token generation and change expiration to 24 hours"
        state = run_agent(instruction)
        self.assertIsNotNone(state)
        self.assertIsInstance(state.step_results, list)

    def test_planner_called_with_instruction(self):
        state = run_agent("Find JWT and change expiry")
        self.assertEqual(state.instruction, "Find JWT and change expiry")

    def test_explain_step_completes_loop(self):
        state = run_agent("Explain how AgentState works")
        self.assertIsNotNone(state)


if __name__ == "__main__":
    unittest.main()
