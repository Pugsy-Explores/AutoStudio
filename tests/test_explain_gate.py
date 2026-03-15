"""Tests for context gate before EXPLAIN."""

import unittest

from agent.execution.explain_gate import ensure_context_before_explain
from agent.memory.state import AgentState


class TestExplainGate(unittest.TestCase):
    def test_explain_without_context_injects_search(self):
        """When ranked_context is empty, gate returns synthetic SEARCH step."""
        step = {"id": 1, "action": "EXPLAIN", "description": "Explain how dispatch works"}
        state = AgentState(
            instruction="Explain dispatch",
            current_plan={"steps": [step]},
            context={"ranked_context": []},
        )
        has_context, synthetic = ensure_context_before_explain(step, state)
        self.assertFalse(has_context)
        self.assertIsNotNone(synthetic)
        self.assertEqual(synthetic.get("action"), "SEARCH")
        self.assertEqual(synthetic.get("description"), "Explain how dispatch works")
        self.assertEqual(synthetic.get("id"), 1)

    def test_explain_with_context_skips_gate(self):
        """When ranked_context is present, gate returns no synthetic step."""
        step = {"id": 1, "action": "EXPLAIN", "description": "Explain"}
        state = AgentState(
            instruction="Explain",
            current_plan={"steps": [step]},
            context={
                "ranked_context": [
                    {"file": "agent/execution/step_dispatcher.py", "symbol": "dispatch", "snippet": "def dispatch"}
                ]
            },
        )
        has_context, synthetic = ensure_context_before_explain(step, state)
        self.assertTrue(has_context)
        self.assertIsNone(synthetic)

    def test_explain_with_none_context_skips_gate(self):
        """When ranked_context is None (missing), treat as empty."""
        step = {"id": 1, "action": "EXPLAIN", "description": "Explain"}
        state = AgentState(
            instruction="Explain",
            current_plan={"steps": [step]},
            context={},
        )
        has_context, synthetic = ensure_context_before_explain(step, state)
        self.assertFalse(has_context)
        self.assertIsNotNone(synthetic)
