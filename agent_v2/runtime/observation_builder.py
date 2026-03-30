"""
Observation builder for runtime loop outputs.

Handles both the new agent_v2.schemas.execution.ExecutionResult (Phase 2+) and the
legacy dict / old-ToolResult-dataclass shapes produced before Phase 2 wiring is complete.
"""
# DO NOT import from agent.* here

from agent_v2.runtime.react_context import MAX_OBS_CHARS


def _format_react_error(action: str, error: str) -> str:
    return f"""Tool: {action}

Result: failed

Error:
{error}

Fix your input and try again."""


def _action_to_tool_name(action: str) -> str:
    """Map step action to ReAct tool name for error messages."""
    m = {"SEARCH": "search", "READ": "open_file", "EDIT": "edit", "RUN_TEST": "run_tests"}
    return m.get((action or "").upper(), (action or "").lower())


# ---------------------------------------------------------------------------
# Normalised field extractors — work for ExecutionResult, old ToolResult, dict
# ---------------------------------------------------------------------------

def _extract_success(result) -> bool:
    if isinstance(result, dict):
        return bool(result.get("success", True))
    return bool(getattr(result, "success", True))


def _extract_error_message(result) -> str:
    """
    Return the error message string.

    ExecutionResult: result.error is ExecutionError | None → use .message
    Old ToolResult / dict: result.error is str | None
    """
    err = getattr(result, "error", None)
    if err is None:
        if isinstance(result, dict):
            return result.get("error", "") or ""
        return ""
    # ExecutionError Pydantic model (Phase 2+)
    if hasattr(err, "message"):
        return str(err.message)
    # Legacy: error is a plain string
    return str(err) if err else ""


def _extract_output_dict(result) -> dict | None:
    """
    Return the structured output dict for the result.

    ExecutionResult: result.output is ExecutionOutput with .data (dict) and .summary (str)
    Old ToolResult / dict: result.output is raw dict or string
    """
    out = getattr(result, "output", None)
    if out is None and isinstance(result, dict):
        out = result.get("output")

    if out is None:
        return None

    # ExecutionOutput Pydantic model (Phase 2+) — unwrap to the data dict
    if hasattr(out, "data"):
        return out.data if isinstance(out.data, dict) else {}

    if isinstance(out, dict):
        return out

    return None


def _extract_output_raw(result):
    """Return the raw output value (string or dict) for simple str() rendering."""
    out = getattr(result, "output", None)
    if out is None and isinstance(result, dict):
        out = result.get("output")

    # ExecutionOutput Pydantic model — prefer data, fall back to summary
    if hasattr(out, "data") and hasattr(out, "summary"):
        raw_data = out.data
        if isinstance(raw_data, dict):
            # For file reads the legacy path stores the content under "output" or "file_content"
            content = (
                raw_data.get("file_content")
                or raw_data.get("output")
                or raw_data.get("content")
            )
            if content:
                return content
        return out.summary

    return out


# ---------------------------------------------------------------------------
# Main observation builder
# ---------------------------------------------------------------------------

def build_observation(action: str, result) -> str:
    """Build a readable observation from action and result. Decoupled from step structure."""
    action = (action or "").upper()
    success = _extract_success(result)
    err = _extract_error_message(result)
    tool_name = _action_to_tool_name(action)

    def _fail_obs(msg: str) -> str:
        return _format_react_error(tool_name, msg)

    if action == "SEARCH":
        if not success and err:
            return _fail_obs(err)
        out_dict = _extract_output_dict(result)
        if isinstance(out_dict, dict):
            results = out_dict.get("results") or out_dict.get("candidates") or []
            lines = [f"Found {len(results)} result(s)."]
            for r in results[:12]:
                if isinstance(r, dict):
                    f = r.get("file", "")
                    s = (r.get("snippet") or r.get("content") or "")[:300].replace("\n", " ")
                    lines.append(f"  {f}: {s}...")
            return "\n".join(lines)
        raw = _extract_output_raw(result)
        return str(raw)[:2000] if raw else ""

    if action == "READ":
        if not success and err:
            return _fail_obs(err)
        content = _extract_output_raw(result)
        return str(content)[:MAX_OBS_CHARS] if content else ""

    if action == "EDIT":
        out_dict = _extract_output_dict(result)
        if isinstance(out_dict, dict):
            files = out_dict.get("files_modified") or out_dict.get("target_files") or []
            files_str = ", ".join(str(f) for f in files[:5] if f) if files else "-"
            test_out = (
                (out_dict.get("stdout") or "")
                + "\n"
                + (out_dict.get("stderr") or "")
            ).strip() or (out_dict.get("reason") or "") or (out_dict.get("test_output") or "")
            fr = out_dict.get("failure_reason_code", "")
            patch_applied = out_dict.get("patch_applied", False)
            tests_passed = out_dict.get("tests_passed", True)

            if success:
                return f"Patch applied successfully.\nModified file(s): {files_str}\nTests passed.\n{str(test_out)[:500]}".strip()
            if fr == "syntax_error":
                syn_err = out_dict.get("syntax_error") or out_dict.get("reason") or err
                return f"Syntax error:\n{syn_err}\n\nModified file(s) before rollback: {files_str}"
            if patch_applied and not tests_passed:
                return f"Patch applied successfully.\nModified file(s): {files_str}\n\nTests failed:\n\n{test_out[:2000]}"
            return f"Edit failed: {err or 'unknown'}.\nTarget file(s): {files_str}\n\nOutput: {str(test_out)[:1500]}"
        raw = _extract_output_raw(result)
        if not success and err:
            return _fail_obs(err)
        return f"Edit {'succeeded' if success else 'failed'}: {str(raw)[:500]}"

    if action == "RUN_TEST":
        out_dict = _extract_output_dict(result)
        if isinstance(out_dict, dict):
            raw = (out_dict.get("stdout") or "") + "\n" + (out_dict.get("stderr") or "")
        else:
            raw_val = _extract_output_raw(result)
            raw = str(raw_val)[:5000] if raw_val else ""
        if success:
            return (
                f"All tests passed. If task is complete, call finish.\n\n{raw}".strip()
                if raw
                else "All tests passed. If task is complete, call finish."
            )
        return f"Tests failed:\n\n{raw}\n\nUse this to fix the issue."

    return f"Result: success={success}, error={err}" + (
        f", output={str(_extract_output_raw(result))[:500]}" if result else ""
    )


class ObservationBuilder:
    """Thin wrapper for building loop observations."""

    def build(self, action: str, result) -> str:
        return build_observation(action, result)
