"""Phase 5 — PlanExecutor: plan-driven steps, mock dispatcher + argument generator."""

import json
import unittest
from unittest.mock import MagicMock, patch

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


class TestToolExecutionLogging(unittest.TestCase):
    @patch("agent_v2.runtime.plan_executor._emit_tool_execution_log")
    def test_one_tool_execution_log_per_execute_step(self, mock_emit):
        mock_dispatch = MagicMock()
        mock_dispatch.execute.return_value = _ok_result("s1", "found")
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "AgentLoop"}

        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="find loop")
        plan = _minimal_plan()
        _attach_plan(state, plan)
        ex.run(plan, state)

        # One dispatch for search; finish does not call _execute_step.
        self.assertEqual(mock_emit.call_count, 1)
        kwargs = mock_emit.call_args.kwargs
        self.assertTrue(kwargs["success"])
        self.assertIsNone(kwargs["error"])
        self.assertGreaterEqual(kwargs["latency_ms"], 0)
        step = kwargs["step"]
        self.assertEqual(step.step_id, "s1")
        self.assertEqual(step.action, "search")
        self.assertIn("input_summary", kwargs)
        self.assertIn("AgentLoop", kwargs["input_summary"])

    @patch("agent_v2.runtime.plan_executor._emit_tool_execution_log")
    def test_tool_execution_log_includes_required_fields_on_failure(self, mock_emit):
        plan = _minimal_plan()
        plan.steps[0].execution = PlanStepExecution(max_attempts=1)
        bad = ExecutionResult(
            step_id="s1",
            success=False,
            status="failure",
            output=ExecutionOutput(summary="failed", data={}),
            error=ExecutionError(type=ErrorType.tool_error, message="boom"),
            metadata=ExecutionMetadata(tool_name="t", duration_ms=0, timestamp=""),
        )
        mock_dispatch = MagicMock()
        mock_dispatch.execute.return_value = bad
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "q"}

        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="x")
        _attach_plan(state, plan)
        ex.run(plan, state)

        self.assertEqual(mock_emit.call_count, 1)
        kwargs = mock_emit.call_args.kwargs
        self.assertFalse(kwargs["success"])
        self.assertIsNotNone(kwargs["error"])
        self.assertIn("boom", kwargs["error"])

    @patch("agent_v2.runtime.plan_executor._LOG")
    def test_tool_execution_json_shape_on_logger(self, mock_log):
        mock_dispatch = MagicMock()
        mock_dispatch.execute.return_value = _ok_result("s1", "found")
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "q"}

        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="x")
        plan = _minimal_plan()
        _attach_plan(state, plan)
        ex.run(plan, state)

        found = False
        for call in mock_log.info.call_args_list:
            args, _ = call
            if len(args) >= 2 and args[0] == "tool_execution %s":
                payload = json.loads(args[1])
                self.assertEqual(payload["component"], "tool_execution")
                self.assertEqual(payload["tool"], "search_code")
                self.assertEqual(payload["action"], "search")
                self.assertEqual(payload["step_id"], "s1")
                self.assertIn("success", payload)
                self.assertIn("latency_ms", payload)
                self.assertIn("error", payload)
                self.assertIn("input_summary", payload)
                self.assertIn("query", payload["input_summary"])
                found = True
                break
        self.assertTrue(found, "expected tool_execution log line")

    @patch("agent_v2.runtime.plan_executor._LOG")
    def test_tool_execution_includes_mode_from_state_metadata(self, mock_log):
        mock_dispatch = MagicMock()
        mock_dispatch.execute.return_value = _ok_result("s1", "found")
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "q"}

        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="x")
        state.metadata["tool_policy_mode"] = "plan"
        plan = _minimal_plan()
        _attach_plan(state, plan)
        ex.run(plan, state)

        for call in mock_log.info.call_args_list:
            args, _ = call
            if len(args) >= 2 and args[0] == "tool_execution %s":
                payload = json.loads(args[1])
                self.assertEqual(payload.get("mode"), "plan")
                return
        self.fail("expected tool_execution log with mode")

    def test_plan_safe_guard_blocks_edit_without_dispatching(self):
        plan = PlanDocument(
            plan_id="psafe",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="e1",
                    index=1,
                    type="modify",
                    goal="edit",
                    action="edit",
                    inputs={"path": "a.py", "instruction": "x"},
                    dependencies=[],
                    execution=PlanStepExecution(max_attempts=1),
                ),
                PlanStep(
                    step_id="f1",
                    index=2,
                    type="finish",
                    goal="done",
                    action="finish",
                    dependencies=["e1"],
                    execution=PlanStepExecution(),
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        mock_dispatch = MagicMock()
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {}
        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="x")
        state.context["plan_safe_execute"] = True
        _attach_plan(state, plan)
        out = ex.run_one_step(plan, state)
        self.assertEqual(out["status"], "failed_step")
        mock_dispatch.execute.assert_not_called()

    def test_plan_safe_guard_blocks_disallowed_shell_without_dispatching(self):
        plan = PlanDocument(
            plan_id="psh",
            instruction="i",
            understanding="u",
            sources=[PlanSource(type="other", ref="r", summary="s")],
            steps=[
                PlanStep(
                    step_id="sh1",
                    index=1,
                    type="analyze",
                    goal="run",
                    action="shell",
                    inputs={"command": "rm -rf /tmp/x"},
                    dependencies=[],
                    execution=PlanStepExecution(max_attempts=1),
                ),
                PlanStep(
                    step_id="f1",
                    index=2,
                    type="finish",
                    goal="done",
                    action="finish",
                    dependencies=["sh1"],
                    execution=PlanStepExecution(),
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
        )
        mock_dispatch = MagicMock()
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"command": "rm -rf /tmp/x"}
        ex = PlanExecutor(mock_dispatch, arg_gen)
        state = AgentState(instruction="x")
        state.context["plan_safe_execute"] = True
        _attach_plan(state, plan)
        out = ex.run_one_step(plan, state)
        self.assertEqual(out["status"], "failed_step")
        mock_dispatch.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
