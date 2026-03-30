# Tool layer ↔ execution layer contract

Last critical boundary. If this is sloppy, retry, failure classification, and planning break.

Defined cleanly, like a production system.

---

## Part 1 — `ToolResult` (frozen)

### Purpose

```text
Raw output from tool execution (closest to system reality)
```

- tool-specific but normalized
- NOT aware of plan/execution semantics
- MUST be converted → ExecutionResult

### Full schema

```json
{
  "tool_name": "string",

  "success": true,

  "data": {},

  "error": {
    "type": "string",
    "message": "string",
    "details": {}
  },

  "metrics": {
    "duration_ms": 0
  },

  "raw": {}
}
```

### Field contracts

**`tool_name`**

```text
Must match ToolDefinition.name
```

**`success`**

```text
true → tool executed successfully
false → tool failed
```

**`data`**

```text
Structured output ONLY
No formatting logic here
```

Examples:

```json
{ "file_content": "..." }
```

```json
{ "results": [...] }
```

```json
{ "patch_applied": true }
```

**`error`**

```json
{
  "type": "string",
  "message": "string",
  "details": {}
}
```

Rules:

```text
- MUST exist if success=false
- type is tool-native (NOT normalized yet)
```

Example:

```json
{
  "type": "FileNotFoundError",
  "message": "file does not exist"
}
```

**`metrics`**

```json
{
  "duration_ms": 120
}
```

**`raw` (important)**

```text
Original unprocessed output (optional but recommended)
```

Why:

```text
Debugging
Tracing
Future improvements
```

### Hard rules (ToolResult)

**Rule 1**

```text
ToolResult MUST NOT contain:
- plan info
- step_id
- execution metadata
```

**Rule 2**

```text
ToolResult.error.type is NOT standardized
```

**Rule 3**

```text
ToolResult MUST be tool-centric, not agent-centric
```

---

## Part 2 — Mapping → `ExecutionResult`

### Purpose

```text
Normalize ToolResult into agent-understandable format
```

### Critical layer

```text
ToolResult (chaotic, tool-specific)
        ↓
Normalizer (THIS LAYER)
        ↓
ExecutionResult (clean, deterministic)
```

### Mapping function (frozen)

**Signature**

```python
def map_tool_result_to_execution_result(
    tool_result: ToolResult,
    step_id: str
) -> ExecutionResult:
```

### Mapping rules

**1. Identity**

```text
step_id = provided step_id
```

**2. Success + status**

```python
success = tool_result.success
status = "success" if success else "failure"
```

**3. Output mapping**

**`output.data`**

```python
output.data = tool_result.data or {}
```

**`output.summary` (important)**

MUST be generated:

```python
summary = summarize_tool_result(tool_result)
```

Rules:

```text
- short (1 line)
- LLM-readable
- no raw dumps
```

Examples:

| Tool      | Summary                           |
| --------- | --------------------------------- |
| open_file | Opened file agent_loop.py         |
| search    | Found 5 relevant results          |
| edit      | Patch applied successfully        |
| failure   | Patch failed due to test errors   |

**4. Error mapping (critical)**

Normalize error type:

```python
error_type = map_error_type(tool_result.error.type)
```

**Normalized enum**

Mapped values MUST be **`ErrorType`** as defined in **`SCHEMAS.md` Schema 0** (single global enum). Do not introduce a parallel list in code.

Mapping examples:

| Tool Error        | Execution Error  |
| ----------------- | ---------------- |
| FileNotFoundError | not_found        |
| TimeoutError      | timeout          |
| AssertionError    | tests_failed     |
| PermissionError   | permission_error |
| unknown           | unknown          |

Build error block:

```python
if not success:
    error = {
        "type": normalized_type,
        "message": tool_result.error.message,
        "details": tool_result.error.details
    }
else:
    error = None
```

**5. Metadata mapping**

```python
metadata = {
    "tool_name": tool_result.tool_name,
    "duration_ms": tool_result.metrics.duration_ms,
    "timestamp": now()
}
```

### Final output (ExecutionResult)

```json
{
  "step_id": "s1",

  "success": true,
  "status": "success",

  "output": {
    "data": {},
    "summary": "..."
  },

  "error": null,

  "metadata": {
    "tool_name": "open_file",
    "duration_ms": 45,
    "timestamp": "..."
  }
}
```

### Full mapping flow

```text
ToolHandler
   ↓
ToolResult (raw)
   ↓
Normalizer (mapping layer)
   ↓
ExecutionResult (clean)
   ↓
PlanStep.execution updated
   ↓
Failure handling / trace
```

### Examples

**Success**

ToolResult:

```json
{
  "tool_name": "open_file",
  "success": true,
  "data": {
    "file_path": "agent_loop.py"
  },
  "metrics": {
    "duration_ms": 50
  }
}
```

ExecutionResult:

```json
{
  "step_id": "s1",
  "success": true,
  "status": "success",

  "output": {
    "data": {
      "file_path": "agent_loop.py"
    },
    "summary": "Opened file agent_loop.py"
  },

  "error": null,

  "metadata": {
    "tool_name": "open_file",
    "duration_ms": 50,
    "timestamp": "..."
  }
}
```

**Failure**

ToolResult:

```json
{
  "tool_name": "edit",
  "success": false,
  "error": {
    "type": "AssertionError",
    "message": "Tests failed"
  },
  "metrics": {
    "duration_ms": 300
  }
}
```

ExecutionResult:

```json
{
  "step_id": "s3",
  "success": false,
  "status": "failure",

  "output": {
    "data": {},
    "summary": "Patch failed due to test errors"
  },

  "error": {
    "type": "tests_failed",
    "message": "Tests failed",
    "details": {}
  },

  "metadata": {
    "tool_name": "edit",
    "duration_ms": 300,
    "timestamp": "..."
  }
}
```

### Hard rules (non-negotiable)

**Rule 1**

```text
ALL tools MUST return ToolResult
```

**Rule 2**

```text
ONLY ExecutionResult is used by AgentLoop
```

**Rule 3**

```text
Error normalization MUST happen here (not in loop)
```

**Rule 4**

```text
output.summary MUST always exist
```

**Rule 5**

```text
ToolResult.raw MUST NEVER leak to ExecutionResult
```

### Principal verdict

This layer gives you:

```text
Messy tools → Clean execution → Deterministic planning
```

Without this:

```text
Retries break
Replanning breaks
Tracing becomes useless
```

---

## Next step

**“start implementation plan (phase-wise)”** — convert contracts into a surgical rollout.
