# Phase 5 — Plan executor (controlled engine)

**Scope:** This document is the authoritative Phase 5 specification. It defines **execution control**: the plan drives actions; the LLM only fills arguments. Code lives in `agent_v2/runtime/plan_executor.py` (and related generator changes) when this phase is executed; this file is not executable.

---

## Objective (non-negotiable)

Execute:

```text
PlanDocument → step-by-step → ExecutionResult → update PlanStep
```

---

## Hard rule

```text
LLM DOES NOT CHOOSE ACTIONS ANYMORE
```

Only:

```text
PlanStep.action → fixed (from plan)
LLM → fills arguments ONLY
```

If this phase is wrong: plans get ignored, the model freelances, retries and replan become impossible.

---

## Role in the system

```text
PlanDocument
   ↓
PlanExecutor   ← THIS PHASE
   ↓
ExecutionResults
   ↓
Updated PlanStep.execution (and failure metadata)
```

---

## File to create

```text
agent_v2/runtime/plan_executor.py
```

**Related change:** `ActionGenerator` (or successor) becomes an **argument generator** — see Step 7.

---

## Step 1 — Basic structure

**Target:** `agent_v2/runtime/plan_executor.py`

```python
from agent_v2.schemas.plan import PlanDocument, PlanStep
from agent_v2.schemas.execution import ExecutionResult
# from agent_v2.schemas.agent_state import AgentState  # per Phase 1 agent_state.py


class PlanExecutor:

    def __init__(self, dispatcher, argument_generator):
        self.dispatcher = dispatcher
        self.argument_generator = argument_generator

    def run(self, plan: PlanDocument, state: AgentState):
        ...
```

- **dispatcher:** After **Phase 2**, returns **`ExecutionResult`** only.
- **argument_generator:** Implements `generate(step: PlanStep, state: AgentState) -> dict` (Step 7).

---

## Step 2 — Step iteration (core)

**Inside `run()`:**

```python
for step in plan.steps:

    if step.execution["status"] == "completed":
        continue

    result = self._execute_step(step, state)

    self._update_step(step, result)

    if step.action == "finish":
        break
```

**Implementation note:** If `PlanStep.execution` is a Pydantic sub-model, use attribute access (`step.execution.status`) instead of dict indexing. The pseudocode below uses dict style for readability.

**Order:** Integrate **dependency check** (Step 6) before executing a step — skip or wait until dependencies complete.

---

## Step 3 — Execute step (critical)

```python
def _execute_step(self, step: PlanStep, state: AgentState) -> ExecutionResult:

    # 1. Generate arguments (LLM) — action is FIXED
    args = self.argument_generator.generate(step, state)

    # 2. Build execution payload for dispatcher
    execution_step = {
        "step_id": step.step_id,
        "action": step.action,
        "arguments": args,
        "reasoning": "",  # optional; align with ExecutionStep schema
    }

    # 3. Dispatcher (already returns ExecutionResult after Phase 2)
    result = self.dispatcher.execute(execution_step, state)

    return result
```

**Must not happen:**

```text
DO NOT let LLM modify action
DO NOT let LLM skip steps
```

The dispatcher contract may use `(execution_step, state)` or a single structured step object — align with existing `agent_v2/runtime/dispatcher.py`.

---

## Step 4 — Update step state

```python
def _update_step(self, step: PlanStep, result: ExecutionResult):

    step.execution["attempts"] += 1

    if result.success:
        step.execution["status"] = "completed"
        step.execution["last_result"] = {
            "success": True,
            "output_summary": result.output["summary"],
        }

    else:
        step.execution["status"] = "failed"
        step.execution["last_result"] = {
            "success": False,
            "error": result.error["type"],
            "output_summary": result.output["summary"],
        }

        step.failure["failure_type"] = result.error["type"]
```

**Implementation note:** **`ExecutionResult.output`** and **`error`** are structured per **PHASE_1_SCHEMA_LAYER** — use `result.output.summary` (and optional `result.output.data`) and optional `result.error` with normalized `type`. Guard `result.error` when `success` is True.

---

## Step 5 — History (state update)

```python
def _update_state(self, state: AgentState, step: PlanStep, result: ExecutionResult):

    state.history.append({
        "step_id": step.step_id,
        "action": step.action,
        "observation": result.output["summary"],
    })

    state.step_results.append({
        "step_id": step.step_id,
        "result_summary": result.output["summary"],
    })
```

**Call from `run()`** after each step execution (after `_update_step` or alongside it), consistent with **`AgentState`** fields defined in `agent_v2/schemas/agent_state.py`.

---

## Step 6 — Dependency check

```python
def _can_execute(self, step: PlanStep, plan: PlanDocument) -> bool:

    completed = {
        s.step_id for s in plan.steps
        if s.execution["status"] == "completed"
    }

    return all(dep in completed for dep in step.dependencies)
```

**Modify iteration:** Before `_execute_step`:

```python
if not self._can_execute(step, plan):
    continue
```

(Or queue / topological order if you require strict ordering beyond linear scan — document choice in code.)

---

## Step 7 — Argument generator adaptation

**Shift:**

| Before | After |
|--------|--------|
| `ActionGenerator` returns action + args | Generator returns **arguments only** |

**Contract:**

```python
def generate(self, step: PlanStep, state: AgentState) -> dict:
    ...
```

**Prompt must include:**

- `step.goal`
- `step.action` (**fixed** — not negotiable)
- exploration summary (optional, from state)
- history / prior observations

**LLM output:** JSON or structured dict of **arguments only** for `step.action`.

Rename class or add `ArgumentGenerator` wrapper as appropriate; do not use **`AgentLoop.next_action`** for plan execution.

---

## Step 8 — Validation test

```bash
python -m agent_v2 "Find AgentLoop and explain it"
```

**Expect:**

```text
✅ Steps executed IN ORDER (respecting dependencies)
✅ Actions follow plan EXACTLY
✅ No random tool calls driven by LLM choice of action
✅ History / state updates correctly
```

---

## Common failure modes

```text
❌ LLM changing action
❌ Skipping dependency steps
❌ Not updating execution.attempts / status
❌ Using AgentLoop next_action instead of the plan
```

---

## Exit criteria (strict)

```text
✅ PlanExecutor exists
✅ Steps executed strictly from PlanDocument
✅ Dispatcher returns ExecutionResult (Phase 2)
✅ State and PlanStep.execution updated correctly
```

---

## Principal verdict

```text
Autonomous chaos ❌ → Deterministic execution ✅
```

Without this, **the planner is useless**.

---

## Next step

After validation:

👉 **Phase 5 done** (implementation + tests)

Then **Phase 6 — Failure + retry system** (production-grade). See `PHASED_IMPLEMENTATION_PLAN.md`.
