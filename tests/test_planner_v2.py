"""Planner v2 (Phase 4): PlannerInput → PlanDocument + PlanValidator."""

import json
import unittest

from agent_v2.planner.planner_v2 import PlannerV2, _coerce_step_action_and_type
from agent_v2.schemas.exploration import (
    ExplorationResult,
    ExplorationResultMetadata,
    ExplorationSummary,
)
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.schemas.replan import (
    ReplanContext,
    ReplanFailureContext,
    ReplanFailureError,
)
from agent_v2.validation.plan_validator import PlanValidationError, PlanValidator


def _minimal_exploration(instruction: str = "Explain AgentLoop") -> ExplorationResult:
    return ExplorationResult(
        exploration_id="exp_test",
        instruction=instruction,
        items=[],
        summary=ExplorationSummary(
            overall="Exploration found agent loop in agent_v2/runtime.",
            key_findings=["AgentLoop coordinates dispatcher and action generator."],
            knowledge_gaps=[],
            knowledge_gaps_empty_reason="No gaps for this smoke test.",
        ),
        metadata=ExplorationResultMetadata(total_items=0, created_at="2026-01-01T00:00:00Z"),
    )


def _valid_plan_json() -> str:
    payload = {
        "understanding": "User wants an explanation of AgentLoop.",
        "sources": [
            {"type": "file", "ref": "agent_v2/runtime/agent_loop.py", "summary": "Main loop"}
        ],
        "risks": [
            {
                "risk": "File path may differ",
                "impact": "low",
                "mitigation": "Search if open_file fails",
            }
        ],
        "completion_criteria": ["Explanation delivered"],
        "steps": [
            {
                "step_id": "s1",
                "type": "analyze",
                "goal": "Read AgentLoop source",
                "action": "open_file",
                "dependencies": [],
                "inputs": {"path": "agent_v2/runtime/agent_loop.py"},
                "outputs": {},
            },
            {
                "step_id": "s2",
                "type": "finish",
                "goal": "Finish",
                "action": "finish",
                "dependencies": ["s1"],
                "inputs": {},
                "outputs": {},
            },
        ],
    }
    return json.dumps(payload)


class TestPlannerActionTypeCoercion(unittest.TestCase):
    """
    Lock the temporary _coerce_step_action_and_type() behavior in planner_v2.py.

    CRITICAL: These tests document RCA-driven shims. When the replanner module and
    structured planner output replace coercion, update or delete this class per
    agent_v2/planner/planner_v2.py module TODO(replanner).
    """

    def test_coerce_llm_swapped_analyze_action(self):
        tool, intent = _coerce_step_action_and_type("analyze", "analyze")
        self.assertEqual(tool, "open_file")
        self.assertEqual(intent, "analyze")

    def test_coerce_type_was_tool_name(self):
        tool, intent = _coerce_step_action_and_type("search", "open_file")
        self.assertEqual(tool, "search")
        self.assertIn(intent, ("explore", "analyze"))


