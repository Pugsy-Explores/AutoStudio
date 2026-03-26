"""Phase 5 — PlanExecutor: plan-driven steps, mock dispatcher + argument generator."""

import unittest
from unittest.mock import MagicMock

from agent_v2.schemas.execution import (
    ErrorType,
    ExecutionError,
    ExecutionMetadata,
    ExecutionOutput,
    ExecutionResult,
)
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanMetadata,
    PlanRisk,
    PlanSource,
    PlanStep,
    PlanStepExecution,
)
from agent_v2.runtime.plan_executor import PlanExecutor
from agent_v2.state.agent_state import AgentState


def _ok_result(sid: str, summary: str) -> ExecutionResult:
    return ExecutionResult(
        step_id=sid,
        success=True,
        status="success",
        output=ExecutionOutput(summary=summary, data={}),
        error=None,
        metadata=ExecutionMetadata(tool_name="t", duration_ms=0, timestamp=""),
    )


def _attach_plan(state: AgentState, plan: PlanDocument) -> None:
    """Phase 10 — mirror ModeManager: executor requires current_plan before run."""
    state.current_plan = plan.model_dump(mode="json")


def _minimal_plan() -> PlanDocument:
    return PlanDocument(
        plan_id="p1",
        instruction="test",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[
            PlanStep(
                step_id="s1",
                index=1,
                type="explore",
                goal="search",
                action="search",
                inputs={"query": "AgentLoop"},
                execution=PlanStepExecution(),
            ),
            PlanStep(
                step_id="s2",
                index=2,
                type="finish",
                goal="done",
                action="finish",
                dependencies=["s1"],
                execution=PlanStepExecution(),
            ),
        ],
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )


