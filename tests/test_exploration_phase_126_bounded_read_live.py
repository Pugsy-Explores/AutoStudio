"""
Phase 12.6.D — Bounded exploration reads (read_snippet, bound-before-I/O).

Offline integration (default CI):
  - Real Dispatcher → _dispatch_react, real repo search + bounded read router
  - No LLM (llm_generate_fn=None → heuristics only)
  - Asserts inspection uses read_snippet, never open_file, and full read_file() is not used

Live (optional; same gap checks with real LLM):
  export AGENT_V2_LIVE=1
  pytest tests/test_exploration_phase_126_bounded_read_live.py -v -m agent_v2_live
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

# ReAct registry must be populated before _dispatch_react (same as runtime entrypoints).
from agent.tools.react_tools import register_all_tools

register_all_tools()

from agent.tools import filesystem_adapter
from agent_v2.schemas.final_exploration import FinalExplorationSchema
from agent_v2.runtime.dispatcher import Dispatcher


def _wire_capture_dispatcher(runtime, dispatcher: Dispatcher) -> None:
    """
    Exploration has nested dispatcher references captured at construction time.
    Wire all of them so action capture is reliable in live tests.
    """
    runtime.dispatcher = dispatcher
    runtime.exploration_runner.dispatcher = dispatcher
    engine = getattr(runtime.exploration_runner, "_engine_v2", None)
    if engine is not None:
        if hasattr(engine, "_dispatcher"):
            engine._dispatcher = dispatcher
        reader = getattr(engine, "_inspection_reader", None)
        if reader is not None and hasattr(reader, "_dispatcher"):
            reader._dispatcher = dispatcher
        expander = getattr(engine, "_graph_expander", None)
        if expander is not None and hasattr(expander, "_dispatcher"):
            expander._dispatcher = dispatcher


def _autostudio_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _agent_v2_live_requested() -> bool:
    return os.getenv("AGENT_V2_LIVE", "").lower() in ("1", "true", "yes")


@pytest.fixture
def project_root(monkeypatch) -> Path:
    root = _autostudio_root()
    monkeypatch.chdir(root)
    monkeypatch.setenv("SERENA_PROJECT_DIR", str(root))
    return root


@pytest.fixture
def llm_reachable() -> bool:
    if not _agent_v2_live_requested():
        return False
    try:
        from agent.models.model_client import call_reasoning_model

        out = call_reasoning_model(
            "Reply with exactly OK.",
            max_tokens=16,
            task_name="PLANNER_DECISION_ACT",
        )
        return bool(out and str(out).strip())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"AGENT_V2_LIVE set but reasoning model unreachable: {exc}")


@pytest.fixture
def require_live(llm_reachable: bool):
    if not _agent_v2_live_requested():
        pytest.skip("Set AGENT_V2_LIVE=1 to run live Phase 12.6.D tests")
    if not llm_reachable:
        pytest.skip("LLM probe failed")


def _make_runner_with_capture(monkeypatch, project_root: Path):
    """ExplorationRunner with real ReAct dispatch; records _react_action_raw per step."""
    monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
    monkeypatch.setenv("AGENT_V2_ENABLE_EXPLORATION_ENGINE_V2", "1")

    from agent.execution.step_dispatcher import _dispatch_react
    from agent_v2.runtime.bootstrap import _exploration_action_fn, _next_action
    from agent_v2.runtime.action_generator import ActionGenerator
    from agent_v2.runtime.exploration_runner import ExplorationRunner

    captured: list[str] = []

    def _capture_execute(step: dict, state: Any) -> dict:
        raw = (step.get("_react_action_raw") or "").strip()
        if raw:
            captured.append(raw)
        # _dispatch_react now returns ExecutionResult; convert to dict for test compatibility
        result = _dispatch_react(step, state)
        return result.model_dump()

    runner = ExplorationRunner(
        action_generator=ActionGenerator(fn=_next_action, exploration_fn=_exploration_action_fn),
        dispatcher=Dispatcher(execute_fn=_capture_execute),
        llm_generate_fn=None,
        enable_v2=True,
    )
    return runner, captured


def _assert_phase_126_gaps_fixed(
    captured: list[str],
    full_read_paths: list[str],
    exploration: FinalExplorationSchema,
) -> None:
    """Phase 12.6.D merged RCA: unified bounded read; no full-file open_file in exploration."""
    assert "open_file" not in captured, (
        "Exploration must not dispatch open_file for inspection (use read_snippet). "
        f"Captured: {captured}"
    )
    assert "read_snippet" in captured, (
        "Inspection must dispatch read_snippet for bounded reads. "
        f"Captured: {captured}"
    )
    assert not full_read_paths, (
        "filesystem_adapter.read_file (full read) must not run during bounded exploration. "
        f"Got: {full_read_paths}"
    )
    assert isinstance(exploration, FinalExplorationSchema)
    assert exploration.metadata.total_items == len(exploration.evidence)
    tool_names = {it.metadata.tool_name for it in exploration.evidence}
    assert "read_snippet" in tool_names, f"Expected read_snippet in item tool names, got {tool_names}"
    assert "open_file" not in tool_names, f"open_file must not appear in exploration items, got {tool_names}"


@pytest.mark.timeout(120)
class TestPhase126BoundedReadIntegration:
    """Offline: real tools, no LLM — verifies gaps from Phase 12.6.D RCA are fixed."""

    def test_v2_exploration_uses_read_snippet_not_open_file_no_full_read(
        self, monkeypatch, project_root
    ):
        full_reads: list[str] = []
        orig_read_file = filesystem_adapter.read_file

        def _trap_full_read(path: str) -> str:
            full_reads.append(path)
            return orig_read_file(path)

        monkeypatch.setattr(filesystem_adapter, "read_file", _trap_full_read)

        runner, captured = _make_runner_with_capture(monkeypatch, project_root)
        exploration = runner.run(
            "Locate ModeManager class definition in agent_v2 (file agent_v2/runtime/mode_manager.py)"
        )
        _assert_phase_126_gaps_fixed(captured, full_reads, exploration)


@pytest.mark.timeout(900)
@pytest.mark.slow
@pytest.mark.agent_v2_live
class TestPhase126BoundedReadLive:
    """Live: same gap checks with real LLM + tools (optional)."""

    def test_live_v2_bounded_read_gaps_fixed(self, require_live, monkeypatch, project_root):
        full_reads: list[str] = []
        orig_read_file = filesystem_adapter.read_file

        def _trap_full_read(path: str) -> str:
            full_reads.append(path)
            return orig_read_file(path)

        monkeypatch.setattr(filesystem_adapter, "read_file", _trap_full_read)

        monkeypatch.setenv("SKIP_STARTUP_CHECKS", "1")
        monkeypatch.setenv("AGENT_V2_ENABLE_EXPLORATION_ENGINE_V2", "1")

        captured: list[str] = []

        from agent.execution.step_dispatcher import _dispatch_react
        from agent_v2.runtime.bootstrap import create_runtime

        rt = create_runtime()

def _capture_execute(step: dict, state: Any) -> dict:
        raw = (step.get("_react_action_raw") or "").strip()
        if raw:
            captured.append(raw)
        # _dispatch_react now returns ExecutionResult; convert to dict for test compatibility
        result = _dispatch_react(step, state)
        return result.model_dump()

        capture_dispatcher = Dispatcher(execute_fn=_capture_execute)
        _wire_capture_dispatcher(rt, capture_dispatcher)

        exploration = rt.explore(
            "Find where ModeManager or ExplorationRunner is defined under agent_v2; use repo context."
        )
        _assert_phase_126_gaps_fixed(captured, full_reads, exploration)
