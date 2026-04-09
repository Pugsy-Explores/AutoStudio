
Here is a **Phase 0 / contract-layer** read of what the code actually does today and what it would take to be DAG-scheduler ready.

---

## 1. Plan vs execution contract

**What exists**

`PlanDocument` + `PlanStep` are defined in ```59:69:agent_v2/schemas/plan.py
class PlanStep(BaseModel):
    step_id: str
    index: int
    type: Literal["explore", "analyze", "modify", "validate", "finish"]
    goal: str
    action: Literal["search", "open_file", "edit", "run_tests", "shell", "finish"]
    inputs: dict = {}
    outputs: dict = {}
    dependencies: list[str] = []
    execution: PlanStepExecution = PlanStepExecution()
    failure: PlanStepFailure = PlanStepFailure()
```

**Is `PlanStep` a real execution unit?**

**Partially.** It is strong on *intent* (goal, coarse `action`, string deps) and on *runtime bookkeeping* (`execution`, `failure`), but weak on *executable contract*:

| Needed for a scheduler-ready task | In code? |
|-----------------------------------|----------|
| Stable task id | `step_id` (string) |
| DAG edges | `dependencies` |
| Tool identity | `action` (small closed set) |
| **Typed / validated inputs** | `inputs` is an unvalidated `dict`; real args come from LLM + merge (see below) |
| **Expected output schema / artifacts** | `outputs: dict` is opaque; nothing validates shape |
| **Idempotency / side-effect class** | Absent |
| **Per-task retry policy (executable)** | `PlanStepExecution.max_attempts` exists; `failure.retry_strategy` is **not** enforced in the executor (only set from planner JSON) |
| **Execution boundary** (what counts as one dispatch) | Implicit: one `PlanStep` → one `_execute_step` except internal retries |

So **`PlanStep` is primarily a planner artifact plus an in-place runtime scratch pad**, not a clean “task spec” for a DAG engine.

**Gaps vs a real `ExecutionTask`**

- **Inputs**: Planner `inputs` are hints; `PlanArgumentGenerator.generate()` produces the real dict, merged in `_merge_args` (```989:998:agent_v2/runtime/plan_executor.py```). The “contract” is split across `PlanStep`, LLM output, and `validate_action` from legacy `agent.*` — not a single schema on the task.
- **Outputs**: `outputs` is documentation-only for the planner; the executor never checks it against `ExecutionResult`.
- **Boundaries**: Dispatch goes through an ad hoc ReAct-shaped dict (`_to_dispatch_step`) (```1001:1028:agent_v2/runtime/plan_executor.py```), not a first-class `ToolCall` from `schemas/tool.py` at the executor boundary.

---

## 2. Proposed `ExecutionTask` schema (clean target)

Design goal: **immutable spec** + **mutable runtime state** kept separate (scheduler owns the latter).

```python
# Proposed shape (conceptual — not in repo today)

class TaskRetryPolicy(BaseModel):
    max_attempts: int = 2
    # Executor must implement these; today PlanStep.failure.retry_strategy is inert.
    on_failure: Literal["retry_same", "regenerate_args", "abort", "replan"] = "retry_same"
    retryable_error_types: frozenset[ErrorType] | None = None  # None = all

class TaskInputs(BaseModel):
    """Fully specified after compile: planner hints + resolved defaults; optional before arg-gen."""
    arguments: dict[str, Any]  # or a typed union per tool once tools are schema-first

class ExecutionTaskSpec(BaseModel):
    """Immutable after compile — what the scheduler schedules."""
    id: str                          # == step_id for migration
    tool: str                        # normalized tool name (not ReAct action)
    dependencies: list[str]
    inputs: TaskInputs               # post–argument-generation snapshot for dispatch
    # Optional until you add artifact typing:
    expected_output: dict[str, Any] | None = None  # JSON-schema ref or logical name
    side_effect: Literal["none", "read", "mutate", "exec"] = "read"
    idempotency_key: str | None = None               # for safe parallel/retry

class TaskRuntimeState(BaseModel):
    """Owned by scheduler / runner — not embedded in planner output."""
    status: Literal["pending", "ready", "running", "completed", "failed", "skipped"]
    attempts: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    last_result_ref: str | None = None  # pointer into trace store, or embed ExecutionResult
```

