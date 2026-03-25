"""Phase 7 — Replanner, ReplanResultValidator, PlanExecutor replan loop."""

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
    PlanStepLastResult,
)
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.schemas.replan import ReplanContext, ReplanNewPlan, ReplanResult
from agent_v2.runtime.plan_executor import PlanExecutor
from agent_v2.runtime.replanner import Replanner, merge_preserved_completed_steps
from agent_v2.state.agent_state import AgentState
from agent_v2.validation.plan_validator import PlanValidationError
from agent_v2.validation.replan_result_validator import ReplanResultValidator


def _fail_result(sid: str, msg: str = "e") -> ExecutionResult:
    return ExecutionResult(
        step_id=sid,
        success=False,
        status="failure",
        output=ExecutionOutput(summary="boom", data={}),
        error=ExecutionError(type=ErrorType.tool_error, message=msg),
        metadata=ExecutionMetadata(tool_name="t", duration_ms=0, timestamp=""),
    )


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
    state.current_plan = plan.model_dump(mode="json")


def _plan(pid: str, sid_search: str, sid_finish: str, max_attempts: int = 2) -> PlanDocument:
    return PlanDocument(
        plan_id=pid,
        instruction="test",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[
            PlanStep(
                step_id=sid_search,
                index=1,
                type="explore",
                goal="search",
                action="search",
                inputs={"query": "q"},
                execution=PlanStepExecution(max_attempts=max_attempts),
            ),
            PlanStep(
                step_id=sid_finish,
                index=2,
                type="finish",
                goal="done",
                action="finish",
                dependencies=[sid_search],
                execution=PlanStepExecution(max_attempts=max_attempts),
            ),
        ],
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )


class TestReplanResultValidator(unittest.TestCase):
    def test_success_requires_new_plan(self):
        from agent_v2.schemas.replan import (
            ReplanChanges,
            ReplanMetadata,
            ReplanReasoning,
            ReplanValidation,
        )

        bad = ReplanResult(
            replan_id="r1",
            status="success",
            new_plan=None,
            changes=ReplanChanges(
                type="partial_update",
                summary="s",
                modified_steps=[],
                added_steps=[],
                removed_steps=[],
            ),
            reasoning=ReplanReasoning(failure_analysis="a", strategy="s"),
            validation=ReplanValidation(is_valid=True, issues=[]),
            metadata=ReplanMetadata(timestamp="t", replan_attempt=1),
        )
        with self.assertRaises(PlanValidationError):
            ReplanResultValidator.validate_replan_result(bad)

    def test_failed_forbids_new_plan(self):
        from agent_v2.schemas.replan import (
            ReplanChanges,
            ReplanMetadata,
            ReplanReasoning,
            ReplanValidation,
        )

        bad = ReplanResult(
            replan_id="r1",
            status="failed",
            new_plan=ReplanNewPlan(plan_id="x"),
            changes=ReplanChanges(
                type="full_replacement",
                summary="s",
                modified_steps=[],
                added_steps=[],
                removed_steps=[],
            ),
            reasoning=ReplanReasoning(failure_analysis="a", strategy="s"),
            validation=ReplanValidation(is_valid=False, issues=["x"]),
            metadata=ReplanMetadata(timestamp="t", replan_attempt=1),
        )
        with self.assertRaises(PlanValidationError):
            ReplanResultValidator.validate_replan_result(bad)


class TestMergePreserved(unittest.TestCase):
    def test_copies_completed_execution_by_step_id(self):
        old = _plan("p1", "s1", "s2")
        old.steps[0].execution = PlanStepExecution(
            status="completed",
            attempts=1,
            max_attempts=2,
            last_result=PlanStepLastResult(success=True, output_summary="done"),
        )
        new = _plan("p2", "s1", "s2")
        merged = merge_preserved_completed_steps(old, new)
        self.assertEqual(merged.steps[0].execution.status, "completed")
        self.assertEqual(merged.steps[0].execution.last_result.output_summary, "done")


