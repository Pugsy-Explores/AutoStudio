"""Integration test: full agent loop with mocked planner."""

import unittest
from unittest.mock import patch

from agent.orchestrator.agent_loop import run_agent


FAKE_PLAN = {
    "steps": [
        {"id": 1, "action": "SEARCH", "description": "JWT token generation", "reason": "Locate code"},
        {"id": 2, "action": "EDIT", "description": "Change expiration to 24 hours", "reason": "User request"},
    ]
}


class TestAgentLoop(unittest.TestCase):
    """Test that run_agent runs planner -> executor -> results."""

    @patch("agent.orchestrator.agent_loop.plan")
    def test_run_agent_returns_state_with_plan_and_results(self, mock_plan):
        mock_plan.return_value = FAKE_PLAN
        instruction = "Find JWT token generation and change expiration to 24 hours"
        state = run_agent(instruction)
        self.assertIn("steps", state.current_plan)
        self.assertGreaterEqual(len(state.current_plan["steps"]), 1)
        self.assertGreaterEqual(len(state.step_results), 1)
        self.assertEqual(len(state.step_results), len(state.completed_steps))
        # First step should be SEARCH, second EDIT (if both ran)
        if len(state.step_results) >= 1:
            self.assertEqual(state.step_results[0].action, "SEARCH")
        if len(state.step_results) >= 2:
            self.assertEqual(state.step_results[1].action, "EDIT")

    @patch("agent.orchestrator.agent_loop.plan")
    def test_planner_called_with_instruction(self, mock_plan):
        mock_plan.return_value = FAKE_PLAN
        run_agent("Find JWT and change expiry")
        mock_plan.assert_called_once_with("Find JWT and change expiry")


if __name__ == "__main__":
    unittest.main()
