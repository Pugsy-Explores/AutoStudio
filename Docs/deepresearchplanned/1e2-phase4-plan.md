# -- plan --

Tool Execution Safety Hardening

Overview

Fix 5 critical safety gaps identified in the tooling layer audit. All changes are minimal, targeted, and preserve existing architecture.

Changes

1. Block Retries for Non-Idempotent Tools (single owner: DagExecutor)

Problem: Tools like write, edit, shell run on every retry, corrupting state (duplicate writes, duplicate shell commands).

Solution: Simple set-based block list checked only in DagExecutor._should_retry.

Rule: DagExecutor is the only layer that decides whether a failed plan task is re-dispatched. No duplicate retry logic elsewhere for the same tool execution.

Current gap (must fix before relying on _should_retry alone): [agent_v2/runtime/agent_loop.py](agent_v2/runtime/agent_loop.py) wraps dispatcher.execute in an inner while True with MAX_RETRIES (lines ~98–124). That re-invokes the full dispatch path without going through DagExecutor._should_retry, so non-idempotent tools can still run multiple times in exploration / ReAct-style loops.

Required companion change — same work item as (1):





File: [agent_v2/runtime/agent_loop.py](agent_v2/runtime/agent_loop.py)



Remove the inner retry loop: one dispatcher.execute(step, state) per generated step; on failure, update state once and break (same as success path structure-wise: no continue retry).



Drop or repurpose retry_count / state.metadata["retry_count"] if they only served this loop (keep metadata if other code reads it — prefer setting to 0 or document removal).

File: [agent_v2/runtime/dag_executor.py](agent_v2/runtime/dag_executor.py)

Add at module level (after imports):

# Tools that MUST NOT be retried - non-idempotent
_NON_RETRYABLE_TOOLS = frozenset({"write", "edit", "shell"})

Implementation note: ExecutionTask.tool uses the same strings as _react_action_raw for ReAct-backed steps (e.g. edit, shell). If the compiler uses a different name for a tool (e.g. alias), extend the frozenset to match actual task.tool values.

Modify _should_retry (existing signature returns bool, not a tuple — match the real method):

def _should_retry(self, result: ExecutionResult, task: ExecutionTask, state: Any) -> bool:
    # FIRST: hard block — executor is the single policy gate for task retries
    if task.tool in _NON_RETRYABLE_TOOLS:
        return False
    # ... existing error-type / attempts logic unchanged ...

Impact: Prevents duplicate mutations from plan DAG retries; removing AgentLoop inner retries prevents bypass for paths that use AgentLoop + Dispatcher.

Verification: Grep for MAX_RETRIES, retry_count, and while True around dispatcher.execute / execute_fn after the change; only DagExecutor should re-dispatch failed tasks.



2. Remove Legacy Execution Path Bypass (ExecutionResult end-to-end inside execute_fn)

Problem: _dispatch_react returns a raw dict, so the stack mixes “already normalized” and “legacy dict” shapes. Returning model_dump() from _dispatch_react still leaves internal dual types: Dispatcher.execute receives dicts while the DAG path conceptually uses ExecutionResult.

Solution:





**_dispatch_react returns ExecutionResult only** (no model_dump inside it).



**Dispatcher.execute** in [agent_v2/runtime/dispatcher.py](agent_v2/runtime/dispatcher.py): after raw = self._execute_fn(...), short-circuit if raw is already ExecutionResult:





Resolve step_id from step as today; if raw.step_id differs from resolved step_id, use raw.model_copy(update={"step_id": step_id}) (or equivalent) so the bound step id stays consistent.



Enforce the same invariants as today: output.summary non-empty, success / error consistency.



Return that ExecutionResult and skip coerce_to_tool_result / map_tool_result_to_execution_result for this branch.



API boundary that still requires dicts: [agent/execution/step_dispatcher.py](agent/execution/step_dispatcher.py) dispatch() — in the ReAct branch only, convert once: er = _dispatch_react(step, state); return er.model_dump() so external callers of dispatch() keep a dict contract without dicts flowing through Dispatcher.execute’s inner pipeline.

File: [agent/execution/step_dispatcher.py](agent/execution/step_dispatcher.py)

Modify _dispatch_react (signature and internals):

from agent_v2.runtime.tool_mapper import coerce_to_tool_result, map_tool_result_to_execution_result
from agent_v2.schemas.execution import ExecutionResult, ExecutionOutput, ExecutionError, ExecutionMetadata, ErrorType
from datetime import datetime, timezone

