# PLAN mode safe iterative loop — design specification

**Status:** design locked (pre-implementation).  
**Audience:** production engineers implementing agent_v2 mode behavior.

---

## 1. Goals

Transform **PLAN** from plan-only into a **controlled iterative loop** (Anthropic-style: reason + safe tools + iterate), while **ACT** remains full execution (including `edit`).

- **Safe:** no `edit`, no destructive shell; optional policy for `run_tests`.
- **Reuse:** single controller loop with ACT; extend ModeManager / runtime / validator only.
- **Do not** replace PlanExecutor, planner core, or execution engine architecture.

---

## 2. Current vs target architecture

### 2.1 Today (confirmed)

| Mode | Path | Planner policy | Controller JSON | PlanExecutor | Trace |
|------|------|----------------|-----------------|--------------|-------|
| `plan` | `run_plan_only` | `PLAN_MODE_TOOL_POLICY` | No (`require_controller_json=False`) | **No** | `plan_only_trace` (no tool steps) |
| `deep_plan` | `run_deep_plan_only` | Same | No | **No** | Same |
| `act` / `plan_execute` | `run_explore_plan_execute` | `ACT_MODE_TOOL_POLICY` | Yes if `controller_loop_enabled` | **Yes** | ACT-style (`TraceEmitter` + tool steps) |

### 2.2 After implementation (locked semantics)

| CLI / mode name | Behavior | Policy | Executor | Trace |
|-----------------|----------|--------|----------|-------|
| **`plan_legacy`** | Exploration → **one** planner call → `PlanDocument` only (today’s `plan`) | `PLAN_MODE_TOOL_POLICY` on planner only | **No** | Plan-only trace (legacy) |
| **`plan`** | Exploration → **same controller loop as ACT** → `PlanExecutor.run_one_step` for safe tools | `PLAN_MODE_TOOL_POLICY` | **Yes** | **ACT-style trace** (mandatory) |
| **`deep_plan`** | Decision TBD: either align with **`plan`** but `deep=True`, or keep as legacy-only; document in PR | — | — | — |
| **`act`** | Unchanged | `ACT_MODE_TOOL_POLICY` | **Yes** | ACT-style |

**Rationale (mode naming — LOCKED):**  
Redefining the string `plan` to mean “safe execution” without renaming would confuse CLI users, tests, and docs. Therefore:

- **`plan_legacy`** = old “plan = no execution.”
- **`plan`** = new “plan = execution (safe)” — the iterative loop.

Any transitional alias (e.g. `plan_loop`) should map to **`plan`** and be documented as deprecated once stable.

---

## 3. Controller loop (single source of truth)

**Reuse** `PlannerTaskRuntime._run_act_controller_loop` unchanged in structure.

New entry (e.g. `run_plan_explore_execute_safe`) shall:

1. Set `_set_planner_tool_policy(self.planner, PLAN_MODE_TOOL_POLICY)`.
2. Run initial exploration (same as ACT).
3. If `get_config().planner_loop.controller_loop_enabled`: call **`_run_act_controller_loop`** with same budgets (`max_planner_controller_calls`, `max_sub_explorations_per_task`, etc.).
4. Else: single planner + `plan_executor.run` (fallback) — still subject to validator **`task_mode="plan_safe"`** on merged plans.

**Do not** duplicate the `while True` / explore / replan / stop / `run_one_step` logic in a second module.

---

## 4. MUST-FIX: Validator — `task_mode = "plan_safe"` (NOT optional)

### 4.1 Problem

`task_mode="read_only"` uses `get_config().planner.allowed_actions_read_only`, currently:

`frozenset({"search", "open_file", "finish"})`

That **excludes** `run_tests` and `shell`. If PlanExecutor runs steps the planner is allowed to emit under `PLAN_MODE_TOOL_POLICY`, **PlanValidator will reject valid plans** and the loop will fail unpredictably.

### 4.2 Decision (LOCKED)

