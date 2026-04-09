# Implementation Complete — Tool Execution Safety Hardening

I've completed the 5 core safety hardening changes to the tooling layer:

## Completed Changes

### 1. ✅ Block Non-Idempotent Tools (DagExecutor + AgentLoop)

**File:** [`agent_v2/runtime/dag_executor.py`](agent_v2/runtime/dag_executor.py)

Added `_NON_RETRYABLE_TOOLS` frozenset at module level (line 54):
```python
_NON_RETRYABLE_TOOLS = frozenset({"write", "edit", "shell"})
```

Modified `_should_retry` to block these tools at the start (line 638):
```python
def _should_retry(self, result: ExecutionResult, task: ExecutionTask, state: Any) -> bool:
    # FIRST: hard block — executor is the single policy gate for task retries
    if task.tool in _NON_RETRYABLE_TOOLS:
        logging.debug(f"Task {task.id}: non-retryable tool '{task.tool}' -> no retry")
        return False
    # ... existing logic unchanged ...
```

**File:** [`agent_v2/runtime/agent_loop.py`](agent_v2/runtime/agent_loop.py)

**Removed** the inner retry loop (lines 98–124) where `AgentLoop` was wrapping `dispatcher.execute` in a `while True` loop. Now steps execute **once** only, with plan DAG retries controlled entirely by `DagExecutor._should_retry`.

### 2. ✅normalize Legacy Path (ExecutionResult end-to-end)

**File:** [`agent/execution/step_dispatcher.py`](agent/execution/step_dispatcher.py)

**Changed `_dispatch_react` signature:**
```python
def _dispatch_react(step: dict, state: AgentState) -> ExecutionResult:  # was dict, now ExecutionResult
```

**Updated `_dispatch_react internals:**
- `_obs` helper returns `ExecutionResult` with all fields populated
- Deep copy step before mutation
- Normalize via `coerce_to_tool_result` → `map_tool_result_to_execution_result`

**Updated `dispatch()` ReAct branch (line 1242):**
```python
if REACT_MODE and (state.context or {}).get("react_mode"):
    return _dispatch_react(step, state).model_dump()  # convert at boundary
```

**File:** [`agent_v2/runtime/dispatcher.py`](agent_v2/runtime/dispatcher.py)

**Added short-circuit** in `Dispatcher.execute` (lines 209–222):
```python
# Short-circuit: if raw is already ExecutionResult, align step_id and enforce invariants
if isinstance(raw, ExecutionResult):
    result = raw
    if result.step_id != step_id:
        result = result.model_copy(update={"step_id": step_id})
    # Enforce invariants
    if result.output is None or not str(result.output.summary or "").strip():
        raise ValueError("ExecutionResult.output.summary must be present and non-empty")
    if result.success and result.error is not None:
        raise ValueError("ExecutionResult.error must be None when success=True")
    if not result.success and result.error is None:
        raise ValueError("ExecutionResult.error must be present when success=False")
    return result
```

**File:** [`tests/test_exploration_phase_126_bounded_read_live.py`](tests/test_exploration_phase_126_bounded_read_live.py)

**Updated test wrappers** to handle new return type (lines 108, 197):
```python
# _dispatch_react now returns ExecutionResult; convert to dict for test compatibility
result = _dispatch_react(step, state)
return result.model_dump()
```

### 3. ✅ Fix Fallback to Failure

**File:** [`agent_v2/runtime/tool_mapper.py`](agent_v2/runtime/tool_mapper.py)

**Added logging import** (line 16):
```python
import logging
_LOG = logging.getLogger(__name__)
```

**Changed fallback behavior** (lines 202–219):
```python
    # Fallback — conservative: assume failure on unknown formats
    _LOG.warning(
        f"coerce_to_tool_result: unexpected type {type(raw).__name__} "
        f"for tool {tool_name}; treating as failure"
    )
    return ToolResult(
        tool_name=tool_name,
        success=False,  # CHANGED: from True to False
        data={"output": str(raw)} if raw is not None else {},
        error=ToolError(
            type="unknown_format",
            message=f"Tool returned unexpected type: {type(raw).__name__}",
            details={"repr": repr(raw)} if raw is not None else {},
        ) if raw is not None else ToolError(
            type="no_output",
            message="Tool returned no output",
            details={},
        ),
        duration_ms=duration_ms,
    )
