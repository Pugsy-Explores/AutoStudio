# ---Cursor prompt based on complete refactor , no safe fallbacks---
You are a staff engineer performing a full architectural refactor of AgentV2 runtime.

IMPORTANT:

* Do NOT preserve backward compatibility
* Do NOT add fallbacks
* Do NOT patch existing executor
* You are allowed to delete or replace core components

Your goal is to redesign the system to support a DAG-based execution engine.

---

## CONTEXT

The current system:

* Uses PlanDocument / PlanStep for both planning and execution
* Mutates PlanStep during execution
* Uses sequential iteration (PlanExecutor)
* Generates arguments dynamically at runtime
* Has shared mutable state

This architecture is NOT suitable for:

* DAG execution
* parallelism
* reliable retries

---

## OBJECTIVE (PHASE 0)

Redesign the contract layer so that Phase 1 (DAG scheduler) can be built cleanly.

---

## STEP 1 — DEFINE TARGET ARCHITECTURE

First, design the correct architecture BEFORE coding.

You must define:

### Execution Flow

PlanDocument
→ Compile Phase
→ ExecutionTask DAG
→ Scheduler (future)
→ Dispatcher

---

### Required Components

1. ExecutionTask (core unit of execution)
2. TaskRuntimeState (separate from plan)
3. Compiler (Plan → ExecutionTask)
4. Minimal Scheduler Interface (even if not fully implemented)

---

Output:

* Clean architecture diagram (text)
* Responsibilities of each component

DO NOT WRITE CODE YET

---

## STEP 2 — DESIGN NEW SCHEMAS

Define clean schemas for:

### 1. ExecutionTask

Must include:

* id
* tool
* dependencies
* arguments (frozen before execution)
* status
* attempts

### 2. TaskRuntimeState

* status
* attempts
* timestamps
* result reference

### 3. Compiler Output

* list of ExecutionTasks
* dependency graph structure

---

Compare with existing PlanStep:

* what is removed
* what is kept
* what is moved

---

## STEP 3 — IDENTIFY WHAT TO DELETE

List exactly:

* which parts of PlanExecutor must be removed
* which fields in PlanStep should no longer be used
* which modules are tightly coupled and must be refactored

Be explicit.

---

## STEP 4 — IMPLEMENT COMPILER

Implement:

PlanDocument → ExecutionTask[]

Rules:

* step_id → task.id
* action → tool
* dependencies preserved
* arguments initialized empty

NO execution logic here

---

## STEP 5 — IMPLEMENT NEW EXECUTION LOOP (MINIMAL)

Replace PlanExecutor with:

* simple scheduler loop
* ready queue (tasks with no unmet dependencies)
* sequential execution (no parallelism yet)

Flow:

1. find ready tasks
2. generate arguments
3. assign to task.arguments
4. execute via Dispatcher
5. update task state
6. mark complete
7. repeat

---

## STEP 6 — REMOVE PLAN MUTATION

* PlanDocument must become read-only
* DO NOT update PlanStep.execution or failure
* All runtime state must live in ExecutionTask / TaskRuntimeState

---

## STEP 7 — ARGUMENT SNAPSHOT

* Argument generation must happen BEFORE execution
* Arguments must be stored on ExecutionTask
* Retries must reuse same arguments

---

## STEP 8 — OUTPUT

Provide:

1. Architecture summary
2. List of deleted/replaced components
3. New schemas
4. New execution loop
5. Migration notes (what breaks, what is removed)

---

## IMPORTANT RULES

* Prefer simplicity over completeness
* Do NOT implement parallelism yet
* Do NOT touch model routing
* Do NOT introduce multi-agent concepts

Focus ONLY on:
👉 clean execution foundation

---

Be decisive. Do not hedge. Do not preserve old abstractions.


# -----Implementation by cursor-----

Here is what was completed for the DAG handoff.

### Tests and fixtures
- **`test_act_tool_execution_dispatch.py`**: `_dag_last_summary` now uses `plan.steps[0].step_id` (fixes the stray `step_id` `NameError`).
- **`test_dispatcher_fault_injection.py`**: Recovery plan steps use **indices 1 and 2** again; merged plans are fixed upstream (see below).
- **`test_execution_graph.py`**: Retry coverage uses `TraceStep.metadata["attempts"]`; **`test_retry_then_replan`** keeps **`plan_step_index=1`** on the post-failure step so the replan edge still matches `graph_builder`’s rule.
- **`test_replanner.py`**: **`test_replan_then_success`** now fails **twice** on `x1` (matching `max_retries_per_step=2`), then succeeds on `r1`; **`execute` call count** is **3**.
- **`test_mode_manager.py`**: **`_stub_final_exploration`** returns a real **`FinalExplorationSchema`**; **`plan_legacy`** uses it too so `maybe_synthesize_to_state` / coverage helpers do not see broken mocks.

