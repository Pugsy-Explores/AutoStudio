# Frozen schemas (contract layer)

No drift, no ambiguity. Locked before implementation.

---

## Schema 0 — `ErrorType` (frozen)

**Single classification enum** for failures across **`ExecutionResult.error.type`**, **`PlanStep.failure.failure_type`**, trace, replan, and Langfuse. Do not fork per layer.

```text
tool_error
validation_error
not_found
timeout
tests_failed
permission_error
unknown
```

**Rules**

```text
- Normalization from ToolResult / tool-native errors happens in the dispatcher → ExecutionResult mapping (see TOOL_EXECUTION_CONTRACT.md).
- TraceStep.error.type MUST use the same enum (see SUPPORTING_SCHEMAS.md TraceStep).
```

---

## Cross-cutting — Retry authority (single rule)

**Problem:** **`ExecutionPolicy.max_retries_per_step`**, **`PlanStep.execution.max_attempts`**, and **`RetryState`** must not diverge.

**Rule (frozen):**

```text
1. ExecutionPolicy.max_retries_per_step is the ONLY policy source for per-step retry budget.
2. When a PlanDocument is loaded or accepted (planner output or replan swap), each PlanStep.execution.max_attempts MUST be set from ExecutionPolicy (same value for all steps unless a future per-step override is explicitly added to SCHEMAS).
3. PlanExecutor (and its retry wrapper) is the ONLY component that increments execution.attempts and transitions execution.status for a step.
4. RetryState is OPTIONAL: a convenience projection of (step_id, attempts, max_attempts, …) for logging/UI — NOT a second source of truth. If present, it MUST mirror PlanStep.execution for that step.
```

**Planner** sets **`execution.attempts = 0`** and **`max_attempts`** from policy at plan creation; **must not** mutate **`execution.status`** during a run (see PlanStep hard rules).

---

## Schema 1 — `PlanDocument` (frozen)

The **control plane** of the entire system. Everything else depends on this being correct.

### Purpose

```text
Single source of truth for execution
```

- defines WHAT to do
- defines ORDER
- defines INTENT
- executor MUST follow it

### Strict schema (JSON)

```json
{
  "plan_id": "string",

  "instruction": "string",

  "understanding": "string",

  "sources": [
    {
      "type": "file | search | other",
      "ref": "string",
      "summary": "string"
    }
  ],

  "steps": [
    {
      "step_id": "string",
      "index": 1,

      "type": "explore | analyze | modify | validate | finish",

      "goal": "string",

      "action": "search | open_file | edit | run_tests | shell | finish",

      "inputs": {
        "expected": "object describing expected inputs (optional)"
      },

      "outputs": {
        "expected": "object describing expected outputs (optional)"
      },

      "dependencies": ["step_id"]
    }
  ],

  "risks": [
    {
      "risk": "string",
      "impact": "low | medium | high",
      "mitigation": "string"
    }
  ],

  "completion_criteria": [
    "string"
  ],

  "metadata": {
    "created_at": "timestamp",
    "version": 1
  }
}
```

**Step status (critical):** There is **no** `steps[].status` on `PlanDocument`. Per-step lifecycle lives **only** in **`PlanStep.execution.status`** (Schema 2). Any “status” derived for UI is computed from **`execution.status`** (or mirrored there by the executor only).

### Field contracts (strict)

**`plan_id`**

- unique identifier
- required for traceability

**`instruction`**

- original user instruction
- immutable

**`understanding`**

- LLM’s interpretation of task
- MUST be explicit (no implicit reasoning)

**`sources[]`**

- derived ONLY from exploration phase
- no hallucinated sources allowed

**`steps[]` (critical)**

Each step must:

- have deterministic order (`index`)
- define **intent (`type`)**
- define **execution (`action`)**

**Rule**

```text
type ≠ action
```

Example:

```text
type = analyze
action = open_file
```

**`dependencies`**

- DAG-style execution support
- allows future parallelization

**`risks[]`**

- required for production system
- must not be empty (at least 1)

**`completion_criteria[]`**

- defines termination condition
- executor checks against this

**`metadata`**

- versioning required for evolution

### Hard rules (non-negotiable)

**Rule 1**

```text
Plan MUST be executable without LLM deciding structure
```

**Rule 2**

```text
Executor CANNOT change steps
```

**Rule 3**

```text
Every step MUST map to exactly one action
```

**Rule 4**

```text
At least one of:
- modify
- analyze
- finish
must exist
```

**Rule 5**

```text
Final step MUST be type=finish
```

