"""Unit tests for ExplainGate: ensure_context_before_explain.

Step 5 — ExplainGate triggers SEARCH automatically when EXPLAIN without context.
"""

import unittest

from agent.execution.explain_gate import ensure_context_before_explain
from agent.memory.state import AgentState


class TestExplainGate(unittest.TestCase):
    """ensure_context_before_explain returns (has_context, synthetic_search_step)."""

    def test_has_context_when_ranked_context_non_empty(self):
        state = AgentState(
            instruction="Explain X",
            current_plan={"steps": []},
            context={"ranked_context": [{"file": "a.py", "symbol": "X", "snippet": "class X: pass"}]},
        )
        step = {"id": 1, "action": "EXPLAIN", "description": "Explain X"}
        has_context, synthetic = ensure_context_before_explain(step, state)
        self.assertTrue(has_context)
        self.assertIsNone(synthetic)

    def test_no_context_returns_synthetic_search_step(self):
        state = AgentState(
            instruction="Explain X",
            current_plan={"steps": []},
            context={"ranked_context": []},
        )
        step = {"id": 1, "action": "EXPLAIN", "description": "Explain StepExecutor"}
        has_context, synthetic = ensure_context_before_explain(step, state)
        self.assertFalse(has_context)
        self.assertIsNotNone(synthetic)
        self.assertEqual(synthetic.get("action"), "SEARCH")
        self.assertEqual(synthetic.get("description"), "Explain StepExecutor")
        self.assertEqual(synthetic.get("id"), 1)

    def test_empty_ranked_context_missing_key_treated_as_empty(self):
        state = AgentState(
            instruction="Explain X",
            current_plan={"steps": []},
            context={},
        )
        step = {"id": 2, "action": "EXPLAIN", "description": "Explain AgentState"}
        has_context, synthetic = ensure_context_before_explain(step, state)
        self.assertFalse(has_context)
        self.assertIsNotNone(synthetic)
        self.assertEqual(synthetic.get("action"), "SEARCH")


if __name__ == "__main__":
    unittest.main()
