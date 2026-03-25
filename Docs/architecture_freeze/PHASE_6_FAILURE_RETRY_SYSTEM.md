# Phase 6 — Failure + retry system

**Scope:** This document is the authoritative Phase 6 specification. It turns **terminal failures** into **controlled retries, failure propagation, and replan signals**. Code changes focus on **`agent_v2/runtime/plan_executor.py`** (extend **Phase 5**); this file is not executable.

---

## Objective (non-negotiable)

Implement **deterministic retry + failure propagation**:

```text
ExecutionResult → Retry → Step failure → Replan trigger
```

---

## Design principle (lock this)

```text
NO scattered hardcoded failure logic
NO brittle one-off rules

→ execution_result.error (+ policy) drives decisions
```

**Retry authority:** **`ExecutionPolicy.max_retries_per_step`** seeds **`PlanStep.execution.max_attempts`**; **`PlanExecutor`** alone mutates **`execution.attempts`** and status. **`RetryState`** is optional mirror — see **`SCHEMAS.md`** (Cross-cutting — Retry authority).

**Retry decision** = function of **`ExecutionResult`**, **`PlanStep.failure`**, and **policies** — **not** ad-hoc `if error == "tests_failed": retry` scattered in the executor. Prefer **`RetryState`** only as a **view**, not a parallel counter.

---

## Important shift

```text
retry decision = based on execution result + failure state (+ policy)
NOT based on if/else hacks
```

Initial implementation may be minimal; **extend** toward policy-driven recoverability rather than duplicating magic strings.

---

## File to modify

```text
agent_v2/runtime/plan_executor.py
```

(No new top-level file required unless you split `retry_policy.py` later.)

---

## Step 1 — Retry loop (per step)

**Replace** a single `_execute_step` call with a **retry wrapper**:

```python
def _run_with_retry(self, step: PlanStep, state: AgentState) -> ExecutionResult:

    max_attempts = step.execution["max_attempts"]

    while step.execution["attempts"] < max_attempts:

        result = self._execute_step(step, state)

        step.execution["attempts"] += 1

        if result.success:
            return result

        # failure path
        self._handle_failure(step, result)

    # exhausted retries — result is last failed ExecutionResult
    return result
```

**Implementation notes:**

- Avoid **double incrementing** `attempts` if Phase 5’s `_update_step` also increments — **one** place must own attempt counting per try (typically inside `_run_with_retry` or inside `_execute_step`, not both).
- Reset or initialize `attempts` before entering retry for a fresh step execution if the schema expects count **per attempt cycle** vs **lifetime** (document the chosen semantics in code).

---

## Step 2 — Failure handler

```python
def _handle_failure(self, step: PlanStep, result: ExecutionResult):

    step.failure["failure_type"] = result.error["type"]

    step.execution["last_result"] = {
        "success": False,
        "error": result.error["type"],
        "output_summary": result.output["summary"],
    }

    # Recoverability: do NOT permanently hardcode True/False here long-term.
    # Wire to policy / error classification when available.
    step.failure["is_recoverable"] = True
```

**This step does not** decide replan vs retry final outcome — it **records** failure. **Replan** is set when retries are exhausted (Step 4).

Guard **`result.error`** when absent (should not happen on `success=False`; align with **ExecutionResult** schema).

---

## Step 3 — Integrate retry into `run`

Replace:

```python
result = self._execute_step(step, state)
```

with:

```python
result = self._run_with_retry(step, state)
```

Ensure **`_update_step`** logic is **merged** with retry/failure paths so status, `last_result`, and `attempts` stay consistent (avoid duplicating Phase 5 updates).

---

## Step 4 — Final failure detection

After the retry loop exits without success:

```python
if not result.success:

    step.execution["status"] = "failed"

    step.failure["replan_required"] = True

    return {
        "status": "failed",
        "failed_step": step,
        "result": result,
    }
```

Prefer a **typed** return (e.g. small dataclass / Pydantic model) in production instead of bare dicts.

---

## Step 5 — Success path

On success (from `_run_with_retry` or immediately after):

```python
if result.success:

    step.execution["status"] = "completed"

    step.execution["last_result"] = {
        "success": True,
        "output_summary": result.output["summary"],
    }
```

Call **`_update_state`** as in Phase 5 for successful observations.

---

## Step 6 — Stop execution on failure

**Modify `run()`:**

```python
for step in plan.steps:

    if not self._can_execute(step, plan):
        continue

    result = self._run_with_retry(step, state)

    self._update_state(state, step, result)

    if not result.success:
        return {
            "status": "failed",
            "failed_step": step,
            "result": result,
        }

    if step.action == "finish":
        break
```

**Invariant:** Do **not** continue to later plan steps after a step has **failed** post-retry (unless product spec explicitly allows partial plans — default is **stop**).

---

## Step 7 — Track failure streak (optional state)

```python
# Inside failure handling or after failed retry exhaustion

state.metadata["failure_streak"] += 1
state.metadata["last_error"] = result.error["type"]
```

**Requires:** `AgentState.metadata` (or equivalent) exists and is initialized. Align with **`agent_v2/schemas/agent_state.py`**.

---

## Step 8 — Test cases (mandatory)

### 1. Retry success

```text
Step fails once → succeeds second time
```

**Expect:**

```text
attempts == 2 (or per your semantics)
step.execution.status == completed
```

### 2. Retry exhausted

```text
Fails on every attempt until max_attempts
```

**Expect:**

```text
status == failed
failure.replan_required == True
```

### 3. Success first try

**Expect:**

```text
attempts == 1
status == completed
```

---

## Common failure modes

```text
❌ Hardcoded retry rules like: if error == "tests_failed" → retry (only)
❌ Retry loop that doesn’t belong to the step (wrong scope)
❌ attempts incremented twice or not at all
❌ Continuing execution after unrecoverable step failure
```

---

## Exit criteria (strict)

```text
✅ Retry works per step within max_attempts
✅ attempts tracked correctly
✅ Failure after retries stops plan execution and sets replan_required
✅ State / metadata updated consistently
```

---

## Principal verdict

```text
Fragile execution ❌ → Fault-tolerant system ✅
```

Without this: **one failure = total collapse** with no structured path to **Phase 7 (Replanner)**.

---

## Next step

After validation:

👉 **Phase 6 done** (implementation + tests)

Then **Phase 7 — Replanner (closing the loop)**. See `PHASED_IMPLEMENTATION_PLAN.md`.