### Minimal example (valid)

```json
{
  "plan_id": "plan_001",
  "instruction": "Explain AgentLoop",

  "understanding": "User wants explanation of AgentLoop implementation",

  "sources": [
    {
      "type": "file",
      "ref": "agent_v2/runtime/agent_loop.py",
      "summary": "Contains main loop logic"
    }
  ],

  "steps": [
    {
      "step_id": "s1",
      "index": 1,
      "type": "analyze",
      "goal": "Read AgentLoop implementation",
      "action": "open_file",
      "dependencies": [],
      "execution": {
        "status": "pending",
        "attempts": 0,
        "max_attempts": 2,
        "started_at": null,
        "completed_at": null,
        "last_result": { "success": null, "error": null, "output_summary": null }
      },
      "failure": {
        "is_recoverable": true,
        "failure_type": null,
        "retry_strategy": "retry_same",
        "replan_required": false
      }
    },
    {
      "step_id": "s2",
      "index": 2,
      "type": "finish",
      "goal": "Provide explanation",
      "action": "finish",
      "dependencies": ["s1"],
      "execution": {
        "status": "pending",
        "attempts": 0,
        "max_attempts": 2,
        "started_at": null,
        "completed_at": null,
        "last_result": { "success": null, "error": null, "output_summary": null }
      },
      "failure": {
        "is_recoverable": true,
        "failure_type": null,
        "retry_strategy": "retry_same",
        "replan_required": false
      }
    }
  ],

  "risks": [
    {
      "risk": "File may not exist",
      "impact": "low",
      "mitigation": "search for correct file"
    }
  ],

  "completion_criteria": [
    "AgentLoop explanation provided"
  ],

  "metadata": {
    "created_at": "2026-01-01T00:00:00Z",
    "version": 1
  }
}
```

### Why this schema is correct

- supports exploration-first systems
- supports deterministic execution
- supports replanning
- supports tracing + UI
- matches planner–executor architecture used in production agents

---

## Schema 2 — `PlanStep` (frozen)

If this is wrong, execution becomes unreliable.

### Full structure

```json
{
  "step_id": "string",
  "index": 1,

  "type": "explore | analyze | modify | validate | finish",

  "goal": "string",

  "action": "search | open_file | edit | run_tests | shell | finish",

  "inputs": {
    "expected": {}
  },

  "outputs": {
    "expected": {}
  },

  "dependencies": ["step_id"],

  "execution": {
    "status": "pending | in_progress | completed | failed",

    "attempts": 0,

    "max_attempts": 2,

    "started_at": null,
    "completed_at": null,

    "last_result": {
      "success": null,
      "error": null,
      "output_summary": null
    }
  },

  "failure": {
    "is_recoverable": true,

    "failure_type": null,

    "retry_strategy": "retry_same | adjust_inputs | abort",

    "replan_required": false
  }
}
```

### Section-by-section contract

**1. Identity + order**

```json
{
  "step_id": "s1",
  "index": 1
}
```

Rules:

```text
- step_id MUST be unique
- index MUST be strictly ordered
```

**2. Intent layer**

```json
{
  "type": "analyze",
  "goal": "Understand AgentLoop implementation"
}
```

**Rule**

```text
type = intent
goal = human-readable purpose
```

**3. Execution layer**

```json
{
  "action": "open_file"
}
```

**Rule**

```text
One step = one action
```

**4. I/O contracts**

```json
{
  "inputs": { "expected": {} },
  "outputs": { "expected": {} }
}
```

Purpose:

- helps LLM generate correct arguments
- helps validation layer

**5. Dependencies**

```json
{
  "dependencies": ["s1"]
}
```

Meaning:

```text
This step requires previous steps to complete
```

### 6. Execution block (critical)

**Runtime-owned (not planner)**

```json
{
  "execution": {
    "status": "pending",
    "attempts": 0,
    "max_attempts": 2,
    "started_at": null,
    "completed_at": null,
    "last_result": {
      "success": null,
      "error": null,
      "output_summary": null
    }
  }
}
```

**Semantics**

**`status`**

```text
pending → not started
in_progress → executing
completed → success
failed → exhausted retries
```

**`attempts`**

```text
incremented every execution attempt
```

**`max_attempts`**

```text
Upper bound for attempts for this step (paired with execution.attempts).
```

**`last_result`**

```json
{
  "success": true,
  "error": "tests_failed",
  "output_summary": "patch rejected"
}
```

**Important**

```text
Store summary, NOT raw output
```

### 7. Failure block (planner + executor shared)

