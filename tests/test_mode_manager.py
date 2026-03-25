"""Tests for agent_v2 ModeManager: Phase 8 ACT / PLAN / DEEP_PLAN / plan_execute."""

import unittest
from unittest.mock import MagicMock

from agent_v2.runtime.mode_manager import ModeManager
from agent_v2.schemas.trace import Trace, TraceMetadata
from agent_v2.state.agent_state import AgentState


def _fake_plan_executor_return(doc, st: AgentState) -> dict:
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
    mock_doc = MagicMock()
    mock_doc.model_dump.return_value = {"plan_id": "p1", "steps": [{"step_id": "s1"}]}
    mock_planner = MagicMock()
    mock_planner.plan.return_value = mock_doc

    mock_exp = MagicMock()
    mock_exp.summary.overall = "explore summary"
    mock_exp.model_dump.return_value = {"exploration_id": "e1"}
    mock_er = MagicMock()
    mock_er.run.return_value = mock_exp

    mock_pe = MagicMock()
    mock_pe.run.side_effect = _fake_plan_executor_return
    mock_loop = MagicMock()
    return mock_er, mock_planner, mock_pe, mock_doc, mock_loop


class TestModeManager(unittest.TestCase):
    def test_act_mode_runs_explore_plan_execute_not_agent_loop(self):
        mock_er, mock_planner, mock_pe, mock_doc, mock_loop = _make_mocks_for_pipeline()
        mode_manager = ModeManager(mock_er, mock_planner, mock_pe, loop=mock_loop)
        state = AgentState(instruction="do something")
        result = mode_manager.run(state, mode="act")

        mock_loop.run.assert_not_called()
        mock_er.run.assert_called_once_with("do something")
        mock_planner.plan.assert_called_once()
        call_kw = mock_planner.plan.call_args.kwargs
        self.assertEqual(call_kw.get("exploration"), mock_er.run.return_value)
        self.assertFalse(call_kw.get("deep"))
        mock_pe.run.assert_called_once_with(mock_doc, state)
        self.assertIsInstance(result, dict)
        self.assertIn("trace", result)
        self.assertIs(result["state"], state)
        self.assertIs(state.exploration_result, mock_er.run.return_value)

    def test_plan_mode_explores_then_plans_no_execution(self):
        mock_loop = MagicMock()
        mock_planner = MagicMock()
        mock_planner.plan.return_value = {
            "steps": [
                {"step": 1, "action": "SEARCH", "description": "find code"},
                {"step": 2, "action": "EDIT", "description": "modify"},
            ]
        }
        state = AgentState(instruction="add a feature")
        mock_exp = MagicMock()
        mock_exp.summary.overall = "sum"
        mock_exp.model_dump.return_value = {"exploration_id": "e1"}
        mock_er = MagicMock()
        mock_er.run.return_value = mock_exp
        mock_pe = MagicMock()

        mode_manager = ModeManager(mock_er, mock_planner, mock_pe, loop=mock_loop)
        result = mode_manager.run(state, mode="plan")

        mock_er.run.assert_called_once_with("add a feature")
        mock_planner.plan.assert_called_once_with(
            "add a feature", deep=False, exploration=mock_exp
        )
        mock_pe.run.assert_not_called()
        mock_loop.run.assert_not_called()
        self.assertEqual(result.current_plan, mock_planner.plan.return_value["steps"])
        self.assertIs(result.exploration_result, mock_exp)

    def test_deep_plan_mode_explores_then_deep_plans_no_execution(self):
        mock_loop = MagicMock()
        mock_planner = MagicMock()
        mock_planner.plan.return_value = {"steps": [{"step": 1, "action": "EXPLAIN", "description": "analyze"}]}
        state = AgentState(instruction="analyze code")
        mock_exp = MagicMock()
        mock_exp.summary.overall = "sum"
        mock_exp.model_dump.return_value = {}
        mock_er = MagicMock()
        mock_er.run.return_value = mock_exp
        mock_pe = MagicMock()

        mode_manager = ModeManager(mock_er, mock_planner, mock_pe, loop=mock_loop)
        result = mode_manager.run(state, mode="deep_plan")

        mock_er.run.assert_called_once()
        mock_planner.plan.assert_called_once_with(
            "analyze code", deep=True, exploration=mock_exp
        )
        mock_pe.run.assert_not_called()
        mock_loop.run.assert_not_called()
        self.assertEqual(result.current_plan, mock_planner.plan.return_value["steps"])

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

    def test_plan_execute_wires_exploration_planner_executor(self):
        mock_er, mock_planner, mock_pe, mock_doc, mock_loop = _make_mocks_for_pipeline()
        mode_manager = ModeManager(mock_er, mock_planner, mock_pe, loop=mock_loop)
        state = AgentState(instruction="do thing")
        result = mode_manager.run(state, mode="plan_execute")

        mock_er.run.assert_called_once_with("do thing")
        mock_planner.plan.assert_called_once()
        call_kw = mock_planner.plan.call_args.kwargs
        self.assertEqual(call_kw.get("exploration"), mock_er.run.return_value)
        mock_pe.run.assert_called_once_with(mock_doc, state)
        self.assertIn("trace", result)
        self.assertEqual(state.current_plan, {"plan_id": "p1", "steps": [{"step_id": "s1"}]})
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


if __name__ == "__main__":
    unittest.main()