def _dispatch_react(step: dict, state: AgentState) -> ExecutionResult:
    """ReAct mode: registry tools → normalized ExecutionResult (no dict)."""
    action = (step.get("action") or "EXPLAIN").upper()
    react_name_by_action = { ... }  # unchanged

    def _obs(err: str, *, tool_name: str = "unknown") -> ExecutionResult:
        sid = str(step.get("step_id") or step.get("id") or "unknown")
        return ExecutionResult(
            step_id=sid,
            success=False,
            status="failure",
            output=ExecutionOutput(data={}, summary=f"Tool failed: {err}", full_output=None),
            error=ExecutionError(type=ErrorType.tool_error, message=err, details={}),
            metadata=ExecutionMetadata(
                tool_name=tool_name,
                duration_ms=0,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
        )

    # ... resolve tool_name, tool, args (with deepcopy per section 4) ...

    try:
        raw_result = tool.handler(args, state)
        step_id = str(step.get("step_id") or step.get("id") or "unknown")
        tool_result = coerce_to_tool_result(raw_result, tool_name=tool_name)
        return map_tool_result_to_execution_result(tool_result, step_id=step_id)
    except Exception as e:
        logger.warning("Failed to normalize ReAct result: %s", e)
        return _obs(f"Normalization failed: {e}", tool_name=tool_name or "unknown")

**dispatch() ReAct branch:**

if REACT_MODE and (state.context or {}).get("react_mode"):
    return _dispatch_react(step, state).model_dump()

Callers of _dispatch_react outside Dispatcher: e.g. [tests/test_exploration_phase_126_bounded_read_live.py](tests/test_exploration_phase_126_bounded_read_live.py) — update to expect ExecutionResult or call .model_dump() at the test assertion boundary.

Impact: Inside execute_fn → Dispatcher.execute, the primary shape is **ExecutionResult; dict serialization is confined to the **dispatch() (and similar) boundary.



3. Fix Fallback to Default to Failure

Problem: coerce_to_tool_result wraps unknown formats as success=True, silently corrupting execution logic.

Solution: Default to success=False on unknown formats.

File: agent_v2/runtime/tool_mapper.py

Modify coerce_to_tool_result function (line 147):

# At line 202 (end of function)
# CHANGED: Fallback is CONSERVATIVE - assume FAILURE
import logging
_LOG = logging.getLogger(__name__)

# ... existing handling for ToolResult, dict, dataclass ...

# OLD CODE (remove):
    # return ToolResult(
    #     tool_name=tool_name,
    #     success=True,
    #     data={"output": str(raw)} if raw is not None else {},
    #     error=None,
    #     duration_ms=duration_ms,
    # )

# NEW CODE:
    _LOG.warning(
        f"coerce_to_tool_result: unexpected type {type(raw).__name__} "
        f"for tool {tool_name}; treating as failure"
    )
    return ToolResult(
        tool_name=tool_name,
        success=False,  # CHANGED: Assume failure, not success
        data={"output": str(raw)} if raw is not None else {},
        error=ToolError(
            type="unknown_format",
            message=f"Tool returned unexpected type: {type(raw).__name__}",
            details={"repr": repr(raw)},
        ) if raw is not None else ToolError(
            type="no_output",
            message="Tool returned no output",
            details={},
        ),
        duration_ms=duration_ms,
    )

Impact: Unknown tool outputs flagged as failures instead of silent successes.



4. Deep Copy Arguments Before Execution

Problem: Execution mutates step["_react_args"] and task.arguments, corrupting source data and breaking replay/dedup.

Solution: Deep copy before execution.

File 1: agent_v2/runtime/dispatcher.py

Modify execute method (line 90):

def execute(self, step, state) -> ExecutionResult | list[ExecutionResult]:
    """Execute a step and return a normalized ExecutionResult."""
    
    # NEW: Deep copy step to prevent mutation
    safe_step = copy.deepcopy(step)
    
    if getattr(state, "context", None) is not None:
        state.context.setdefault("shell", self._shell)
        state.context.setdefault("editor", self._editor)
        state.context.setdefault("browser", self._browser)

    tool_name = _resolve_tool_name(safe_step)  # Use safe_step
    step_id = _resolve_step_id(safe_step)  # Use safe_step

    # Use safe_step for all subsequent operations
    fault_raw = (
        maybe_inject_open_file_fault_raw(tool_name, safe_step, state)
        if isinstance(safe_step, dict)
        else None
    )
    if fault_raw is not None:
        raw = fault_raw
    else:
        raw = self._execute_fn(safe_step, state)
    
    # Rest of function unchanged...

File 2: agent_v2/runtime/dag_executor.py

Modify _execute_task method (around line 200):

def _execute_task(
    self,
    task: ExecutionTask,
    state: Any,
    *,
    allow_retry: bool = True,
) -> ExecutionResult:
    """Execute a single task with retry logic."""
    
    # NEW: Deep copy arguments to prevent internal mutation
    frozen_args = copy.deepcopy(task.arguments)
    
    # Use frozen_args (not task.arguments) for dispatch
    args = _merge_args_hints(task, generated=frozen_args)
    step = _to_dispatch_step(task, args)
    
    # Rest of execution using safe step...

File 3: agent/execution/step_dispatcher.py

Modify _dispatch_react (line ~1191) — return type ExecutionResult (see section 2):

def _dispatch_react(step: dict, state: AgentState) -> ExecutionResult:
    """
    ReAct mode: direct tool execution via registry.
    Returns ExecutionResult (normalized).
    """
    
    # NEW: Deep copy step before mutation
    safe_step = copy.deepcopy(step)
    
    action = safe_step.get("action")  # Use safe_step
    
    # NEW: Work with safe_step, not original step
    args = safe_step.get("_react_args")
    if not isinstance(args, dict):
        args = {}  # Mutates copy, not original
        if action == Action.SEARCH.value:
            args["query"] = safe_step.get("query") or safe_step.get("description") or ""
        elif action == Action.READ.value:
            args["path"] = safe_step.get("path") or safe_step.get("description") or safe_step.get("file") or ""
        elif action == Action.EDIT.value:
            args["instruction"] = safe_step.get("description") or ""
            args["path"] = safe_step.get("path") or safe_step.get("edit_target_path") or ""
    
    # Rest of function using safe_step...

Impact: Arguments no longer mutated during execution, preserving original state for replay/troubleshooting.



5. Add Minimal Input Validation

Problem: Tools execute without validating required fields (empty search queries, empty paths, empty shell commands).

Solution: Minimal validation for critical fields only.

File: agent_v2/runtime/dispatcher.py

Add at module level (after imports):

class ToolInputValidationError(Exception):
    """Raised when tool input is invalid."""

Add validation helper:

# Add after class Dispatcher, before execute method
def _validate_tool_inputs(tool_name: str, args: dict) -> None:
    """Minimal validation for critical tool inputs."""
    errors = []
    
    if tool_name in ("search", "search_multi"):
        query = str(args.get("query") or "").strip()
        queries = args.get("queries")
        if tool_name == "search" and not query:
            errors.append("search requires non-empty 'query' argument")
        if tool_name == "search_multi":
            if not isinstance(queries, list) or not queries:
                errors.append("search_multi requires non-empty 'queries' list")
    
    elif tool_name in ("open_file", "write", "edit"):
        path = str(args.get("path") or "").strip()
        if not path:
            errors.append(f"{tool_name} requires non-empty 'path' argument")
    
    elif tool_name == "edit":
        instruction = str(args.get("instruction") or "").strip()
        if not instruction:
            errors.append("edit requires non-empty 'instruction' argument")
    
    elif tool_name == "shell":
        command = str(args.get("command") or "").strip()
        if not command:
            errors.append("shell requires non-empty 'command' argument")
        # Basic safety: forbid chaining tokens
        if any(tok in command for tok in ("&&", ";", "|", "`")):
            errors.append("shell command contains forbidden chaining tokens (&&, ;, |, `)")
    
    if errors:
        raise ToolInputValidationError("; ".join(errors))

Call validation in execute method:

def execute(self, step, state) -> ExecutionResult | list[ExecutionResult]:
    """Execute a step and return a normalized ExecutionResult."""
    
    # NEW: Deep copy step to prevent mutation
    safe_step = copy.deepcopy(step)
    
    # ... existing setup ...
    
    # NEW: Validate inputs before execution
    try:
        tool_name = _resolve_tool_name(safe_step)
        args = safe_step.get("_react_args") if isinstance(safe_step, dict) else {}
        _validate_tool_inputs(tool_name, args)
    except ToolInputValidationError as e:
        # Return synthetic ExecutionResult with validation error (fix: do not reference undefined `result`)
        step_id = _resolve_step_id(safe_step)
        from datetime import datetime, timezone
        from agent_v2.schemas.execution import ExecutionResult, ExecutionOutput, ExecutionError, ExecutionMetadata, ErrorType
        
        return ExecutionResult(
            step_id=step_id,
            success=False,
            status="failure",
            output=ExecutionOutput(
                data={},
                summary=f"Input validation failed: {e}",
                full_output=None,
            ),
            error=ExecutionError(
                type=ErrorType.validation_error,
                message=str(e),
                details={},
            ),
            metadata=ExecutionMetadata(
                tool_name=tool_name,
                duration_ms=0,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
        )
    
    # Rest of execution...

Impact: Catches empty/invalid inputs before execution, preventing downstream errors.



Testing Checklist





Run existing tools tests



Verify _dispatch_react return type is ExecutionResult; dispatch() react path returns model_dump()



Verify Dispatcher.execute accepts ExecutionResult from execute_fn without double-mapping



Verify retries blocked for write/edit/shell only via DagExecutor._should_retry



Verify AgentLoop does not re-call dispatcher.execute on the same step after failure



Verify arguments not mutated after execution



Verify empty inputs rejected with validation errors



Verify unknown formats treated as failures in coerce_to_tool_result



Verify no regressions in existing tool execution



Architecture Notes

Retry ownership (target state):

flowchart LR
  AgentLoop[AgentLoop]
  Dispatcher[Dispatcher.execute]
  ExecuteFn[execute_fn e.g. _dispatch_react]
  DagExecutor[DagExecutor]
  ShouldRetry[_should_retry + NON_RETRYABLE]

  DagExecutor -->|"re-dispatch failed task"| Dispatcher
  AgentLoop -->|"one shot per step"| Dispatcher
  Dispatcher --> ExecuteFn
  ShouldRetry -->|"only here"| DagExecutor

No new frameworks introduced. All changes are:





Minimal (targeted edits)



dict serialization for legacy dispatch() callers only at the boundary



Safe: update tests and any direct _dispatch_react callers to the new return type
The tooling layer remains stable with hardened safety guards.
