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

**Rollout scope:** **Phases 1–10** = core control plane (schemas through plan-driven ACT mode); **Phases 11–16** = observability, graph/UI, **exploration engine V2 (12.5)**, **exploration control semantics (12.6)**, LLM trace nodes, diff viewer, replay, and memory layer (product / transparency / exploration-upgrade layers — specs in this folder). See **Rollout order** at the end of this document.

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

### Phase 12.5 — exploration engine V2 (progressive + controlled) **(control plane — exploration quality)**

**Goal:** Replace ad-hoc exploration with a **deterministic, staged, LLM-assisted** **`ExplorationEngineV2`** that fills **Schema 4 `ExplorationResult`** for **PlannerV2** — system-controlled state machine; LLM only for local decisions (intent, selection, understanding). See **`PHASE_12_5_EXPLORATION_ENGINE_V2.md`**.

**Architecture (aligned with freeze)**

```text
NO EDITS during exploration — same as Phase 3
ExplorationResult (Schema 4) remains the only planner-facing contract; PlannerInput = ExplorationResult | ReplanContext (Schema 4c)
ExplorationRunner remains contract surface; may delegate to ExplorationEngineV2 (CONTRACT_LAYER.md)
Retrieval pipeline order unchanged; discovery uses graph / grep / vector as sources (not a new upstream pipeline stage)
Read-only tools via ToolRegistry / dispatcher — no ad-hoc filesystem bypass
```

**Exit criteria**

```text
✅ explore(instruction) → valid ExplorationResult per SCHEMAS.md Schema 4 (Rules 2–5: items ≤ 6, summaries only, real sources, knowledge_gaps rules)
✅ Bounded steps / backtracks; dedup and no-op stop
✅ ExplorationRunner → ExplorationEngineV2 delegation path documented / feature-flagged
```

---

### Phase 12.6 — exploration control semantics (completion, policy, planner gating) **(control plane — termination + planner boundary)**

**Goal:** Fix **termination and completion semantics** on top of Phase 12.5: **separate** “snippet relevance / can answer” (`ExplorationDecision.status`) from **“exploration structurally complete”** (system predicate). **LLM suggests** (`next_action`, `needs`); **system decides** exit via **`should_stop(state, decision)`**, **expansion policy**, and **`pending_targets`** queue. **PlannerV2 runs only when** `ExplorationResult` metadata records **exploration complete** set by the engine — **not** when the LLM alone says stop or sufficient. See **`PHASE_12_6_EXPLORATION_CONTROL_SEMANTICS.md`**.

**Architecture (aligned with freeze)**

```text
NO EDITS during exploration — unchanged
ExplorationResult (Schema 4) remains planner-facing; metadata extended per SCHEMAS.md amendment
sufficient MUST NOT be the sole unconditional terminal branch
should_stop + completion contract + pending_targets are authoritative for loop exit and planner gating
```

**Exit criteria**

```text
✅ should_stop / exploration_complete documented and implemented in code paths (not prompt-only)
✅ pending_targets + expansion policy prevent LLM-only flow control
✅ ModeManager (or equivalent) does not call planner when exploration_complete is false (per spec)
✅ SCHEMAS.md / SUPPORTING_SCHEMAS.md updated for new metadata and ExplorationState fields
```

---

### Phase 12.6.F — exploration scoper (breadth before selection) **(control plane — internal)**

**Goal:** Add an **LLM-based Exploration Scoper** after discovery dedup and **before** `CandidateSelector.select_batch`, so retrieval breadth is **not silently truncated** by the selector’s fixed window (`candidates[:10]` today). Scoper uses a **deterministic cap K** (~20) prompt budget, **`selected_indices` only** in JSON output, **sorted rehydration** (scoper is subset-only, not ordering), **no second output cap** (selector `limit` is the only batch truncation), **skip scoper** in the engine when `len(capped) <= skip_below` (trivial list — execution only), and **pass-through** on invalid/empty selection. **No** `SCHEMAS.md` changes; **no** selector/analyzer edits. See **`PHASE_12_6_F_EXPLORATION_SCOPER.md`**.

**Exit criteria**

```text
✅ dedup → cap K → if len(capped) > skip_below then scoper else capped → select_batch unchanged
✅ Rehydrate by sorted indices only; invalid/empty → deterministic pass-through
✅ Snippet truncation uses existing MAX_SNIPPET_CHARS; K / skip_below are config (budget / orchestration, not relevance scoring)
✅ Optional trace: scoper_input_n, scoper_output_n, scoper_selected_ratio, scoper_skipped
```

