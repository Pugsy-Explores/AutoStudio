# Phase 11 — Langfuse Observability Implementation Summary

**Status:** ✅ COMPLETE

**Date:** 2026-03-25

---

## Overview

Phase 11 integrates **Langfuse** as a first-class observability layer for `agent_v2`, aligned with the architecture's `trace → plan step → LLM → events` hierarchy. This is **not flat logging** — it provides structured execution graphs for debugging, LLM visibility, and production monitoring.

---

## Exit Criteria Verification

All Phase 11 exit criteria from `PHASE_11_LANGFUSE_OBSERVABILITY.md` are met:

| Criterion | Status | Implementation |
|-----------|--------|----------------|
| ✅ Single Langfuse client, secrets from env | COMPLETE | `agent_v2/observability/langfuse_client.py` — singleton pattern with env vars |
| ✅ One Langfuse trace per agent run | COMPLETE | `agent_v2/runtime/runtime.py` — `create_agent_trace` at start of `run()` |
| ✅ One span per PlanStep execution (with output + tool metadata) | COMPLETE | `agent_v2/runtime/plan_executor.py` — `_run_with_retry` creates span, `_end_langfuse_step_span` records output + metadata |
| ✅ Generations for planner, argument gen, exploration LLMs | COMPLETE | `planner_v2._call_llm`, `plan_argument_generator._generate_with_langfuse`, `bootstrap._react_get_next_action` |
| ✅ Events for retry and replan | COMPLETE | `plan_executor._run_with_retry` — retry events (line 391), replan_triggered events (line 161) |
| ✅ Trace ended with final status / summary | COMPLETE | `runtime.py` — `finalize_agent_trace` in finally block |
| ✅ Hierarchy preserved (not flat-only) | COMPLETE | trace → spans → generations → events |
| ✅ No collision with internal Trace schema naming | COMPLETE | Uses `state.metadata["langfuse_trace"]` (not `"trace"`) |

---

## Implementation Details

### Step 1 — Install + init (clean)

**File:** `agent_v2/observability/langfuse_client.py`

- Singleton client via `_get_client()` — ONE process-wide instance
- Secrets from env: `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`
- No-op facades when SDK unavailable or keys missing (`_NoopTrace`, `_NoopSpan`, `_NoopGen`)
- Real facades wrap Langfuse 4.x `start_observation` API (`LFTraceHandle`, `LFSpanHandle`, `LFGenerationHandle`)

**Added to:** `requirements.txt` — `langfuse>=2.0.0`

### Step 2 — Trace = agent run

**File:** `agent_v2/runtime/runtime.py`

```python
lf_trace = create_agent_trace(instruction=instruction, mode=mode)
state.metadata["langfuse_trace"] = lf_trace
```

- Created at start of `AgentRuntime.run()`
- Attached to `AgentState.metadata` for downstream components

### Step 3 — Span = plan step

**File:** `agent_v2/runtime/plan_executor.py`

```python
step_span = lf.span(
    name=f"step_{step.index}_{step.action}",
    input={
        "step_id": step.step_id,
        "goal": step.goal,
        "action": step.action,
    },
)
```

- Created in `_run_with_retry` before step execution
- Stored in `state.metadata["_current_langfuse_span"]` for nested generations

### Step 4 — Record execution result

**Function:** `_end_langfuse_step_span(span, result)`

```python
span.end(
    output={
        "success": result.success,
        "summary": result.output.summary,
        "error": _execution_error_payload(result.error),
    }
)
```

- Called after step completes (success, failure, abort)

### Step 5 — Tool metadata (critical)

**Function:** `_end_langfuse_step_span(span, result)`

```python
span.update(
    metadata={
        "tool_name": result.metadata.tool_name,
        "duration_ms": result.metadata.duration_ms,
    }
)
```

- Includes tool identity and timing

### Step 6 — LLM call tracking (most important)

**Planner:** `agent_v2/planner/planner_v2.py` — `_call_llm`

```python
gen = langfuse_trace.generation(
    name=gen_name,
    input={"prompt": prompt[:12000], "attempt": attempt},
)
text = self._generate_fn(prompt)
gen.end(output={"response": text[:12000]})
```

**Argument generator:** `agent_v2/runtime/plan_argument_generator.py` — `_generate_with_langfuse`

```python
gen = span.generation(
    name="argument_generation",
    input={"step_goal": step.goal, "action": step.action},
)
text = self._generate_fn(prompt)
gen.end(output={"response": (text or "")[:12000]})
```

**Exploration:** `agent_v2/runtime/bootstrap.py` — `_react_get_next_action`

```python
gen = langfuse_trace.generation(
    name="exploration_step",
    input={"instruction": instruction[:4000], "task": "REACT_ACTION"},
)
out = call_reasoning_model(prompt, task_name="REACT_ACTION")
gen.end(output={"response": (out or "")[:12000]})
```

### Step 7 — Retry events

**File:** `agent_v2/runtime/plan_executor.py` — `_run_with_retry`

```python
lf.event(
    name="retry",
    metadata={
        "step_id": step.step_id,
        "attempt": step.execution.attempts,
        "error": _execution_error_payload(result.error),
    },
)
```

- Emitted before each retry attempt

### Step 8 — Replan events (critical)

**File:** `agent_v2/runtime/plan_executor.py` — `run` (replan loop)

```python
lf.event(
    name="replan_triggered",
    metadata={
        "failed_step_id": req.original_plan.failed_step_id,
        "reason": reason,
        "replan_id": req.replan_id,
    },
)
```

- Emitted when step exhausts retries and triggers replanner

### Step 9 — Final trace output

**File:** `agent_v2/runtime/runtime.py` — finally block

