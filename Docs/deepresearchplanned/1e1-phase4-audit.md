# ---cursor prompt --
You are a staff engineer auditing the tooling layer in AgentV2.

Focus ONLY on tool execution, tool contracts, and dispatcher behavior.

Do NOT implement changes yet.

---

## GOAL

Evaluate whether tools are:

* safe to execute
* retry-safe
* deterministic enough for agent runtime

---

## STEP 1 — TOOL CONTRACT STRUCTURE

Analyze:

* Tool input schema (Pydantic models or dicts)
* Tool output structure (ExecutionResult / ToolResult)

Check:

* Are inputs validated before execution?
* Are outputs always structured?

---

## STEP 2 — DISPATCHER BEHAVIOR

Analyze dispatcher:

* How tools are invoked
* How errors are handled

Check:

* Does dispatcher:

  * normalize errors?
  * return consistent ExecutionResult?
  * leak exceptions?

---

## STEP 3 — IDEMPOTENCY / RETRY SAFETY

For each tool type:

* file operations (edit, write)
* shell execution
* search / read

Evaluate:

* Is tool safe to retry?
* What happens if retried twice?

---

## STEP 4 — SIDE EFFECTS

Identify:

* which tools mutate external state
* whether mutations are:

  * reversible
  * repeat-safe

---

## STEP 5 — ARGUMENT HANDLING

Check:

* are arguments passed cleanly from ExecutionTask?
* any mutation or transformation inside tools?

---

## STEP 6 — GAPS

List:

* unsafe tools
* inconsistent return formats
* retry risks
* missing validation

---

## STEP 7 — MINIMAL HARDENING PLAN

Propose:

* small changes to make tools safer
* no redesign
* no new frameworks

---

## OUTPUT

Provide:

1. Tooling layer summary
2. Top 5 risks
3. Minimal fixes (actionable)

---

Rules:

* keep it simple
* no overengineering
* no new abstractions unless critical

Focus:
👉 make tools safe + predictable


# ----audit---
# TOOLING LAYER AUDIT — AgentV2

## SUMMARY

The tooling layer has a **solid foundation** with well-defined contracts and normalization. However, there are **critical safety gaps** around idempotency, exception handling, and retry safety that could cause:

- Non-retryable mutations
- Inconsistent error handling
- Silent failures
- State corruption

---

## STEP 1 — Tool Contract Structure

### ✅ STRENGTHS

**Well-defined schemas:**

```15:36:agent_v2/schemas/tool.py
# ToolResult: tool_name, success, data, error, duration_ms, raw
class ToolResult(BaseModel):
    tool_name: str
    success: bool
    data: dict
    error: Optional[ToolError] = None
    duration_ms: int
    raw: Optional[dict] = None
```

```51:64:agent_v2/schemas/execution.py
# ExecutionResult: normalized contract with summary/full_output
class ExecutionResult(BaseModel):
    step_id: str
    success: bool
    status: Literal["success", "failure"]
    output: ExecutionOutput  # summary (short) + full_output (debug)
    error: Optional[ExecutionError] = None
    metadata: ExecutionMetadata  # tool_name, duration_ms, timestamp
```

**Single normalization boundary:**

```314:366:agent_v2/runtime/tool_mapper.py
def map_tool_result_to_execution_result(
    tool_result: ToolResult,
    step_id: str,
) -> ExecutionResult:
    # Normalizes ToolResult → ExecutionResult
    # Error types mapped to canonical ErrorType enum
    # output.summary always present
```

### ⚠️ CONCERNS

**DUAL ToolResult definitions:**

```1:12:agent_v2/runtime/tool_result.py
# OLD dataclass ToolResult (legacy)
@dataclass
class ToolResult:
    success: bool
    output: Any = None
    error: str | None = None
```

```14:36:agent_v2/schemas/tool.py
# NEW Pydantic ToolResult (Phase 2 contract)
class ToolResult(BaseModel):
    tool_name: str
    success: bool
    data: dict
    error: Optional[ToolError] = None
    duration_ms: int
    raw: Optional[dict] = None
```

**Legacy bridge handles too many formats:**