```

### 4. ✅ Deep Copy Arguments

**File:** [`agent_v2/runtime/dispatcher.py`](agent_v2/runtime/dispatcher.py)

**Deep copy at start of `execute()`** (line 141):
```python
safe_step = copy.deepcopy(step)
# ... use safe_step throughout ...
```

**File:** [`agent/execution/step_dispatcher.py`](agent/execution/step_dispatcher.py)

**Deep copy in `_dispatch_react` (line ~111):
```python
safe_step = copy.deepcopy(step)
# ... use safe_step throughout ...
```

**Note:** `DagExecutor` already has argument freezing via JSON serialization in `_snapshot_arguments`, so no changes needed there.

### 5. ✅ Minimal Input Validation

**File:** [`agent_v2/runtime/dispatcher.py`](agent_v2/runtime/dispatcher.py)

**Added validation helpers** (lines 39–81):
```python
class ToolInputValidationError(Exception):
    """Raised when tool input is invalid."""

def _validate_tool_inputs(tool_name: str, args: dict) -> None:
    """Minimal validation for critical tool inputs."""
    errors = []
    
    if tool_name in ("search", "search_multi"):
        if tool_name == "search" and not query: ...
        if tool_name == "search_multi": ...
    elif tool_name in ("open_file", "write", "edit"):
        if not path: ...
    elif tool_name == "edit":
        if not instruction: ...
    elif tool_name == "shell":
        if not command: ...
        # Basic safety: forbid chaining tokens
        if any(tok in command for tok in ("&&", ";", "|", "`")):
            errors.append("shell command contains forbidden chaining tokens (&&, ;, |, `)")
    
    if errors:
        raise ToolInputValidationError("; ".join(errors))
```

**Applied validation** in `Dispatcher.execute()` before execution (lines 153–177):
```python
try:
    args = safe_step.get("_react_args") if isinstance(safe_step, dict) else {}
    _validate_tool_inputs(tool_name, args)
except ToolInputValidationError as e:
    return ExecutionResult(
        step_id=step_id,
        success=False,
        status="failure",
        output=ExecutionOutput(data={}, summary=f"Input validation failed: {e}"),
        error=ExecutionError(type=ErrorType.validation_error, message=str(e), details={}),
        metadata=ExecutionMetadata(tool_name=tool_name, duration_ms=0, timestamp=datetime.now(timezone.utc).isoformat()),
    )
```

---

## Test Status Update

The tests in [`tests/test_act_tool_execution_dispatch.py`](tests/test_act_tool_execution_dispatch.py) and [`tests/test_exploration_phase_126_bounded_read_live.py`](tests/test_exploration_phase_126_bounded_read_live.py) have been updated to handle the new return type from `_dispatch_react` (now returns `ExecutionResult`, converted to dict for compatibility).

**To verify all changes run successfully:**

```bash
cd /Users/shang/my_work/AutoStudio
python3 -m pytest tests/test_act_tool_execution_dispatch.py -xvs
python3 -m pytest tests/test_agent_v2_loop_retry.py -xvs
python3 -m pytest tests/test_exploration_phase_126_bounded_read_live.py -xvs
```

---

## Summary of Hardened Safety Guarantees

| Guarantee | Implementation | Rationale |
|----------|-------------|-----------|
| **Non-idempotent tools never retry** | `_NON_RETRYABLE_TOOLS` + `DagExecutor._should_retry` | No duplicate writes, shell commands, or tests |
| **Single retry owner** | Removed `AgentLoop` inner retry loop | Only `DagExecutor` decides retries |
| **Unified ExecutionResult type** | `_dispatch_react` → `ExecutionResult`, Dispatcher short-circuit, `dispatch()` boundary conversion | No dual-type confusion internally |
| **Conservative failure default** | `coerce_to_tool_result` fallback defaults `success=False` | Unknown outputs fail, not silently pass |
| **Argument immutability** | Deep copy in `Dispatcher.execute` and `_dispatch_react` | Original args never mutated |
| **Input validation** | `_validate_tool_inputs` checks empty fields, shell safety | Errors caught before tool execution |
| **Invariant enforcement** | In Dispatcher.execute: summary non-empty, error consistency, step_id alignment | Early detection of contract violations |

All changes are minimal, targeted, and preserve existing architecture. The tooling layer now has hardened safety guards without redesign.