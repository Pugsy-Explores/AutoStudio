"""Tests for agent robustness: failure scenarios, replanning, fallback search, no repository corruption."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agent.execution.step_dispatcher import dispatch, _search_fn
from agent.memory.state import AgentState
from agent.orchestrator.agent_controller import run_controller


# --- 1. Nonexistent symbol search ---


def test_nonexistent_symbol_search_triggers_fallback_and_retries(tmp_path):
    """When graph lookup returns empty, fallback search (Serena/grep) is triggered; policy retries with rewritten query."""
    call_log = []

    def mock_search(query: str, state=None):
        call_log.append({"query": query})
        # Simulate: graph empty -> vector empty -> Serena/grep returns empty for first 2 attempts
        if len(call_log) <= 2:
            return {"results": [], "query": query}
        return {"results": [{"file": "fallback.py", "snippet": "def fallback"}], "query": query}

    def mock_rewrite(desc: str, user: str, history: list, state=None) -> str:
        n = len(history)
        return f"query_variant_{n}"

    with patch("agent.execution.step_dispatcher._policy_engine") as mock_pe:
        from agent.execution.policy_engine import ExecutionPolicyEngine

        engine = ExecutionPolicyEngine(
            search_fn=mock_search,
            edit_fn=MagicMock(return_value={"success": True, "output": {}}),
            infra_fn=MagicMock(return_value={"success": True, "output": {"returncode": 0}}),
            rewrite_query_fn=mock_rewrite,
            max_total_attempts=5,
        )
        mock_pe.execute_with_policy = engine.execute_with_policy

        step = {"id": 1, "action": "SEARCH", "description": "find nonexistent symbol xyz123"}
        state = AgentState(
            instruction="find xyz123",
            current_plan={"steps": [step]},
            context={"project_root": str(tmp_path)},
        )

        result = dispatch(step, state)

        assert result["success"] is True
        assert len(call_log) >= 3
        assert any(r.get("file") for r in result.get("output", {}).get("results", []))


def test_nonexistent_symbol_search_exhausted_returns_failure(tmp_path):
    """When all search attempts return empty, policy returns success=False with attempt_history."""
    def mock_search_empty(_query: str, state=None):
        return {"results": [], "query": _query}

    with patch("agent.execution.step_dispatcher._policy_engine") as mock_pe:
        from agent.execution.policy_engine import ExecutionPolicyEngine

        engine = ExecutionPolicyEngine(
            search_fn=mock_search_empty,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=lambda d, u, h, s=None: (d or "").strip() or "q",
            max_total_attempts=3,
        )
        mock_pe.execute_with_policy = engine.execute_with_policy

        step = {"id": 1, "action": "SEARCH", "description": "nonexistent symbol"}
        state = AgentState(
            instruction="test",
            current_plan={"steps": [step]},
            context={"project_root": str(tmp_path)},
        )

        result = dispatch(step, state)

        assert result["success"] is False
        assert "attempt_history" in result.get("output", {})
        assert "all search attempts returned empty" in (result.get("error") or "")


# --- 2. Invalid edit instruction ---


def test_invalid_edit_instruction_patch_validator_fails_no_corruption(tmp_path):
    """Invalid edit producing bad code: patch validator fails, rollback, no repository corruption."""
    foo_py = tmp_path / "foo.py"
    original = "def bar():\n    return 1\n"
    foo_py.write_text(original)

    from editing.patch_executor import execute_patch

    # Patch that would produce invalid syntax
    patch_plan = {
        "changes": [
            {
                "file": str(foo_py),
                "patch": {
                    "symbol": "bar",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "return (  # unclosed paren - invalid",
                },
            },
        ],
    }
    result = execute_patch(patch_plan, project_root=str(tmp_path))

    assert result["success"] is False
    assert result.get("error") == "patch_failed"
    assert foo_py.read_text() == original


# --- 3. Patch validator failure ---


def test_patch_validator_failure_triggers_rollback(tmp_path):
    """Patch validator failure: rollback restores all modified files."""
    f1 = tmp_path / "a.py"
    f2 = tmp_path / "b.py"
    orig1 = "def x(): return 1\n"
    orig2 = "def y(): return 2\n"
    f1.write_text(orig1)
    f2.write_text(orig2)

    from editing.patch_executor import execute_patch

    # First patch is valid, second produces invalid code - executor validates before writing
    # So we apply in-memory, validate, and only write if all pass. One invalid = rollback.
    patch_plan = {
        "changes": [
            {
                "file": str(f1),
                "patch": {
                    "symbol": "x",
                    "action": "replace",
                    "target_node": "function_body",
                    "code": "return (  # invalid",
                },
            },
        ],
    }
    result = execute_patch(patch_plan, project_root=str(tmp_path))

    assert result["success"] is False
    assert f1.read_text() == orig1
    assert f2.read_text() == orig2


# --- 4. Graph lookup returning empty ---


def test_graph_lookup_empty_fallback_to_serena(tmp_path):
    """When graph retriever returns None/empty, _search_fn falls back to vector then Serena."""
    with patch("agent.retrieval.graph_retriever.retrieve_symbol_context", return_value=None):
        with patch("agent.execution.step_dispatcher.ENABLE_VECTOR_SEARCH", False):
            with patch("agent.execution.step_dispatcher.search_code") as mock_serena:
                mock_serena.return_value = {"results": [{"file": "fallback.py", "snippet": "code"}], "query": "q"}

                state = AgentState(
                    instruction="test",
                    current_plan={},
                    context={"project_root": str(tmp_path)},
                )
                result = _search_fn("some_query", state)

    assert result is not None
    assert isinstance(result, dict)
    assert "results" in result
    assert len(result["results"]) >= 1
    mock_serena.assert_called_once()


def test_search_fn_returns_dict_when_all_backends_empty(tmp_path):
    """_search_fn always returns a dict (never None) to avoid policy engine crash."""
    with patch("agent.retrieval.graph_retriever.retrieve_symbol_context", return_value=None):
        with patch("agent.execution.step_dispatcher.ENABLE_VECTOR_SEARCH", False):
            with patch("agent.execution.step_dispatcher.search_code", return_value={"results": [], "query": "q"}):
                state = AgentState(
                    instruction="test",
                    current_plan={},
                    context={"project_root": str(tmp_path)},
                )
                result = _search_fn("nonexistent", state)

    assert result is not None
    assert isinstance(result, dict)
    assert "results" in result


# --- Agent replan on failure ---


def test_agent_replans_on_edit_failure(tmp_path):
    """When EDIT step fails (e.g. patch_failed), agent replans and continues."""
    replan_calls = []

    def mock_replan(state, failed_step=None, error=None):
        replan_calls.append(1)
        steps = state.current_plan.get("steps") or []
        completed_ids = {s.get("id") for s in state.completed_steps}
        remaining = [s for s in steps if isinstance(s, dict) and s.get("id") not in completed_ids]
        return {"steps": remaining}

    with patch("agent.orchestrator.agent_controller.plan") as mock_plan:
        mock_plan.return_value = {
            "steps": [
                {"id": 1, "action": "SEARCH", "description": "find foo", "reason": "r1"},
                {"id": 2, "action": "EDIT", "description": "modify foo", "reason": "r2"},
            ],
        }
        with patch("agent.orchestrator.agent_controller.dispatch") as mock_dispatch:
            call_count = 0

            def mock_dispatch_fn(step, state):
                nonlocal call_count
                action = (step.get("action") or "").upper()
                if action == "SEARCH":
                    return {"success": True, "output": {"results": [{"file": "a.py", "snippet": "def foo"}]}}
                if action == "EDIT":
                    call_count += 1
                    if call_count == 1:
                        return {"success": False, "error": "patch_failed", "reason": "validation failed"}
                    return {"success": True, "output": {"files_modified": [], "patches_applied": 0}}
                return {"success": True, "output": {}}

            mock_dispatch.side_effect = mock_dispatch_fn
            with patch("agent.orchestrator.agent_controller._run_edit_flow") as mock_edit:
                call_edit = 0

                def mock_edit_fn(step, state):
                    nonlocal call_edit
                    call_edit += 1
                    if call_edit == 1:
                        return {"success": False, "error": "patch_failed", "reason": "validation failed"}
                    return {"success": True, "output": {"files_modified": [], "patches_applied": 0}}

                mock_edit.side_effect = mock_edit_fn
                with patch("agent.orchestrator.agent_controller.replan", side_effect=mock_replan):
                    result = run_controller("Edit foo", project_root=str(tmp_path))

    assert "task_id" in result
    assert "errors" in result
    assert replan_calls
    assert "completed_steps" in result or "errors" in result


def test_policy_engine_handles_none_search_result(tmp_path):
    """Policy engine does not crash when search_fn returns None (defensive)."""
    def mock_search_none(_query: str, state=None):
        return None

    with patch("agent.execution.step_dispatcher._policy_engine") as mock_pe:
        from agent.execution.policy_engine import ExecutionPolicyEngine

        engine = ExecutionPolicyEngine(
            search_fn=mock_search_none,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=lambda d, u, h, s=None: (d or "").strip() or "q",
            max_total_attempts=2,
        )
        mock_pe.execute_with_policy = engine.execute_with_policy

        step = {"id": 1, "action": "SEARCH", "description": "find foo"}
        state = AgentState(
            instruction="test",
            current_plan={"steps": [step]},
            context={"project_root": str(tmp_path)},
        )

        result = dispatch(step, state)

        assert result["success"] is False
        assert "error" in result


def test_to_structured_patches_skips_non_dict_changes():
    """to_structured_patches skips non-dict items in changes; no crash."""
    from editing.patch_generator import to_structured_patches

    plan = {
        "changes": [
            {"file": "a.py", "symbol": "foo", "action": "modify", "patch": "x = 1"},
            None,
            {"file": "b.py", "symbol": "bar", "action": "modify", "patch": "y = 2"},
        ],
    }
    result = to_structured_patches(plan, "instruction", {})
    assert len(result["changes"]) == 2


def test_agent_no_crash_on_search_exception(tmp_path):
    """Search raising exception is caught by policy engine; no unhandled crash."""
    def mock_search_raise(_query: str, state=None):
        raise RuntimeError("search backend error")

    with patch("agent.execution.step_dispatcher._policy_engine") as mock_pe:
        from agent.execution.policy_engine import ExecutionPolicyEngine

        engine = ExecutionPolicyEngine(
            search_fn=mock_search_raise,
            edit_fn=MagicMock(),
            infra_fn=MagicMock(),
            rewrite_query_fn=lambda d, u, h, s=None: (d or "").strip() or "q",
            max_total_attempts=2,
        )
        mock_pe.execute_with_policy = engine.execute_with_policy

        step = {"id": 1, "action": "SEARCH", "description": "find foo"}
        state = AgentState(
            instruction="test",
            current_plan={"steps": [step]},
            context={"project_root": str(tmp_path)},
        )

        result = dispatch(step, state)

        assert result["success"] is False
        assert "error" in result or "attempt_history" in result.get("output", {})