```147:209:agent_v2/runtime/tool_mapper.py
def coerce_to_tool_result(
    raw: Any,
    *,
    tool_name: str = "unknown",
    duration_ms: int = 0,
) -> ToolResult:
    # Handles: Pydantic ToolResult, old dataclass, dict, arbitrary objects
    if isinstance(raw, ToolResult):
        return raw  # Good: passthrough
    if isinstance(raw, dict):
        # Extract success, output, error from dict
    if hasattr(raw, "success") and hasattr(raw, "output"):
        # Handle old dataclass
    # FALLBACK: wrap ANYTHING as success! ❌
    return ToolResult(
        tool_name=tool_name,
        success=True,
        data={"output": str(raw)} if raw is not None else {},
        error=None,
        duration_ms=duration_ms,
    )
```

**Inconsistent input validation:**

```13:28:agent/tools/react_tools.py
def _search_handler(args: dict, state) -> dict:
    query = str(args.get("query") or "").strip()
    if not query:  # ✅ Validates input
        return {
            "success": False,
            "error": "SEARCH requires non-empty query..."
        }
```

```202:210:agent_v2/primitives/shell.py
class Shell:
    def run(self, command: str) -> dict:
        # ❌ NO input validation
        result = run_command(command)
        return {...}
```

---

## STEP 2 — Dispatcher Behavior

### ✅ STRENGTHS

**Consistent normalization:**

```90:154:agent_v2/runtime/dispatcher.py
def execute(self, step, state) -> ExecutionResult | list[ExecutionResult]:
    raw = self._execute_fn(step, state)
    
    # Legacy bridge: coerce ANYTHING to ToolResult
    tool_result = coerce_to_tool_result(raw, tool_name=tool_name)
    
    # Type assertion
    assert isinstance(tool_result, ToolResult), (
        f"coerce_to_tool_result must return ToolResult; got {type(tool_result).__name__}"
    )
    
    # Normalize to ExecutionResult
    result = map_tool_result_to_execution_result(tool_result, step_id=step_id)
    
    # Validate output.summary exists
    if result.output is None or not str(result.output.summary or "").strip():
        raise ValueError("ExecutionResult.output.summary must be present and non-empty")
    
    return result
```

**No exception leakage:**

```103:112:agent_v2/runtime/dispatcher.py
# Fault injection hooks executed BEFORE real tools
fault_raw = (
    maybe_inject_open_file_fault_raw(tool_name, step, state)
    if isinstance(step, dict)
    else None
)
if fault_raw is not None:
    raw = fault_raw  # Synthetic failure
else:
    raw = self._execute_fn(step, state)
```

### ⚠️ CONCERNS

**Legacy bypass path:**

```1191:1232:agent/execution/step_dispatcher.py
def _dispatch_react(step: dict, state: AgentState) -> dict:
    # ❌ Returns dict, NOT ExecutionResult ❌
    # This bypasses dispatcher normalization!
    
    tool = get_tool_by_name(tool_name)
    try:
        args = step.get("_react_args")
        return tool.handler(args, state)  # Returns dict
    except Exception as e:
        return {
            "success": False,
            "output": {},
            "error": str(e),
            "classification": ResultClassification.RETRYABLE_FAILURE.value,
        }
```

**Multiple execution paths:**

1. **DAG path:** `DagExecutor` → `Dispatcher.execute()` → `ExecutionResult` ✅
2. **ReAct path:** `AgentLoop` → `Dispatcher.execute()` → `ExecutionResult` ✅
3. **Legacy ReAct path:** `AgentLoop` → `_dispatch_react()` → `dict` ❌

---

## STEP 3 — Idempotency / Retry Safety

### SAFE TO RETRY ⚡

| Tool | Safety | Notes |
|------|--------|-------|
| **search** | ✅ Safe | Read-only, no side effects |
| **open_file** | ✅ Safe | Read-only, idempotent |
| **read_snippet** | ✅ Safe | Bounded read, no mutation |
| **read_head** | ✅ Safe | Bounded read, no mutation |

### NOT SAFE TO RETRY ⚠️

| Tool | Risk | What Happens on Retry |
|------|------|---------------------|
| **edit** | 🗲 **DANGEROUS** | Applies patch AGAIN → corrupts file or creates duplicate edits |
| **write** | 🗲 **DANGEROUS** | Writes content AGAIN → overwrites with same (or worse) |
| **run_tests** | ⚠️ Risky | Runs tests AGAIN → wastes time, but no side effects |
| **shell** | ⚠️ Risky | Runs command AGAIN → creates duplicate side effects (mkdir, npm install, etc.) |

### EVIDENCE

```95:101:agent_v2/primitives/editor.py
def write(self, path: str, content: str) -> dict:
    write_file(path, content)  # ❌ RUNS ON EVERY RETRY
    return {"success": True, "path": path}

```

