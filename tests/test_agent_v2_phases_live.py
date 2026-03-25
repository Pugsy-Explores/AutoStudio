"""
agent_v2 Phases 1–9 — offline smoke + optional live integration (LLM, retrieval, dispatch).

Offline (default CI):
  pytest tests/test_agent_v2_phases_live.py -v -m "not agent_v2_live"

Live (real reasoning model + tools; run from repo root with endpoints configured):
  export AGENT_V2_LIVE=1
  export SKIP_STARTUP_CHECKS=1          # optional: skip retrieval daemon auto-start in entrypoints
  cd /path/to/AutoStudio && pytest tests/test_agent_v2_phases_live.py -v -m agent_v2_live

Recommended for full stack (daemon + LLM):
  AGENT_V2_LIVE=1 pytest tests/test_agent_v2_phases_live.py -v -m agent_v2_live --tb=short

Fault injection (Dispatcher, see agent_v2/runtime/fault_hooks.py):
  AGENT_V2_FAULT_OPEN_FILE_ONCE=1 — first open_file fails once (Phase 6 retry), then real tool runs.
  AGENT_V2_FAULT_OPEN_FILE_HARD_UNTIL_REPLAN=1 — first open_file step fails all tries until replan
    (replan_attempt metadata > 0 disables injection); covered offline in tests/test_dispatcher_fault_injection.py.
  Opt-in live fault smoke: AGENT_V2_LIVE=1 AGENT_V2_RUN_FAULT_LIVE=1 pytest ... -k fault_injection

Live suite is slow (~minutes): plan_execute / act paths run exploration + planner + executor.
Each class is marked with a 15-minute timeout when pytest-timeout is installed.

Phase map:
  1 — Pydantic schemas: round-trip JSON / invariants
  2 — Dispatcher: ToolResult → ExecutionResult (live: real SEARCH dispatch)
  3 — ExplorationRunner: bounded read-only loop + ExplorationResult
  12.5 / 12.6.D — ExplorationEngineV2: staged loop + Schema 4 (live: real LLM + search + read_snippet for inspection)
  4 — PlannerV2 + PlanValidator → PlanDocument
  5 — plan_execute: exploration → plan → PlanExecutor (LLM arg gen + tools)
  6 — Per-step retry (final outcome only in trace); tests: test_plan_executor.py, test_dispatcher_fault_injection.py
  7 — Replanner loop; tests: test_replanner.py + fault_hooks HARD path in test_dispatcher_fault_injection.py
  8 — ModeManager: act/plan_execute vs plan/deep_plan (executor skipped on plan-only modes; plan/deep_plan still emit Phase 13 LLM trace)
  9 — TraceEmitter → Trace / TraceStep JSON-serializable observability
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from agent_v2.schemas.execution import ExecutionResult
from agent_v2.schemas.exploration import (
    ExplorationResult,
    ExplorationResultMetadata,
    ExplorationSummary,
)
from agent_v2.schemas.plan import PlanDocument, PlanMetadata, PlanRisk, PlanSource, PlanStep
from agent_v2.schemas.trace import Trace


# ---------------------------------------------------------------------------
# Paths & gates
# ---------------------------------------------------------------------------


def _autostudio_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _agent_v2_live_requested() -> bool:
    return os.getenv("AGENT_V2_LIVE", "").lower() in ("1", "true", "yes")


@pytest.fixture(scope="module")
def llm_reachable() -> bool:
    """Single probe per module when live mode requested; skips all live tests if unreachable."""
    if not _agent_v2_live_requested():
        return False
    try:
        from agent.models.model_client import call_reasoning_model

        out = call_reasoning_model(
            'Reply with exactly the two letters OK and nothing else.',
            max_tokens=16,
            task_name="PLANNER_V2",
        )
        return bool(out and str(out).strip())
    except Exception as exc:  # noqa: BLE001 — surface as skip reason
        pytest.skip(f"AGENT_V2_LIVE set but reasoning model unreachable: {exc}")


@pytest.fixture
def require_live(llm_reachable: bool):
    if not _agent_v2_live_requested():
        pytest.skip("Set AGENT_V2_LIVE=1 to run live agent_v2 tests")
    if not llm_reachable:
        pytest.skip("LLM probe failed (see llm_reachable fixture)")


@pytest.fixture
def project_root(monkeypatch) -> Path:
    root = _autostudio_root()
    monkeypatch.chdir(root)
    monkeypatch.setenv("SERENA_PROJECT_DIR", str(root))
    return root


def _coerce_trace(obj: Any) -> Trace:
    return obj if isinstance(obj, Trace) else Trace.model_validate(obj)


def _assert_exploration_result_schema4(exp: ExplorationResult) -> None:
    """Schema 4 invariants used by planner input (items cap, gaps vs empty_reason, relevance)."""
    assert len(exp.items) <= 6
    assert exp.metadata.total_items == len(exp.items)
    if exp.summary.knowledge_gaps:
        assert exp.summary.knowledge_gaps_empty_reason is None
    else:
        assert exp.summary.knowledge_gaps_empty_reason
        assert str(exp.summary.knowledge_gaps_empty_reason).strip()
    for it in exp.items:
        assert it.source.ref.strip()
        assert it.content.summary.strip()
        assert 0.0 <= it.relevance.score <= 1.0
        assert it.metadata.tool_name in ("search", "open_file", "read_snippet", "shell", "other")


def _fault_live_requested() -> bool:
    return os.getenv("AGENT_V2_RUN_FAULT_LIVE", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Phase 1 — schemas (always on)
# ---------------------------------------------------------------------------


class TestPhase1Schemas:
    def test_plan_document_roundtrip_json(self):
        doc = PlanDocument(
            plan_id="live_p1",
            instruction="smoke",
            understanding="u",
            sources=[PlanSource(type="file", ref="agent_v2/runtime/plan_executor.py", summary="s")],
            steps=[
                PlanStep(
                    step_id="s1",
                    index=1,
                    type="analyze",
                    goal="g",
                    action="open_file",
                ),
                PlanStep(
                    step_id="s2",
                    index=2,
                    type="finish",
                    goal="done",
                    action="finish",
                    dependencies=["s1"],
                ),
            ],
            risks=[PlanRisk(risk="r", impact="low", mitigation="m")],
            completion_criteria=["done"],
            metadata=PlanMetadata(created_at="2026-03-25T00:00:00Z", version=1),
        )
        raw = doc.model_dump(mode="json")
        blob = json.dumps(raw)
        restored = PlanDocument.model_validate_json(blob)
        assert restored.plan_id == doc.plan_id
        assert len(restored.steps) == 2
        assert restored.steps[-1].action == "finish"

    def test_exploration_summary_invariant_empty_gaps(self):
        s = ExplorationSummary(
            overall="o",
            key_findings=["a"],
            knowledge_gaps=[],
            knowledge_gaps_empty_reason="none",
        )
        assert s.knowledge_gaps_empty_reason == "none"


# ---------------------------------------------------------------------------
# Phase 2 — dispatcher + real SEARCH (no LLM; may still use retrieval stack)
# ---------------------------------------------------------------------------


class TestPhase2DispatcherRetrieval:
    def test_dispatcher_search_returns_execution_result(self, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime
        from agent_v2.state.agent_state import AgentState

        rt = create_runtime()
        state = AgentState(instruction="phase2 search smoke")
        state.context["react_mode"] = True
        state.context["project_root"] = str(project_root)

        step: dict[str, Any] = {
            "id": 1,
            "action": "SEARCH",
            "artifact_mode": "code",
            "_react_thought": "",
            "_react_action_raw": "search",
            "_react_args": {"query": "PlanExecutor class"},
            "query": "PlanExecutor class",
            "description": "PlanExecutor class",
        }
        result = rt.dispatcher.execute(step, state)
        assert isinstance(result, ExecutionResult)
        assert result.step_id == "1"
        assert result.output is not None
        assert isinstance(result.output.summary, str)
        assert len(result.output.summary) > 0


# ---------------------------------------------------------------------------
# Phases 3–5 — live LLM + pipeline
# ---------------------------------------------------------------------------


@pytest.mark.timeout(900)
@pytest.mark.slow
@pytest.mark.agent_v2_live
class TestPhase3ExplorationLive:
    def test_exploration_returns_valid_exploration_result(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        exp = rt.explore("Find where PlanExecutor is defined in agent_v2")
        assert isinstance(exp, ExplorationResult)
        assert exp.exploration_id
        assert exp.metadata.total_items == len(exp.items)
        assert len(exp.items) <= 6
        assert exp.summary.overall
        # Read-only: no edit/run_tests in exploration item tool names
        for it in exp.items:
            assert it.metadata.tool_name in ("search", "open_file", "read_snippet", "shell", "other"), it.metadata.tool_name


@pytest.mark.timeout(900)
@pytest.mark.slow
@pytest.mark.agent_v2_live
class TestPhase125ExplorationEngineV2Live:
    """
    Phase 12.5 — ExplorationEngineV2 behind ExplorationRunner (real reasoning model + dispatcher).

    Run subset: AGENT_V2_LIVE=1 pytest ... -k Phase125
    """

    def test_exploration_v2_live_schema4_and_read_only_tools(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        monkeypatch.setenv("AGENT_V2_ENABLE_EXPLORATION_ENGINE_V2", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        exp = rt.explore(
            "Find where ExplorationRunner or ModeManager is defined under agent_v2; use repo search context."
        )
        assert isinstance(exp, ExplorationResult)
        assert exp.exploration_id.startswith("exp_")
        assert exp.instruction
        _assert_exploration_result_schema4(exp)
        for it in exp.items:
            assert it.metadata.tool_name in ("search", "open_file", "read_snippet", "shell", "other"), it.metadata.tool_name

    def test_exploration_legacy_live_when_v2_flag_off(self, require_live, project_root, monkeypatch):
        """Cutover: AGENT_V2_ENABLE_EXPLORATION_ENGINE_V2=0 uses Phase 3 ReAct exploration loop."""
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        monkeypatch.setenv("AGENT_V2_ENABLE_EXPLORATION_ENGINE_V2", "0")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        exp = rt.explore("Find PlanExecutor class in agent_v2")
        assert isinstance(exp, ExplorationResult)
        assert len(exp.items) <= 6
        assert exp.metadata.total_items == len(exp.items)
        if exp.summary.knowledge_gaps:
            assert exp.summary.knowledge_gaps_empty_reason is None
        else:
            assert exp.summary.knowledge_gaps_empty_reason


@pytest.mark.timeout(900)
@pytest.mark.slow
@pytest.mark.agent_v2_live
class TestPhase4PlannerLive:
    def test_planner_produces_valid_plan_document(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        exploration = rt.explore("Locate agent_v2/runtime/plan_executor.py for reading only")
        plan = rt.mode_manager.planner.plan(
            instruction="Explain how PlanExecutor runs steps in order",
            deep=False,
            exploration=exploration,
        )
        assert isinstance(plan, PlanDocument)
        assert plan.understanding
        assert len(plan.steps) <= 8
        assert any(s.action == "finish" for s in plan.steps)
        assert plan.steps[-1].type == "finish"
        assert plan.steps[-1].action == "finish"


@pytest.mark.timeout(900)
@pytest.mark.slow
@pytest.mark.agent_v2_live
class TestPhase5PlanExecuteLive:
    def test_plan_execute_runs_without_crash(self, require_live, project_root, monkeypatch):
        """
        End-to-end: exploration → PlannerV2 → PlanExecutor (arg LLM + tools).

        Covers Phase 5–6–9 on the execute path: executor + retries (6) collapse to one TraceStep
        per plan step (9). Assertions stay soft on planner/tool content; strict on contracts.
        """
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        result = rt.run(
            "Where is class PlanExecutor defined? Answer using search and open_file only.",
            mode="plan_execute",
        )
        assert isinstance(result, dict)
        assert result.get("status") in ("success", "failed")
        state = result["state"]
        trace = _coerce_trace(result["trace"])
        assert trace.instruction
        assert trace.plan_id
        assert trace.status in ("success", "failure")
        assert trace.metadata.total_steps == len(trace.steps)
        assert trace.metadata.total_duration_ms >= 0
        for step in trace.steps:
            assert step.step_id
            assert step.plan_step_index >= 1
            assert step.action
            assert step.duration_ms >= 0
            if step.success:
                assert step.error is None
            else:
                assert step.error is not None
                assert step.error.message
        blob = json.dumps(trace.model_dump(mode="json"))
        assert "trace_id" in blob
        # Overall trace status matches per-step success (Phase 9 contract)
        assert trace.status == (
            "success" if trace.steps and all(s.success for s in trace.steps) else "failure"
        )

        assert state.current_plan is not None
        plan = state.current_plan
        assert isinstance(plan, dict)
        assert plan.get("plan_id")
        assert plan.get("steps")
        steps = plan.get("steps") or []
        assert len(steps) >= 1
        assert state.history is not None
        assert isinstance(state.history, list)


@pytest.mark.timeout(900)
@pytest.mark.slow
@pytest.mark.agent_v2_live
class TestPhase8ModesLive:
    """Phase 8 — plan / deep_plan skip PlanExecutor. Phase 13 — trace + graph from explore + planner LLMs."""

    def test_plan_mode_includes_trace_and_graph(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        out = rt.run(
            "List what lives under agent_v2/runtime/ (exploration only; no execution).",
            mode="plan",
        )
        assert isinstance(out, dict)
        assert out.get("trace") is not None
        assert out.get("graph") is not None
        assert "state" in out
        st = out["state"]
        assert st.metadata.get("trace") is out["trace"]
        assert st.metadata.get("execution_trace_id") == _coerce_trace(out["trace"]).trace_id
        tr = _coerce_trace(out["trace"])
        assert tr.plan_id
        assert len(tr.steps) >= 1
        assert any(getattr(s, "kind", "tool") == "llm" for s in tr.steps)
        assert st.exploration_result is not None
        assert st.current_plan is not None
        cp = st.current_plan
        assert isinstance(cp, dict) and cp.get("steps")

    def test_deep_plan_mode_includes_trace_and_graph(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        out = rt.run(
            "Plan how you would trace execution in agent_v2 (risks and steps only, no run).",
            mode="deep_plan",
        )
        assert isinstance(out, dict)
        assert out.get("trace") is not None
        assert out.get("graph") is not None
        st = out["state"]
        assert st.metadata.get("trace") is out["trace"]
        tr = _coerce_trace(out["trace"])
        assert any(getattr(s, "kind", "tool") == "llm" for s in tr.steps)
        assert st.current_plan is not None
        doc = st.current_plan
        assert isinstance(doc, dict)
        assert doc.get("steps")
        # Deep planning should surface at least minimal risk awareness when the model complies
        risks = doc.get("risks") or []
        assert isinstance(risks, list)

    def test_act_mode_returns_same_shape_as_plan_execute(self, require_live, project_root, monkeypatch):
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        result = rt.run(
            "Where is TraceEmitter defined? Use search then open_file only.",
            mode="act",
        )
        assert isinstance(result, dict)
        assert result.get("status") in ("success", "failed")
        assert result.get("trace") is not None
        tr = _coerce_trace(result["trace"])
        assert tr.metadata.total_steps == len(tr.steps)


@pytest.mark.timeout(900)
@pytest.mark.slow
@pytest.mark.agent_v2_live
class TestPhase67FaultInjectionLiveOptional:
    """
    Requires AGENT_V2_RUN_FAULT_LIVE=1 in addition to AGENT_V2_LIVE=1.
    Exercises real LLM + tools with Dispatcher fault_hooks (no flake from forcing bad paths in the model).
    """

    def test_open_file_once_fault_then_success(self, require_live, project_root, monkeypatch):
        if not _fault_live_requested():
            pytest.skip("Set AGENT_V2_RUN_FAULT_LIVE=1 to run fault-injection live tests")
        monkeypatch.setenv("AGENT_V2_FAULT_OPEN_FILE_ONCE", "1")
        monkeypatch.delenv("AGENT_V2_FAULT_OPEN_FILE_HARD_UNTIL_REPLAN", raising=False)
        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()
        result = rt.run(
            "Where is class PlanExecutor defined? Answer using search and open_file only.",
            mode="plan_execute",
        )
        st = result["state"]
        assert int(st.metadata.get("agent_v2_fault_inject_count", 0)) >= 1
        assert result.get("status") == "success"
        tr = _coerce_trace(result["trace"])
        assert tr.status == "success"
