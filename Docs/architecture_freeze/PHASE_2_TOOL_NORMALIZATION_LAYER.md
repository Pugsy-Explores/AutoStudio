# Phase 2 — Tool normalization layer

**Scope:** This document is the authoritative Phase 2 specification. It describes the first **integration** boundary (`ToolResult` → `ExecutionResult`). Code belongs in `agent_v2/runtime/` when this phase is executed; this file is not executable.

---

## Objective (non-negotiable)

Introduce a **single normalization boundary**:

```text
ToolResult  →  ExecutionResult
```

---

## Outcome

After this phase:

```text
AgentLoop / Executor NEVER sees ToolResult
AgentLoop ONLY sees ExecutionResult
```

---

## Why this matters

**Before (undesirable):**

```text
Tools → random dicts → loop ❌
```

**After (required):**

```text
Tools → ToolResult → Normalizer → ExecutionResult → loop ✅
```

Without this: retries, replanning, and tracing degrade.

---

## Files to create / modify

### New file

```text
agent_v2/runtime/tool_mapper.py
```

### Modify

```text
agent_v2/runtime/dispatcher.py
```

### Do not touch (this phase)

```text
tools/*
primitives/*
AgentLoop (for now)
```

---

## Step 1 — Create normalizer

**Target:** `agent_v2/runtime/tool_mapper.py`

**Responsibilities:**

- Import `ToolResult` from `agent_v2.schemas.tool` and `ExecutionResult` from `agent_v2.schemas.execution`.
- Define `ERROR_TYPE_MAP` mapping exception class names (strings) to normalized error kinds: `not_found`, `timeout`, `permission_error`, `tests_failed`, etc., with fallback to `unknown`.
- `map_error_type(tool_error_type: str) -> str` — use map, else `unknown`.
- `summarize_tool_result(tool_result: ToolResult) -> str` — human-readable one-line summary; branch on `tool_name` for known tools (`open_file`, `search`, `edit`, `run_tests`, …) and success vs failure (include `error.message` when present).
- `map_tool_result_to_execution_result(tool_result: ToolResult, step_id: str) -> ExecutionResult`:
  - Set `success` / `status` from tool outcome.
  - On failure, build normalized error `type`, `message`, `details` from `ToolError` when present; use `map_error_type` on `error.type` where applicable.
  - Populate `output.data` from `tool_result.data`, `output.summary` from `summarize_tool_result`.
  - Populate `metadata`: `tool_name`, `duration_ms` from `metrics`, `timestamp` (e.g. UTC ISO).

**Implementation note:** The return shape must match **`PHASE_1_SCHEMA_LAYER.md`** / `agent_v2.schemas.execution` exactly (nested models, literals, optional error). The sketch below is illustrative; adjust field constructors to the actual Pydantic models.

```python
# Illustrative sketch — align with frozen ExecutionResult model fields

from agent_v2.schemas.tool import ToolResult
from agent_v2.schemas.execution import ExecutionResult
from datetime import datetime


ERROR_TYPE_MAP = {
    "FileNotFoundError": "not_found",
    "TimeoutError": "timeout",
    "PermissionError": "permission_error",
    "AssertionError": "tests_failed",
}


def map_error_type(tool_error_type: str) -> str:
    if tool_error_type in ERROR_TYPE_MAP:
        return ERROR_TYPE_MAP[tool_error_type]
    return "unknown"


def summarize_tool_result(tool_result: ToolResult) -> str:
    name = tool_result.tool_name

    if tool_result.success:
        if name == "open_file":
            return "Opened file successfully"
        elif name == "search":
            return "Search returned results"
        elif name == "edit":
            return "Edit applied successfully"
        elif name == "run_tests":
            return "Tests executed successfully"
        else:
            return f"{name} executed successfully"
    else:
        if tool_result.error:
            return f"{name} failed: {tool_result.error.message}"
        return f"{name} failed"


def map_tool_result_to_execution_result(
    tool_result: ToolResult,
    step_id: str,
) -> ExecutionResult:
    success = tool_result.success

    if success:
        error_block = None
    else:
        normalized_type = (
            map_error_type(tool_result.error.type)
            if tool_result.error
            else "unknown"
        )

        error_block = {
            "type": normalized_type,
            "message": tool_result.error.message if tool_result.error else "Unknown error",
            "details": tool_result.error.details if tool_result.error else {},
        }

    return ExecutionResult(
        step_id=step_id,
        success=success,
        status="success" if success else "failure",
        output={
            "data": tool_result.data or {},
            "summary": summarize_tool_result(tool_result),
        },
        error=error_block,
        metadata={
            "tool_name": tool_result.tool_name,
            "duration_ms": tool_result.metrics.get("duration_ms", 0),
            "timestamp": datetime.utcnow().isoformat(),
        },
    )
```

---

## Step 2 — Enforce `ToolResult` contract

**Audit**

Search the codebase (examples):

```bash
grep -R "run_command" .
grep -R "read_file" .
grep -R "execute_patch" .
```

**Requirement:** Every tool handler returns a **`ToolResult`** instance:

```python
ToolResult(
    tool_name="...",
    success=True/False,
    data={...},
    error=ToolError(...) or None,
    metrics={"duration_ms": ...},
)
```

**Do not** return raw dicts (e.g. `return {"output": "..."}`).

---

## Step 3 — Modify dispatcher (critical)

**Target:** `agent_v2/runtime/dispatcher.py`

1. Import `map_tool_result_to_execution_result` from `agent_v2.runtime.tool_mapper`.
2. After the tool handler runs: `tool_result = handler(...)`.
3. Assert `isinstance(tool_result, ToolResult)` (see Step 4).
4. `execution_result = map_tool_result_to_execution_result(tool_result, step_id=step.step_id)` (or equivalent step id source).
5. **Return `execution_result` only** — not `ToolResult`, not raw dicts.

**Invariant:** Dispatcher **always** returns **`ExecutionResult`**.

---

## Step 4 — Type assertion (safety)

Inside dispatcher, after handler returns:

```python
assert isinstance(tool_result, ToolResult), "Tool must return ToolResult"
```

---

## Step 5 — Validation test (mandatory)

Add a test (e.g. `tests/` or `agent_v2` test package) that:

- Builds a successful `ToolResult` (e.g. `open_file`, small `data`, `metrics`).
- Calls `map_tool_result_to_execution_result(tool_result, "s1")`.
- Asserts `exec_result.success is True`, `exec_result.status == "success"`, and `exec_result.output` summary field is non-empty (per schema: `output.summary`).

---

## Step 6 — Trace sanity check

Run the agent entrypoint (when wired), e.g.:

```bash
python -m agent_v2 "simple task"
```

**Confirm:**

```text
✅ summary present
✅ error normalized
✅ tool_name present
```

---

## Common failure modes

```text
❌ Returning dict from tool
❌ Normalizing inside the tool instead of at the boundary
❌ Loop or executor consuming raw tool outputs
```

---

## Exit criteria (strict)

```text
✅ All tools return ToolResult
✅ Dispatcher returns ONLY ExecutionResult
✅ No raw tool output leaks past dispatcher
✅ Error types normalized
✅ Summary always present on execution path
```

---

## Principal verdict

This phase turns an unstable tool surface into a stable execution surface for the loop, retries, and tracing.

---

## Next step

After validation:

👉 **Phase 2 done** (implementation + tests + checks)

Then proceed to **Phase 3 — Exploration runner** (first read-only intelligence layer). See `PHASED_IMPLEMENTATION_PLAN.md`.