```133:138:agent/tools/filesystem_adapter.py
def write_file(path: str, content: str) -> None:
    p = Path(path).resolve()
    p.parent.mkdir(parents=True, exist_ok=True)  # ❌ Creates dirs AGAIN
    p.write_text(content, encoding="utf-8")  # ❌ Overwrites AGAIN
```

```9:19:agent_v2/primitives/shell.py
class Shell:
    def run(self, command: str) -> dict:
        result = run_command(command)  # ❌ RUNS AGAIN
        return {...}
```

```1:6:agent/tools/terminal_adapter.py
def run_command(command: str) -> dict:
    result = subprocess.run(
        command,
        shell=True,  # ❌ The SAME command runs AGAIN
        capture_output=True,
        text=True,
    )
```

---

## STEP 4 — Side Effects

### MUTATING TOOLS

| Tool | Mutates | Reversible? | Repeat-Safe? |
|------|---------|------------|--------------|
| **write** | ✅ File system | ❌ No | ❌ No |
| **edit** | ✅ File system | ⚠️ Partial (rollback on failure) | ❌ No |
| **shell** | ✅ Any external state | ❌ No | ❌ No |
| **run_tests** | ❌ No | N/A | ✅ N/A |

### PARTIAL ROLLBACK

```202:217:editing/patch_executor.py
# execute_patch has rollback on failure:
originals: dict[str, str] = {}  # Backups

for change in changes:
    # ... apply patches ...

except Exception as e:
    logger.info("[patch_executor] rollback triggered")
    for path, content in originals.items():
        Path(path).write_text(content, encoding="utf-8")  # ✅ ROLLBACK
    return {"success": False, "error": "patch_failed", ...}

# All valid: write files (BUT ONLY AFTER ALL PATCHES VALIDATE)
for abs_path_str, new_code in patched_content.items():
    Path(abs_path_str).write_text(new_code, encoding="utf-8")  # ❌ NO ROLLBACK AFTER WRITE
```

**⚠️ Gap:** Rollback restores backups, but only on EXCEPTION. If execution succeeds silently (e.g., duplicate patch), no rollback happens.

---

## STEP 5 — Argument Handling

### ✅ GOOD: Arguments passed cleanly

```98:108:agent_v2/runtime/dispatcher.py
def execute(self, step, state) -> ExecutionResult | list[ExecutionResult]:
    # Args passed via step dict
    tool_name = _resolve_tool_name(step)
    step_id = _resolve_step_id(step)
    raw = self._execute_fn(step, state)  # Args carried in `step`
```

### ⚠️ CONCERNS

**Arguments frozen flag not enforced:**

```23:40:agent_v2/schemas/execution_task.py
class ExecutionTask(BaseModel):
    id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_frozen: bool = False  # ❌ Flag exists but NOT ENFORCED
    # ...
```

**No deep copy before execution:**

```116:150:agent_v2/runtime/dag_executor.py
def _to_dispatch_step(task: ExecutionTask, args: dict) -> dict:
    # ...
    row["_react_args"] = args  # ❌ Direct reference, not deep-copied
    # If tool mutates args, original task.arguments changes!
```

**Arguments modified in tools:**

```1200:1230:agent/execution/step_dispatcher.py
def _dispatch_react(step: dict, state: AgentState) -> dict:
    args = step.get("_react_args")
    if not isinstance(args, dict):
        args = {}  # ❌ Mutates step dict!
        if action == Action.SEARCH.value:
            args["query"] = step.get("query") or ...  # ❌ Mutates args!
    return tool.handler(args, state)
```

---

## STEP 6 — GAPS

### CRITICAL SAFETY GAPS

**1. 🔴 NON-IDEMPOTENT MUTATIONS UNPROTECTED**

- `write`, `edit`, `shell` run on EVERY retry
- No check: "Did this already succeed?"
- Dangerous: Could corrupt files, run duplicate builds, create duplicate dirs

**2. 🔴 EXCEPTION LEAKAGE IN LEGACY PATH**

```1191:1232:agent/execution/step_dispatcher.py
def _dispatch_react(step: dict, state: AgentState) -> dict:
    # Returns dict, NOT ExecutionResult
    # This bypasses dispatcher normalization!
```

**3. ⚠️ INCONSISTENT INPUT VALIDATION**

- `search`: Validates non-empty query ✅
- `shell`: NO validation ❌
- `write`: NO validation of path/content ❌