### Runtime / replanner
- **`merge_preserved_completed_steps`** (`replanner.py`): After `prefix + merged`, steps are **reindexed to `1..n`**. That satisfies `PlanValidator` when the recovery plan used global indices (e.g. 2,3) or when the merge shape would otherwise leave gaps.
- **`_record_session_after_executor_step`** (`planner_task_runtime.py`): No longer reads **`PlanStep.execution`**. It takes **`state`** and derives completed-tool summaries and **`open_file`** paths from **`state.context["dag_graph_tasks"]`** (JSON-shaped runtime rows). All four call sites pass **`state`**.

### Verification
- `tests/test_mode_manager.py`, `tests/test_replanner.py`, `tests/test_dispatcher_fault_injection.py`: **27 passed**
- `tests/test_langfuse_phase11.py`: **25 passed**
- `tests/test_execution_graph.py`: **22 passed** (run earlier)

If you want a full `pytest` on the whole repo, say so and we can run it (expect longer wall time and possible unrelated flakes).

# ------ fix prompt -----
You are a staff engineer implementing Phase 0 core refactor.

Goal:
Introduce ExecutionTask as the ONLY runtime unit and remove PlanStep from execution.

No fallbacks. No partial migration.

---

## STEP 1 — CREATE EXECUTIONTASK

Create:

ExecutionTask:

* id (str)
* tool (str)
* dependencies (list[str])
* arguments (dict)
* status ("pending" | "ready" | "running" | "completed" | "failed")
* attempts (int)

TaskRuntimeState:

* optional if separated, else keep inside ExecutionTask for now

---

## STEP 2 — REMOVE PLANSTEP FROM RUNTIME

* PlanStep must NOT be mutated

* Remove all reads/writes to:

  * PlanStep.execution
  * PlanStep.failure

* PlanDocument becomes read-only input

---

## STEP 3 — REMOVE dag_graph_tasks FROM CONTEXT

* Delete usage of:
  state.context["dag_graph_tasks"]

* Replace with:
  in-memory list/dict of ExecutionTask

---

## STEP 4 — BUILD COMPILER

Implement:

compile_plan(plan: PlanDocument) -> list[ExecutionTask]

Rules:

* step_id → task.id
* action → tool
* dependencies preserved
* arguments = {}

No execution logic

---

## STEP 5 — REWRITE EXECUTION LOOP

Replace PlanExecutor with:

execute_tasks(tasks):

Loop:

1. find tasks where all dependencies are completed
2. mark as running
3. generate arguments
4. assign to task.arguments
5. call dispatcher
6. update status + attempts
7. repeat until all complete or failure

NO index usage anywhere

---

## STEP 6 — REMOVE INDEX AS DRIVER

* Remove ordering based on:
  step.index

* Execution must depend ONLY on:
  dependencies

---

## STEP 7 — UPDATE CALL SITES

* All execution must use ExecutionTask
* Planner still outputs PlanDocument
* Executor ONLY consumes ExecutionTask

---

## STEP 8 — OUTPUT

Provide:

1. New ExecutionTask implementation
2. Compiler implementation
3. New execution loop
4. List of deleted code paths

---

Rules:

* Keep it minimal
* No parallelism yet
* No retries sophistication yet
* No model routing changes

Focus:
👉 clean runtime replacement


# --- implementation ----

Here’s what was implemented for **Phase 0** and how it maps to your steps.

### 1) `ExecutionTask` (`agent_v2/schemas/execution_task.py`)
- Fields: `id`, `tool`, `dependencies`, `arguments`, `status` (`pending` | `ready` | `running` | `completed` | `failed`), `attempts`, plus `max_attempts`, `goal`, `input_hints`, `last_result`, timestamps for the current executor.
- **`TaskRuntimeState`** is folded into `ExecutionTask` (no separate type).
- **`TaskScheduler.ready_tasks`**: dependencies satisfied → `pending` → ready; tie-break **`task.id`**.