- **Do not** reuse `read_only` for this feature. **`read_only` ≠ `plan_safe`.**
- **Add** a new validator task mode: **`task_mode="plan_safe"`**.

**Allowlist for `plan_safe` (PlanStep.action):**

| Action | Allowed |
|--------|---------|
| `search` | yes |
| `open_file` | yes |
| `run_tests` | yes |
| `shell` | yes |
| `finish` | yes |
| `edit` | **no** |

Implementation sketch (for implementers):

- In `agent_v2/validation/plan_validator.py`, branch `_validate_step` (or equivalent) on `task_mode == "plan_safe"` using a **dedicated** frozen set (or config key `planner.allowed_actions_plan_safe`), **not** `allowed_actions_read_only`.
- Every `PlanValidator.validate_plan(..., task_mode="plan_safe")` call on paths that merge/replan plans for the new **`plan`** mode must use this mode consistently (replanner, controller merges, etc.).

---

## 5. `react_mode` vs `plan_safe_execute` (LOCKED)

### 5.1 Fact

Today **PlanExecutor** dispatches via `Dispatcher(execute_fn=_dispatch_react)` and does **not** depend on `state.context["react_mode"]` for that path.

### 5.2 Risk

Future code may gate behavior on `if react_mode:` and conflate “ReAct-style tools” with “ACT mode,” breaking PLAN semantics.

### 5.3 Decision (LOCKED)

For the new **`plan`** mode runtime path:

- Set **`state.context["plan_safe_execute"] = True`** when running the safe plan loop.
- **Do not** set `react_mode = True` for PLAN solely to enable tools.

`AgentRuntime.run` may continue to set `react_mode` only for `act` / `plan_execute` unless a separate audit proves a specific tool requires it for PLAN (avoid unless necessary).

---

## 6. Executor guard — **REQUIRED** (minimal)

### 6.1 Decision (LOCKED)

**Not optional.** `PlanExecutor` (or the single choke point before dispatch) must enforce:

- When **`state.metadata["mode"] == "plan"`** (or when `state.context.get("plan_safe_execute")` is True — pick **one** canonical signal and document it; prefer **metadata mode** for consistency with Langfuse):

  - **Reject `edit`** before any patch / handler runs (clear error → `ExecutionResult` / retry policy as today).

Optional second line: reject **shell** that fails the same rules as `apply_tool_policy` (shared helper in `tool_policy.py` to avoid drift).

**Why:** planner bugs and prompt injections happen; executor is the **last safety net**.

---

## 7. Trace consistency (LOCKED)

### 7.1 Problem

`run_plan_only` uses `_attach_plan_only_trace` — trace **without** per-tool steps.

### 7.2 Decision (LOCKED)

The new **`plan`** mode (iterative loop) must use **ACT-style tracing**:

- Same `TraceEmitter` lifecycle as `run_explore_plan_execute` (set/clear active emitter, `plan_executor.run_one_step` / full run recording tool steps).
- Return shape compatible with `normalize_run_result` and downstream CLI/graph where ACT already works.

**Do not** attach plan-only trace for the new **`plan`** path.

`plan_legacy` may keep the existing plan-only trace behavior.

---

## 8. Tool policy (planner) — unchanged list, enforcement layers

