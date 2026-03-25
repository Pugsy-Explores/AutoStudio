# Phased implementation plan

Execution discipline: no invention, no deviation from frozen architecture and schemas.

Strict phased rollout to:

- avoid breaking runtime
- preserve current behavior until replacement is ready
- introduce planner system safely

---

## Implementation strategy (lock this)

```text
DO NOT rewrite system
DO NOT replace loop immediately

→ Build parallel system
→ Integrate gradually
→ Switch control at end
```

---

## Phase plan (strict, ordered)

**Rollout scope:** **12 phases** total — **Phases 1–10** = core control plane (schemas through plan-driven ACT mode); **Phases 11–12** = observability + execution graph/UI (product layer). See **Rollout order** at the end of this document.

### Phase 1 — schema layer (foundation)

**Goal**

Implement all schemas as **pure dataclasses / Pydantic models** (NO logic, NO integration).

**Files**

```text
agent_v2/schemas/
    agent_state.py
    plan.py              (PlanDocument, PlanStep)
    execution.py         (ExecutionStep, ExecutionResult, RetryState)
    exploration.py       (ExplorationResult)
    replan.py            (ReplanRequest, ReplanResult)
    tool.py              (ToolResult, ToolCall, ToolError)
    trace.py             (Trace, TraceStep)
    context.py           (ContextWindow, ContextItem)
    policies.py          (ExecutionPolicy, FailurePolicy)
    output.py            (FinalOutput, ExecutionSummary)
```

**Rules**

```text
- Strict typing
- No business logic
- Validation only (enums, required fields)
```

**Exit criteria**

```text
✅ All schemas importable
✅ No circular deps
✅ JSON serialization works
```

---

### Phase 2 — tool normalization layer

**Goal**

Introduce:

```text
ToolResult → ExecutionResult mapping
```

**Files**

```text
agent_v2/runtime/tool_mapper.py
```

**Implement**

```python
map_tool_result_to_execution_result(tool_result, step_id)
```

**Refactor**

ONLY inside:

```text
agent_v2/runtime/dispatcher.py
```

**Rules**

```text
- ALL tools return ToolResult
- dispatcher MUST convert → ExecutionResult
- AgentLoop MUST ONLY see ExecutionResult
```

**Exit criteria**

```text
✅ All tool paths normalized
✅ No raw tool outputs in loop
```

---

### Phase 3 — exploration runner

**Goal**

Implement **read-only exploration phase**.

**Files**

```text
agent_v2/runtime/exploration_runner.py
```

**Responsibilities**

```text
- run limited steps (3–6)
- allowed tools:
    search
    open_file
    shell (read-only)
- build ExplorationResult
```

**Important**

```text
NO edit
NO write
NO patch
```

**LLM usage**

Use existing:

```text
ActionGenerator (controlled prompt)
```

BUT restrict action space.

**Exit criteria**

```text
✅ Returns valid ExplorationResult
✅ No write actions executed
```

---

### Phase 4 — planner (first-class)

**Goal**

Upgrade planner to output:

```text
PlanDocument (STRICT schema)
```

**Files**

```text
agent_v2/planner/planner_v2.py
agent_v2/validation/plan_validator.py   # PlanValidator — see VALIDATION_REGISTRY.md
```

**Input**

```text
instruction + PlannerInput
```

Where **`PlannerInput` = `ExplorationResult | ReplanContext`** (`SCHEMAS.md` Schema 4c). Initial run uses **`ExplorationResult`**; replan path uses **`ReplanContext`**.

**Output**

```text
PlanDocument
```

**Requirements**

```text
- must include:
    understanding
    sources
    steps
    risks
    completion_criteria
```

**Add**

```text
PlanValidator (validate schema correctness)
```

**Exit criteria**

```text
✅ Valid PlanDocument generated
✅ Passes validation
```

---

### Phase 5 — plan executor (critical)

**Goal**

Convert AgentLoop → controlled executor.

**Approach (important)**

DO NOT rewrite AgentLoop.

**Instead**

Create:

```text
agent_v2/runtime/plan_executor.py
```

**Behavior**