class TestPlannerV2(unittest.TestCase):
    def test_plan_returns_valid_plan_document(self):
        calls = []

        def gen(prompt: str) -> str:
            calls.append(prompt)
            return _valid_plan_json()

        policy = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
        planner = PlannerV2(generate_fn=gen, policy=policy)
        doc = planner.plan("Explain AgentLoop", _minimal_exploration())

        self.assertTrue(doc.understanding)
        self.assertLessEqual(len(doc.steps), 8)
        actions = [s.action for s in doc.steps]
        self.assertIn("finish", actions)
        self.assertEqual(doc.steps[-1].type, "finish")
        self.assertEqual(doc.steps[-1].action, "finish")
        for s in doc.steps:
            self.assertEqual(s.execution.max_attempts, policy.max_retries_per_step)
        self.assertTrue(calls)
        self.assertIn("TASK:", calls[0])
        self.assertIn("EXPLORATION SUMMARY:", calls[0])
        self.assertIn("EXPLORATION SOURCES:", calls[0])
        self.assertIn("EXPLORATION ITEMS:", calls[0])

    def test_plan_from_replan_context(self):
        def gen(prompt: str) -> str:
            self.assertIn("FAILURE:", prompt)
            return _valid_plan_json()

        planner = PlannerV2(generate_fn=gen, policy=ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2))
        ctx = ReplanContext(
            failure_context=ReplanFailureContext(
                step_id="s1",
                error=ReplanFailureError(type="not_found", message="missing"),
                attempts=2,
                last_output_summary="not found",
            ),
            completed_steps=[],
        )
        doc = planner.plan("Fix thing", ctx, deep=False)
        self.assertEqual(doc.steps[-1].action, "finish")

    def test_invalid_json_raises(self):
        def gen(_: str) -> str:
            return "not json"

        planner = PlannerV2(generate_fn=gen)
        with self.assertRaises(PlanValidationError):
            planner.plan("x", _minimal_exploration())

    def test_plan_validator_rejects_cyclic_dependencies(self):
        from agent_v2.schemas.plan import PlanDocument, PlanMetadata, PlanRisk, PlanSource, PlanStep

        bad = PlanDocument(
            plan_id="p",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="a",
                    index=1,
                    type="explore",
                    goal="g",
                    action="search",
                    dependencies=["b"],
                ),
                PlanStep(
                    step_id="b",
                    index=2,
                    type="analyze",
                    goal="g2",
                    action="open_file",
                    dependencies=["a"],
                ),
                PlanStep(
                    step_id="c",
                    index=3,
                    type="finish",
                    goal="done",
                    action="finish",
                    dependencies=["b"],
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        with self.assertRaises(PlanValidationError):
            PlanValidator.validate_plan(bad)

    def test_plan_validator_rejects_dependency_on_later_step(self):
        from agent_v2.schemas.plan import PlanDocument, PlanMetadata, PlanRisk, PlanSource, PlanStep

        bad = PlanDocument(
            plan_id="p",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="a",
                    index=1,
                    type="analyze",
                    goal="g",
                    action="open_file",
                    dependencies=["b"],
                ),
                PlanStep(
                    step_id="b",
                    index=2,
                    type="finish",
                    goal="done",
                    action="finish",
                    dependencies=[],
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        with self.assertRaises(PlanValidationError):
            PlanValidator.validate_plan(bad)

    def test_plan_validator_rejects_missing_finish(self):
        from agent_v2.schemas.plan import PlanDocument, PlanMetadata, PlanRisk, PlanSource, PlanStep

        bad = PlanDocument(
            plan_id="p",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="a",
                    index=1,
                    type="analyze",
                    goal="g",
                    action="open_file",
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        with self.assertRaises(PlanValidationError):
            PlanValidator.validate_plan(bad)


class TestTrimPlanStepsPreservingFinish(unittest.TestCase):
    def test_finish_after_cap_is_spliced_in(self):
        from agent_v2.planner.planner_v2 import _trim_plan_steps_preserving_finish

        steps: list[dict] = []
        for i in range(1, 10):
            steps.append(
                {
                    "step_id": str(i),
                    "type": "analyze",
                    "goal": "g",
                    "action": "open_file",
                    "dependencies": [str(i - 1)] if i > 1 else [],
                    "inputs": {},
                    "outputs": {},
                }
            )
        steps.append(
            {
                "step_id": "10",
                "type": "finish",
                "goal": "done",
                "action": "finish",
                "dependencies": ["9"],
                "inputs": {},
                "outputs": {},
            }
        )
        out = _trim_plan_steps_preserving_finish(steps, 8)
        self.assertEqual(len(out), 8)
        self.assertEqual(out[-1].get("action"), "finish")
        self.assertEqual(out[-1].get("type"), "finish")


if __name__ == "__main__":
    unittest.main()
