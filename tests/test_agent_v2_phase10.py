"""Phase 10 — hardening: executor limits, contracts, stable runtime shape."""

import unittest
from unittest.mock import MagicMock

from agent_v2.runtime.runtime import normalize_run_result
from agent_v2.schemas.execution import (
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
)
from agent_v2.schemas.policies import ExecutionPolicy
from agent_v2.runtime.dag_executor import DagExecutor
from agent_v2.state.agent_state import AgentState


def _ok(sid: str, summary: str = "ok") -> ExecutionResult:
    return ExecutionResult(
        step_id=sid,
        success=True,
        status="success",
        output=ExecutionOutput(summary=summary, data={}),
        error=None,
        metadata=ExecutionMetadata(tool_name="t", duration_ms=0, timestamp=""),
    )


def _plan_two_step() -> PlanDocument:
    return PlanDocument(
        plan_id="p10",
        instruction="t",
        understanding="u",
        sources=[PlanSource(type="other", ref="r", summary="s")],
        steps=[
            PlanStep(
                step_id="s1",
                index=1,
                type="explore",
                goal="g",
                action="search",
                inputs={"query": "q"},
            ),
            PlanStep(
                step_id="s2",
                index=2,
                type="finish",
                goal="d",
                action="finish",
                dependencies=["s1"],
            ),
        ],
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )


class TestNormalizeRunResult(unittest.TestCase):
    def test_act_shape_has_status_trace_state(self):
        st = AgentState(instruction="i")
        wrapped = {"status": "success", "trace": None, "state": st}
        out = normalize_run_result(wrapped, st)
        self.assertEqual(out["status"], "success")
        self.assertIs(out["state"], st)

    def test_plan_mode_agent_state(self):
        st = AgentState(instruction="i")
        out = normalize_run_result(st, st)
        self.assertEqual(out["status"], "plan_ready")
        self.assertIsNone(out["trace"])


class TestExecutorDispatchCap(unittest.TestCase):
    def test_aborts_when_dispatch_cap_is_zero(self):
        policy = ExecutionPolicy(
            max_steps=8,
            max_retries_per_step=1,
            max_replans=0,
            max_executor_dispatches=0,
            max_runtime_seconds=600,
        )
        mock_dispatch = MagicMock()
        arg_gen = MagicMock()
        ex = DagExecutor(mock_dispatch, arg_gen, policy=policy)
        plan = _plan_two_step()
        state = AgentState(instruction="x")
        state.current_plan = plan.model_dump(mode="json")
        out = ex.run(plan, state)
        self.assertEqual(out["status"], "failed")
        self.assertIn("plan_executor_abort", state.metadata)
        mock_dispatch.execute.assert_not_called()


class TestDagExecutorRequiresCurrentPlan(unittest.TestCase):
    def test_raises_without_current_plan(self):
        mock_dispatch = MagicMock()
        mock_dispatch.execute.return_value = _ok("s1")
        arg_gen = MagicMock()
        arg_gen.generate.return_value = {"query": "q"}
        ex = DagExecutor(mock_dispatch, arg_gen)
        plan = _plan_two_step()
        state = AgentState(instruction="x")
        with self.assertRaises(ValueError) as ctx:
            ex.run(plan, state)
        self.assertIn("current_plan", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
