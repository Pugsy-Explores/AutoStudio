"""Tests for agent_v2 ModeManager: ACT / plan (safe loop) / plan_legacy / deep_plan / plan_execute."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from agent_v2.runtime.mode_manager import ModeManager
from agent_v2.schemas.planner_plan_context import PlannerPlanContext
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanMetadata,
    PlanRisk,
    PlanSource,
    PlanStep,
    PlanStepExecution,
)
from agent_v2.schemas.trace import Trace, TraceMetadata
from agent_v2.state.agent_state import AgentState


def _minimal_plan(*, plan_id: str = "p1") -> PlanDocument:
    return PlanDocument(
        plan_id=plan_id,
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
    )


def _fake_plan_executor_return(doc, st: AgentState, **kwargs) -> dict:
    pid = getattr(doc, "plan_id", None) or "p1"
    return {
        "status": "success",
        "state": st,
        "trace": Trace(
            trace_id="test-trace",
            instruction=st.instruction,
            plan_id=str(pid),
            steps=[],
            status="success",
            metadata=TraceMetadata(total_steps=0, total_duration_ms=0),
        ),
    }


def _make_mocks_for_pipeline():
    mock_doc = _minimal_plan()
    mock_planner = MagicMock()
    mock_planner.plan.return_value = mock_doc

    mock_exp = MagicMock()
    mock_exp.exploration_summary = SimpleNamespace(overall="explore summary")
    mock_exp.model_dump.return_value = {"exploration_id": "e1"}
    mock_er = MagicMock()
    mock_er.run.return_value = mock_exp

    mock_pe = MagicMock()
    mock_pe.run.side_effect = _fake_plan_executor_return
    mock_pe.run_one_step.side_effect = _fake_plan_executor_return
    mock_loop = MagicMock()
    return mock_er, mock_planner, mock_pe, mock_doc, mock_loop


class TestModeManager(unittest.TestCase):
    def test_act_mode_runs_explore_plan_execute_not_agent_loop(self):
        mock_er, mock_planner, mock_pe, mock_doc, mock_loop = _make_mocks_for_pipeline()
        mode_manager = ModeManager(mock_er, mock_planner, mock_pe, loop=mock_loop)
        state = AgentState(instruction="do something")
        result = mode_manager.run(state, mode="act")

        mock_loop.run.assert_not_called()
        mock_er.run.assert_called_once()
        self.assertEqual(mock_er.run.call_args.args[0], "do something")
        mock_planner.plan.assert_called_once()
        call_kw = mock_planner.plan.call_args.kwargs
        pctx = call_kw.get("planner_context")
        self.assertIsInstance(pctx, PlannerPlanContext)
        self.assertIs(pctx.exploration, mock_er.run.return_value)
        self.assertFalse(call_kw.get("deep"))
        self.assertTrue(call_kw.get("require_controller_json"))
        self.assertIsNone(call_kw.get("validation_task_mode"))
        mock_pe.run_one_step.assert_called_once()
        mock_pe.run.assert_not_called()
        pe_call = mock_pe.run_one_step.call_args
        self.assertIs(pe_call.args[0], mock_doc)
        self.assertIs(pe_call.args[1], state)
        self.assertIn("trace_emitter", pe_call.kwargs)
        self.assertIsNotNone(pe_call.kwargs.get("trace_emitter"))
        self.assertIsInstance(result, dict)
        self.assertIn("trace", result)
        self.assertIs(result["state"], state)
        self.assertIs(state.exploration_result, mock_er.run.return_value)

    def test_plan_legacy_mode_explores_then_plans_no_execution(self):
        mock_loop = MagicMock()
        mock_planner = MagicMock()
        plan_doc = _minimal_plan(plan_id="plan-mode")
        mock_planner.plan.return_value = plan_doc
        state = AgentState(instruction="add a feature")
        mock_exp = MagicMock()
        mock_exp.exploration_summary = SimpleNamespace(overall="sum", key_findings=[], knowledge_gaps=[])
        mock_exp.metadata = SimpleNamespace(completion_status="complete")
        mock_exp.model_dump.return_value = {"exploration_id": "e1"}
        mock_er = MagicMock()
        mock_er.run.return_value = mock_exp
        mock_pe = MagicMock()

        mode_manager = ModeManager(mock_er, mock_planner, mock_pe, loop=mock_loop)
        result = mode_manager.run(state, mode="plan_legacy")

        mock_er.run.assert_called_once()
        self.assertEqual(mock_er.run.call_args.args[0], "add a feature")
        mock_planner.plan.assert_called_once()
        p_kw = mock_planner.plan.call_args.kwargs
        self.assertFalse(p_kw.get("deep"))
        self.assertIs(p_kw.get("planner_context").exploration, mock_exp)
        mock_pe.run.assert_not_called()
        mock_pe.run_one_step.assert_not_called()
        mock_loop.run.assert_not_called()
        self.assertIsInstance(result.current_plan, dict)
        self.assertEqual(result.current_plan.get("plan_id"), "plan-mode")
        self.assertIs(result.exploration_result, mock_exp)
        tr = result.metadata.get("trace")
        self.assertIsNotNone(tr)
        self.assertEqual(tr.plan_id, "plan-mode")
        self.assertEqual(result.metadata.get("execution_trace_id"), tr.trace_id)

    def test_plan_mode_runs_safe_controller_loop_like_act(self):
        mock_er, mock_planner, mock_pe, mock_doc, mock_loop = _make_mocks_for_pipeline()
        mode_manager = ModeManager(mock_er, mock_planner, mock_pe, loop=mock_loop)
        state = AgentState(instruction="inspect only")
        result = mode_manager.run(state, mode="plan")

        mock_er.run.assert_called_once()
        mock_planner.plan.assert_called_once()
        p_kw = mock_planner.plan.call_args.kwargs
        self.assertTrue(p_kw.get("require_controller_json"))
        self.assertEqual(p_kw.get("validation_task_mode"), "plan_safe")
        mock_pe.run_one_step.assert_called_once()
        mock_pe.run.assert_not_called()
        self.assertIn("trace", result)

    def test_deep_plan_mode_runs_safe_loop_with_deep_planner_calls(self):
        mock_er, mock_planner, mock_pe, mock_doc, mock_loop = _make_mocks_for_pipeline()
        mode_manager = ModeManager(mock_er, mock_planner, mock_pe, loop=mock_loop)
        state = AgentState(instruction="analyze code")
        result = mode_manager.run(state, mode="deep_plan")

        mock_er.run.assert_called_once()
        mock_planner.plan.assert_called_once()
        dp_kw = mock_planner.plan.call_args.kwargs
        self.assertTrue(dp_kw.get("deep"))
        self.assertIs(dp_kw.get("planner_context").exploration, mock_er.run.return_value)
        self.assertEqual(dp_kw.get("validation_task_mode"), "plan_safe")
        mock_pe.run_one_step.assert_called_once()
        mock_pe.run.assert_not_called()
        self.assertIn("trace", result)

    def test_unknown_mode_raises(self):
        mock_er, mock_planner, mock_pe, _, _ = _make_mocks_for_pipeline()
        mode_manager = ModeManager(mock_er, mock_planner, mock_pe)
        state = AgentState(instruction="x")
        with self.assertRaises(ValueError) as ctx:
            mode_manager.run(state, mode="invalid")
        self.assertIn("Unknown mode", str(ctx.exception))

    def test_act_and_plan_execute_require_plan_executor(self):
        mock_er = MagicMock()
        mock_planner = MagicMock()
        mode_manager = ModeManager(mock_er, mock_planner, plan_executor=None)
        state = AgentState(instruction="x")
        with self.assertRaises(ValueError) as ctx:
            mode_manager.run(state, mode="plan_execute")
        self.assertIn("PlanExecutor", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx2:
            mode_manager.run(state, mode="act")
        self.assertIn("PlanExecutor", str(ctx2.exception))
        with self.assertRaises(ValueError) as ctx3:
            mode_manager.run(state, mode="plan")
        self.assertIn("PlanExecutor", str(ctx3.exception))

    def test_plan_execute_wires_exploration_planner_executor(self):
        mock_er, mock_planner, mock_pe, mock_doc, mock_loop = _make_mocks_for_pipeline()
        mode_manager = ModeManager(mock_er, mock_planner, mock_pe, loop=mock_loop)
        state = AgentState(instruction="do thing")
        result = mode_manager.run(state, mode="plan_execute")

        mock_er.run.assert_called_once()
        self.assertEqual(mock_er.run.call_args.args[0], "do thing")
        mock_planner.plan.assert_called_once()
        call_kw = mock_planner.plan.call_args.kwargs
        self.assertIs(call_kw.get("planner_context").exploration, mock_er.run.return_value)
        self.assertTrue(call_kw.get("require_controller_json"))
        self.assertIsNone(call_kw.get("validation_task_mode"))
        mock_pe.run_one_step.assert_called_once()
        mock_pe.run.assert_not_called()
        pe_call = mock_pe.run_one_step.call_args
        self.assertIs(pe_call.args[0], mock_doc)
        self.assertIs(pe_call.args[1], state)
        self.assertIn("trace_emitter", pe_call.kwargs)
        self.assertIn("trace", result)
        dumped = mock_doc.model_dump(mode="json")
        self.assertEqual(state.current_plan, dumped)
        self.assertEqual(state.context.get("exploration_summary_text"), "explore summary")

    def test_plan_mode_requires_exploration_runner(self):
        mode_manager = ModeManager(
            exploration_runner=None,
            planner=MagicMock(),
            plan_executor=MagicMock(),
        )
        state = AgentState(instruction="x")
        with self.assertRaises(ValueError) as ctx:
            mode_manager.run(state, mode="plan")
        self.assertIn("exploration_runner", str(ctx.exception).lower())

    def test_planner_receives_insufficiency_when_exploration_incomplete(self):
        mock_er, mock_planner, mock_pe, _, mock_loop = _make_mocks_for_pipeline()
        mock_exp = MagicMock()
        mock_exp.exploration_summary = SimpleNamespace(
            overall="incomplete exploration",
            key_findings=[],
            knowledge_gaps=["gap1"],
        )
        mock_exp.model_dump.return_value = {"exploration_id": "e_incomplete"}
        mock_exp.metadata = SimpleNamespace(
            completion_status="incomplete",
            termination_reason="max_steps",
        )
        mock_er.run.return_value = mock_exp
        mode_manager = ModeManager(mock_er, mock_planner, mock_pe, loop=mock_loop)
        state = AgentState(instruction="do thing")

        mode_manager.run(state, mode="plan_execute")

        mock_planner.plan.assert_called_once()
        p_kw = mock_planner.plan.call_args.kwargs
        pctx = p_kw.get("planner_context")
        self.assertIsInstance(pctx, PlannerPlanContext)
        self.assertIs(pctx.exploration, mock_exp)
        self.assertIsNotNone(pctx.insufficiency)
        self.assertEqual(pctx.insufficiency.termination_reason, "max_steps")
        mock_pe.run_one_step.assert_called_once()


if __name__ == "__main__":
    unittest.main()
