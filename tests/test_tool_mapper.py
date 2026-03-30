"""
Phase 2 — Tool normalization layer tests (Step 5, mandatory).

Validates the ToolResult → ExecutionResult normalization boundary:
  - Successful tool result is mapped correctly
  - Failed tool result normalizes error type and sets failure fields
  - coerce_to_tool_result handles dict and legacy dataclass inputs
  - ToolResult.raw never leaks into ExecutionResult
  - output.summary is always present
"""

import pytest

from agent_v2.runtime.tool_mapper import (
    coerce_to_tool_result,
    map_error_type,
    map_tool_result_to_execution_result,
    summarize_tool_result,
)
from agent_v2.schemas.execution import ErrorType, ExecutionResult
from agent_v2.schemas.tool import ToolError, ToolResult


# ---------------------------------------------------------------------------
# map_error_type
# ---------------------------------------------------------------------------

def test_map_error_type_known_exception_names():
    assert map_error_type("FileNotFoundError") == ErrorType.not_found
    assert map_error_type("TimeoutError") == ErrorType.timeout
    assert map_error_type("PermissionError") == ErrorType.permission_error
    assert map_error_type("AssertionError") == ErrorType.tests_failed


def test_map_error_type_known_schema_values():
    assert map_error_type("not_found") == ErrorType.not_found
    assert map_error_type("tests_failed") == ErrorType.tests_failed


def test_map_error_type_unknown_falls_back():
    assert map_error_type("SomeObscureError") == ErrorType.unknown
    assert map_error_type("") == ErrorType.unknown


# ---------------------------------------------------------------------------
# summarize_tool_result
# ---------------------------------------------------------------------------

def test_summarize_open_file_success():
    tr = ToolResult(
        tool_name="open_file",
        success=True,
        data={"file_path": "agent_loop.py"},
        duration_ms=50,
    )
    assert "agent_loop.py" in summarize_tool_result(tr)


def test_summarize_search_success():
    tr = ToolResult(
        tool_name="search",
        success=True,
        data={"results": [{"file": "a.py"}, {"file": "b.py"}]},
        duration_ms=30,
    )
    summary = summarize_tool_result(tr)
    assert "2" in summary


def test_summarize_edit_failure():
    tr = ToolResult(
        tool_name="edit",
        success=False,
        data={},
        error=ToolError(type="AssertionError", message="Tests failed after patch", details={}),
        duration_ms=300,
    )
    summary = summarize_tool_result(tr)
    assert "Patch failed" in summary
    assert "Tests failed after patch" in summary


def test_summarize_unknown_tool_success():
    tr = ToolResult(tool_name="my_custom_tool", success=True, data={}, duration_ms=10)
    assert "my_custom_tool" in summarize_tool_result(tr)


# ---------------------------------------------------------------------------
# map_tool_result_to_execution_result — success path (Step 5 mandatory)
# ---------------------------------------------------------------------------

def test_map_success_includes_full_output_for_shell():
    tr = ToolResult(
        tool_name="shell",
        success=True,
        data={"stdout": "line1\nline2\n", "stderr": ""},
        duration_ms=1,
    )
    result = map_tool_result_to_execution_result(tr, step_id="s_shell")
    assert result.output.full_output is not None
    assert "line1" in result.output.full_output


def test_map_success_open_file():
    """Step 5 mandatory: success=True, status='success', output.summary non-empty."""
    tr = ToolResult(
        tool_name="open_file",
        success=True,
        data={"file_path": "agent_loop.py", "file_content": "class AgentLoop: ..."},
        duration_ms=45,
    )
    result = map_tool_result_to_execution_result(tr, step_id="s1")

    assert isinstance(result, ExecutionResult)
    assert result.success is True
    assert result.status == "success"
    assert result.step_id == "s1"
    assert result.output.summary  # non-empty — Step 5 requirement
    assert result.output.data == tr.data
    assert result.error is None
    assert result.metadata.tool_name == "open_file"
    assert result.metadata.duration_ms == 45
    assert result.metadata.timestamp  # non-empty ISO timestamp


def test_map_success_search():
    tr = ToolResult(
        tool_name="search",
        success=True,
        data={"results": [{"file": "x.py", "snippet": "def foo(): ..."}]},
        duration_ms=20,
    )
    result = map_tool_result_to_execution_result(tr, step_id="s2")

    assert result.success is True
    assert result.status == "success"
    assert result.error is None
    assert result.output.summary


# ---------------------------------------------------------------------------
# map_tool_result_to_execution_result — failure path
# ---------------------------------------------------------------------------

def test_map_failure_edit_tests_failed():
    tr = ToolResult(
        tool_name="edit",
        success=False,
        data={},
        error=ToolError(type="AssertionError", message="Tests failed", details={"count": 3}),
        duration_ms=300,
    )
    result = map_tool_result_to_execution_result(tr, step_id="s3")

    assert result.success is False
    assert result.status == "failure"
    assert result.error is not None
    assert result.error.type == ErrorType.tests_failed  # normalized from AssertionError
    assert result.error.message == "Tests failed"
    assert result.error.details == {"count": 3}
    assert result.output.summary  # still non-empty even on failure