**Compared to `PlanStep`**

| Item | `PlanStep` today | `ExecutionTask` |
|------|------------------|-----------------|
| Planner-only | `type`, `goal`, `index`, `outputs` (opaque) | Keep on a separate `PlanIntent` or strip after compile |
| Execution | `execution` + `failure` embedded | Move to `TaskRuntimeState` + `TaskRetryPolicy` |
| Ordering | `index` is **legally required** 1..N and drives iteration | Scheduler uses graph + stable tie-break, not “the plan’s total order” |
| Tool args | Split across `inputs`, LLM, merge | Single `TaskInputs.arguments` snapshot before run |

**Wrong place today**

- **`index` + “last step must be finish”** are **pipeline** rules (```86:93:agent_v2/validation/plan_validator.py```), not DAG rules.
- **`execution` / `failure` on the same object as planner fields** encourages in-place mutation of the plan document during run (what `PlanExecutor` does with `model_copy(update=...)`).

---

## 3. State model audit

**Runtime bag**

- `agent_v2/state/agent_state.py` — dataclass `AgentState` with `context`, `history`, `current_plan`, `plan_index`, etc.
- `agent_v2/schemas/agent_state.py` — Pydantic `AgentState` with different fields (`step_results: list[ExecutionResult]` vs dicts in the dataclass).

That is already a **contract fracture**: two “AgentState” shapes in one product area.

**What is shared**

- `state.context` — holds `active_plan_document`, `shell`, session memory, primitives; **global to the run**.
- `state.metadata` — dispatch counts, Langfuse, abort reasons.

**Mutation**

- **`PlanStep` is mutated in place** (via `model_copy` reassignment to `step.execution` / `step.failure`) inside `PlanExecutor` (e.g. ```731:756:agent_v2/runtime/plan_executor.py```).
- **`state.history` / `state.step_results`** appended in `_update_state` (```1068:1090:agent_v2/runtime/plan_executor.py```).
- **Dispatcher** may `setdefault` shell/editor/browser on `state.context` (```95:97:agent_v2/runtime/dispatcher.py```).

**Unsafe / unclear for DAG + parallelism**

- No **per-task isolated context**; parallel tasks would share `context` unless you deep-copy like `search_batch` does for **state** (```200:204:agent_v2/runtime/dispatcher.py```) — that pattern is **not** generalized for arbitrary tools.
- **Plan document is both spec and live state**; replan merges completed steps onto new plans (`merge_preserved_completed_steps`), which is fragile if two runnable tasks were hypothetically in flight.

**What should be what (for Phase 0 design)**

| Concern | Should be |
|---------|-----------|
| Planner output (`PlanDocument` without execution filled) | **Immutable** snapshot per plan version |
| Compiled `ExecutionTaskSpec` | **Immutable** for a given compile |
| `TaskRuntimeState` | **Per-task**, owned by scheduler |
| Repo/session handles, Langfuse, budgets | **Global** (or session-scoped) |
| Tool args after LLM | **Snapshotted** on the task before dispatch (for replay and safe retry) |

---

## 4. Tool contract audit (`ToolCall`, `ToolResult`, `ExecutionResult`)

**Normalization path (actual)**

`Dispatcher.execute` → `coerce_to_tool_result` → `map_tool_result_to_execution_result` → `ExecutionResult` (```90:154:agent_v2/runtime/dispatcher.py```).

**Does the stack guarantee idempotent / retry-safe tools?**

**No.** The schemas describe **shape**, not **semantics**. Nothing marks a tool as idempotent; retries in `_run_with_retry` **repeat the same dispatch** with the same merged args.

**Does `ExecutionResult` cover success / structured output / errors?**

**Reasonably for a single dispatch**: `success`, `status`, `output.data` + `output.summary` + optional `full_output`, `error` with `ErrorType`, `metadata` (```51:63:agent_v2/schemas/execution.py```).