```json
{
  "failure": {
    "is_recoverable": true,
    "failure_type": null,
    "retry_strategy": "retry_same | adjust_inputs | abort",
    "replan_required": false
  }
}
```

**Field meanings**

**`is_recoverable`**

```text
true → retry allowed
false → immediate replan
```

**`failure_type`**

Uses **`ErrorType`** (Schema 0). Executor assigns from **`ExecutionResult.error.type`** after normalization.

**`retry_strategy`**

```text
retry_same → retry identical step
adjust_inputs → modify arguments (LLM-assisted)
abort → stop step immediately
```

**`replan_required`**

```text
true → trigger planner
false → continue local handling
```

### 8. Execution state machine (frozen)

```text
pending
  ↓
in_progress
  ↓
completed → DONE

or

in_progress
  ↓
failed (after max_attempts)
  ↓
replan trigger
```

### 9. Failure flow (deterministic)

**Case A — recoverable**

```text
attempt 1 → fail
attempt 2 → success
→ completed
```

**Case B — retry exhausted**

```text
attempt 1 → fail
attempt 2 → fail
→ execution.status = failed
→ failure.replan_required = true
```

### Hard rules (non-negotiable)

**Rule 1**

```text
Planner CANNOT set execution.status
```

**Rule 2**

```text
Executor CANNOT change type or action
```

**Rule 3**

```text
failure_type MUST be set on failure
```

**Rule 4**

```text
replan_required ONLY true after retry exhaustion
```

**Rule 5**

```text
execution.attempts MUST increment atomically
```

### Example — failed step

```json
{
  "step_id": "s3",
  "index": 3,
  "type": "modify",
  "goal": "Add retry logic to AgentLoop",
  "action": "edit",

  "dependencies": ["s2"],

  "execution": {
    "status": "failed",
    "attempts": 2,
    "max_attempts": 2,
    "started_at": "2026-01-01T00:00:00Z",
    "completed_at": "2026-01-01T00:01:00Z",
    "last_result": {
      "success": false,
      "error": "tests_failed",
      "output_summary": "patch rejected due to failing tests"
    }
  },

  "failure": {
    "is_recoverable": false,
    "failure_type": "tests_failed",
    "retry_strategy": "abort",
    "replan_required": true
  }
}
```

### Why this design is correct

**Separation of concerns**

```text
Planner → defines WHAT
Executor → manages HOW
```

**Deterministic failure handling**

```text
retry → fail → replan
```

**Traceable**

Every step:

```text
plan → execution → result → failure
```

**Scalable**

Supports:

- retries
- replanning
- UI trace
- analytics

---

## Schema 3 — `ExecutionResult` (frozen)

Bridge between tools and the plan system. Tool-agnostic, failure-aware.

### Full structure

```json
{
  "step_id": "string",

  "success": true,

  "status": "success | failure",

  "output": {
    "data": {},
    "summary": "string"
  },

  "error": {
    "type": "ErrorType",
    "message": "string",
    "details": {}
  },

  "metadata": {
    "tool_name": "string",
    "duration_ms": 0,
    "timestamp": "ISO-8601"
  }
}
```

### Field contracts (strict)

**1. Identity**

```json
{
  "step_id": "s3"
}
```

Rule:

```text
Must match PlanStep.step_id exactly
```

**2. Success vs status**

```json
{
  "success": true,
  "status": "success"
}
```

**Rule**

```text
success = boolean (fast checks)
status = enum (semantic clarity)
```

Valid combos:

| success | status  |
| ------- | ------- |
| true    | success |
| false   | failure |

**3. Output block**

```json
{
  "output": {
    "data": {},
    "summary": "string"
  }
}
```

**`data`**

```text
Structured output (machine-readable)
```

Examples:

```json
{ "file_content": "...", "lines": 120 }
```

```json
{ "patch_applied": true }
```

**`summary`**

```text
Human-readable, short description
```

**Rule**

```text
ALWAYS include summary
NEVER rely on raw data for reasoning
```

**4. Error block**

```json
{
  "error": {
    "type": "...",
    "message": "...",
    "details": {}
  }
}
```

When present:

```text
Only when success = false
```

**`type`**

Must be **`ErrorType`** (Schema 0). Do not define a second enum here.

**`message`**

```text
Short, LLM-readable explanation
```

**`details`**

```text
Optional structured debugging info
```

**Rule**

```text
error.type MUST be set when success=false
```

**5. Metadata**

