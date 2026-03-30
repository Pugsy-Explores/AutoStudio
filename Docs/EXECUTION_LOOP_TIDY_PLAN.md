# execution_loop.py Tidy Plan (Prod Grade)

## Scope

File: `agent/orchestrator/execution_loop.py`  
Callers: `deterministic_runner.run_hierarchical` → `agent_controller.run_controller`

## 1. Bug Fix

| Issue | Fix |
|-------|-----|
| `max_runtime_seconds` param ignored | Loop checks `MAX_TASK_RUNTIME_SECONDS` directly; should use `execution_limits["max_runtime_seconds"]` (which already receives the override) |

## 2. Remove / Simplify

| Item | Action | Rationale |
|------|--------|-----------|
| `description = (step.get("description") or "")[:]` | Replace with `(step.get("description") or "")` | `[:]` on str is a no-op copy |
| Log prefix `[control]` in guardrail block | Change to `[execution_loop]` | Consistent prefix |

## 3. Structural Improvements

| Item | Action |
|------|--------|
| Limit checks | Extract `_should_stop_loop()` to return `(bool, str | None)` — reduces repetition and centralizes limit logic |
| Runtime check | Use `max_runtime` from `execution_limits` so override is respected |

## 4. Keep (Do Not Remove)

- `loop_output` keys used by agent_controller: `completed_steps`, `patches_applied`, `files_modified`, `errors_encountered`, `tool_calls`, `plan_result`, `start_time`
- `edit_telemetry` (passed through in loop_output for downstream)
- `react_history` in loop_output
- All trace logging (`log_fn` events)
- `_REACT_SYSTEM_PROMPT`, `_REACT_TO_STEP`, validation, observation building

## 5. Docstrings

- Module: clarify ReAct loop, limits, and loop_output contract
- `execution_loop()`: document params and return shape
- `LoopResult`: add field descriptions

## 6. Architecture Alignment

- Rule 1: No redesign; extend/refactor for clarity only
- Rule 10: Preserve trace logging
- Rule 21: Limits remain mandatory