**Gaps for safe retries**

- No **attempt id** or **deduplication key** passed to tools.
- **`ToolResult.data` is an unbounded dict**; coercion can paper over inconsistent handler return types.
- **`search_multi`** returns `list[ExecutionResult]` with synthetic step ids `f"{step_id}_{i}"` (```126:127:agent_v2/runtime/dispatcher.py```) — a **different contract** than single-step execution; `PlanExecutor` never consumes that path (only `search_batch` does), so **two dispatch contracts** coexist.

---

## 5. Execution model mismatch

**Today:** `PlanDocument` → sort by `index` → for each round, scan in index order, skip if deps not satisfied → one runnable step at a time → `_run_with_retry` → dispatcher (```417:496:agent_v2/runtime/plan_executor.py```).

**Needed for DAG scheduling:** `CompiledGraph` → **ready set** (deps satisfied) → pick N tasks (future) → execute → record outputs → update deps; no global “scan list in index order” as the core algorithm.

| Piece | Remove / replace for DAG | Reuse |
|-------|---------------------------|--------|
| Outer replan loop + `PlanValidator` after replan | Keep at **plan** level | ✓ |
| `_can_execute` (deps ⊆ completed) | Keep logic, move to **scheduler** | ✓ idea |
| `ordered = sorted(..., key=index)` scheduling | **Remove** as primary driver | — |
| Rounds until no progress (deadlock detection) | Replace with explicit **cycle / stuck detection** on ready-set | Similar intent |
| `_to_dispatch_step` legacy dict | Replace with **`ToolCall` or executor-native step** built from `ExecutionTask` | Temporary adapter layer |
| `_merge_args` + argument generator | Keep behind **compile phase** that outputs frozen `TaskInputs` | ✓ |

---

## 6. Dependency model

**Represented as:** `PlanStep.dependencies: list[str]` of `step_id`s.

**Validated (when full plan validation runs):**

- Unique `step_id` (```59:61:agent_v2/validation/plan_validator.py```)
- No cycles — DFS (```225:247:agent_v2/validation/plan_validator.py```)
- Each dep exists and is not self (```216:222:agent_v2/validation/plan_validator.py```)
- **Extra rule:** every dependency must have **strictly lower `index`** than the dependent (```251:266:agent_v2/validation/plan_validator.py```)

That last rule means the graph must be **compatible with a single topological order that matches increasing `index`**. It is **stricter than a general DAG** and encodes **“pipeline with optional skip-ahead edges only backward in the linear order”**.

**Executor assumption:** steps are iterated in **index order**, not in topological layers; readiness is still correct for any DAG that satisfies the validator, but **parallel-ready tasks at the same depth are serialized** by index.

**Gap for Phase 1 DAG scheduler:** you will want **either** to drop `_validate_dependencies_precede_step` **or** keep it only in a “legacy sequential mode” — otherwise you cannot represent arbitrary DAGs once indices are no longer the semantic spine.

---

## 7. Contract consistency (planner → arg gen → dispatcher → executor)

**Alignments**

- `PlanStep.action` set is aligned with `ALLOWED_PLAN_STEP_ACTIONS` and mapping to legacy ReAct in `phase1_tool_exposure` (referenced from executor).
- `ExecutionResult.step_id` is enforced to match the step (```874:878:agent_v2/runtime/plan_executor.py```).

**Mismatches**

1. **`ToolCall` schema is unused** at the executor boundary; dispatch uses **dict** with `_react_*` keys.
2. **`PlanArgumentGenerator`** imports **`agent.execution.react_schema`** and **`agent.tools.react_registry`** — planner v2 **depends on legacy tool registration**, not on `agent_v2.schemas.tool`.
3. **`RetryState`** in `execution.py` is documented as a mirror of `PlanStep.execution` (```66:75:agent_v2/schemas/execution.py```) — easy to drift; not authoritative.
4. **`plan_state_from_plan_document`** assumes **first incomplete step in index order** is “current” (```38:49:agent_v2/schemas/plan_state.py```), which is **wrong** for a DAG with multiple ready tasks.
5. **Two `AgentState` types** (dataclass vs Pydantic) break a single “state contract” for tooling.