| Layer | Role |
|--------|------|
| `PLAN_MODE_TOOL_POLICY` + `apply_tool_policy` | Blocks `edit`; constrains `run_shell` (allowlist `ls`, `rg`, `grep`, `cat`; forbid `&&`, `;`, `|`, `` ` ``). |
| **`PlanValidator` + `task_mode="plan_safe"`** | Structural allowlist for steps including **`run_tests`** and **`shell`**. |
| **`PlanExecutor` guard** | **Required** deny `edit` (and optional shell mirror) when mode is plan-safe. |

---

## 9. Stop conditions and telemetry

Reuse existing controller budgets:

- `max_planner_controller_calls`, `max_sub_explorations_per_task`
- Planner `stop`
- `PlanExecutor` policy: `max_executor_dispatches`, `max_runtime_seconds`
- Deadlock / `failed_step` → replan

**Telemetry (recommended):**

- `tool_execution` logs (already from PlanExecutor) with `tool_policy_mode: plan` via `_sync_tool_policy_mode_to_state`.
- Log **loop iteration** / `planner_controller_calls` in metadata (existing counters).
- **Planner telemetry** paths used in ACT should run unchanged for **`plan`**.

---

## 10. Implementation checklist (file / function level)

| # | Location | Action |
|---|----------|--------|
| 1 | `agent_v2/validation/plan_validator.py` | Add **`plan_safe`** task mode + allowlist `{search, open_file, run_tests, shell, finish}`. |
| 2 | `agent_v2/config.py` | Optional: `allowed_actions_plan_safe` frozen set (single source for validator). |
| 3 | `agent_v2/runtime/mode_manager.py` | Map **`plan`** → new safe loop; add **`plan_legacy`** → current `run_plan_only` (and document `deep_plan`). |
| 4 | `agent_v2/runtime/planner_task_runtime.py` | Add `run_plan_explore_execute_safe` (or parameterized shared entry) calling `_run_act_controller_loop` + `PLAN_MODE_TOOL_POLICY` + ACT trace; validate merged plans with **`task_mode="plan_safe"`**. |
| 5 | `agent_v2/runtime/plan_executor.py` | **Required** guard: deny **`edit`** when plan-safe (metadata or flag per §6). |
| 6 | `agent/cli/run_agent.py` / `agent/cli/entrypoint.py` | Extend `--mode` choices: **`plan`**, **`plan_legacy`** (remove or alias old `plan` as `plan_legacy`). |
| 7 | `agent_v2/runtime/runtime.py` | Set `plan_safe_execute` for new plan path; keep `react_mode` for act/plan_execute only unless audited. |
| 8 | Replanner / merge call sites | Pass **`task_mode="plan_safe"`** wherever `PlanValidator.validate_plan` runs for this mode. |

---

## 11. Test plan

1. **`plan` executes `open_file`** — tool step in trace; file content in observations.
2. **`plan` executes `search`** — search dispatched; results present.
3. **`plan` blocks `edit`** — policy and/or **executor guard**; no patch applied.
4. **`plan` iterates** — multiple `run_one_step` / progress cycles; planner called >1.
5. **`plan` stops** — planner `stop`; no further executor calls.
6. **Validator** — plan document with `run_tests` + `shell` steps passes under **`plan_safe`**; fails under **`read_only`** (regression that they differ).
7. **ACT regression** — `act` unchanged; full tool set + policy.
8. **`plan_legacy`** — single planner call, no executor, legacy trace shape preserved.

---

## 12. Risks and safeguards

| Risk | Mitigation |
|------|------------|
| Validator rejects valid safe steps | **`plan_safe`** mode (§4) — mandatory before merge. |
| Users expect old `plan` | **Rename lock** (§2): `plan_legacy` + docs + CLI help. |
| `react_mode` misuse | **`plan_safe_execute`** only (§5). |
| Planner emits `edit` | **`apply_tool_policy` + required executor guard** (§6–8). |
| Shell escape | Allowlist + substring checks; document limits; optional executor duplicate. |
| `deep_plan` ambiguity | Explicit product decision in PR (align to **`plan`** + `deep=True` vs legacy-only). |

---

## 13. Architecture compliance

- **Extend** ModeManager / PlannerTaskRuntime / PlanValidator / PlanExecutor guards — **do not** replace execution engine, planner, or retrieval pipeline.
- Retrieval-before-reasoning and dispatcher-as-tool-entrypoint rules remain in force for existing paths.

---

## 14. Revision history

| Date | Change |
|------|--------|
| 2026-03-29 | Initial design + audit merged into this doc. |
| 2026-03-29 | Locked: `plan_safe` validator, mode rename `plan`/`plan_legacy`, `plan_safe_execute`, required executor guard, ACT-style trace for new `plan`. |
