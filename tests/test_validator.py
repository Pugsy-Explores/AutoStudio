"""Tests for step validator: loop-aware rules, structured feedback."""

import unittest

from agent.memory.state import AgentState
from agent.memory.step_result import StepResult
from agent.orchestrator.validator import (
    validate_step,
    _validate_step_rules,
    _search_results_only_tests,
    _instruction_suggests_implementation,
    _is_test_file,
    _is_valid_explain,
)


class TestValidatorHelpers(unittest.TestCase):
    def test_is_test_file(self):
        self.assertTrue(_is_test_file("tests/test_foo.py"))
        self.assertTrue(_is_test_file("a/b/tests/bar.py"))
        self.assertTrue(_is_test_file("test_something.py"))
        self.assertFalse(_is_test_file("agent/execution/step_dispatcher.py"))
        self.assertFalse(_is_test_file(""))

    def test_search_results_only_tests(self):
        self.assertTrue(_search_results_only_tests([{"file": "tests/a.py", "snippet": "x"}]))
        self.assertTrue(
            _search_results_only_tests(
                [{"file": "tests/a.py"}, {"file": "test_b.py"}]
            )
        )
        self.assertFalse(
            _search_results_only_tests([{"file": "agent/dispatcher.py", "snippet": "def x"}]),
        )
        self.assertFalse(
            _search_results_only_tests(
                [{"file": "tests/a.py"}, {"file": "agent/dispatcher.py"}]
            ),
        )

    def test_instruction_suggests_implementation(self):
        self.assertTrue(_instruction_suggests_implementation("Explain how the dispatcher routes"))
        self.assertTrue(_instruction_suggests_implementation("how does step_dispatcher work"))
        self.assertFalse(_instruction_suggests_implementation("run the tests"))


class TestValidateStepRules(unittest.TestCase):
    def test_search_empty_results_invalid(self):
        step = {"id": 1, "action": "SEARCH", "description": "find x"}
        result = StepResult(
            step_id=1, action="SEARCH", success=True, output={"results": []}, latency_seconds=0.0
        )
        valid, feedback = _validate_step_rules(step, result)
        self.assertFalse(valid)
        self.assertIn("empty", feedback.lower())

    def test_search_tests_only_next_explain_invalid(self):
        step = {"id": 1, "action": "SEARCH", "description": "find dispatcher"}
        result = StepResult(
            step_id=1,
            action="SEARCH",
            success=True,
            output={
                "results": [
                    {"file": "tests/test_dispatcher.py", "snippet": "def test_dispatch"},
                ]
            },
            latency_seconds=0.0,
        )
        plan = {
            "steps": [
                step,
                {"id": 2, "action": "EXPLAIN", "description": "Explain how dispatcher routes"},
            ]
        }
        state = AgentState(
            instruction="Explain how the dispatcher routes SEARCH steps",
            current_plan=plan,
        )
        state.record(step, result)
        valid, feedback = _validate_step_rules(step, result, state)
        self.assertFalse(valid)
        self.assertIn("test files", feedback)
        self.assertIn("implementation", feedback)

    def test_search_tests_only_next_edit_valid(self):
        """When next step is EDIT, tests-only is acceptable (rules don't flag it)."""
        step = {"id": 1, "action": "SEARCH", "description": "find tests"}
        result = StepResult(
            step_id=1,
            action="SEARCH",
            success=True,
            output={
                "results": [{"file": "tests/test_foo.py", "snippet": "def test_x"}],
            },
            latency_seconds=0.0,
        )
        plan = {"steps": [step, {"id": 2, "action": "EDIT", "description": "fix test"}]}
        state = AgentState(instruction="fix the test", current_plan=plan)
        state.record(step, result)
        valid, feedback = _validate_step_rules(step, result, state)
        self.assertTrue(valid)

    def test_explain_short_output_invalid(self):
        """Short output (< 40 chars) fails validation."""
        step = {"id": 1, "action": "EXPLAIN", "description": "explain"}
        result = StepResult(
            step_id=1,
            action="EXPLAIN",
            success=True,
            output="No context",
            latency_seconds=0.0,
        )
        self.assertFalse(_is_valid_explain(result))
        valid, feedback = _validate_step_rules(step, result)
        self.assertFalse(valid)
        self.assertIn("search", feedback.lower())

    def test_explain_long_output_with_code_refs_valid(self):
        """Long output with code references passes validation."""
        step = {"id": 1, "action": "EXPLAIN", "description": "explain"}
        result = StepResult(
            step_id=1,
            action="EXPLAIN",
            success=True,
            output="The dispatch function in agent/execution/step_dispatcher.py routes steps to tools via the policy engine.",
            latency_seconds=0.0,
        )
        self.assertTrue(_is_valid_explain(result))
        valid, feedback = _validate_step_rules(step, result)
        self.assertTrue(valid)

    def test_explain_empty_context_output_invalid(self):
        """EXPLAIN output containing empty-context fallback triggers replanner."""
        step = {"id": 1, "action": "EXPLAIN", "description": "explain StepExecutor"}
        result = StepResult(
            step_id=1,
            action="EXPLAIN",
            success=True,
            output="I cannot answer without relevant code context. Please run a SEARCH step first to locate the relevant code.",
            latency_seconds=0.0,
        )
        valid, feedback = _validate_step_rules(step, result)
        self.assertFalse(valid)
        self.assertIn("empty context", feedback.lower())
        self.assertIn("SEARCH", feedback)

    def test_validate_step_returns_tuple(self):
        step = {"id": 1, "action": "EDIT", "description": "edit"}
        result = StepResult(
            step_id=1, action="EDIT", success=True, output={}, latency_seconds=0.0
        )
        valid, feedback = validate_step(step, result)
        self.assertTrue(valid)
        self.assertEqual(feedback, "")