---

## 8. Required output summary

### 1) Current system contract (what it really is)

A **validated, mostly-linear plan** (indices 1..N, finish last, deps only to earlier indices) whose steps **mutate in place** during execution; each step resolves to a **legacy ReAct-shaped dict**, is normalized to **`ExecutionResult`** at the dispatcher, with **retries** implemented by re-dispatching the same arguments until `max_attempts`. **No separate immutable task spec; no scheduler-ready output contract; no executable retry strategy branching.**

### 2) Critical contract gaps (top 5)

1. **No frozen, typed task inputs** — args are LLM + merge + legacy validation, not a single post-compile artifact.
2. **Plan step = planner + runtime** — breaks clear DAG spec vs run state; unsafe for concurrency.
3. **Dependency model is DAG-like but validation + iteration are pipeline-centric** (`index` ordering rule + sequential scan).
4. **Dual dispatch contracts** (single `ExecutionResult` vs `list[ExecutionResult]` for `search_multi`) without a unified task abstraction.
5. **`retry_strategy` / idempotency / side effects** are **not enforced** — retries are blind repeats.

### 3) Proposed `ExecutionTask` (clean)

See **section 2**: split **`ExecutionTaskSpec`** (immutable) + **`TaskRuntimeState`** + **`TaskRetryPolicy`**, with optional `expected_output`, `side_effect`, `idempotency_key`.

### 4) Refactor plan

| Change | Delete | Keep |
|--------|--------|------|
| Introduce compile step: `PlanDocument` → `list[ExecutionTaskSpec]` + graph | Primary scheduling by `sorted(index)` | Cycle detection, dep existence checks, `ExecutionPolicy` limits (reinterpret per-task / global) |
| Move `PlanStepExecution` / failure fields to runtime store or `TaskRuntimeState` | Eventually: embedding execution on planner `PlanStep` | `ToolResult` → `ExecutionResult` mapping, coercion, trace hooks |
| Dispatch accepts `ToolCall`-like structure from task spec | Ad hoc `_to_dispatch_step` once callers migrated | `Dispatcher.execute` normalization pipeline |
| Relax or mode-gate `_validate_dependencies_precede_step` | Rule for true DAG mode | Strict mode for backward compatibility |
| Unify on one `AgentState` definition | Duplicate state module | Session/context patterns |

### 5) Migration strategy (PlanStep → ExecutionTask without breaking the system)

1. **Add** `ExecutionTaskSpec` + `TaskRuntimeState` **without** removing `PlanStep`.
2. **Compiler function** `compile_plan(plan: PlanDocument) -> tuple[list[ExecutionTaskSpec], GraphMeta]` that copies `step_id`, `dependencies`, `action`→`tool`, and initially puts `inputs.arguments = {}`.
3. **Hook argument generation** to run **as today**, then **write the merged dict into `ExecutionTaskSpec.inputs.arguments`** (immutable copy / new spec instance per attempt if you want full auditability).
4. **Feature flag**: `USE_DAG_SCHEDULER=0` keeps current `PlanExecutor` path; `=1` uses scheduler that reads **only** compiled tasks + runtime state, while still updating **legacy** `PlanStep.execution` for UI/tests until retired.
5. **Tests**: same golden plans, assert **compiled task graph** + **execution order** for deps; then flip scheduler.
6. **Deprecate** `index`-as-authority after consumers (validator, `PlanState`, UI) can use **topological rank** or explicit `priority` on `ExecutionTaskSpec`.

---

**Bottom line for Phase 0:** the **normalization boundary (`ToolResult` → `ExecutionResult`)** and **basic dependency + cycle checks** are a usable foundation. The system **cannot** honestly claim DAG-scheduler readiness until **task spec is separated from planner artifacts**, **inputs are snapshotted**, **retry/side-effect semantics are explicit**, and **pipeline-only validation rules** (`index`-ordered deps, single “current” step) are **relaxed or isolated** behind a compatibility mode.