### 2) Compiler (`agent_v2/runtime/plan_compiler.py`)
- **`compile_plan(plan, policy=...)`** → `list[ExecutionTask]`: `step_id→id`, `action→tool`, deps copied, `arguments={}`, hints from `PlanStep.inputs`, `max_attempts` from policy.
- **`tasks_by_id(tasks)`** → `dict[str, ExecutionTask]`.

### 3) Execution loop (`agent_v2/runtime/dag_executor.py`)
- In-memory **`_tasks_by_id`**, **`_persistent_completed_ids`**, **`_active_plan_id`**.
- **No** `state.context["dag_graph_tasks"]` (only **pops** legacy keys on `run()`).
- Progress published on **`state.metadata`**: `executor_dag_plan_id`, `executor_dag_total`, `executor_dag_completed`, **`executor_dag_completed_ids`**.
- **`TraceEmitter.record_execution_task`** (emission order index, not `PlanStep.index`).

### 4) Removed / replaced code paths (high level)
- **Context DAG as runtime store**: `dag_graph_tasks`, `dag_completed_step_ids` no longer drive execution, merge, replan summaries, or `plan_state_from_plan_document`.
- **Replanner**: `completed_steps_for_replan(state, …)` / `failure_attempts_from_dag` → **`completed_steps_for_replan_from_tasks(plan, tasks_by_id)`** and **`failed_task.attempts`**; **`build_replan_request(..., failed_task: ExecutionTask, *, tasks_by_id)`**.
- **`plan_document_has_runnable_work`**: uses **executor metadata** when `plan_id` matches (`agent_v2/planning/planner_v2_invocation.py`).
- **`plan_state_from_plan_document`**: reads **`metadata.executor_dag_*`** and walks **`plan.steps` in document order** (no index sort); per-step summaries from context rows **removed** (empty summaries; `last_result_summary` still passed where needed).
- **`PlannerTaskRuntime`**: `_record_session_after_executor_step` / `_merge` / progress / failure replan use **`plan_executor.get_tasks_by_id()`** / **`get_completed_step_ids()`**.
- **`PlanArgumentGenerator.generate`**: takes **`ExecutionTask`** only.
- **Schema package**: dropped **`CompiledExecutionGraph`** / **`TaskRuntimeState`** exports from `agent_v2/schemas/__init__.py` (they are no longer defined).
- **Tests** updated for metadata, `ExecutionTask`, `compile_plan`, and executor introspection (e.g. `tests/test_planner_v2.py` no longer touches `PlanStep.execution`).

### 5) Verification
- Focused suites passed, including **`tests/test_planner_v2.py`** (44), replanner/dispatcher/act/planner_v2-invocation batches.

`TraceEmitter.record_step(PlanStep, …)` remains for **non-executor** callers (tests/scripts); the DAG path uses **`record_execution_task`** only.

# -----Remaining for next step - ----
✅ What is now CORRECT
ExecutionTask is real → runtime finally has a proper unit
Compiler exists → clear Plan → Runtime boundary
Executor is dependency-driven → no index usage ✔
PlanStep is no longer mutated ✔
State moved out of context hack (dag_graph_tasks) ✔

👉 This is now a true pre-DAG runtime, not a fake one

⚠️ Remaining issues (must fix before Phase 1)
1. ❗ Runtime state still mixed inside ExecutionTask

You folded TaskRuntimeState inside ExecutionTask.

👉 This is fine temporarily, but:

will block concurrency later
makes persistence messy

Action (next phase, not now):
Split:

ExecutionTask (immutable spec)
TaskRuntimeState (mutable)
2. ❗ task.id tie-break = hidden ordering

You replaced index with:

tie-break by task.id

👉 This reintroduces implicit ordering

Fix later:

scheduler should not depend on ordering
only for determinism in logs (not execution semantics)
3. ❗ Metadata still leaking runtime state

You’re using:

state.metadata.executor_dag_*

👉 This is okay short-term, but:

not scalable
not replay-safe
4. ❗ Argument generation still inside loop

You generate args during execution loop.

👉 Acceptable for now, but:

Phase 1 → move to “task preparation step”
🧠 Verdict

👉 Phase 0 = SUCCESSFULLY COMPLETED
👉 System is now architecturally correct

You’ve crossed the hardest part:

You now have a real runtime boundary