**4. ⚠️ ARGUMENT MUTATION**

- `step["_react_args"]` mutated in `_dispatch_react`
- No deep copy before execution
- If tool mutates args, durability compromised

**5. ⚠️ FALLBACK WRAPS FAILURES AS SUCCESS**

```202:209:agent_v2/runtime/tool_mapper.py
# Fallback: wrap UNEXPECTED formats as success!
return ToolResult(
    tool_name=tool_name,
    success=True,  # ❌ ASSUMES SUCCESS EVEN IF UNKNOWN
    data={"output": str(raw)} if raw is not None else {},
    error=None,
    duration_ms=duration_ms,
)
```

### DESIGN GAPS

**6. 🟡 Dual ToolResult definitions confusing**

- Old dataclass (`agent_v2.runtime.tool_result.ToolResult`)
- New Pydantic model (`agent_v2.schemas.tool.ToolResult`)
- Legacy bridge (`coerce_to_tool_result`) handles both
- Unclear which code uses which

**7. 🟡 Retry loop at WRONG LAYER**

```98:134:agent_v2/runtime/agent_loop.py
# RETRY LOGIC IN AgentLoop (wrong!)
retry_count = 0
while True:
    result = self.dispatcher.execute(step, state)
    failed = is_failure(result)
    if failed:
        if retry_count < MAX_RETRIES:
            retry_count += 1
            continue  # ❌ RETRIES WITHOUT KNOWING IF SAFE
    break
```

- Retry logic should be in `DagExecutor` (has tool context)
- `AgentLoop` doesn't know: "Is this retry-safe?"
- Should check: `tool.is_retryable()` before retrying

**8. 🟡 No tool-level metadata**

- No annotation: "this tool mutates state"
- No annotation: "this tool is retry-safe"
- No annotation: "this tool requires fresh args"
- Relies on inspection / documentation

---

## STEP 7 — Minimal Hardening Plan

### TOP 5 RISKS & FIXES

---

### 🔴 RISK 1: Non-Idempotent Mutations

**Fix: Add idempotency checks before execution**

```python
# In agent_v2/runtime/tool_policy.py (NEW FILE)

@dataclass(frozen=True)
class ToolSafetyMetadata:
    """Per-tool safety metadata."""
    tool_name: str
    retry_safe: bool
    mutates_state: bool
    requires_fresh_args: bool
    # Optional: deduplication key function
    dedup_key: Callable[[dict], str] | None = None

_TOOL_SAFETY_REGISTRY: dict[str, ToolSafetyMetadata] = {
    "search": ToolSafetyMetadata(
        tool_name="search",
        retry_safe=True,
        mutates_state=False,
        requires_fresh_args=False,
    ),
    "open_file": ToolSafetyMetadata(
        tool_name="open_file",
        retry_safe=True,
        mutates_state=False,
        requires_fresh_args=False,
    ),
    "edit": ToolSafetyMetadata(
        tool_name="edit",
        retry_safe=False,  # ❌ NOT SAFE TO RETRY
        mutates_state=True,
        requires_fresh_args=True,
    ),
    "write": ToolSafetyMetadata(
        tool_name="write",
        retry_safe=False,  # ❌ NOT SAFE TO RETRY
        mutates_state=True,
        requires_fresh_args=True,
    ),
    "run_tests": ToolSafetyMetadata(
        tool_name="run_tests",
        retry_safe=False,  # ❌ NOT SAFE TO RETRY
        mutates_state=False,  # Tests don't mutate, but waste time
        requires_fresh_args=False,
    ),
    "shell": ToolSafetyMetadata(
        tool_name="shell",
        retry_safe=False,  # ❌ NOT SAFE TO RETRY
        mutates_state=True,
        requires_fresh_args=True,
    ),
}

def get_tool_safety(tool_name: str) -> ToolSafetyMetadata:
    """Get safety metadata for a tool."""
    return _TOOL_SAFETY_REGISTRY.get(tool_name) or ToolSafetyMetadata(
        tool_name=tool_name,
        retry_safe=False,  # CONSERVATIVE DEFAULT
        mutates_state=True,
        requires_fresh_args=False,
    )
```

**Fix: Check safety before retry**

