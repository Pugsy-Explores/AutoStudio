"""
Full-stack agent_v2 integration tests — real PlannerV2, SessionMemory, ToolPolicy,
PlanExecutor, exploration, and live reasoning model (no mocks).

Gate (default CI skips entire module):
  AGENT_V2_LIVE=1

Run from repo root with model endpoints configured:
  AGENT_V2_LIVE=1 SKIP_STARTUP_CHECKS=1 pytest tests/test_agent_v2_integration_live.py -m agent_v2_integration_live --tb=short -q

Narrow run:
  AGENT_V2_LIVE=1 pytest tests/test_agent_v2_integration_live.py -k phase1_search --tb=short

Some cases are LLM-dependent; assertions prefer structural contracts and stable file paths
under this repository. A small number of tests use instructions designed to trigger specific
planner tools or policy violations; if the model drifts, investigate prompts before loosening checks.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import pytest

from agent_v2.observability.langfuse_client import create_agent_trace, finalize_agent_trace
from agent_v2.observability.observability_context import ObservabilityContext
from agent_v2.runtime.runtime import AgentRuntime, normalize_run_result
from agent_v2.state.agent_state import AgentState
from agent_v2.runtime.tool_policy import ToolPolicyViolationError
from agent_v2.validation.plan_validator import PlanValidationError


def _autostudio_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _agent_v2_live_requested() -> bool:
    return os.getenv("AGENT_V2_LIVE", "").lower() in ("1", "true", "yes")


@pytest.fixture(scope="module")
def llm_reachable() -> bool:
    if not _agent_v2_live_requested():
        return False
    try:
        from agent.models.model_client import call_reasoning_model

        out = call_reasoning_model(
            "Reply with exactly the two letters OK and nothing else.",
            max_tokens=16,
            task_name="PLANNER_DECISION_ACT",
        )
        return bool(out and str(out).strip())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"AGENT_V2_LIVE set but reasoning model unreachable: {exc}")


@pytest.fixture
def require_live(llm_reachable: bool):
    if not _agent_v2_live_requested():
        pytest.skip("Set AGENT_V2_LIVE=1 to run agent_v2 integration live tests")
    if not llm_reachable:
        pytest.skip("LLM probe failed (see llm_reachable fixture)")


@pytest.fixture
def project_root(monkeypatch) -> Path:
    root = _autostudio_root()
    monkeypatch.chdir(root)
    monkeypatch.setenv("SERENA_PROJECT_DIR", str(root))
    return root


def _continue_agent_run(rt: AgentRuntime, state: AgentState, instruction: str, mode: str) -> dict[str, Any]:
    """Second+ turns on a shared AgentState (session memory in state.context)."""
    state.instruction = instruction
    state.metadata["mode"] = mode
    lf_trace = create_agent_trace(instruction=instruction, mode=mode)
    state.metadata["langfuse_trace"] = lf_trace
    state.metadata["obs"] = ObservabilityContext(langfuse_trace=lf_trace, owns_root=False)
    state.context["react_mode"] = mode in ("act", "plan_execute")
    if mode in ("plan", "deep_plan"):
        state.context["plan_safe_execute"] = True
    else:
        state.context.pop("plan_safe_execute", None)
    run_status = "unknown"
    plan_id_out: str | None = None
    try:
        mgr_out = rt.mode_manager.run(state, mode)
        out = normalize_run_result(mgr_out, state)
        run_status = str(out.get("status", "unknown"))
        if isinstance(mgr_out, dict) and mgr_out.get("trace") is not None:
            tr = mgr_out["trace"]
            plan_id_out = getattr(tr, "plan_id", None)
        elif isinstance(state.current_plan, dict):
            plan_id_out = state.current_plan.get("plan_id")
        return out
    finally:
        if run_status == "unknown":
            run_status = "plan_ready" if mode == "plan_legacy" else "unknown"
        lf_fin = state.metadata.get("langfuse_trace")
        if lf_fin is None and state.metadata.get("obs") is not None:
            lf_fin = getattr(state.metadata["obs"], "langfuse_trace", None)
        finalize_agent_trace(lf_fin, status=run_status, plan_id=plan_id_out)


def _parse_log_json_after_prefix(line: str, prefix: str) -> dict[str, Any] | None:
    idx = line.find(prefix)
    if idx < 0:
        return None
    blob = line[idx + len(prefix) :].strip()
    try:
        data = json.loads(blob)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _collect_tool_execution_payloads(caplog) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in caplog.records:
        msg = rec.getMessage()
        if "tool_execution " not in msg:
            continue
        p = _parse_log_json_after_prefix(msg, "tool_execution ")
        if p and p.get("component") == "tool_execution":
            out.append(p)
    return out


def _collect_planner_telemetry_payloads(caplog) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in caplog.records:
        msg = rec.getMessage()
        if "planner_telemetry " not in msg:
            continue
        p = _parse_log_json_after_prefix(msg, "planner_telemetry ")
        if p and p.get("component") == "planner_telemetry":
            out.append(p)
    return out


pytestmark = [
    pytest.mark.agent_v2_integration_live,
    pytest.mark.agent_v2_live,
    pytest.mark.slow,
    pytest.mark.timeout(1200),
]


# --- Phase 1: component-shaped integration (single plan_execute / act hop) -----------------


class TestPhase1PlannerToolIntegration:
    def test_search_code_path_act_and_results(self, require_live, project_root, monkeypatch, caplog):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        caplog.set_level(logging.INFO)
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        # Avoid leading "find"/"locate"/… so _infer_task_mode does not force read_only
        # (read_only blocks run_tests; model sometimes picks tests despite narrow wording).
        result = rt.run(
            "Using search_code in the repo: locate exploration engine code under agent_v2",
            mode="plan_execute",
        )
        assert result.get("status") in ("success", "failed")
        st = result["state"]
        plan = st.current_plan
        assert isinstance(plan, dict)
        eng = plan.get("engine") or {}
        # Model may explore first in controller loop; accept act+search_code on final attached plan
        # or success with search evidence in trace.
        if isinstance(eng, dict):
            assert eng.get("decision") in ("act", "explore", "stop", "replan", None)
        trace = result.get("trace")
        assert trace is not None
        blob = json.dumps(trace.model_dump(mode="json"), default=str).lower()
        assert "exploration" in blob or "search" in blob or "planexecutor" in blob

        telem = _collect_planner_telemetry_payloads(caplog)
        assert any(t.get("component") == "planner_telemetry" for t in telem)
        tools = [t.get("tool") for t in telem if "tool_policy_violation" not in t]
        assert any(x in ("search_code", "explore", "none") for x in tools if x)

    def test_open_file_returns_content(self, require_live, project_root, monkeypatch, caplog):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        caplog.set_level(logging.INFO)
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        result = rt.run(
            "open the file agent_v2/exploration/exploration_engine_v2.py and read it (open_file only)",
            mode="plan_execute",
        )
        assert result.get("status") in ("success", "failed")
        trace = result.get("trace")
        assert trace is not None
        text = json.dumps(trace.model_dump(mode="json"), default=str)
        assert "exploration_engine_v2" in text or "ExplorationEngine" in text

        payloads = _collect_tool_execution_payloads(caplog)
        assert any(p.get("tool") == "open_file" for p in payloads)

    def test_run_tests_invocation(self, require_live, project_root, monkeypatch, caplog):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        caplog.set_level(logging.INFO)
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        result = rt.run(
            "Run tests using run_tests for tests/test_tool_policy.py only.",
            mode="plan_execute",
        )
        assert result.get("status") in ("success", "failed")
        payloads = _collect_tool_execution_payloads(caplog)
        names = [p.get("tool") for p in payloads]
        if result.get("status") == "success":
            assert "run_tests" in names, f"expected run_tests in tool_execution logs, got {names}"

    def test_plan_mode_edit_rejected_by_policy(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        try:
            out = rt.run(
                "Use tool edit to modify README.md: add a single comment line at the top.",
                mode="plan",
            )
        except ToolPolicyViolationError as e:
            m = str(e).lower()
            assert "tool policy" in m or "edit" in m
            return
        except PlanValidationError as e:
            m = str(e).lower()
            assert "edit" in m or "tool policy" in m
            return
        plan = out["state"].current_plan
        assert isinstance(plan, dict)
        eng = plan.get("engine") or {}
        assert not (eng.get("decision") == "act" and eng.get("tool") == "edit")
        for s in plan.get("steps") or []:
            if isinstance(s, dict):
                assert s.get("action") != "edit", "plan mode must not synthesize edit steps"

    def test_plan_mode_shell_ls_allowed(self, require_live, project_root, monkeypatch, caplog):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        caplog.set_level(logging.INFO)
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        result = rt.run(
            "List files in agent_v2/runtime using run_shell with command starting with ls",
            mode="plan_execute",
        )
        assert result.get("status") in ("success", "failed")
        payloads = _collect_tool_execution_payloads(caplog)
        shell_cmds = [
            (p.get("input_summary") or "") + str(p.get("tool"))
            for p in payloads
            if p.get("tool") == "run_shell"
        ]
        joined = " ".join(shell_cmds).lower()
        summaries = " ".join(str(p.get("input_summary") or "") for p in payloads).lower()
        assert "ls" in joined or "ls" in summaries


# --- Phase 2: session memory (multi-turn on one state) ------------------------------------


class TestPhase2SessionMemoryMultiTurn:
    def test_open_it_resolves_after_find_auth(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        out1 = rt.run(
            "find where authentication or API key handling is implemented in agent_v2",
            mode="plan_execute",
        )
        st = out1["state"]
        mem = st.context.get("planner_session_memory")
        assert mem is not None
        assert getattr(mem, "intent_anchor", None) is not None

        out2 = _continue_agent_run(rt, st, "open the most relevant file you found", mode="plan_execute")
        assert out2.get("status") in ("success", "failed")
        tr = out2.get("trace")
        assert tr is not None
        text = json.dumps(tr.model_dump(mode="json"), default=str)
        assert ".py" in text


# --- Phase 3: exploration + planner loop ---------------------------------------------------


class TestPhase3ExplorationPlannerLoop:
    def test_exploration_scoper_question_progresses(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        result = rt.run(
            "how does exploration scope layer work in this repo",
            mode="plan_execute",
        )
        assert result.get("status") in ("success", "failed")
        st = result["state"]
        assert st.exploration_result is not None
        tr = result.get("trace")
        assert tr is not None
        assert len(tr.steps) >= 1


# --- Phase 4: explore cap override --------------------------------------------------------


class TestPhase4ExploreCapOverride:
    def test_after_three_explore_decisions_override_forces_act_search_code(
        self, require_live, project_root, monkeypatch
    ):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime
        from agent_v2.runtime.exploration_planning_input import call_planner_with_context
        from agent_v2.runtime.session_memory import SessionMemory
        from agent_v2.schemas.planner_plan_context import PlannerPlanContext

        rt = create_runtime()
        exploration = rt.explore("ExplorationScoper class in agent_v2")
        mem = SessionMemory()
        for _ in range(3):
            mem.record_planner_output(decision="explore", tool="explore")
        state = AgentState(
            instruction=(
                "Output decision explore with tool explore once (query: ExplorationScoper). "
                "Session already used three explores; system will override if you explore again."
            )
        )
        state.metadata["langfuse_trace"] = create_agent_trace(
            instruction=state.instruction, mode="plan"
        )
        state.metadata["obs"] = ObservabilityContext(
            langfuse_trace=state.metadata["langfuse_trace"], owns_root=False
        )
        pctx = PlannerPlanContext(exploration=exploration, session=mem)
        plan = call_planner_with_context(
            rt.mode_manager.planner,
            state.instruction,
            pctx,
            deep=False,
            obs=state.metadata.get("obs"),
            langfuse_trace=state.metadata.get("langfuse_trace"),
            require_controller_json=True,
            session=mem,
        )
        eng = plan.engine
        assert eng is not None
        assert eng.decision == "act"
        assert eng.tool == "search_code"
        assert "explore_cap_override" in (eng.reason or "")


# --- Phase 5: failure handling ------------------------------------------------------------


class TestPhase5FailureScenarios:
    def test_open_missing_file_structured_failure(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.schemas.plan import (
            PlanDocument,
            PlanMetadata,
            PlanRisk,
            PlanSource,
            PlanStep,
            PlanStepExecution,
            PlanStepFailure,
            PlannerEngineOutput,
            PlannerEngineStepSpec,
        )
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        engine = PlannerEngineOutput(
            decision="act",
            tool="open_file",
            reason="open missing",
            query="",
            step=PlannerEngineStepSpec(
                action="open_file",
                input="agent_v2/runtime/__file_does_not_exist_zz__.py",
                metadata={},
            ),
        )
        steps = [
            PlanStep(
                step_id="s1",
                index=1,
                type="analyze",
                goal="open",
                action="open_file",
                inputs={
                    "path": "agent_v2/runtime/__file_does_not_exist_zz__.py",
                },
                dependencies=[],
                execution=PlanStepExecution(max_attempts=2),
                failure=PlanStepFailure(),
            ),
            PlanStep(
                step_id="s2",
                index=2,
                type="finish",
                goal="done",
                action="finish",
                dependencies=["s1"],
                execution=PlanStepExecution(max_attempts=2),
                failure=PlanStepFailure(),
            ),
        ]
        plan = PlanDocument(
            plan_id="integ_fail_open",
            instruction="open missing",
            understanding="u",
            sources=[PlanSource(type="file", ref="x", summary="s")],
            steps=steps,
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["c"],
            metadata=PlanMetadata(created_at="2026-01-01T00:00:00Z", version=1),
            engine=engine,
        )
        st = AgentState(instruction="open missing")
        st.context["project_root"] = str(project_root)
        st.context["react_mode"] = True
        st.current_plan = plan.model_dump(mode="json")
        st.metadata["langfuse_trace"] = create_agent_trace(instruction=st.instruction, mode="plan_execute")
        st.metadata["obs"] = ObservabilityContext(
            langfuse_trace=st.metadata["langfuse_trace"], owns_root=False
        )
        from agent_v2.runtime.trace_context import clear_active_trace_emitter, set_active_trace_emitter
        from agent_v2.runtime.trace_emitter import TraceEmitter

        te = TraceEmitter()
        te.reset()
        set_active_trace_emitter(te)
        try:
            out = rt.plan_executor.run(plan, st, trace_emitter=te)
        finally:
            clear_active_trace_emitter()
        assert out.get("status") == "failed"
        tr = out.get("trace")
        assert tr is not None
        failed = [s for s in tr.steps if not s.success]
        assert failed, "expected at least one failed trace step for missing file"
        assert failed[0].error is not None
        assert str(failed[0].error.message or "").strip()


# --- Phase 6: end-to-end --------------------------------------------------------------------


class TestPhase6EndToEnd:
    def test_exploration_scoper_query_no_infinite_loop(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        result = rt.run("what does ExplorationScoper do", mode="plan_execute")
        assert result.get("status") in ("success", "failed")
        tr = result.get("trace")
        assert tr is not None
        assert len(tr.steps) <= 80

    def test_search_then_open_chain(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        result = rt.run(
            "find auth-related code in agent_v2 then open the best matching file",
            mode="plan_execute",
        )
        assert result.get("status") in ("success", "failed")
        tr = result.get("trace")
        assert tr is not None
        actions = [s.action for s in tr.steps if getattr(s, "kind", "tool") == "tool"]
        assert "search" in actions or "open_file" in actions

    def test_vague_fix_after_planner_find(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        out1 = rt.run("find planner_v2.py in the repo", mode="plan_execute")
        st = out1["state"]
        out2 = _continue_agent_run(rt, st, "fix it", mode="plan_execute")
        assert out2.get("status") in ("success", "failed")

    def test_delete_instruction_plan_mode_shell_blocked(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        instruction = (
            "Delete files: use run_shell with command `rm -rf /tmp/autostudio_safe_delete_test`"
        )
        try:
            out = rt.run(instruction, mode="plan")
        except PlanValidationError as e:
            m = str(e).lower()
            assert "tool policy" in m or "rm" in m or "shell" in m or "first command token" in m
            return
        st = out["state"]
        plan = st.current_plan
        assert isinstance(plan, dict)
        eng = plan.get("engine")
        if isinstance(eng, dict) and eng.get("decision") == "act" and eng.get("tool") == "run_shell":
            step = eng.get("step") or {}
            cmd = (
                str(step.get("input") or "")
                or str((step.get("metadata") or {}).get("command") or "")
            ).lower()
            assert "rm" not in cmd, "plan-mode shell must not synthesize rm/delete commands"


# --- Phase 7: logging -----------------------------------------------------------------------


class TestPhase7LoggingVerification:
    def test_planner_telemetry_and_tool_execution_present(self, require_live, project_root, monkeypatch, caplog):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        caplog.set_level(logging.INFO)
        for name in (
            "agent_v2.planner.planner_v2",
            "agent_v2.runtime.plan_executor",
        ):
            caplog.set_level(logging.INFO, logger=name)

        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        rt.run(
            "Where is PlanExecutor class defined? Use search then open_file.",
            mode="plan_execute",
        )
        assert _collect_planner_telemetry_payloads(caplog)
        assert _collect_tool_execution_payloads(caplog)

    def test_tool_execution_count_matches_tool_trace_steps_when_no_retries(
        self, require_live, project_root, monkeypatch, caplog
    ):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        monkeypatch.delenv("AGENT_V2_FAULT_OPEN_FILE_ONCE", raising=False)
        monkeypatch.delenv("AGENT_V2_FAULT_OPEN_FILE_HARD_UNTIL_REPLAN", raising=False)
        caplog.set_level(logging.INFO)
        caplog.set_level(logging.INFO, logger="agent_v2.runtime.plan_executor")

        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        result = rt.run(
            "Open agent_v2/runtime/plan_executor.py only (open_file).",
            mode="plan_execute",
        )
        tr = result.get("trace")
        assert tr is not None
        tool_steps = [s for s in tr.steps if getattr(s, "kind", "tool") == "tool" and s.action != "finish"]
        payloads = _collect_tool_execution_payloads(caplog)
        # One log line per dispatch attempt; successful runs without retries align 1:1 with tool steps.
        assert len(payloads) >= len(tool_steps)
        assert len(payloads) <= len(tool_steps) * 3
