# ReAct Mode Clean Implementation Plan

**Status: IMPLEMENTED** (2025-03-23)

## Summary

Transform REACT_MODE from a "partially disabled" variant into a **full architectural override**: Model is the only brain. No planner, no policy engine, no blockers, no system retry.

---

## Phase 1: Orchestrator (execution_loop.py)

### 1.1 Action selection (already correct)
- REACT_MODE: `step = _react_get_next_action(instruction, state)` only
- No `state.next_step()` in ReAct path ✓

### 1.2 Bypass planner upstream
- **deterministic_runner**: When REACT_MODE, skip get_parent_plan/get_plan; pass minimal plan so execution_loop can run.
- **execution_loop**: ReAct path never uses plan steps.

### 1.3 Remove from ReAct path (HARD)
- `step_retry_count` logic → never retry same step; always continue to next model call
- `replan()` → never call
- `goal_evaluator` → never use (step is None only when model says finish)

### 1.4 Convert blockers to observations (ReAct only)
- `FATAL_FAILURE` from result → do NOT break; record observation, continue
- `GuardrailError` → do NOT break; record observation, continue

### 1.5 Termination (ReAct only)
- Break ONLY on: `react_finish`, `step is None`, hard caps (max_steps, max_runtime, max_iterations)

---

## Phase 2: Dispatcher (step_dispatcher.py)

### 2.1 REACT_MODE branch at top
- When `REACT_MODE`: use `_dispatch_react()` which bypasses policy_engine entirely.

### 2.2 _dispatch_react() implementation
- SEARCH: call `_search_fn(query)` directly, one shot, no policy
- READ: call read_file directly
- EDIT: call `_edit_fn` directly
- RUN_TEST: call run_tests directly
- No validate_step_input (or convert to warning in result)
- No lane contract (or convert to observation in result)
- All failures → return `{success: False, output: error_msg}` — never FATAL

### 2.3 SEARCH as pure function in ReAct
- Single query in → single result/empty out
- No `get_initial_search_variants`, no `_rewrite_query_fn`, no `max_attempts` loop

---

## Phase 3: Executor (executor.py)

### 3.1 ReAct path
- When REACT_MODE, executor must NOT propagate classification to cause loop break.
- Option: executor receives `react_mode` from state.context; when True, always return RETRYABLE or SUCCESS (never let FATAL bubble as stop signal).

Actually: the execution_loop checks `classification == FATAL_FAILURE` and breaks. So we need execution_loop to IGNORE that in ReAct mode. Simpler: in ReAct, treat all results as "observation" — never break on classification.

---

## Phase 4: deterministic_runner

### 4.1 REACT_MODE entry
- When REACT_MODE, bypass hierarchical plan resolution.
- Create minimal state with `current_plan = {"steps": [], "plan_id": "react"}` and invoke execution_loop directly.

---

## File Change Summary

| File | Changes |
|------|---------|
| `agent/orchestrator/execution_loop.py` | ReAct: no retry/replan/goal_eval; FATAL/Guardrail → observation; termination = finish + caps only |
| `agent/execution/step_dispatcher.py` | Add `_dispatch_react()`; when REACT_MODE call it instead of main dispatch path; SEARCH = pure _search_fn |
| `agent/orchestrator/deterministic_runner.py` | When REACT_MODE, skip planner, minimal plan, direct to execution_loop |

---

## Invariants (ReAct Mode)

1. **One brain**: Model selects every action.
2. **No mutation**: No query variants, no rewrites, no symbol_retry.
3. **No blocking**: All "errors" become observations.
4. **No system retry**: Model decides next action after any result.
5. **Termination**: finish or hard caps only.

---

## Implemented Changes (2025-03-23)

| File | Change |
|------|--------|
| `agent/orchestrator/execution_loop.py` | FATAL_FAILURE → record + continue (no break); GuardrailError → record + observation + continue; loop_output built when enable_react; state.context["react_mode"]=True |
| `agent/execution/step_dispatcher.py` | `_dispatch_react()` added: direct _search_fn, read_file, _edit_fn, run_tests; no policy_engine; never FATAL; REACT_MODE + react_mode → use _dispatch_react |
| `agent/orchestrator/deterministic_runner.py` | run_hierarchical: when REACT_MODE, skip get_parent_plan; create minimal state; call execution_loop directly |