```json
{
  "metadata": {
    "tool_name": "open_file",
    "duration_ms": 120,
    "timestamp": "2026-01-01T00:00:00Z"
  }
}
```

Purpose:

```text
Tracing, analytics, performance monitoring
```

### Normalization rule (critical)

All tools MUST return results in this format.

**Not allowed**

```text
Different formats per tool
Raw strings
Custom dicts
```

**Required**

```text
Every tool → normalized → ExecutionResult
```

### Execution pipeline (how it fits)

```text
Tool Handler
   ↓
Raw Result
   ↓
Normalize → ExecutionResult
   ↓
AgentLoop
   ↓
Update PlanStep.execution + failure
   ↓
Trace
```

### Failure mapping (important)

**ExecutionResult → PlanStep.failure**

Example mapping:

```text
ExecutionResult.error.type = "tests_failed"
→ PlanStep.failure.failure_type = "tests_failed"
```

Rule:

```text
ExecutionResult is source of truth for failure classification
```

### Examples

**Success — open_file**

```json
{
  "step_id": "s1",
  "success": true,
  "status": "success",

  "output": {
    "data": {
      "file_path": "agent_loop.py"
    },
    "summary": "Opened file agent_loop.py successfully"
  },

  "error": null,

  "metadata": {
    "tool_name": "open_file",
    "duration_ms": 45,
    "timestamp": "2026-01-01T00:00:00Z"
  }
}
```

**Failure — edit**

```json
{
  "step_id": "s3",
  "success": false,
  "status": "failure",

  "output": {
    "data": {},
    "summary": "Patch application failed"
  },

  "error": {
    "type": "tests_failed",
    "message": "Tests failed after applying patch",
    "details": {
      "failed_tests": 3
    }
  },

  "metadata": {
    "tool_name": "edit",
    "duration_ms": 320,
    "timestamp": "2026-01-01T00:01:00Z"
  }
}
```

### Hard rules (non-negotiable)

**Rule 1**

```text
ExecutionResult MUST exist for every step execution
```

**Rule 2**

```text
error MUST be null if success=true
```

**Rule 3**

```text
output.summary MUST always be present
```

**Rule 4**

```text
No raw tool outputs outside output.data
```

**Rule 5**

```text
metadata.tool_name MUST match ToolDefinition
```

### Why this schema is correct

**Tool-agnostic**

Works for:

```text
search
file read
edit
tests
shell
```

**Failure-aware**

Supports:

```text
retry logic
replanning
error classification
```

**Trace-ready**

Directly maps to:

```text
Langfuse spans
UI visualization
```

**Deterministic**

No ambiguity in:

```text
success vs failure
error types
output meaning
```

---

## Schema 4 — `ExplorationResult` (frozen)

Foundation of planning quality. Strict, source-grounded, LLM-consumable, non-noisy.

### Full structure

```json
{
  "exploration_id": "string",

  "instruction": "string",

  "items": [
    {
      "item_id": "string",

      "type": "file | search | command | other",

      "source": {
        "ref": "string",
        "location": "string"
      },

      "content": {
        "summary": "string",
        "key_points": ["string"],
        "entities": ["string"]
      },

      "relevance": {
        "score": 0.0,
        "reason": "string"
      },

      "metadata": {
        "timestamp": "ISO-8601",
        "tool_name": "string"
      }
    }
  ],

  "summary": {
    "overall": "string",
    "key_findings": ["string"],
    "knowledge_gaps": ["string"],
    "knowledge_gaps_empty_reason": "string | null"
  },

  "metadata": {
    "total_items": 0,
    "created_at": "ISO-8601"
  }
}
```

### Field contracts (strict)

**1. Identity**

```json
{
  "exploration_id": "exp_001",
  "instruction": "User task"
}
```

Rules:

```text
- exploration_id MUST be unique
- instruction MUST match PlanDocument.instruction
```

**2. `items[]` (critical)**

Atomic unit of knowledge. Each item represents:

```text
One source → one summarized understanding
```

**Hard rule**

```text
NO raw data dumps
ONLY summarized, structured content
```

**3. Item structure**

**`type`**

```text
file → local file
search → retrieval result
command → shell output
other → fallback
```

**`source`**

```json
{
  "ref": "agent_v2/runtime/agent_loop.py",
  "location": "lines 1-120"
}
```

Meaning:

```text
ref = identifier (file path / query / command)
location = optional granularity
```

**`content` (most important)**

```json
{
  "summary": "string",
  "key_points": ["string"],
  "entities": ["string"]
}
```

**`summary`**

```text
- MUST be concise (1–3 sentences)
- MUST capture meaning, not text
```