```text
for step in plan.steps:
    → build ExecutionStep
    → call dispatcher
    → receive ExecutionResult
    → update PlanStep.execution
```

**LLM role**

```text
ONLY fills arguments
NOT action selection
```

**Modify**

```text
ActionGenerator → argument generator only
```

**Exit criteria**

```text
✅ Execution follows plan strictly
✅ No spontaneous tool usage
```

---

### Phase 6 — failure + retry system

**Goal**

Implement:

```text
RetryState + failure propagation
```

**Inside PlanExecutor**

**Logic**

```text
if step fails:
    if attempts < max_attempts:
        retry
    else:
        mark failed
        trigger replan
```

**Update**

```text
PlanStep.execution
PlanStep.failure
```

**Exit criteria**

```text
✅ Retries working
✅ Failure state correct
```

---

### Phase 7 — replanner

**Goal**

Implement:

```text
ReplanRequest → Planner → ReplanResult
```

**Files**

```text
agent_v2/runtime/replanner.py
```

**Flow**

```text
build ReplanRequest
call planner
validate ReplanResult
replace or update plan
```

**Constraints**

```text
max_replans = 2
```

**Exit criteria**

```text
✅ Replan works after failure
✅ No infinite loops
```

---

### Phase 8 — mode manager integration

**Modify ONLY ModeManager**

**ACT mode becomes:**

```text
1. exploration_runner.run()
2. planner.plan()
3. plan_executor.run()
```

**PLAN mode**

```text
planner only (no execution)
```

**DEEP_PLAN**

```text
planner with deep=True
```

**Exit criteria**

```text
✅ ACT uses new pipeline
✅ PLAN unchanged behavior
```

---

### Phase 9 — trace integration

**Goal**

Upgrade trace to:

```text
Plan → Step → ExecutionResult
```

**Files**

```text
trace_formatter.py
trace_emitter.py
```

**Add**

```text
plan_id
step_id
failure info
```

**Exit criteria**

```text
✅ Clear step-by-step trace
```

---

### Phase 10 — hard switch (control-plane completion)

**Scope:** Final phase of the **core** planner–executor control plane (Phases 1–10). **Not** the end of observability/product work (see Phases 11–12).

**Remove old path usage**

```text
NO direct AgentLoop usage in ACT
```

**Keep**

```text
AgentLoop (internal executor only)
```

**Validate**

```text
grep:
- no direct next_action loop
```

**Exit criteria**

```text
✅ System fully plan-driven
```

---

### Phase 11 — Langfuse observability (product layer)

**Goal:** First-class tracing integration (spans, generations, events) aligned with plan steps. See **`PHASE_11_LANGFUSE_OBSERVABILITY.md`**.

**Exit criteria**

```text
✅ Trace IDs align with PlanStep / ExecutionResult
✅ Hierarchy trace → spans → generations per SCHEMAS + Phase 9
```

---

### Phase 12 — execution graph + UI (product layer)

**Goal:** Graph projection and UI on top of traces. See **`PHASE_12_EXECUTION_GRAPH_UI.md`**.

**Exit criteria**

```text
✅ Navigable execution graph (nodes/edges) from Trace / Langfuse IDs
```

---

## Critical guardrails (do not break)

**Do not**

```text
- modify tool implementations
- change primitives
- mix plan + execution logic
- allow LLM to choose actions
```

**Always**

```text
- plan drives execution
- execution updates state
- failure triggers replan
```

---

## Rollout order (very important)

```text
Core control plane:  Phase 1 → 2 → 3 → 4 → 5 → 6 → 7 → 8 → 9 → 10
Product / observability: Phase 11 → Phase 12 (after Phase 10 unless explicitly parallelized)
```

---

## Principal verdict

This plan aims for:

```text
ZERO architecture drift
ZERO hidden coupling
CONTROLLED migration
```

---

## Next step

**Control plane:** **Phase 1 implementation** (schemas under `agent_v2/schemas/`) — exact instructions in **`PHASE_1_SCHEMA_LAYER.md`**.

**After Phase 10:** proceed **Phase 11 → 12** per specs in this folder (Langfuse, then graph/UI).
