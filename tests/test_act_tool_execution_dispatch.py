"""
ACT-mode tool execution wiring: Planner output shape → DagExecutor → Dispatcher → _dispatch_react.

Uses a mock argument generator (fixed args) so no PLANNER_TOOL_ARGS_* / planner LLM is required.
Mirrors production path: ``AgentRuntime`` wires ``Dispatcher(execute_fn=_dispatch_react)``.

See also: ``tests/test_replanner.py`` (DagExecutor + mock dispatcher).
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.execution import react_schema  # noqa: F401 — initializes tool registry
from agent.execution.step_dispatcher import _dispatch_react
from agent_v2.runtime.dispatcher import Dispatcher
from agent_v2.runtime.dag_executor import DagExecutor
from agent_v2.schemas.plan import (
    PlanDocument,
    PlanMetadata,
    PlanRisk,
    PlanSource,
    PlanStep,
)
from agent_v2.state.agent_state import AgentState

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _dag_last_summary(state: AgentState, step_id: str) -> str:
    raw = (state.context or {}).get("dag_graph_tasks") or {}
    cell = raw.get(step_id) or {}
    lr = (cell.get("runtime") or {}).get("last_result") or {}
    out = lr.get("output") or {}
    return str(out.get("summary") or "")


_SANDBOX_EDIT_FILE = "tweak_me.py"
_SANDBOX_EDIT_BEFORE = (
    "# autostudio sandbox edit e2e — safe to delete\nAUTOSTUDIO_SANDBOX_MARKER = 0\n"
)


def _sandbox_patch_plan(rel_path: str) -> dict:
    """Deterministic text_sub plan (avoids LLM in generate_edit_proposals)."""
    return {
        "changes": [
            {
                "file": rel_path,
                "patch": {
                    "action": "text_sub",
                    "old": "AUTOSTUDIO_SANDBOX_MARKER = 0",
                    "new": "AUTOSTUDIO_SANDBOX_MARKER = 1",
                },
            }
        ]
    }


def _base_plan_doc(steps: list[PlanStep]) -> PlanDocument:
    return PlanDocument(
        plan_id="act_dispatch_test",
        instruction="tool wiring probe",
        understanding="u",
        sources=[PlanSource(type="other", ref="t", summary="s")],
        steps=steps,
        risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
        completion_criteria=["c"],
        metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
    )


def _work_then_finish(
    *,
    step_id: str,
    index: int,
    action: str,
    inputs: dict,
    arg_gen_return: dict,
) -> tuple[PlanDocument, MagicMock]:
    """One runnable tool step + terminal finish."""
    arg_gen = MagicMock()
    arg_gen.generate.return_value = arg_gen_return
    plan = _base_plan_doc(
        [
            PlanStep(
                step_id=step_id,
                index=index,
                type="explore",
                goal="probe",
                action=action,  # type: ignore[arg-type]
                inputs=inputs,
            ),
            PlanStep(
                step_id="fin",
                index=index + 1,
                type="finish",
                goal="done",
                action="finish",
                dependencies=[step_id],
            ),
        ]
    )
    return plan, arg_gen


def _act_state(**extra_ctx) -> AgentState:
    st = AgentState(instruction="ACT tool probe")
    st.context["react_mode"] = True
    st.context["project_root"] = str(_REPO_ROOT)
    st.context["inner_validation_test_cmd"] = (
        f'"{sys.executable}" -m pytest tests/test_phase1_tool_exposure.py -q'
    )
    st.context.update(extra_ctx)
    st.metadata = dict(st.metadata) if getattr(st, "metadata", None) else {}
    return st


@pytest.fixture
def executor() -> DagExecutor:
    return DagExecutor(Dispatcher(execute_fn=_dispatch_react), MagicMock())


def test_open_file_returns_content(executor: DagExecutor):
    plan, arg_gen = _work_then_finish(
        step_id="s_open",
        index=1,
        action="open_file",
        inputs={"path": "agent_v2/runtime/exploration_runner.py"},
        arg_gen_return={"path": "agent_v2/runtime/exploration_runner.py"},
    )
    executor.argument_generator = arg_gen
    state = _act_state()
    state.current_plan = plan.model_dump(mode="json")
    out = executor.run(plan, state)
    assert out["status"] == "success"
    summary = _dag_last_summary(state, plan.steps[0].step_id)
    assert "ExplorationRunner" in summary or "exploration" in summary.lower()


def test_search_returns_results(executor: DagExecutor):
    plan, arg_gen = _work_then_finish(
        step_id="s_search",
        index=1,
        action="search",
        inputs={"query": "DagExecutor class"},
        arg_gen_return={"query": "DagExecutor class"},
    )
    executor.argument_generator = arg_gen
    state = _act_state()
    state.current_plan = plan.model_dump(mode="json")
    out = executor.run(plan, state)
    assert out["status"] == "success"
    summary = _dag_last_summary(state, plan.steps[0].step_id)
    assert "dag_executor" in summary.lower() or "DagExecutor" in summary


def test_shell_lists_files(executor: DagExecutor):
    plan, arg_gen = _work_then_finish(
        step_id="s_sh",
        index=1,
        action="shell",
        inputs={"command": "ls agent_v2/runtime"},
        arg_gen_return={"command": "ls agent_v2/runtime"},
    )
    executor.argument_generator = arg_gen
    state = _act_state()
    state.current_plan = plan.model_dump(mode="json")
    out = executor.run(plan, state)
    assert out["status"] == "success"
    summary = _dag_last_summary(state, plan.steps[0].step_id)
    assert "dag_executor.py" in summary


def test_run_tests_produces_pytest_output(executor: DagExecutor):
    plan, arg_gen = _work_then_finish(
        step_id="s_test",
        index=1,
        action="run_tests",
        inputs={},
        arg_gen_return={},
    )
    executor.argument_generator = arg_gen
    state = _act_state()
    state.current_plan = plan.model_dump(mode="json")
    out = executor.run(plan, state)
    assert out["status"] == "success"
    summary = _dag_last_summary(state, plan.steps[0].step_id)
    low = summary.lower()
    assert "executed successfully" in low
    assert "passed" in low or "ok" in low


def test_edit_applies_patch_in_temp_project(executor: DagExecutor, tmp_path: Path):
    """
    End-to-end edit via DagExecutor → _dispatch_react → _edit_react → execute_patch.

    ``_generate_patch_once`` is patched to return a fixed ``text_sub`` plan so the test
    does not depend on the coding model; snapshot, apply_patch, syntax check, and
    post-edit validation command still run for real.
    """
    root = tmp_path.resolve()
    (root / _SANDBOX_EDIT_FILE).write_text(_SANDBOX_EDIT_BEFORE, encoding="utf-8")

    def _fake_generate(_instruction: str, _context: dict, project_root: str) -> dict:
        assert Path(project_root).resolve() == root
        return _sandbox_patch_plan(_SANDBOX_EDIT_FILE)

    plan, arg_gen = _work_then_finish(
        step_id="s_edit",
        index=1,
        action="edit",
        inputs={
            "path": _SANDBOX_EDIT_FILE,
            "instruction": "Set AUTOSTUDIO_SANDBOX_MARKER to 1",
        },
        arg_gen_return={
            "path": _SANDBOX_EDIT_FILE,
            "instruction": "Set AUTOSTUDIO_SANDBOX_MARKER to 1",
        },
    )
    executor.argument_generator = arg_gen
    state = AgentState(instruction="sandbox edit probe")
    state.context["react_mode"] = True
    state.context["project_root"] = str(root)
    state.context["inner_validation_test_cmd"] = f'"{sys.executable}" -c "exit(0)"'
    state.metadata = dict(state.metadata) if getattr(state, "metadata", None) else {}
    state.current_plan = plan.model_dump(mode="json")

    with patch("agent.execution.step_dispatcher._generate_patch_once", side_effect=_fake_generate):
        out = executor.run(plan, state)

    assert out["status"] == "success"
    after = (root / _SANDBOX_EDIT_FILE).read_text(encoding="utf-8")
    assert "AUTOSTUDIO_SANDBOX_MARKER = 1" in after
    assert "AUTOSTUDIO_SANDBOX_MARKER = 0" not in after
