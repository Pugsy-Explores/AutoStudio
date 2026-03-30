# Phase 9 — Trace + observability (production)

**Scope:** This document is the authoritative Phase 9 specification. It upgrades ad-hoc logging into **structured, serializable traces** suitable for **CLI**, **Langfuse**, **graph UIs**, and **monitoring**. Code lives in **`trace_emitter.py`**, **`plan_executor.py`**, **`runtime.py`**, **`__main__.py`**, and **`trace_printer.py`** when this phase is executed; this file is not executable.

---

## Objective (non-negotiable)

Upgrade tracing from:

```text
print logs ❌
```

to:

```text
structured execution graph ✅
```

---

## Target artifact

Every run produces a **`Trace`** (**PHASE_1_SCHEMA_LAYER** — `agent_v2/schemas/trace.py`):

```text
Trace
 ├── plan_id / instruction
 ├── Steps (TraceStep[])
 │    ├── action
 │    ├── target
 │    ├── success / error
 │    └── duration_ms
 └── metadata (total_steps, total_duration_ms, status)
```

**Errors:** **`TraceStep.error`** is **structured** (`type: ErrorType`, `message: str`), aligned with **`ExecutionResult.error`** — see **`SCHEMAS.md`** Schema 0 + **`SUPPORTING_SCHEMAS.md`** §7 (not a single opaque string).

**Optional extensions:** persist **inputs** / **full output summaries** in **`TraceStep`** or sidecar records if needed; base contract is **`Trace` / `TraceStep`** in **`SCHEMAS.md`** / **`SUPPORTING_SCHEMAS.md`**.

---

## Files to create / modify

| Action | Path |
|--------|------|
| **New** | `agent_v2/runtime/trace_emitter.py` |
| **New or extend** | `agent_v2/runtime/trace_printer.py` (if missing, create) |
| **Modify** | `agent_v2/runtime/plan_executor.py` |
| **Modify** | `agent_v2/runtime/runtime.py` |
| **Modify** | `agent_v2/__main__.py` |

---

## Step 1 — Trace emitter (core)

**Target:** `agent_v2/runtime/trace_emitter.py`

**From:** `agent_v2.schemas.trace` import `Trace`, `TraceStep`.

**Illustrative behavior:**

- **`TraceEmitter`**: `trace_id` (UUID), `steps: list[TraceStep]`, `start_time`.
- **`record_step(step, result, index)`**: append a **`TraceStep`** with `step_id`, `plan_step_index`, `action`, `target`, `success`, **`error`** (`None` on success; on failure `{ type: ErrorType, message: str }` from **`ExecutionResult.error`**), `duration_ms` from **`result.metadata`**.
- **`build_trace(instruction, plan_id)`**: compute `total_duration_ms`, set **`Trace.status`** to `"success"` if all steps succeeded else `"failure"`, set **`metadata.total_steps`**, **`metadata.total_duration_ms`**.

**`_extract_target(step)`:** **`PlanStep`** may not have `path` / `query` — derive **target** from **`step.inputs`**, **`step.goal`**, or last known arguments from the argument generator. The sketch using **`getattr(step, "path", None)`** applies if execution uses a duck-typed step dict; for strict **`PlanStep`**, implement a small mapper.

**Implementation notes:**

- **`ExecutionResult`**: Use attribute access if Pydantic (`result.error`, `result.metadata.duration_ms`).
- **`TraceStep.error`**: **`Optional[{ type: ErrorType, message: str }]`** per **`PHASE_1_SCHEMA_LAYER.md`** / **`SUPPORTING_SCHEMAS.md`** — must match **`ExecutionResult`** taxonomy for Langfuse and graph UI.

---

## Step 2 — Integrate into `PlanExecutor`

**In `__init__`:** `self.trace_emitter = TraceEmitter()` (or inject for testing).