**`key_points`**

```text
- bullet facts
- no fluff
- directly useful for planning
```

**`entities`**

```text
- function names
- classes
- modules
- key variables
```

Enables:

```text
better planning + context selection
```

**`relevance`**

```json
{
  "score": 0.85,
  "reason": "Contains core execution logic"
}
```

Rules:

```text
score ∈ [0,1]
planner may filter by relevance
```

**`metadata`**

```json
{
  "timestamp": "...",
  "tool_name": "open_file"
}
```

Purpose:

```text
traceability + debugging
```

---

**4. Summary block (critical for planner)**

```json
{
  "summary": {
    "overall": "...",
    "key_findings": ["..."],
    "knowledge_gaps": ["..."],
    "knowledge_gaps_empty_reason": "string | null"
  }
}
```

**`overall`**

```text
High-level synthesis of exploration
```

**`key_findings`**

```text
Facts that influence plan
```

**`knowledge_gaps`**

```text
What is still unknown → drives additional exploration or plan steps when non-empty.

Empty list is valid when there are no material gaps (see Hard rules Rule 5).
```

**`knowledge_gaps_empty_reason`**

```text
When knowledge_gaps is empty: REQUIRED non-empty string (audit why no gaps).
When knowledge_gaps is non-empty: MUST be null or omitted.
```

**5. Metadata**

```json
{
  "total_items": 3,
  "created_at": "..."
}
```

### Hard rules (non-negotiable)

**Rule 1**

```text
Every item MUST have summary + key_points
```

**Rule 2**

```text
NO raw file contents stored
```

**Rule 3**

```text
Exploration MUST be bounded (≤ 6 items)
```

**Rule 4**

```text
Sources MUST be real (no hallucinated refs)
```

**Rule 5**

```text
knowledge_gaps SHOULD list genuine unknowns or verification needs for planning.

MAY be empty when exploration is sufficient and no material gaps remain — do NOT invent placeholder gaps.

When knowledge_gaps is empty, knowledge_gaps_empty_reason MUST be a non-empty string explaining why (e.g. "Task scope fully covered by items above" or "Single-file read sufficient for question").

When knowledge_gaps is non-empty, knowledge_gaps_empty_reason MUST be null or omitted.
```

### Example (valid)

```json
{
  "exploration_id": "exp_001",
  "instruction": "Explain AgentLoop",

  "items": [
    {
      "item_id": "i1",
      "type": "file",

      "source": {
        "ref": "agent_v2/runtime/agent_loop.py",
        "location": "full file"
      },

      "content": {
        "summary": "AgentLoop manages execution of steps using dispatcher and validator.",
        "key_points": [
          "Loop iterates until finish condition",
          "Uses dispatcher to execute tools",
          "Maintains state history"
        ],
        "entities": ["AgentLoop", "Dispatcher", "Validator"]
      },

      "relevance": {
        "score": 0.95,
        "reason": "Core execution logic"
      },

      "metadata": {
        "timestamp": "2026-01-01T00:00:00Z",
        "tool_name": "open_file"
      }
    }
  ],

  "summary": {
    "overall": "AgentLoop orchestrates execution and tool dispatch.",
    "key_findings": [
      "Execution is step-based",
      "State updated per iteration"
    ],
    "knowledge_gaps": [
      "How failure handling works",
      "Retry logic details"
    ],
    "knowledge_gaps_empty_reason": null
  },

  "metadata": {
    "total_items": 1,
    "created_at": "2026-01-01T00:00:00Z"
  }
}
```

**Example (valid — `knowledge_gaps` empty):** `knowledge_gaps` is `[]` and **`knowledge_gaps_empty_reason`** is required (non-empty string), e.g. `"Single source file read; question fully answerable from cited content."`

### Why this schema is correct

**Prevents garbage context**

```text
No raw dumps → only distilled info
```

**Planner-ready**

```text
summary + key_findings → direct plan input
```

**Supports traceability**

```text
every insight tied to source
```

**Enables future improvements**

- ranking
- filtering
- caching
- retrieval

### Principal verdict

This schema ensures:

```text
Exploration → Structured Knowledge → High-quality Plan
```

Without this:

```text
Planner = hallucination engine ❌
```

---

## Schema 4b — `ReplanContext` (frozen)

**Purpose:** Minimal, planner-consumable input for **replanning** after failure. Built from **`ReplanRequest`** (and runtime state); not a substitute for full **`ExplorationResult`** on the first pass.

### Structure

