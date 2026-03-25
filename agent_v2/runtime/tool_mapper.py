"""
Tool normalization layer — Phase 2.

Single normalization boundary: ToolResult → ExecutionResult.

AgentLoop / PlanExecutor MUST NEVER see ToolResult; they only see ExecutionResult.

Responsibilities:
  - ERROR_TYPE_MAP: maps tool-native exception names to normalized ErrorType values
  - map_error_type: normalize a raw error type string to an ErrorType value
  - summarize_tool_result: generate a human-readable one-line summary per tool
  - coerce_to_tool_result: bridge legacy/dict/old-dataclass results to the Pydantic ToolResult schema
  - map_tool_result_to_execution_result: the main normalization function (ToolResult → ExecutionResult)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent_v2.schemas.execution import (
    ErrorType,
    ExecutionError,
    ExecutionMetadata,
    ExecutionOutput,
    ExecutionResult,
)
from agent_v2.schemas.tool import ToolError, ToolResult

# ---------------------------------------------------------------------------
# Error type mapping (Schema 0 — ErrorType)
# Maps tool-native / exception class names → normalized ErrorType values.
# Do NOT add a parallel error enum elsewhere; extend this map only.
# ---------------------------------------------------------------------------

ERROR_TYPE_MAP: dict[str, str] = {
    # Python built-in exception names
    "FileNotFoundError": ErrorType.not_found,
    "IsADirectoryError": ErrorType.not_found,
    "NotADirectoryError": ErrorType.not_found,
    "TimeoutError": ErrorType.timeout,
    "asyncio.TimeoutError": ErrorType.timeout,
    "concurrent.futures.TimeoutError": ErrorType.timeout,
    "PermissionError": ErrorType.permission_error,
    "AssertionError": ErrorType.tests_failed,
    # Tool-native string codes that may appear in ToolError.type
    "not_found": ErrorType.not_found,
    "timeout": ErrorType.timeout,
    "permission_error": ErrorType.permission_error,
    "tests_failed": ErrorType.tests_failed,
    "tool_error": ErrorType.tool_error,
    "validation_error": ErrorType.validation_error,
    "unknown": ErrorType.unknown,
}


def map_error_type(raw_type: str) -> str:
    """Normalize a tool-native error type string to a canonical ErrorType value."""
    if not raw_type:
        return ErrorType.unknown
    return ERROR_TYPE_MAP.get(raw_type, ErrorType.unknown)


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------

def summarize_tool_result(tool_result: ToolResult) -> str:
    """
    Generate a human-readable one-line summary for a tool result.
    Short, LLM-readable, no raw data dumps.
    """
    name = tool_result.tool_name
    data = tool_result.data or {}

    if tool_result.success:
        if name == "open_file":
            path = data.get("file_path") or data.get("path") or data.get("output", "")
            if isinstance(path, str) and path:
                return f"Opened file {path}"
            return "Opened file successfully"

        if name == "search":
            results = data.get("results") or data.get("candidates") or []
            count = len(results) if isinstance(results, list) else data.get("count", "some")
            
            # Phase 2 Fix (LIVE-TEST-001): Show file paths to LLM, not just count
            if isinstance(results, list) and results:
                lines = [f"Search returned {count} result(s):"]
                for i, r in enumerate(results[:10], 1):  # Top 10
                    if isinstance(r, dict):
                        file_path = r.get("file") or r.get("path") or ""
                        snippet = (r.get("snippet") or r.get("content") or "")[:100]
                        snippet = snippet.replace("\n", " ").strip()
                        if file_path:
                            lines.append(f"  {i}. {file_path}")
                            if snippet:
                                lines.append(f"     {snippet}...")
                return "\n".join(lines)
            
            return f"Search returned {count} result(s)"

        if name == "edit":
            files = data.get("files_modified") or data.get("target_files") or []
            if isinstance(files, list) and files:
                return f"Edit applied successfully to {len(files)} file(s)"
            return "Edit applied successfully"

        if name == "run_tests":
            return "Tests executed successfully"

        if name == "shell":
            return "Shell command executed successfully"

        return f"{name} executed successfully"

    # Failure path
    msg = tool_result.error.message if tool_result.error else "unknown error"
    if name == "edit":
        return f"Patch failed: {msg}"
    if name == "run_tests":
        return f"Tests failed: {msg}"
    return f"{name} failed: {msg}"


# ---------------------------------------------------------------------------
# Legacy bridge — coerce to schema ToolResult
# ---------------------------------------------------------------------------

def coerce_to_tool_result(
    raw: Any,
    *,
    tool_name: str = "unknown",
    duration_ms: int = 0,
) -> ToolResult:
    """
    Convert any legacy/raw tool output (dict, old dataclass, or schema ToolResult)
    into a Pydantic schema ToolResult.

    This is the ONLY place that bridges the old dispatch surface. Once all tool
    handlers natively return agent_v2.schemas.tool.ToolResult, this function
    becomes a no-op passthrough.
    """
    # Already the correct schema type — passthrough
    if isinstance(raw, ToolResult):
        return raw

    # Handle dict (most legacy tools return dicts)
    if isinstance(raw, dict):
        success = bool(raw.get("success", True))
        output = raw.get("output")
        error_raw = raw.get("error")
        raw_duration = raw.get("duration_ms", duration_ms)

        data = _extract_data(output)
        error = _build_tool_error(error_raw)

        return ToolResult(
            tool_name=tool_name,
            success=success,
            data=data,
            error=error,
            duration_ms=raw_duration,
            raw=raw,
        )

    # Handle old dataclass ToolResult (agent_v2.runtime.tool_result.ToolResult)
    # which has: success, output (Any), error (str | None)
    if hasattr(raw, "success") and hasattr(raw, "output"):
        success = bool(raw.success)
        output = getattr(raw, "output", None)
        error_raw = getattr(raw, "error", None)

        data = _extract_data(output)
        error = _build_tool_error(error_raw)

        return ToolResult(
            tool_name=tool_name,
            success=success,
            data=data,
            error=error,
            duration_ms=duration_ms,
        )

    # Fallback — wrap whatever came back as opaque output
    return ToolResult(
        tool_name=tool_name,
        success=True,
        data={"output": str(raw)} if raw is not None else {},
        error=None,
        duration_ms=duration_ms,
    )


def _extract_data(output: Any) -> dict:
    """Convert a raw output value into a structured data dict."""
    if output is None:
        return {}
    if isinstance(output, dict):
        return output
    return {"output": output}


def _build_tool_error(error_raw: Any) -> ToolError | None:
    """Build a ToolError from a raw error value (dict, str, or None)."""
    if not error_raw:
        return None
    if isinstance(error_raw, ToolError):
        return error_raw
    if isinstance(error_raw, dict):
        return ToolError(
            type=error_raw.get("type", "unknown"),
            message=error_raw.get("message", str(error_raw)),
            details=error_raw.get("details", {}),
        )
    # Plain string error
    return ToolError(type="unknown", message=str(error_raw), details={})


# ---------------------------------------------------------------------------
# Main normalization function (ToolResult → ExecutionResult)
# ---------------------------------------------------------------------------

def map_tool_result_to_execution_result(
    tool_result: ToolResult,
    step_id: str,
) -> ExecutionResult:
    """
    Normalize a ToolResult into an ExecutionResult.

    This is the single normalization boundary described in PHASE_2_TOOL_NORMALIZATION_LAYER.md
    and TOOL_EXECUTION_CONTRACT.md. Error types are mapped to the canonical ErrorType enum
    (Schema 0). ToolResult.raw MUST NOT leak into ExecutionResult.

    Contract:
      - output.summary MUST always be non-empty
      - error MUST be None when success=True
      - error.type MUST be a canonical ErrorType value when present
    """
    success = tool_result.success
    summary = (summarize_tool_result(tool_result) or "").strip() or "(no summary)"

    if success:
        error_block = None
    else:
        if tool_result.error:
            normalized_type = map_error_type(tool_result.error.type)
            error_block = ExecutionError(
                type=normalized_type,
                message=tool_result.error.message,
                details=tool_result.error.details if tool_result.error.details else {},
            )
        else:
            error_block = ExecutionError(
                type=ErrorType.unknown,
                message="Tool execution failed with no error details",
                details={},
            )

    return ExecutionResult(
        step_id=step_id,
        success=success,
        status="success" if success else "failure",
        output=ExecutionOutput(
            data=tool_result.data or {},
            summary=summary,
        ),
        error=error_block,
        metadata=ExecutionMetadata(
            tool_name=tool_result.tool_name,
            duration_ms=tool_result.duration_ms,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ),
    )
