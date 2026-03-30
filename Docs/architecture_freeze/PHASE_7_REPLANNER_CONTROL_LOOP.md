# Phase 7 — Replanner (control loop)

**Scope:** This document is the authoritative Phase 7 specification. It **closes the loop**: failure triggers **explicit, structured** replanning. Code lives in **`agent_v2/runtime/replanner.py`** and integrates with **`plan_executor.py`**; this file is not executable.

**Validation:** New **`PlanDocument`** and **`ReplanResult`** after replan MUST pass the shared **`agent_v2/validation/`** package (**`VALIDATION_REGISTRY.md`**) — **no** one-off **`_validate_replan`** diverging from **`PlanValidator`** rules.

---

## Objective (non-negotiable)

Implement:

```text
Failure → ReplanRequest → Planner → ReplanResult → New Plan → Continue
```

---

## Design principle (lock this)

```text
NO silent retries beyond policy limits
NO implicit in-place plan mutation

→ ALL replanning is explicit + structured
```

---

## Role in the system

After Phase 7:

```text
Plan → Execute → Fail → Replan → Continue
```

Without this, the pipeline stays **linear**, not **adaptive**.

---

## File to create

```text
agent_v2/runtime/replanner.py
```

**Also modify:** `agent_v2/runtime/plan_executor.py` (integrate replanner + guards).

---

## Step 1 — Basic structure

**Target:** `agent_v2/runtime/replanner.py`

```python
from agent_v2.schemas.replan import ReplanContext, ReplanRequest, ReplanResult
from agent_v2.schemas.plan import PlanDocument, PlanStep


class Replanner:

    def __init__(self, planner):
        self.planner = planner

    def build_replan_context(self, request: ReplanRequest) -> ReplanContext:
        ...

    def replan(self, request: ReplanRequest) -> tuple[ReplanResult, PlanDocument]:
        ...
```

**Note:** `build_replan_request` may live on **`Replanner`** or **`PlanExecutor`**; the spec below places **`build_replan_request`** on **`Replanner`** so **`PlanExecutor`** calls `self.replanner.build_replan_request(state, failed_step)`.

**Note:** **`build_replan_context`** maps **`ReplanRequest` → `ReplanContext`** ( **`SCHEMAS.md` Schema 4b** ) for **`PlannerInput`** on the replan path.

---

## Step 2 — Build `ReplanRequest`

**Must be derived from runtime state** — not invented by the LLM.

**From:** `agent_v2.schemas.replan` import `ReplanRequest`.

**Illustrative construction** (use **PHASE_1_SCHEMA_LAYER** nested models — `original_plan`, `failure_context`, etc. are structured fields, not untyped dicts):

```python
def build_replan_request(self, state: AgentState, failed_step: PlanStep) -> ReplanRequest:

    return ReplanRequest(
        replan_id=f"replan_{state.metadata.get('replan_attempt', 0) + 1}",

        instruction=state.instruction,

        original_plan={
            "plan_id": state.current_plan["plan_id"],
            "failed_step_id": failed_step.step_id,
            "current_step_index": failed_step.index,
        },

        failure_context={
            "step_id": failed_step.step_id,
            "error": {
                "type": failed_step.failure["failure_type"],
                "message": failed_step.execution["last_result"]["output_summary"],
            },
            "attempts": failed_step.execution["attempts"],
            "last_output_summary": failed_step.execution["last_result"]["output_summary"],
        },

        execution_context={
            "completed_steps": [
                {
                    "step_id": s.step_id,
                    "summary": s.execution["last_result"]["output_summary"],
                }
                for s in state.current_plan_steps
                if s.execution["status"] == "completed"
            ],
            "partial_results": [
                {
                    "step_id": failed_step.step_id,
                    "result_summary": failed_step.execution["last_result"]["output_summary"],
                }
            ],
        },

        exploration_context={
            "key_findings": state.exploration_result.summary.key_findings,
            "knowledge_gaps": state.exploration_result.summary.knowledge_gaps,
        },

        constraints={
            "max_steps": 6,
            "preserve_completed": True,
        },

        metadata={
            "timestamp": datetime.utcnow().isoformat(),
            "replan_attempt": state.metadata.get("replan_attempt", 0) + 1,
        },
    )
```

**Implementation notes:**

- **`AgentState`** must expose `instruction`, `current_plan`, `current_plan_steps`, `exploration_result`, `metadata`, etc. — align with **`agent_v2/schemas/agent_state.py`**.
- If `failure_context.error.message` should be the real error string, prefer it from **`ExecutionResult.error.message`** when available, not only `output_summary`.
- Use **attribute access** if `PlanStep` uses Pydantic models instead of dict-style `execution` / `failure`.

---

## Step 3 — Call planner