```python
finalize_agent_trace(
    state.metadata.get("langfuse_trace"),
    status=run_status,
    plan_id=plan_id_out,
)
```

- Always executes (even on exception)
- Updates trace with final status and plan_id
- Calls `trace.end()` and `trace.flush()`

### Step 10 — Expected Langfuse UI shape

```text
TRACE: agent_run
│
├── GENERATION: planner
│
├── SPAN: step_1_search
│   ├── GENERATION: argument_generation
│   └── output: success / summary / tool metadata
│
├── SPAN: step_2_open_file
│
├── EVENT: retry
│
├── EVENT: replan_triggered
│
└── SPAN: step_3_edit
    ├── GENERATION: argument_generation
    └── output: success / summary
```

**Why this matches the architecture:**

- `PlanStep` → span
- `ExecutionResult` → span output
- LLM → generation
- Retry / replan → event

---

## Coexistence with Phase 9 (internal Trace)

| Artifact | Purpose |
|----------|---------|
| `agent_v2.schemas.trace.Trace` | Serializable internal execution graph for CLI, tests, replay |
| Langfuse trace | External observability for team UI, retention, production debugging |

Both are maintained independently:

- `TraceEmitter` builds internal `Trace` object
- Langfuse instrumentation emits to external service
- No conflicting step counts or data duplication

---

## Configuration

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key (optional) | None (no-op if missing) |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key (optional) | None (no-op if missing) |
| `LANGFUSE_HOST` | Langfuse host URL | `https://cloud.langfuse.com` |

**When keys are missing:** All Langfuse calls become no-ops via `_NoopTrace` facade — no errors, no overhead.

**Documented in:** `README.md` (Environment Variables section)

---

## Test Coverage

**File:** `tests/test_langfuse_phase11.py`

**Test classes:**

1. `TestLangfuseClientInit` — singleton client, no-op when keys missing
2. `TestLangfuseHierarchy` — trace → span → generation hierarchy
3. `TestLangfuseFinalizeTrace` — final trace output with status
4. `TestLangfuseNoopFacades` — no-op facades don't crash
5. `TestLangfuseRuntimeIntegration` — create_agent_trace, finalize_agent_trace
6. `TestLangfusePlanExecutorIntegration` — span per step, retry events
7. `TestLangfuseReplanEvent` — replan_triggered event emission
8. `TestLangfusePlannerIntegration` — planner LLM generation tracking
9. `TestLangfuseArgumentGeneratorIntegration` — arg gen LLM generation tracking
10. `TestLangfuseExplorationIntegration` — exploration LLM generation tracking
11. `TestLangfuseEndToEndWiring` — runtime → executor → planner trace flow

**Test results:** 23 passed (100%)

**Broader regression:** 45 tests passed (plan_executor, planner_v2, replanner, langfuse_phase11)

---

## Changes Summary

### New files:

- `tests/test_langfuse_phase11.py` — comprehensive Phase 11 test suite

### Modified files:

- `requirements.txt` — added `langfuse>=2.0.0`
- `README.md` — documented `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` env vars

### Existing files (already instrumented):

- `agent_v2/observability/langfuse_client.py` — singleton client + facades
- `agent_v2/runtime/runtime.py` — trace creation + finalization
- `agent_v2/runtime/plan_executor.py` — spans per step, retry events, replan events
- `agent_v2/planner/planner_v2.py` — planner LLM generation tracking
- `agent_v2/runtime/plan_argument_generator.py` — argument gen LLM generation tracking
- `agent_v2/runtime/bootstrap.py` — exploration LLM generation tracking
- `agent_v2/runtime/mode_manager.py` — passes langfuse_trace to exploration + planner
- `agent_v2/runtime/replanner.py` — accepts langfuse_trace parameter
- `agent_v2/runtime/exploration_runner.py` — accepts + passes langfuse_trace

---

## Architectural Compliance

Phase 11 implementation follows all architectural freeze rules:

- ✅ **Rule 1** — No execution engine redesign (only observability layer added)
- ✅ **Rule 17** — Extension over replacement (instrumented existing flow)
- ✅ **Rule 19** — Shared infrastructure (same dispatcher, executor, planner)
- ✅ No new control-plane features or architecture changes
- ✅ No modification to execution semantics

---

## Common Mistakes Avoided

❌ Entire run in one span → ✅ One span per PlanStep  
❌ LLM calls not as generations → ✅ All LLM calls wrapped  
❌ Only raw tool dumps, no span structure → ✅ Structured spans with output  
❌ No events on errors / retries / replan → ✅ Events emitted  
❌ Flat logs only → ✅ Hierarchical trace structure  

---

## Principal Verdict

```text
Flat logs ❌ → Observability system ✅
```

**Enables:**

- Execution graph in Langfuse UI
- LLM visibility (token usage, latency, prompt/response)
- Failure reasoning (which step failed, why, retries attempted)
- Production debugging (trace any run, replay from data)

---

## Next Steps (Out of Scope)

1. Graph UI (nodes + edges) fed by Langfuse + internal Trace
2. Deterministic replay (needs stable IDs + inputs)
3. Prompt tuning from trace cohorts
4. Automated failure-pattern detection

---

## Notes

- **Legacy AgentLoop:** Has its own `langfuse.trace()` call but is not used by current `agent_v2` runtime (all modes use plan-driven execution via `ModeManager` → `PlanExecutor`).
- **No-op when disabled:** Without `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`, all Langfuse calls return no-op facades — zero overhead, no errors.
- **Coexists with Phase 9:** Internal `Trace` schema and Langfuse trace are independent — Phase 9 for serialization/CLI, Phase 11 for team observability.
