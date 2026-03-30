# Phase 1 — Schema layer (foundation)

**Scope:** This document is the authoritative Phase 1 specification. Implementation belongs in `agent_v2/schemas/` when this phase is executed; this file does not contain runtime code.

---

## Objective (non-negotiable)

Implement **all schemas as strict, typed, importable contracts**.

```text
NO runtime logic
NO tool calls
NO orchestration
ONLY structure + validation
```

---

## Why this matters

If schemas are weak:

- planner output becomes inconsistent
- executor breaks silently
- tracing becomes useless

This phase sets the **system correctness ceiling**.

---

## Directory structure (create exactly)

```text
agent_v2/schemas/
    __init__.py

    agent_state.py
    plan.py
    execution.py
    exploration.py
    replan.py
    tool.py
    trace.py
    context.py
    policies.py
    output.py
```

---

## Implementation standard (lock this)

- Use **dataclasses** OR **Pydantic** (preferred: **Pydantic v2** if available).

**Requirements:**

```text
- All fields typed
- Enums strictly defined
- No optional ambiguity unless required
- JSON serializable
- No circular imports
```

---

## File-by-file specification

### 1. `plan.py`

**Must include:** `PlanDocument`, `PlanStep`.

**PlanStep (strict):**

| Area | Fields |
|------|--------|
| Identity | `step_id: str`, `index: int` |
| Classification | `type: Literal["explore","analyze","modify","validate","finish"]` |
| Intent | `goal: str` |
| Action | `action: Literal["search","open_file","edit","run_tests","shell","finish"]` |
| I/O | `inputs: dict`, `outputs: dict` |
| Graph | `dependencies: list[str]` |
| **execution** | `status: Literal["pending","in_progress","completed","failed"]`, `attempts: int`, `max_attempts: int`, `started_at: Optional[str]`, `completed_at: Optional[str]`, `last_result: Optional[dict]` |
| **failure** | `is_recoverable: bool`, `failure_type: Optional[ErrorType]`, `retry_strategy: Literal["retry_same","adjust_inputs","abort"]`, `replan_required: bool` |

**PlanDocument:**

- `plan_id: str`
- `instruction: str`
- `understanding: str`
- `sources: list[dict]`
- `steps: list[PlanStep]`
- `risks: list[dict]`
- `completion_criteria: list[str]`
- `metadata: dict`

**Constraints:** Pydantic `BaseModel`; field validation where obvious; no methods except validation.

---

### 2. `execution.py`

**Must include:** `ErrorType` (Literal or Enum), `ExecutionStep`, `ExecutionResult`, `RetryState`.

**ErrorType** — values MUST match `SCHEMAS.md` Schema 0 (single source of truth).

**ExecutionStep:**

- `step_id: str`
- `action: Literal["search","open_file","edit","run_tests","shell","finish"]`
- `arguments: dict`
- `reasoning: str`

**ExecutionResult:**

- `step_id: str`
- `success: bool`
- `status: Literal["success","failure"]`
- **output:** `data: dict`, `summary: str`
- **error (optional):** `type: ErrorType`, `message: str`, `details: dict`
- **metadata:** `tool_name: str`, `duration_ms: int`, `timestamp: str`

**RetryState:**

- `step_id: str`
- `attempts: int`
- `max_attempts: int`
- `last_error_type: Optional[str]`
- `strategy: Literal["retry_same","adjust_inputs","abort"]`

**Constraints:** Strict typing; no logic.

---

### 3. `exploration.py`

**Must include:** `ExplorationItem`, `ExplorationResult`.

**ExplorationItem:**

- `item_id: str`
- `type: Literal["file","search","command","other"]`
- **source:** `ref: str`, `location: Optional[str]`
- **content:** `summary: str`, `key_points: list[str]`, `entities: list[str]`
- **relevance:** `score: float`, `reason: str`
- **metadata:** `timestamp: str`, `tool_name: str`

**ExplorationResult:**

- `exploration_id: str`
- `instruction: str`
- `items: list[ExplorationItem]`
- **summary:** `overall: str`, `key_findings: list[str]`, `knowledge_gaps: list[str]`, `knowledge_gaps_empty_reason: Optional[str]` (required non-empty when `knowledge_gaps` empty; null when non-empty)
- **metadata:** `total_items: int`, `created_at: str`

**Constraints:** `summary` and `key_points` required; score range not enforced in schema.

---

### 4. `replan.py`

**Must include:** `ReplanContext`, `PlannerInput` (type alias), `ReplanRequest`, `ReplanResult`.

**ReplanContext:**

- `failure_context` (same shape as `ReplanRequest.failure_context`)
- `completed_steps: list[{ step_id, summary }]`
- `exploration_summary: Optional[{ key_findings, knowledge_gaps, overall }]`

**PlannerInput:**

- `TypeAlias` = `ExplorationResult | ReplanContext`

**ReplanRequest:**

