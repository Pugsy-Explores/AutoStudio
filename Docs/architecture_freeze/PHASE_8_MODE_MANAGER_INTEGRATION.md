# Phase 8 — Mode manager integration (full pipeline)

**Scope:** This document is the authoritative Phase 8 specification. It **activates** the pipeline by wiring **ExplorationRunner**, **PlannerV2**, and **PlanExecutor** (and **Replanner** inside the executor path per **Phase 7**) into **`ModeManager`**. This file is not executable.

---

## Objective (non-negotiable)

Replace ACT-mode execution with:

```text
Exploration → Planning → Execution → Replanning (if needed)
```

Until this phase, components may exist **in isolation**; after this phase they form **one** system.

---

## Final flow

```text
instruction
   ↓
ExplorationRunner
   ↓
ExplorationResult
   ↓
PlannerV2
   ↓
PlanDocument
   ↓
PlanExecutor
   ↓
(result OR replan loop)
```

**Replanner** is not necessarily invoked from **`ModeManager`** directly; if **Phase 7** integrated **`Replanner`** into **`PlanExecutor`**, replanning occurs **during** `plan_executor.run(plan, state)`.

---

## Files to modify

| File | Role |
|------|------|
| `agent_v2/runtime/mode_manager.py` | Wire modes to the new pipeline |
| `agent_v2/runtime/runtime.py` (or equivalent bootstrap) | Construct and inject components |

Paths follow **`PHASED_IMPLEMENTATION_PLAN.md`**; adjust if the repo uses a different entry (e.g. `agent_v2/__main__.py`).

---

## Current vs target state

| | |
|--|--|
| **Current (undesired for ACT)** | `ACT → AgentLoop.run()` (legacy) |
| **Target** | `ACT → full pipeline` (explore → plan → execute [→ replan]) |

---

## Step 1 — Update `ModeManager` init

```python
class ModeManager:

    def __init__(
        self,
        exploration_runner,
        planner,
        plan_executor,
    ):
        self.exploration_runner = exploration_runner
        self.planner = planner
        self.plan_executor = plan_executor
```

**Optional:** Inject **`Replanner`** here only if orchestration lives in **`ModeManager`** instead of **`PlanExecutor`** — **prefer one place** (Phase 7: executor + replanner).

---

## Step 2 — Replace ACT mode

```python
def _run_act(self, state: AgentState):

    # 1. Exploration
    exploration = self.exploration_runner.run(state.instruction)
    state.exploration_result = exploration

    # 2. Planning
    plan = self.planner.plan(
        instruction=state.instruction,
        exploration=exploration,
    )

    state.current_plan = {"plan_id": plan.plan_id}
    state.current_plan_steps = plan.steps

    # 3. Execution
    result = self.plan_executor.run(plan, state)

    return result
```

**Implementation notes:**

- **`PlannerV2.plan`** signature may include **`deep=False`** for ACT unless product requires deep planning by default.
- Ensure **`AgentState`** fields (`instruction`, `exploration_result`, `current_plan`, `current_plan_steps`) match **`agent_v2/schemas/agent_state.py`**.

---

## Step 3 — Plan mode (plan only, no execution)

```python
def _run_plan(self, state: AgentState):

    exploration = self.exploration_runner.run(state.instruction)

    plan = self.planner.plan(
        instruction=state.instruction,
        exploration=exploration,
    )

    state.current_plan = {"plan_id": plan.plan_id}

    return plan
```

**Invariant:** **No** `plan_executor.run` — user gets a **PlanDocument** (or view model) only.

**Optional:** Set **`state.current_plan_steps = plan.steps`** for UI consistency.

---

## Step 4 — Deep plan mode

```python
def _run_deep_plan(self, state: AgentState):

    exploration = self.exploration_runner.run(state.instruction)

    plan = self.planner.plan(
        instruction=state.instruction,
        exploration=exploration,
        deep=True,
    )

    state.current_plan = {"plan_id": plan.plan_id}

    return plan
```

Same as plan mode: **no execution** unless product spec says otherwise.

---

## Step 5 — Remove legacy loop usage

**Audit:**

```bash
grep -R "AgentLoop" .
```

**Requirements:**

```text
ModeManager MUST NOT call:
- AgentLoop.run()
- execution_loop()
- next_action()
```

Route ACT (and other modes) through **`ExplorationRunner` → `PlannerV2` → `PlanExecutor`** only.

---

## Step 6 — Runtime wiring (bootstrap)

**Modify** `agent_v2/runtime/runtime.py` (or the project’s composition root).

**Illustrative:**

```python
from agent_v2.runtime.exploration_runner import ExplorationRunner
from agent_v2.planner.planner_v2 import PlannerV2
from agent_v2.runtime.plan_executor import PlanExecutor

def create_runtime(...):

    exploration_runner = ExplorationRunner(action_generator, dispatcher)

    planner = PlannerV2(llm)

    plan_executor = PlanExecutor(dispatcher, argument_generator, replanner=...)  # per Phase 7

    mode_manager = ModeManager(
        exploration_runner=exploration_runner,
        planner=planner,
        plan_executor=plan_executor,
    )

    return AgentRuntime(mode_manager=mode_manager)
```

**Inject** real **`action_generator`**, **`argument_generator`**, **`dispatcher`**, **`llm` / model router**, and **`Replanner`** + **`PlannerV2`** as required by Phases 2–7.

---

## Step 7 — Validation run (mandatory)

```bash
python -m agent_v2 "Find AgentLoop and explain it"
```

**Trace expectations:**

```text
1. exploration steps (search / open_file / read-only shell)
2. plan generated
3. execution steps follow plan EXACTLY
```

---

## Step 8 — Plan mode check

```bash
python -m agent_v2 --mode=plan "Find AgentLoop"
```

**Expect:**

```text
✅ exploration runs
✅ plan returned
❌ NO execution
```

---

## Step 9 — Failure + replan check

**Task example:** `"Modify file that doesn't exist"` (or another controlled failure).

**Expect:**

```text
step fails
→ retry (Phase 6)
→ replan triggered (Phase 7)
→ new plan executed
```

---

## Common failure modes

```text
❌ LLM still choosing actions (bypass argument-only executor)
❌ Exploration skipped in ACT
❌ Plan ignored during execution
❌ Replanner never triggered when policy says it should
```

---

## Exit criteria (strict)

```text
✅ ACT uses exploration + planner + executor
✅ PLAN / DEEP_PLAN use exploration + planner only (no execution)
✅ No AgentLoop on ACT path
✅ Execution follows PlanDocument strictly (Phase 5)
```

---

## Principal verdict

```text
Loose components ❌ → Unified intelligent system ✅
```

Without this phase: **components exist, but there is no single activated pipeline**.

---

## Next step

After validation:

👉 **Phase 8 done** (implementation + manual / automated checks)

Then **Phase 9 — Trace + observability (Langfuse-ready, graph-ready)**. See `PHASED_IMPLEMENTATION_PLAN.md`.