class TestPlanExecutor(unittest.TestCase):
    def test_executes_in_dependency_order_and_stops_at_finish(self):
        mock_dispatch = MagicMock()
        mock_dispatch.execute.return_value = _ok_result("s1", "found")
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "AgentLoop"}

        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="find loop")
        plan = _minimal_plan()
        _attach_plan(state, plan)
        out = ex.run(plan, state)
        self.assertIs(out["state"], state)
        self.assertEqual(out["status"], "success")
        tr = out["trace"]
        self.assertEqual(len(tr.steps), 2)
        self.assertTrue(all(s.success for s in tr.steps))
        self.assertEqual(tr.status, "success")
        mock_dispatch.execute.assert_called_once()
        call_step = mock_dispatch.execute.call_args[0][0]
        self.assertEqual(call_step.get("_react_action_raw"), "search")
        self.assertEqual(call_step.get("action"), "SEARCH")
        self.assertEqual(plan.steps[0].execution.status, "completed")
        self.assertEqual(plan.steps[1].execution.status, "completed")
        self.assertEqual(plan.steps[1].action, "finish")
        self.assertTrue(len(state.history) >= 2)

    def test_skips_completed_steps(self):
        plan = _minimal_plan()
        plan.steps[0].execution = PlanStepExecution(status="completed", attempts=1)
        mock_dispatch = MagicMock()
        arg_gen = MagicMock()

        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="x")
        _attach_plan(state, plan)
        ex.run(plan, state)

        mock_dispatch.execute.assert_not_called()
        arg_gen.generate.assert_not_called()

    def test_failure_stops_run(self):
        plan = _minimal_plan()
        plan.steps[0].execution = PlanStepExecution(max_attempts=2)
        fail = ExecutionResult(
            step_id="s1",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="boom", data={}),
            error=ExecutionError(type=ErrorType.tool_error, message="e"),
            metadata=ExecutionMetadata(tool_name="t", duration_ms=0, timestamp=""),
        )
        mock_dispatch = MagicMock()
        mock_dispatch.execute.return_value = fail
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "q"}

        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="x")
        _attach_plan(state, plan)
        out = ex.run(plan, state)
        self.assertEqual(out["status"], "failed")
        tr = out["trace"]
        self.assertEqual(len(tr.steps), 1)
        self.assertFalse(tr.steps[0].success)
        self.assertEqual(tr.steps[0].error.type, ErrorType.tool_error)
        self.assertEqual(tr.status, "failure")

        self.assertEqual(mock_dispatch.execute.call_count, 2)
        self.assertEqual(plan.steps[0].execution.status, "failed")
        self.assertTrue(plan.steps[0].failure.replan_required)
        self.assertEqual(plan.steps[0].execution.attempts, 2)
        self.assertEqual(plan.steps[1].execution.status, "pending")
        self.assertEqual(state.metadata.get("failure_streak"), 1)
        self.assertEqual(state.metadata.get("last_error"), "tool_error")

    def test_retry_succeeds_on_second_attempt(self):
        plan = _minimal_plan()
        plan.steps[0].execution = PlanStepExecution(max_attempts=2)
        fail = ExecutionResult(
            step_id="s1",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="transient", data={}),
            error=ExecutionError(type=ErrorType.timeout, message="slow"),
            metadata=ExecutionMetadata(tool_name="t", duration_ms=0, timestamp=""),
        )
        ok = _ok_result("s1", "found")
        mock_dispatch = MagicMock()
        mock_dispatch.execute.side_effect = [fail, ok]
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "q"}

        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="x")
        _attach_plan(state, plan)
        out = ex.run(plan, state)
        self.assertEqual(out["status"], "success")
        self.assertEqual(len(out["trace"].steps), 2)

        self.assertEqual(mock_dispatch.execute.call_count, 2)
        self.assertEqual(plan.steps[0].execution.status, "completed")
        self.assertEqual(plan.steps[0].execution.attempts, 2)
        self.assertFalse(plan.steps[0].failure.replan_required)
        mock_dispatch.execute.assert_called()

    def test_success_first_try_single_attempt(self):
        plan = _minimal_plan()
        plan.steps[0].execution = PlanStepExecution(max_attempts=3)
        mock_dispatch = MagicMock()
        mock_dispatch.execute.return_value = _ok_result("s1", "ok")
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "q"}

        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="x")
        _attach_plan(state, plan)
        out = ex.run(plan, state)
        self.assertEqual(out["status"], "success")

        mock_dispatch.execute.assert_called_once()
        self.assertEqual(plan.steps[0].execution.attempts, 1)
        self.assertEqual(plan.steps[0].execution.status, "completed")

    def test_failure_without_error_uses_unknown_type(self):
        plan = _minimal_plan()
        plan.steps[0].execution = PlanStepExecution(max_attempts=1)
        bad = ExecutionResult(
            step_id="s1",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="x", data={}),
            error=None,
            metadata=ExecutionMetadata(tool_name="t", duration_ms=0, timestamp=""),
        )
        mock_dispatch = MagicMock()
        mock_dispatch.execute.return_value = bad
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "q"}

        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="x")
        _attach_plan(state, plan)
        out = ex.run(plan, state)
        self.assertEqual(out["trace"].steps[0].error.type, ErrorType.unknown)

        self.assertEqual(plan.steps[0].failure.failure_type, ErrorType.unknown)
        self.assertEqual(state.metadata.get("last_error"), "unknown")

    def test_run_one_step_executes_one_tool_step_then_progress(self):
        mock_dispatch = MagicMock()
        mock_dispatch.execute.return_value = _ok_result("s1", "found")
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "AgentLoop"}

        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="find loop")
        plan = _minimal_plan()
        _attach_plan(state, plan)
        out = ex.run_one_step(plan, state)
        self.assertEqual(out["status"], "progress")
        mock_dispatch.execute.assert_called_once()
        self.assertEqual(plan.steps[0].execution.status, "completed")
        self.assertEqual(plan.steps[1].execution.status, "pending")


if __name__ == "__main__":
    unittest.main()