- `replan_id: str`
- `instruction: str`
- **original_plan:** `plan_id: str`, `failed_step_id: str`, `current_step_index: int`
- **failure_context:** `step_id: str`, `error: { type, message }`, `attempts: int`, `last_output_summary: str`
- **execution_context:** `completed_steps: list[dict]`, `partial_results: list[dict]`
- **exploration_context:** `key_findings: list[str]`, `knowledge_gaps: list[str]`
- **constraints:** `max_steps: int`, `preserve_completed: bool`
- **metadata:** `timestamp: str`, `replan_attempt: int`

**ReplanResult:**

- `replan_id: str`
- `status: Literal["success","failed"]`
- **new_plan:** `Optional[{ plan_id: str }]` — **`None`** when **`status == "failed"`**; required when **`status == "success"`** (see **`SCHEMAS.md`** Schema 6)
- **changes:** `type: Literal["partial_update","full_replacement"]`, `summary: str`, `modified_steps: list[str]`, `added_steps: list[str]`, `removed_steps: list[str]`
- **reasoning:** `failure_analysis: str`, `strategy: str`
- **validation:** `is_valid: bool`, `issues: list[str]`
- **metadata:** `timestamp: str`, `replan_attempt: int`

---

### 5. `tool.py`

**Must include:** `ToolCall`, `ToolError`, `ToolResult`.

**ToolCall:**

- `tool_name: str`
- `arguments: dict`

**ToolError:**

- `type: str`
- `message: str`
- `details: dict`

**ToolResult:**

- `tool_name: str`
- `success: bool`
- `data: dict`
- `error: Optional[ToolError]`
- **metrics:** `duration_ms: int`
- `raw: Optional[dict]`

**Constraint:** `ToolResult` **must not** include `step_id`.

---

### 6. `trace.py`

**Must include:** `TraceStep`, `Trace`.

**TraceStep:**

- `step_id: str`
- `plan_step_index: int`
- `action: str`
- `target: str`
- `success: bool`
- `error: Optional[{ type: ErrorType, message: str }]` (or nested Pydantic model; **not** a plain string)
- `duration_ms: int`
- `kind: Literal["tool", "llm", "diff", "memory"]` — discriminator (**not** a second `type` field); see **`SUPPORTING_SCHEMAS.md`** §7
- `input: dict`, `output: dict`, `metadata: dict` (default empty)

**Trace:**

- `trace_id: str`
- `instruction: str`
- `plan_id: str`
- `steps: list[TraceStep]`
- `status: Literal["success","failure"]`
- **metadata:** `total_steps: int`, `total_duration_ms: int`

---

### 7. `context.py`

**Must include:** `ContextItem`, `ContextWindow`.

**ContextItem:**

- `source: str`
- `content_summary: str`
- `relevance_score: float`

**ContextWindow:**

- `items: list[ContextItem]`
- `max_tokens: int`

---

### 8. `policies.py`

**Must include:** `ExecutionPolicy`, `FailurePolicy`.

**ExecutionPolicy:**

- `max_steps: int`
- `max_retries_per_step: int`
- `max_replans: int`

**FailurePolicy:**

- `replan_on_failure: bool`
- `abort_on_unrecoverable: bool`

---

### 9. `output.py`

**Must include:** `ExecutionSummary`, `FinalOutput`.

**ExecutionSummary:**

- `total_steps: int`
- `successful_steps: int`
- `failed_steps: int`
- `replans: int`

**FinalOutput:**

- `status: Literal["success","failure"]`
- `result: str`
- `plan_summary: str`
- `execution_summary: str`
- `errors: list[str]`

---

### 10. `agent_state.py`

Phase 1 directory listing includes `agent_state.py`. Define aggregate state models here (typed containers for plan, execution, exploration, trace slices, policies, etc.) consistent with **`ARCHITECTURE_FREEZE.md`**, **`CONTRACT_LAYER.md`**, and **`SCHEMAS.md`**. No imports from orchestration, dispatcher, or tool implementations—only other `agent_v2.schemas` types and stdlib / pydantic.

---

## Validation step (mandatory after implementation)

```python
from agent_v2.schemas.plan import PlanDocument
from agent_v2.schemas.execution import ExecutionResult
```

**Verify:**

```text
✅ imports work
✅ no circular deps
✅ .model_dump() works
```

---

## Common failure modes

```text
❌ Optional everywhere, loose typing, missing enums
❌ Logic inside schema modules
❌ Referencing runtime modules (orchestrator, dispatcher, tools)
```

---

## Exit criteria (strict)

```text
✅ All schemas implemented
✅ All enums strict
✅ No runtime imports in schema layer
✅ JSON serialization verified
```

---

## Next step (after Phase 1 is done)

**Phase 2 — Tool normalization** (real integration begins). Do not start Phase 2 until Phase 1 exit criteria are met.

---

## Principal verdict

- Phase 1 correct → eliminates most downstream contract bugs.
- Phase 1 sloppy → everything downstream becomes unstable.