**Contract (frozen in `SCHEMAS.md`):** **`PlannerInput` = `ExplorationResult | ReplanContext`**. Replanner MUST pass a **`ReplanContext`** built from the **`ReplanRequest`** (and state), not `None`.

```python
def replan(self, request: ReplanRequest) -> tuple[ReplanResult, PlanDocument]:

    replan_context = self.build_replan_context(request)

    new_plan = self.planner.plan(
        instruction=request.instruction,
        exploration=replan_context,  # PlannerInput; ReplanContext on this path
        deep=True,
    )

    result = self._build_replan_result(request, new_plan)

    return result, new_plan
```

**Interim (v1):**

- Reuse **PlannerV2** (`agent_v2/planner/planner_v2.py`).
- **Do not** build a separate replan-specific LLM prompt yet.

**PlannerV2** MUST accept **`PlannerInput`** (union) on the exploration/context parameter per **`SCHEMAS.md` Schema 4c** — implement as typed union or overload; **no** `exploration=None` on the replan path.

---

## Step 4 — Build `ReplanResult`

**From:** `agent_v2.schemas.replan` import `ReplanResult`.

**Illustrative** (match schema field types):

```python
def _build_replan_result(self, request: ReplanRequest, new_plan: PlanDocument) -> ReplanResult:

    return ReplanResult(
        replan_id=request.replan_id,
        status="success",

        new_plan={
            "plan_id": new_plan.plan_id,
        },

        changes={
            "type": "partial_update",
            "summary": "Adjusted plan after failure",
            "modified_steps": [request.original_plan["failed_step_id"]],
            "added_steps": [],
            "removed_steps": [],
        },

        reasoning={
            "failure_analysis": request.failure_context["error"]["message"],
            "strategy": "Re-attempt with adjusted approach",
        },

        validation={
            "is_valid": True,
            "issues": [],
        },

        metadata={
            "timestamp": datetime.utcnow().isoformat(),
            "replan_attempt": request.metadata["replan_attempt"],
        },
    )
```

Later: derive **`changes`** from a real diff between old and new **`PlanDocument`**; set **`status`** / **`validation`** from actual validation.

---

## Step 5 — Integrate into `PlanExecutor`

**After failure detection** (post-retry exhaustion, per Step 6):

```python
if not result.success:

    if state.metadata.get("replan_attempt", 0) >= 2:
        return {"status": "failed_final", "result": result}

    replan_request = self.replanner.build_replan_request(state, step)

    replan_result, new_plan = self.replanner.replan(replan_request)

    state.metadata["replan_attempt"] = replan_result.metadata["replan_attempt"]

    state.current_plan = {"plan_id": new_plan.plan_id}
    plan = new_plan

    return self.run(plan, state)
```

**Critical:**

- **`PlanExecutor`** must receive **`replanner`** (or construct it) — **inject** in `__init__`.
- **`preserve_completed`** in **`ReplanRequest`** should be honored when merging: either **merge** completed steps into **`new_plan`** or **reset** executor state per product rules (document in code).
- **Recursion:** `return self.run(plan, state)` is a **recursive** replan loop — ensure **max replans** is checked **before** calling replan and that **Python stack depth** is bounded (prefer iterative loop with `while` + `max_replans` if depth is a concern).

---

## Step 6 — Prevent infinite loops

```text
max_replans = 2
```

**Guard:**

```python
if replan_attempt >= max_replans:
    abort  # return failed_final or equivalent
```

Align **`max_replans`** with **`ExecutionPolicy`** / **`FailurePolicy`** (**PHASE_1** `policies.py**) and env config.

---

## Step 7 — Test cases (mandatory)

### Case 1 — Failure → replan → success

```text
Step fails → new plan generated → run completes
```

### Case 2 — Repeated failure

```text
Fail → replan → fail → hit replan limit → abort
```

### Case 3 — No failure

```text
No replan triggered
```

---

## Common failure modes

```text
❌ Mutating existing PlanDocument in place instead of explicit replacement
❌ Replanning without failure / execution context
❌ Infinite recursion (no max replans)
❌ Ignoring completed steps when preserve_completed is True
```

---

## Exit criteria (strict)

```text
✅ Replanner implemented
✅ ReplanRequest built from real state
✅ ReplanResult returned
✅ Plan replaced / continued correctly
✅ Max replans enforced (e.g. 2)
```

---

## Principal verdict

```text
Static agent ❌ → Adaptive agent ✅
```

Without this: **the system cannot recover** from structured failure.

---

## Next step

After validation:

👉 **Phase 7 done** (implementation + tests)

Then **Phase 8 — ModeManager integration (full pipeline activation)**. See `PHASED_IMPLEMENTATION_PLAN.md`.