---

### Phase 13 — LLM node visualization (product / observability)

**Goal:** Every successful `call_reasoning_model` / `call_small_model` appears as a first-class **`TraceStep`** with `kind="llm"`, interleaved with tool steps in one ordered timeline. **`build_graph`** projects **`type="llm"`** nodes; UI shows purple LLM nodes with collapsible trimmed prompt/output and copy actions.

**Architecture (aligned with freeze)**

```text
ContextVar active TraceEmitter (set in ModeManager ACT path)
  → model_client._try_emit_llm_trace → TraceEmitter.record_llm (truncated text)
PlanExecutor.run(..., trace_emitter=shared) reuses the same emitter

PLAN / DEEP_PLAN: same TraceEmitter + context for exploration + planner only;
  build_trace → state.metadata["trace"] + execution_trace_id;
  AgentRuntime.normalize_run_result surfaces trace + graph (plan_ready) like ACT.
```

**Files**

```text
agent_v2/schemas/trace.py          — TraceStep.kind, input, output, metadata
agent_v2/observability/trace_text.py — TRACE_LLM_TEXT_MAX_CHARS truncation
agent_v2/runtime/trace_context.py  — get/set active TraceEmitter
agent_v2/runtime/trace_emitter.py  — record_llm, build_trace status (tool-only failure)
agent/models/model_client.py       — timing + emit after successful model response
agent_v2/runtime/mode_manager.py   — shared TraceEmitter + context for explore→plan→execute
agent_v2/runtime/plan_executor.py  — optional trace_emitter reuse
agent_v2/observability/graph_builder.py — GraphNode type llm, replan heuristic uses tool failures only
ui — ExecutionNode / DetailPanel / MiniMap for LLM styling
tests/test_llm_trace.py
```

**Non-negotiables**

```text
❌ No full prompts in trace (truncation required)
❌ Do not store LLM steps outside the same steps[] list as tools
❌ Observability only — no execution / planning behavior changes
```

**Exit criteria**

```text
✅ Mixed LLM + tool TraceStep order preserved and JSON-serializable
✅ Execution graph shows llm ↔ tool chain
✅ UI detail panel: trimmed prompt/output + copy
```

---

### Phase 14 — diff viewer (patch visualization) (product / observability)

**Goal:** Expose **unified diffs** as first-class **`TraceStep`** / **graph** artifacts after successful edits — *what changed*, not only *edit succeeded*. See **`PHASE_14_DIFF_VIEWER_PATCH_VISUALIZATION.md`**.

**Architecture (aligned with freeze)**

```text
TraceStep.kind = "diff" (observability only)
GraphNode.type = "diff" (projection)
Edits remain inside editing pipeline / patch executor; diff recorded after success; no full file bodies in trace
```

**Exit criteria**

```text
✅ Diff steps JSON-serializable and bounded (truncate per spec)
✅ Graph shows edit → diff → next where applicable
✅ Failed edit → no diff step
```

---

### Phase 15 — replay mode (step-by-step playback) (product / observability)

**Goal:** Deterministic **playback** of a completed run from **`Trace` + artifacts** — no LLM, no tools, no re-execution. See **`PHASE_15_REPLAY_MODE.md`**.

**Exit criteria**

```text
✅ ReplayEngine navigates steps without mutating Trace
✅ Optional reconstructed “view state” is read-only (no workspace writes)
```

---

### Phase 16 — memory layer (production design) (control signal)

**Goal:** **Distilled** episodic/semantic **`MemoryEntry`** list, written at defined execution points and read only via **PlannerInput** — bounded, optional; must not override retrieval or ranking. See **`PHASE_16_MEMORY_LAYER.md`**.

**Exit criteria**

```text
✅ Planner receives optional memory; empty memory preserves behavior
✅ No retrieval pipeline reorder or bypass
✅ Trace may include kind="memory" steps when emission is enabled
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
Product / observability: Phase 11 → 12 → 12.5 → 12.6 → 13 → 14 → 15 → 16 (after Phase 10 unless explicitly parallelized)
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

**After Phase 10:** proceed **Phase 11 → 12 → 12.5 → 12.6 → 13 → 14 → 15 → 16** per specs in this folder (Langfuse, graph/UI, exploration engine V2, exploration control semantics, LLM trace nodes, diff viewer, replay, memory layer).