```python
# In agent_v2/runtime/agent_loop.py

from agent_v2.runtime.tool_policy import get_tool_safety

def _should_retry_tool(step: dict, result, retry_count: int) -> bool:
    """Check if tool is retry-safe."""
    if retry_count >= MAX_RETRIES:
        return False
    
    tool_name = step.get("_react_action_raw", "").strip() or "unknown"
    safety = get_tool_safety(tool_name)
    
    if not safety.retry_safe:
        _LOG.warning(f"Tool {tool_name} is not retry-safe; skipping retry")
        return False
    
    return True
```

---

### 🔴 RISK 2: Legacy Path Bypasses Normalization

**Fix: Wrap `_dispatch_react` result in normalization**

```python
# In agent/execution/step_dispatcher.py

from agent_v2.runtime.tool_mapper import coerce_to_tool_result, map_tool_result_to_execution_result

def _dispatch_react(step: dict, state: AgentState) -> dict:
    """
    ReAct mode: direct tool execution via registry.
    NOW Returns ExecutionResult (normalized) instead of dict.
    """
    # ... existing tool dispatch code ...
    
    raw_result = tool.handler(args, state)
    
    # ADDED: Normalize to ExecutionResult
    tool_name = (step.get("_react_action_raw") or "").strip() or "unknown"
    try:
        tool_result = coerce_to_tool_result(raw_result, tool_name=tool_name)
        
        # Use step_id if available, otherwise generate
        step_id = step.get("step_id") or step.get("id") or "unknown"
        
        execution_result = map_tool_result_to_execution_result(
            tool_result, step_id=step_id
        )
        
        # Return as dict for backward compatibility (Transition Phase)
        return execution_result.model_dump()
    except Exception as e:
        _LOG.warning(f"Failed to normalize ReAct result: {e}")
        # Fallback: return raw dict with error
        return {
            "success": False,
            "output": {},
            "error": str(e),
            "classification": ResultClassification.RETRYABLE_FAILURE.value,
        }
```

---

### 🔴 RISK 3: Inconsistent Input Validation

**Fix: Add validation layer before execution**

```python
# In agent_v2/runtime(tool_policy.py

class ToolInputValidationError(Exception):
    """Raised when tool input is invalid."""

def validate_tool_input(tool_name: str, args: dict) -> None:
    """Validate tool-specific inputs before execution."""
    if tool_name == "search":
        query = str(args.get("query") or "").strip()
        if not query:
            raise ToolInputValidationError(
                "search requires non-empty query argument"
            )
    
    elif tool_name == "open_file":
        path = str(args.get("path") or "").strip()
        if not path:
            raise ToolInputValidationError(
                "open_file requires non-empty path argument"
            )
    
    elif tool_name == "edit":
        instruction = str(args.get("instruction") or "").strip()
        if not instruction:
            raise ToolInputValidationError(
                "edit requires non-empty instruction argument"
            )
    
    elif tool_name == "shell":
        command = str(args.get("command") or "").strip()
        if not command:
            raise ToolInputValidationError(
                "shell requires non-empty command argument"
            )
        # Add shell safety checks (command chaining, etc.)
        if any(tok in command for tok in ("&&", ";", "|", "`")):
            raise ToolInputValidationError(
                "shell command contains forbidden chaining tokens"
            )
    
    elif tool_name == "write":
        path = str(args.get("path") or "").strip()
        content = args.get("content")
        if not path:
            raise ToolInputValidationError(
                "write requires non-empty path argument"
            )
        if content is None:
            raise ToolInputValidationError(
                "write requires content argument"
            )
```

**Apply in dispatcher:**

```python
# In agent_v2/runtime/dispatcher.py

from agent_v2.runtime.tool_policy import (
    validate_tool_input,
    ToolInputValidationError,
)

def execute(self, step, state) -> ExecutionResult | list[ExecutionResult]:
    # ... existing code ...
    
    # ADDED: Validate inputs before execution
    try:
        validate_tool_input(tool_name, args)
    except ToolInputValidationError as e:
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
    
    raw = self._execute_fn(step, state)
    # ... rest of execution ...
```

---

### ⚠️ RISK 4: Argument Mutation

**Fix: Deep copy arguments before execution**

```python
# In agent_v2/runtime/dispatcher.py

import copy

def execute(self, step, state) -> ExecutionResult | list[ExecutionResult]:
    # ADDED: Deep copy step to prevent mutation
    safe_step = copy.deepcopy(step)
    
    tool_name = _resolve_tool_name(safe_step)
    step_id = _resolve_step_id(safe_step)
    
    raw = self._execute_fn(safe_step, state)
    # ... rest of execution ...