```json
{
  "failure_context": {
    "step_id": "string",
    "error": {
      "type": "ErrorType",
      "message": "string"
    },
    "attempts": 0,
    "last_output_summary": "string"
  },

  "completed_steps": [
    {
      "step_id": "string",
      "summary": "string"
    }
  ],

  "exploration_summary": {
    "key_findings": ["string"],
    "knowledge_gaps": ["string"],
    "overall": "string"
  }
}
```

**`exploration_summary`** — optional in strongly typed code (`Optional[...]`); when present, carries condensed prior exploration + gaps for the planner. May be synthesized from **`ReplanRequest.exploration_context`** and original **`ExplorationResult`**.

**Rules**

```text
- failure_context MUST be complete (same semantics as ReplanRequest.failure_context).
- completed_steps MAY be empty when failure occurs before any step completed successfully.
```

---

## Schema 4c — `PlannerInput` (frozen)

**Union type** for the planner’s **context input** (not the user instruction string):

```text
PlannerInput = ExplorationResult | ReplanContext
```

**Semantics**

```text
- Initial plan (after exploration): pass ExplorationResult.
- Replan after failure: pass ReplanContext derived from ReplanRequest + state.
```

Implementations MAY use a single parameter name (e.g. `planner_input` or `exploration`) with type **`PlannerInput`**; the name is less important than the **union** being enforced in **`PlannerV2`** (Phase 4).

---

## Schema 5 — `ReplanRequest` (frozen)

Control handoff between execution → planning. Strict, minimal, grounded in runtime state.

### Full structure

```json
{
  "replan_id": "string",

  "instruction": "string",

  "original_plan": {
    "plan_id": "string",
    "failed_step_id": "string",
    "current_step_index": 0
  },

  "failure_context": {
    "step_id": "string",

    "error": {
      "type": "ErrorType",
      "message": "string"
    },

    "attempts": 0,

    "last_output_summary": "string"
  },

  "execution_context": {
    "completed_steps": [
      {
        "step_id": "string",
        "summary": "string"
      }
    ],

    "partial_results": [
      {
        "step_id": "string",
        "result_summary": "string"
      }
    ]
  },

  "exploration_context": {
    "key_findings": ["string"],
    "knowledge_gaps": ["string"]
  },

  "constraints": {
    "max_steps": 0,
    "preserve_completed": true
  },

  "metadata": {
    "timestamp": "ISO-8601",
    "replan_attempt": 1
  }
}
```

### Field contracts (strict)

**1. Identity**

```json
{
  "replan_id": "replan_001"
}
```

Rules:

```text
- unique per replan attempt
- traceable across system
```

**2. Instruction**

```json
{
  "instruction": "original user task"
}
```

Rule:

```text
MUST match PlanDocument.instruction exactly
```

**3. Original plan reference**

```json
{
  "original_plan": {
    "plan_id": "plan_001",
    "failed_step_id": "s3",
    "current_step_index": 3
  }
}
```

Purpose:

```text
Tell planner:
- where failure occurred
- what was being executed
```

**Rule**

```text
NO full plan duplication here
(only reference + failure point)
```

**4. Failure context (critical)**

```json
{
  "failure_context": {
    "step_id": "s3",
    "error": {
      "type": "tests_failed",
      "message": "Tests failed after patch"
    },
    "attempts": 2,
    "last_output_summary": "Patch applied but broke 3 tests"
  }
}
```

Must include:

```text
WHAT failed
WHY it failed
HOW MANY attempts
LAST known outcome
```

Rule:

```text
Derived ONLY from ExecutionResult + PlanStep.execution
```

**5. Execution context**

```json
{
  "execution_context": {
    "completed_steps": [
      { "step_id": "s1", "summary": "Opened file" }
    ],
    "partial_results": [
      { "step_id": "s2", "result_summary": "Found function X" }
    ]
  }
}
```

Purpose:

```text
Prevent redoing successful work
```

**Rule**

```text
Planner SHOULD reuse completed steps
```

**6. Exploration context**

```json
{
  "exploration_context": {
    "key_findings": [],
    "knowledge_gaps": []
  }
}
```

Purpose:

```text
Anchor replan to real knowledge
```

**Rule**

```text
NO hallucinated additions
```

**7. Constraints**

```json
{
  "constraints": {
    "max_steps": 5,
    "preserve_completed": true
  }
}
```

**`max_steps`**

```text
Limit new plan size
```

**`preserve_completed`**

```text
true → reuse previous steps
false → full reset plan
```

**8. Metadata**

