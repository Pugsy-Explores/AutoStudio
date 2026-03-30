"""Unit tests: PlanDocument → PlannerDecision (single control boundary)."""

import unittest

from agent_v2.runtime.planner_decision_mapper import (
    plan_document_has_no_pending_work,
    planner_decision_from_plan_document,
)
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanMetadata,
    PlanRisk,
    PlanSource,
    PlanStep,
    PlanStepExecution,
    PlannerControllerOutput,
    PlannerEngineOutput,
)


def _doc(controller: PlannerControllerOutput | None, engine: PlannerEngineOutput | None = None) -> PlanDocument:
    return PlanDocument(
        plan_id="p1",
        instruction="i",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[
            PlanStep(
                step_id="s1",
                index=1,
                type="explore",
                goal="g",
                action="search",
                inputs={},
                execution=PlanStepExecution(),
            ),
        ],
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        engine=engine,
        controller=controller,
    )


class TestPlannerDecisionMapper(unittest.TestCase):
    def test_missing_controller_is_act(self):
        d = planner_decision_from_plan_document(_doc(None))
        self.assertEqual(d.type, "act")
        self.assertIsNone(d.step)
        self.assertIsNone(d.query)

    def test_continue_maps_to_act(self):
        d = planner_decision_from_plan_document(
            _doc(PlannerControllerOutput(action="continue", next_step_instruction="x"))
        )
        self.assertEqual(d.type, "act")

    def test_explore_maps_with_query(self):
        d = planner_decision_from_plan_document(
            _doc(
                PlannerControllerOutput(
                    action="explore",
                    exploration_query="find the handler",
                )
            )
        )
        self.assertEqual(d.type, "explore")
        self.assertEqual(d.query, "find the handler")

    def test_replan_maps(self):
        d = planner_decision_from_plan_document(
            _doc(PlannerControllerOutput(action="replan", next_step_instruction=""))
        )
        self.assertEqual(d.type, "replan")

    def test_stop_from_controller(self):
        d = planner_decision_from_plan_document(
            _doc(PlannerControllerOutput(action="stop", next_step_instruction=""))
        )
        self.assertEqual(d.type, "stop")

    def test_all_steps_completed_maps_to_stop(self):
        doc = _doc(PlannerControllerOutput(action="continue"))
        s = doc.steps[0]
        s.execution = PlanStepExecution(status="completed")
        self.assertTrue(plan_document_has_no_pending_work(doc))
        d = planner_decision_from_plan_document(doc)
        self.assertEqual(d.type, "stop")

    def test_engine_explore_overrides_controller(self):
        d = planner_decision_from_plan_document(
            _doc(
                PlannerControllerOutput(action="continue"),
                engine=PlannerEngineOutput(
                    decision="explore",
                    reason="need more",
                    query="find X",
                ),
            )
        )
        self.assertEqual(d.type, "explore")
        self.assertEqual(d.query, "find X")

    def test_engine_stop_overrides_pending_steps(self):
        d = planner_decision_from_plan_document(
            _doc(
                None,
                engine=PlannerEngineOutput(
                    decision="stop",
                    reason="done",
                    query="",
                ),
            )
        )
        self.assertEqual(d.type, "stop")

    def test_engine_synthesize(self):
        d = planner_decision_from_plan_document(
            _doc(
                None,
                engine=PlannerEngineOutput(
                    decision="synthesize",
                    reason="user answer",
                    query="",
                ),
            )
        )
        self.assertEqual(d.type, "synthesize")

    def test_engine_plan(self):
        d = planner_decision_from_plan_document(
            _doc(
                None,
                engine=PlannerEngineOutput(
                    decision="plan",
                    reason="refresh",
                    query="subhint",
                ),
            )
        )
        self.assertEqual(d.type, "plan")
        self.assertEqual(d.query, "subhint")


if __name__ == "__main__":
    unittest.main()