```

**Fix: Enforce arguments_frozen in ExecutionTask**

```python
# In agent_v2/schemas/execution_task.py

from pydantic import field_validator

class ExecutionTask(BaseModel):
    # ... existing fields ...
    arguments: dict[str, Any] = Field(default_factory=dict)
    arguments_frozen: bool = False
    
    @field_validator('arguments', mode='after')
    def freeze_arguments_if_required(cls, v, info):
        data = info.data
        if data.get('arguments_frozen'):
            return frozendict(v)  # frozendict or similar
        return v
```

---

### ⚠️ RISK 5: Fallback Wraps Failures as Success

**Fix: Make fallback CONSERVATIVE**

```python
# In agent_v2/runtime/tool_mapper.py

def coerce_to_tool_result(
    raw: Any,
    *,
    tool_name: str = "unknown",
    duration_ms: int = 0,
) -> ToolResult:
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
    
    # Handle old dataclass ToolResult
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
    
    # CHANGED: Fallback is CONSERVATIVE - assume FAILURE
    _LOG.warning(
        f"coerce_to_tool_result: unexpected type {type(raw).__name__} "
        f"for tool {tool_name}; treating as failure"
    )
    return ToolResult(
        tool_name=tool_name,
        success=False,  # ❌ CHANGED: Assume failure, not success
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
```

---

## SUMMARY: RECOMMENDED ACTION PLAN

### PRIORITY 1 (CRITICAL - Do First)

1. ✅ **Add ToolSafetyMetadata registry** - Annotate retry-safe tools
2. ✅ **Check safety before retry** - Skip retries for non-idempotent tools
3. ✅ **Fix fallback to be conservative** - Assume failure on unknown formats

### PRIORITY 2 (HIGH - Do Next)

4. ✅ **Normalize legacy path output** - Wrap `_dispatch_react` in normalization
5. ✅ **Add input validation** - Validate before execution (shell safety, required args)

### PRIORITY 3 (MEDIUM - Nice to Have)

6. 🟡 **Deep copy arguments** - Prevent argument mutation
7. 🟡 **Enforce arguments_frozen** - Make it actually work
8. 🟡 **Deprecate old ToolResult dataclass** - migrate all to Pydantic model

### PRIORITY 4 (LOW - Future)

9. 🟢 **Add audit logging** - Log every tool execution with safety metadata
10. 🟢 **Add dry-run mode** - `dispatcher.dry_run=True` to simulate without mutation

---

**NO REDESIGN NEEDED** - All fixes are small, targeted changes that extend existing infrastructure without breaking contracts.

# --improvements --
🔴 3 REAL problems (must fix)
1. Non-idempotent tools + retries (BIGGEST)

From report

write, edit, shell → unsafe on retry
system WILL corrupt state eventually
✔ Correct direction
add safety metadata
block retries for unsafe tools
❗ Simplify your plan

You don’t need registry + classes.

Just:

NON_RETRYABLE_TOOLS = {"write", "edit", "shell"}

and in executor:

if task.tool in NON_RETRYABLE_TOOLS:
    do_not_retry

👉 Done. No framework needed.

2. Legacy execution path bypass (critical)

From report

_dispatch_react returns dict
bypasses normalization

👉 This is a hard correctness bug

✔ Fix

Normalize ALWAYS.

No dual path.

👉 This is non-negotiable.

3. Fallback = success (dangerous bug)

From report

success=True on unknown output ❌

👉 This can silently corrupt execution logic.

✔ Fix

Default to:

success=False

👉 Must fix immediately.

🟡 2 Important but keep simple
4. Argument mutation

Yes, real issue.

But your fix is overkill.

✔ Minimal fix
args = copy.deepcopy(task.arguments)

👉 No frozen dict
👉 No validators

5. Input validation

Needed, but:

Your proposal = too big.

✔ Do only critical checks:
empty command
empty file path
empty search query

👉 Don’t build full validation system

❌ What is overengineering (skip)
❌ ToolSafetyMetadata registry

Too heavy for now

❌ frozendict / validators

Not needed

❌ dry-run mode

Not needed

❌ audit logging layer

You already have logs

🧠 Final action plan (clean)
Do ONLY these 5:
Block retries for unsafe tools
Remove _dispatch_react bypass
Fix fallback → failure
Deep copy arguments before execution
Add minimal input validation
🧠 Verdict

👉 Audit is correct
👉 Fix list is too complex
👉 Reduce to 5 changes only