class TestReplannerBuild(unittest.TestCase):
    def test_build_replan_request_and_context(self):
        policy = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
        r = Replanner(MagicMock(), policy=policy)
        plan = _plan("p0", "a1", "a2", max_attempts=2)
        plan.steps[0].execution = plan.steps[0].execution.model_copy(update={"attempts": 1})
        st = AgentState(instruction="instr")
        st.context["exploration_result"] = {
            "summary": {"key_findings": ["f1"], "knowledge_gaps": []},
        }
        last = _fail_result("a1", "tool broke")
        req = r.build_replan_request(st, plan, plan.steps[0], last)
        self.assertEqual(req.original_plan.plan_id, "p0")
        self.assertIn("tool broke", req.failure_context.error.message)

        ctx = r.build_replan_context(req)
        self.assertIsInstance(ctx, ReplanContext)
        self.assertEqual(ctx.failure_context.step_id, "a1")


class TestPlanExecutorReplanLoop(unittest.TestCase):
    def test_no_replan_path_unchanged(self):
        mock_dispatch = MagicMock()
        mock_dispatch.execute.return_value = _fail_result("s1")
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "q"}
        policy = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
        ex = PlanExecutor(mock_dispatch, arg_gen, replanner=None, policy=policy)
        plan = _plan("p1", "s1", "s2", max_attempts=1)
        state = AgentState(instruction="x")
        _attach_plan(state, plan)
        ex.run(plan, state)
        mock_dispatch.execute.assert_called_once()
        self.assertNotIn("plan_executor_status", state.metadata)

    def test_replan_then_success(self):
        mock_dispatch = MagicMock()
        mock_dispatch.execute.side_effect = [
            _fail_result("r1"),
            _ok_result("r1", "ok"),
        ]
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "q"}

        recovery = _plan("p_rec", "r1", "r2", max_attempts=2)
        mock_planner = MagicMock()
        mock_planner.plan.return_value = recovery

        policy = ExecutionPolicy(max_steps=8, max_retries_per_step=2, max_replans=2)
        replanner = Replanner(mock_planner, policy=policy)
        ex = PlanExecutor(mock_dispatch, arg_gen, replanner=replanner, policy=policy)

        initial = _plan("p_init", "x1", "x2", max_attempts=1)
        state = AgentState(instruction="task")
        state.context["exploration_result"] = {"summary": {"key_findings": [], "knowledge_gaps": []}}
        _attach_plan(state, initial)

        ex.run(initial, state)

        mock_planner.plan.assert_called_once()
        kw = mock_planner.plan.call_args.kwargs
        self.assertTrue(kw.get("deep"))
        self.assertIsInstance(kw.get("planner_input"), ReplanContext)
        self.assertEqual(mock_dispatch.execute.call_count, 2)
        self.assertEqual(state.metadata.get("replan_attempt"), 1)
        self.assertEqual(state.context.get("active_plan_document").plan_id, "p_rec")

    def test_replan_budget_exhausted(self):
        mock_dispatch = MagicMock()
        mock_dispatch.execute.return_value = _fail_result("s1")
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "q"}

        policy = ExecutionPolicy(max_steps=8, max_retries_per_step=1, max_replans=2)
        mock_planner = MagicMock()
        # Fresh PlanDocument each replan so steps are not reused with exhausted attempts.
        mock_planner.plan.side_effect = lambda *a, **kw: _plan("p_rec", "s1", "s2", max_attempts=1)
        replanner = Replanner(mock_planner, policy=policy)
        ex = PlanExecutor(mock_dispatch, arg_gen, replanner=replanner, policy=policy)

        initial = _plan("p_init", "s1", "s2", max_attempts=policy.max_retries_per_step)
        state = AgentState(instruction="task")
        _attach_plan(state, initial)

        ex.run(initial, state)

        self.assertEqual(mock_planner.plan.call_count, 2)
        self.assertEqual(state.metadata.get("plan_executor_status"), "failed_final")
        self.assertEqual(state.metadata.get("replan_attempt"), 2)


if __name__ == "__main__":
    unittest.main()
