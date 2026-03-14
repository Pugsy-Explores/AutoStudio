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
  → build_repo_map() — high-level architectural map (repo_map.json)
  → search_similar_tasks() — vector index of past tasks (optional)
  → planner.plan(instruction)
  → AgentState with plan, context
  → while not state.is_finished():
        step = state.next_step()
        if action == EDIT:
            _run_edit_flow(step, state)
        else:
            dispatch(step, state)
        validate_step; on failure → replan
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
          → execute_patch (AST patching, rollback on failure)
          → run tests (pytest)
          → on failure: plan repair, retry (max 3 attempts)
          → flaky detection: re-run failing test with pytest --count=2
          → compile step (py_compile) before tests when COMPILE_BEFORE_TEST=1
  → update_index_for_file() for each modified file
```

---

## Safety Limits

| Limit | Value | Purpose |
|-------|-------|---------|
| `MAX_FILES_EDITED` | 5 | Max files per edit step |
| `MAX_PATCH_SIZE` | 200 lines | Max lines per patch |
| `MAX_TASK_RUNTIME_SECONDS` | 900 (15 min) | Max task runtime |

---

## Task Memory

- **Location:** `.agent_memory/tasks/`
- **Content:** `task_id`, `instruction`, `plan`, `steps`, `patches`, `files_modified`, `errors`, `project_root`
- **API:** `save_task()`, `load_task()`, `list_tasks()` from `agent/memory/task_memory.py`

---

## Trace Logging

- **Location:** `.agent_memory/traces/`
- **API:** `start_trace()`, `log_event()`, `finish_trace()` from `agent/observability/trace_logger.py`
- **Events:** `planner_decision`, `step_executed`, `high_risk_edit`, `task_complete`

---

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `TEST_REPAIR_ENABLED` | 1 (default) or 0 — run tests after patch; 0 = patch only |
| `COMPILE_BEFORE_TEST` | 1 (default) or 0 — run py_compile before tests |
| `SERENA_PROJECT_DIR` | Project root (fallback when `project_root` not passed) |

---

## File Reference

- **Controller:** `agent/orchestrator/agent_controller.py` — `run_controller`, `_run_edit_flow`
- **Task memory:** `agent/memory/task_memory.py`
- **Trace logger:** `agent/observability/trace_logger.py`
- **Conflict resolver:** `editing/conflict_resolver.py`
- **Test repair:** `editing/test_repair_loop.py`
- **Change detector:** `repo_graph/change_detector.py`
