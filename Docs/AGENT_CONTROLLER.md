# Agent Controller — Full Pipeline

The **agent controller** (`run_controller`) orchestrates the complete development workflow: instruction → plan → retrieval → edit → conflict resolution → patch execution → change detection → test repair → task memory. It does not modify `agent_loop` or `StepExecutor`; it uses `dispatch` for non-EDIT steps and a custom `_run_edit_flow` for EDIT.

---

## Entry Point

```python
from agent.orchestrator.agent_controller import run_controller

result = run_controller(
    instruction="Add a retry decorator to the fetch function",
    project_root="/path/to/repo",
)
# Returns: { task_id, instruction, completed_steps, files_modified, errors }
```

---

## Pipeline Flow

```
instruction
  → build_repo_map() — spec format {modules, symbols, calls} → repo_map.json
  → search_similar_tasks() — vector index of past tasks (optional)
  → _get_plan(instruction)
       → [if ENABLE_INSTRUCTION_ROUTER=1] route_instruction() → category
       → if CODE_SEARCH/CODE_EXPLAIN/INFRA: single-step plan, skip planner
       → if CODE_EDIT/GENERAL: planner.plan(instruction)
       → [if ENABLE_INSTRUCTION_ROUTER=0] planner.plan(instruction) directly
  → AgentState with plan, context
  → while not state.is_finished():
        step = state.next_step()
        if action == EDIT:
            _run_edit_flow(step, state)
        else:
            dispatch(step, state)
        validate_step; on failure → replan(state, failed_step=step, error=...)
  → save_task() — persist to .agent_memory/tasks/
  → finish_trace()
  → return task summary
```

---

## EDIT Flow (Extended)

When `action == "EDIT"`, the controller runs an extended pipeline instead of the standard policy-engine edit:

```
plan_diff(instruction, context)
  → changes: [{ file, symbol, action, patch, reason }]
  → safety checks: max 5 files, 200 lines per patch
  → detect_change_impact() — affected callers, risk level (LOW/MEDIUM/HIGH)
  → resolve_conflicts() — same symbol, same file, semantic overlap → sequential_groups
  → for each group:
        to_structured_patches()
        run_with_repair(patch_plan, project_root, context, max_attempts=3)
          → execute_patch (ast_patcher → patch_validator → write; rollback on invalid syntax, validation failure, or apply error)
          → run tests (pytest)
          → on failure: plan repair, retry (max 3 attempts)
          → flaky detection: re-run failing test with pytest --count=2
          → compile step (py_compile) before tests when COMPILE_BEFORE_TEST=1
  → update_index_for_file() for each modified file
  → update_repo_map_for_file() for each modified file (incremental repo_map refresh)
```

---

## Safety Limits

| Limit | Value | Purpose |
|-------|-------|---------|
| `MAX_FILES_EDITED` | 5 | Max files per edit step |
| `MAX_PATCH_SIZE` | 200 lines | Max lines per patch |
| `MAX_TASK_RUNTIME_SECONDS` | 900 (15 min) | Max task runtime |

All limits are defined in `config/` and support env overrides. See [CONFIGURATION.md](CONFIGURATION.md).

---

## Task Memory

- **Location:** `.agent_memory/tasks/`
- **Content:** `task_id`, `instruction`, `plan`, `steps`, `patches`, `files_modified`, `errors`, `project_root`
- **API:** `save_task()`, `load_task()`, `list_tasks()` from `agent/memory/task_memory.py`

---

## Trace Logging

- **Location:** `.agent_memory/traces/`
- **API:** `start_trace()`, `log_event()`, `finish_trace()` from `agent/observability/trace_logger.py`
- **Events:**
  - `planner_decision` — plan with steps
  - `step_executed` — step_id, action, tool (chosen_tool), success
  - `patch_result` — patches_applied, files_modified (when EDIT succeeds)
  - `error` — step failures, max runtime, max replan, exceptions
  - `high_risk_edit` — change impact when risk is HIGH
  - `task_complete` — task_id, completed_steps, errors, patches_applied, files_modified

---

## Environment Variables

See [CONFIGURATION.md](CONFIGURATION.md) for the full list. Key variables:

| Variable | Purpose |
|----------|---------|
| `ENABLE_INSTRUCTION_ROUTER` | 1 or 0 (default) — route instruction before planner; CODE_SEARCH/CODE_EXPLAIN/INFRA skip planner |
| `ROUTER_TYPE` | baseline, fewshot, ensemble, or final — use router from registry when instruction router enabled |
| `TEST_REPAIR_ENABLED` | 1 (default) or 0 — run tests after patch; 0 = patch only |
| `COMPILE_BEFORE_TEST` | 1 (default) or 0 — run py_compile before tests |
| `SERENA_PROJECT_DIR` | Project root (fallback when `project_root` not passed) |

---

## Observability Tests

`tests/test_observability.py` verifies trace creation and content:

- Trace file created in `.agent_memory/traces/`
- Trace contains plan, tool calls, errors, patch results
- `task_complete` includes summary (completed_steps, errors, patches_applied, files_modified)

```bash
python -m pytest tests/test_observability.py -v
```

---

## E2E Tests

`tests/test_agent_e2e.py` exercises the full pipeline with mocked LLM responses:

| Scenario | Instruction | Flow |
|----------|-------------|------|
| Explain code | "Explain how StepExecutor works" | plan → search → retrieval → explain |
| Code edit | "Add logging to StepExecutor.execute_step" | plan → search → diff planner → patch → index update |
| Multi-file change | "Add logging to every executor class" | conflict resolver → sequential patch groups |

Assertions: no exceptions, patches applied, index updated, task memory saved. Uses `TEST_REPAIR_ENABLED=0` and `ENABLE_DIFF_PLANNER=1` for deterministic runs.

Default: tries real LLM; if unreachable, warns and falls back to mock. Use `--mock` to force mock mode.

```bash
python -m pytest tests/test_agent_e2e.py -v          # default: try LLM, fallback to mock
python -m pytest tests/test_agent_e2e.py -v --mock   # always use mock
```

---

## File Reference

- **Controller:** `agent/orchestrator/agent_controller.py` — `run_controller`, `_get_plan`, `_run_edit_flow`
- **Instruction router:** `agent/routing/instruction_router.py` — `route_instruction`
- **Task memory:** `agent/memory/task_memory.py`
- **Trace logger:** `agent/observability/trace_logger.py`
- **Conflict resolver:** `editing/conflict_resolver.py`
- **Test repair:** `editing/test_repair_loop.py`
- **Change detector:** `repo_graph/change_detector.py`
- **Repo map updater:** `repo_graph/repo_map_updater.py` — update_repo_map_for_file (after update_index_for_file)