**After each completed step** (success or failure within the step’s retry cycle — **policy:** record **once per plan step** after final outcome, or **per attempt** — document choice; default **per final attempt per step**):

```python
self.trace_emitter.record_step(step, result, step.index)
```

Avoid duplicating steps on every retry unless product wants **attempt-level** traces.

---

## Step 3 — Return trace from `run()`

**At successful completion:**

```python
trace = self.trace_emitter.build_trace(
    instruction=state.instruction,
    plan_id=plan.plan_id,
)

return {
    "status": "success",
    "trace": trace,
    "state": state,
}
```

**On failure / early exit:**

```python
return {
    "status": "failed",
    "trace": trace,
    "state": state,
}
```

**Merge with Phases 6–7:** If **`run()`** already returns **`failed_final`**, **`replan`**, etc., **include `trace`** in every exit path so observability is never dropped.

---

## Step 4 — Runtime output adapter

**Target:** `agent_v2/runtime/runtime.py`

```python
def run(self, instruction: str, mode: str = "act"):

    state = AgentState(instruction=instruction)

    result = self.mode_manager.run(state, mode)

    if isinstance(result, dict) and "trace" in result:
        return result

    return {"state": result}
```

Normalize so callers can always check **`"trace" in result`**.

---

## Step 5 — CLI trace print

**Target:** `agent_v2/__main__.py`

```python
from agent_v2.runtime.trace_printer import print_trace

result = runtime.run(instruction, mode)

if "trace" in result:
    print_trace(result["trace"])
```

---

## Step 6 — Trace format (human-readable)

**Target:** `agent_v2/runtime/trace_printer.py`

```python
def print_trace(trace):

    print("\n=== EXECUTION TRACE ===\n")

    for step in trace.steps:
        status = "OK" if step.success else "FAIL"

        print(f"[{step.plan_step_index}] {step.action} -> {step.target} -> {status}")

        if step.error:
            print(f"   ERROR: {step.error}")

    print("\n--- SUMMARY ---")
    print(f"Steps: {trace.metadata['total_steps']}")
    print(f"Status: {trace.status}")
```

**Note:** If **`trace.metadata`** is a Pydantic model, use **`.total_steps`** or **`.model_dump()`** for display.

---

## Step 7 — Structured output (serialization)

Ensure:

```python
trace.model_dump()  # Pydantic v2
# or model_dump_json() for wire format
```

**Requirements:** JSON-serializable for **Langfuse**, **UI graphs**, **log sinks**.

---

## Step 8 — Validation run

```bash
python -m agent_v2 "Find AgentLoop and explain it"
```

**Expect (shape):**

```text
=== EXECUTION TRACE ===

[0] search -> ... -> OK
[1] open_file -> ... -> OK
[2] finish -> ... -> OK

--- SUMMARY ---
Steps: 3
Status: success
```

---

## Step 9 — Failure trace

**Task:** e.g. `"Edit non-existent file"`

**Expect:**

```text
FAIL step visible
ERROR line present
Trace.status == failure
```

---

## Common failure modes

```text
❌ Failed steps not recorded
❌ Trace emission mixed into core business rules (keep emitter side-effect focused)
❌ Missing duration_ms or metadata
```

**Boundary:** **TraceEmitter** records facts; it does not decide retries or replans.

---

## Exit criteria (strict)

```text
✅ Every executed plan step recorded (per chosen policy)
✅ Errors visible on TraceStep / Trace.status
✅ Trace serializable (model_dump / JSON)
✅ CLI prints readable trace
```

---

## Principal verdict

```text
Black box ❌ → Transparent system ✅
```

Enables **Langfuse**, **graph UI**, **debugging**, **production monitoring**.

---

## Next step

After validation:

👉 **Phase 9 done** (implementation + checks)

Then **Phase 10** — hardening, cleanup, production readiness (or **Langfuse** integration first, per roadmap). See `PHASED_IMPLEMENTATION_PLAN.md` and product priority.