```json
{
  "metadata": {
    "timestamp": "...",
    "replan_attempt": 1
  }
}
```

Purpose:

```text
Prevent infinite replanning loops
```

### Hard rules (non-negotiable)

**Rule 1**

```text
ReplanRequest MUST only be created after step failure
```

**Rule 2**

```text
failure_context MUST be present and complete
```

**Rule 3**

```text
If any steps completed successfully (execution.status == completed), those steps MUST appear in execution_context.completed_steps with accurate summaries.

If the failure occurs before any step completed successfully, completed_steps MAY be empty (e.g. first step fails after retry exhaustion).
```

**Rule 4**

```text
replan_attempt MUST increment per cycle
```

**Rule 5**

```text
Planner MUST NOT ignore failure_context
```

### Execution flow (with replan)

```text
Plan v1
 ↓
Step 3 fails
 ↓
ReplanRequest created
 ↓
Planner
 ↓
Plan v2
 ↓
Executor continues
```

### Example (realistic)

```json
{
  "replan_id": "replan_001",

  "instruction": "Add retry logic to AgentLoop",

  "original_plan": {
    "plan_id": "plan_001",
    "failed_step_id": "s3",
    "current_step_index": 3
  },

  "failure_context": {
    "step_id": "s3",
    "error": {
      "type": "tests_failed",
      "message": "Tests failed after applying patch"
    },
    "attempts": 2,
    "last_output_summary": "Patch caused 3 failing tests"
  },

  "execution_context": {
    "completed_steps": [
      { "step_id": "s1", "summary": "Located AgentLoop" },
      { "step_id": "s2", "summary": "Analyzed retry logic" }
    ],
    "partial_results": [
      { "step_id": "s3", "result_summary": "Patch applied but invalid" }
    ]
  },

  "exploration_context": {
    "key_findings": [
      "Retry logic missing in loop",
      "Dispatcher already supports retries"
    ],
    "knowledge_gaps": [
      "Best retry strategy implementation"
    ]
  },

  "constraints": {
    "max_steps": 4,
    "preserve_completed": true
  },

  "metadata": {
    "timestamp": "2026-01-01T00:02:00Z",
    "replan_attempt": 1
  }
}
```

### Why this schema is correct

**Grounded replanning**

```text
Uses real execution + exploration context
```

**No plan drift**

```text
Anchored to original plan + failure point
```

**Efficient**

```text
Avoids recomputation of successful steps
```

**Safe**

```text
Prevents infinite loops via replan_attempt
```

### Principal verdict

This ensures:

```text
Failure → Structured Context → Controlled Replan → Stable Execution
```

Without this:

```text
Replanning = hallucinated chaos ❌
```

---

## Schema 6 — `ReplanResult` (frozen)

Output of replanning. Deterministic, traceable, compatible with execution, safe (no drift).

### Full structure

**`new_plan`:** `null` when **`status` = `failed`**; otherwise an object with **`plan_id`** (see rule below).

```json
{
  "replan_id": "string",

  "status": "success | failed",

  "new_plan": {
    "plan_id": "string"
  },

  "changes": {
    "type": "partial_update | full_replacement",

    "summary": "string",

    "modified_steps": ["step_id"],

    "added_steps": ["step_id"],

    "removed_steps": ["step_id"]
  },

  "reasoning": {
    "failure_analysis": "string",

    "strategy": "string"
  },

  "validation": {
    "is_valid": true,
    "issues": ["string"]
  },

  "metadata": {
    "timestamp": "ISO-8601",
    "replan_attempt": 1
  }
}
```

### Field contracts (strict)

**1. Identity**

```json
{
  "replan_id": "replan_001"
}
```

Rule:

```text
MUST match ReplanRequest.replan_id
```

**2. Status**

```json
{
  "status": "success"
}
```

Meaning:

```text
success → valid new plan generated
failed → planner could not produce valid plan
```

**Rule**

```text
If status=failed → new_plan MUST be null (no plan_id)
If status=success → new_plan MUST be non-null with a new plan_id
```

**3. New plan reference**

```json
{
  "new_plan": {
    "plan_id": "plan_002"
  }
}
```

**Failed replan (illustrative):**

```json
{
  "replan_id": "replan_042",
  "status": "failed",
  "new_plan": null,
  "changes": {
    "type": "full_replacement",
    "summary": "Planner could not produce a valid executable plan",
    "modified_steps": [],
    "added_steps": [],
    "removed_steps": []
  },
  "reasoning": {
    "failure_analysis": "...",
    "strategy": "..."
  },
  "validation": {
    "is_valid": false,
    "issues": ["missing finish step", "..."]
  },
  "metadata": { "timestamp": "ISO-8601", "replan_attempt": 1 }
}
```

