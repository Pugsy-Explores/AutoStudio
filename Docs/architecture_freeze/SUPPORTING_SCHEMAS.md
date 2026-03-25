# Supporting schemas (contract layer complete)

Remaining critical schemas only. No redundancy, no fluff.

---

## 1. `AgentState` (extended, final)

### Schema

```json
{
  "instruction": "string",

  "history": [
    {
      "step_id": "string",
      "action": "string",
      "observation": "string"
    }
  ],

  "current_plan": {
    "plan_id": "string"
  },

  "plan_index": 0,

  "exploration_result": {
    "exploration_id": "string"
  },

  "step_results": [
    {
      "step_id": "string",
      "result_summary": "string"
    }
  ],

  "metadata": {
    "runtime": "agent_v2",
    "retry_count": 0,
    "failure_streak": 0,
    "replan_attempt": 0
  }
}
```

### Rules

```text
- current_plan MUST exist before execution
- plan_index MUST track current step
- history is append-only
```

---

## 2. `ExecutionStep` (LLM → executor contract)

### Schema

```json
{
  "step_id": "string",

  "action": "search | open_file | edit | run_tests | shell | finish",

  "arguments": {},

  "reasoning": "string"
}
```

### Purpose

```text
LLM fills arguments ONLY
NOT allowed to change action
```

---

## 3. `RetryState`

### Schema

```json
{
  "step_id": "string",

  "attempts": 0,

  "max_attempts": 2,

  "last_error_type": "ErrorType",

  "strategy": "retry_same | adjust_inputs | abort"
}
```

### Purpose

```text
Optional runtime view of retry progress (logging, UI). Source of truth remains PlanStep.execution — see SCHEMAS.md Retry authority.
```

---

## 4. `ValidationResult`

### Schema

```json
{
  "is_valid": true,

  "errors": ["string"],

  "warnings": ["string"]
}
```

### Used for

```text
- plan validation
- action validation
```

---

## 5. `ActionRequest` (LLM input)

### Schema

```json
{
  "instruction": "string",

  "current_step": {
    "step_id": "string",
    "goal": "string",
    "action": "string"
  },

  "context": {
    "history": [],
    "exploration_summary": "string"
  }
}
```

### Purpose

```text
What LLM sees when generating arguments
```

---

## 6. `ActionResponse` (LLM output)

### Schema

```json
{
  "arguments": {},

  "confidence": 0.0
}
```

### Rule

```text
NO action field here
```

---

## 7. `TraceStep`

### Schema

```json
{
  "step_id": "string",

  "plan_step_index": 0,

  "action": "string",

  "target": "string",

  "success": true,

  "error": {
    "type": "ErrorType",
    "message": "string"
  },

  "duration_ms": 0
}
```

**`error`**

```text
- null when success = true
- when success = false, structured error using ErrorType (SCHEMAS.md Schema 0) — same taxonomy as ExecutionResult.error
```

---

## 8. `Trace`

### Schema

```json
{
  "trace_id": "string",

  "instruction": "string",

  "plan_id": "string",

  "steps": [],

  "status": "success | failure",

  "metadata": {
    "total_steps": 0,
    "total_duration_ms": 0
  }
}
```

(`steps` is an array of `TraceStep`.)

---

## 9. `ContextItem`

### Schema

```json
{
  "source": "string",

  "content_summary": "string",

  "relevance_score": 0.0
}
```

---

## 10. `ContextWindow`

### Schema

```json
{
  "items": [],

  "max_tokens": 0
}
```

(`items` is an array of `ContextItem`.)

---

## 11. `ExecutionPolicy`

### Schema

```json
{
  "max_steps": 20,

  "max_retries_per_step": 2,

  "max_replans": 2
}
```

**Retry authority (with `PlanStep` / `RetryState`):** **`max_retries_per_step`** seeds **`PlanStep.execution.max_attempts`** when a plan is created or accepted. **`PlanExecutor`** alone increments **`execution.attempts`**. **`RetryState`** is an optional mirror for diagnostics — see **`SCHEMAS.md`** (Cross-cutting — Retry authority).

---

## 12. `FailurePolicy`

### Schema

```json
{
  "replan_on_failure": true,

  "abort_on_unrecoverable": true
}
```

---

## 13. `FinalOutput`

### Schema

```json
{
  "status": "success | failure",

  "result": "string",

  "plan_summary": "string",

  "execution_summary": "string",

  "errors": ["string"]
}
```

---

## 14. `ExecutionSummary`

### Schema

```json
{
  "total_steps": 0,

  "successful_steps": 0,

  "failed_steps": 0,

  "replans": 0
}
```

---

## 15. `ToolCall`

### Schema

```json
{
  "tool_name": "string",

  "arguments": {}
}
```

---

## 16. `ToolError` (normalized internal)

### Schema

```json
{
  "type": "string",

  "message": "string",

  "details": {}
}
```

---

## Final check

You now have:

### Control layer

```text
PlanDocument
PlanStep
ReplanRequest
ReplanResult
```

### Execution layer

```text
ExecutionStep
ExecutionResult
RetryState
```

### Tool layer

```text
ToolCall
ToolResult
ToolError
```

### Context + exploration

```text
ExplorationResult
ContextWindow
```

### Orchestration

```text
AgentState
ExecutionPolicy
FailurePolicy
```

### Trace + output

```text
Trace
TraceStep
FinalOutput
ExecutionSummary
```

### Principal verdict

Complete contract system:

```text
NO undefined boundaries
NO implicit behavior
NO hidden coupling
```

Production-grade, buildable without chaos.

---

## Next step

**“start implementation plan (strict phases)”** — surgical build plan with no regressions.