def test_map_failure_no_error_details():
    """Failure with no ToolError should still produce a valid ExecutionResult."""
    tr = ToolResult(
        tool_name="shell",
        success=False,
        data={},
        error=None,
        duration_ms=10,
    )
    result = map_tool_result_to_execution_result(tr, step_id="s4")

    assert result.success is False
    assert result.error is not None
    assert result.error.type == ErrorType.unknown


def test_map_raw_does_not_leak():
    """ToolResult.raw MUST NEVER appear in ExecutionResult."""
    tr = ToolResult(
        tool_name="open_file",
        success=True,
        data={"file_path": "foo.py"},
        duration_ms=5,
        raw={"secret_internal_key": "should not leak"},
    )
    result = map_tool_result_to_execution_result(tr, step_id="s5")
    dumped = result.model_dump()
    assert "raw" not in dumped
    assert "secret_internal_key" not in str(dumped)


# ---------------------------------------------------------------------------
# coerce_to_tool_result
# ---------------------------------------------------------------------------

def test_coerce_passthrough_for_schema_tool_result():
    tr = ToolResult(tool_name="search", success=True, data={}, duration_ms=0)
    coerced = coerce_to_tool_result(tr, tool_name="search")
    assert coerced is tr  # must be the same object — no wrapping


def test_coerce_dict_success():
    raw = {"success": True, "output": {"results": [1, 2, 3]}, "duration_ms": 15}
    tr = coerce_to_tool_result(raw, tool_name="search")
    assert isinstance(tr, ToolResult)
    assert tr.success is True
    assert tr.tool_name == "search"
    assert tr.data == {"results": [1, 2, 3]}
    assert tr.duration_ms == 15


def test_coerce_dict_failure_string_error():
    raw = {"success": False, "output": {}, "error": "file not found"}
    tr = coerce_to_tool_result(raw, tool_name="open_file")
    assert isinstance(tr, ToolResult)
    assert tr.success is False
    assert tr.error is not None
    assert tr.error.message == "file not found"


def test_coerce_dict_failure_dict_error():
    raw = {
        "success": False,
        "output": {},
        "error": {"type": "TimeoutError", "message": "timed out", "details": {}},
    }
    tr = coerce_to_tool_result(raw, tool_name="shell")
    assert tr.error.type == "TimeoutError"


def test_coerce_legacy_dataclass():
    """Simulate old ToolResult dataclass (agent_v2.runtime.tool_result.ToolResult)."""
    from agent_v2.runtime.tool_result import ToolResult as LegacyToolResult

    legacy = LegacyToolResult(
        success=True,
        output={"file_path": "agent_loop.py"},
        error=None,
    )
    tr = coerce_to_tool_result(legacy, tool_name="open_file")
    assert isinstance(tr, ToolResult)
    assert tr.success is True
    assert tr.tool_name == "open_file"


# ---------------------------------------------------------------------------
# Dispatcher integration (no real tools — unit-level wiring)
# ---------------------------------------------------------------------------

class _Sentinel:
    """Dummy object used as a sentinel primitive to avoid circular import in tests."""


def test_dispatcher_returns_execution_result():
    from agent_v2.runtime.dispatcher import Dispatcher

    def fake_execute_fn(step, state):
        return {"success": True, "output": {"file_path": "agent_loop.py"}}

    # Pass sentinel objects for shell/editor/browser so the circular
    # agent_v2.primitives import is never triggered.
    dispatcher = Dispatcher(
        execute_fn=fake_execute_fn,
        shell=_Sentinel(),
        editor=_Sentinel(),
        browser=_Sentinel(),
    )

    class FakeState:
        context = {}

    step = {"id": 1, "_react_action_raw": "open_file"}
    result = dispatcher.execute(step, FakeState())

    assert isinstance(result, ExecutionResult)
    assert result.success is True
    assert result.metadata.tool_name == "open_file"
    assert result.step_id == "1"


def test_dispatcher_returns_execution_result_on_failure():
    from agent_v2.runtime.dispatcher import Dispatcher

    def fake_execute_fn(step, state):
        return {"success": False, "output": {}, "error": "Tests failed after patch"}

    dispatcher = Dispatcher(
        execute_fn=fake_execute_fn,
        shell=_Sentinel(),
        editor=_Sentinel(),
        browser=_Sentinel(),
    )

    class FakeState:
        context = {}

    step = {"id": 3, "_react_action_raw": "edit"}
    result = dispatcher.execute(step, FakeState())

    assert isinstance(result, ExecutionResult)
    assert result.success is False
    assert result.status == "failure"
    assert result.error is not None
    assert result.output.summary