**Rule**

```text
Full PlanDocument is returned separately when status=success (not embedded here)
```

Why:

```text
Avoid duplication
Maintain clean separation
```

**4. Changes block (critical)**

```json
{
  "changes": {
    "type": "partial_update",
    "summary": "Replaced failing edit step with safer approach",
    "modified_steps": ["s3"],
    "added_steps": ["s4"],
    "removed_steps": []
  }
}
```

Purpose:

```text
Explain WHAT changed between plans
```

**`type`**

```text
partial_update → reuse most of plan
full_replacement → discard old plan entirely
```

**Rule**

```text
Planner MUST declare change type explicitly
```

**5. Reasoning block**

```json
{
  "reasoning": {
    "failure_analysis": "Previous patch caused test failures due to incorrect logic",
    "strategy": "Refactor retry logic before applying patch"
  }
}
```

Purpose:

```text
Make replanning transparent and debuggable
```

Rules:

```text
- MUST reference failure_context
- MUST justify strategy change
```

**6. Validation block**

```json
{
  "validation": {
    "is_valid": true,
    "issues": []
  }
}
```

Purpose:

```text
Ensure new plan is executable
```

Examples of issues:

```text
- missing finish step
- circular dependencies
- invalid actions
```

**Rule**

```text
Executor MUST reject plan if is_valid=false
```

**7. Metadata**

```json
{
  "metadata": {
    "timestamp": "...",
    "replan_attempt": 1
  }
}
```

Purpose:

```text
traceability + loop protection
```

### Hard rules (non-negotiable)

**Rule 1**

```text
ReplanResult MUST always include changes.summary
```

**Rule 2**

```text
new_plan.plan_id MUST differ from original plan_id
```

**Rule 3**

```text
partial_update MUST NOT remove completed steps
```

**Rule 4**

```text
full_replacement MUST ignore previous steps completely
```

**Rule 5**

```text
validation.is_valid MUST be true for execution to proceed
```

### Execution flow (final loop)

```text
Step fails
 ↓
ReplanRequest
 ↓
Planner
 ↓
ReplanResult
 ↓
if valid:
    load new plan
    continue execution
else:
    abort
```

### Example — partial update

```json
{
  "replan_id": "replan_001",

  "status": "success",

  "new_plan": {
    "plan_id": "plan_002"
  },

  "changes": {
    "type": "partial_update",
    "summary": "Adjusted modify step to avoid breaking tests",
    "modified_steps": ["s3"],
    "added_steps": [],
    "removed_steps": []
  },

  "reasoning": {
    "failure_analysis": "Edit introduced invalid logic causing test failures",
    "strategy": "Apply minimal patch instead of full rewrite"
  },

  "validation": {
    "is_valid": true,
    "issues": []
  },

  "metadata": {
    "timestamp": "2026-01-01T00:03:00Z",
    "replan_attempt": 1
  }
}
```

### Example — full replacement

```json
{
  "replan_id": "replan_002",

  "status": "success",

  "new_plan": {
    "plan_id": "plan_003"
  },

  "changes": {
    "type": "full_replacement",
    "summary": "Original approach invalid, switching strategy completely",
    "modified_steps": [],
    "added_steps": ["s1", "s2", "s3"],
    "removed_steps": ["s1", "s2", "s3"]
  },

  "reasoning": {
    "failure_analysis": "Initial plan based on incorrect assumptions",
    "strategy": "Re-explore and rebuild solution from scratch"
  },

  "validation": {
    "is_valid": true,
    "issues": []
  },

  "metadata": {
    "timestamp": "2026-01-01T00:05:00Z",
    "replan_attempt": 2
  }
}
```

### Why this schema is correct

**Controlled evolution**

```text
No silent plan mutation
All changes explicit
```

**Traceability**

```text
Plan v1 → failure → Plan v2 → reason tracked
```

**Safe execution**

```text
Validation prevents bad plans
```

**Production-ready**

Supports:

```text
UI diff view
analytics
debugging
```

### Final principal verdict

You now have:

```text
Plan → Execute → Fail → Replan → Continue
```

This is a **fully closed-loop, production-grade agent control system**.

---

## Next steps

- **“define ToolResult schema vs ExecutionResult mapping”**
- **“start implementation plan (phase-wise)”**

At this point, architecture + contracts are solid enough to build without chaos.
