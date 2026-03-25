# Agent Runtime — Edit→Test→Fix (`agent/runtime/`)

Safety loop around **EDIT** and test execution: snapshots, syntax validation, rollback, optional sandbox. Consumed from **`agent/execution`** dispatch paths when an edit or test step runs.

## Relation to `agent_v2`

- **`agent_v2`** `PlanExecutor` drives high-level steps; **`step_dispatcher`** eventually invokes **`agent/runtime/execution_loop.py`** (and related) for edit/test repair behavior where wired.
- This is **not** the same as **`agent_v2.runtime.agent_loop.AgentLoop`** (composable ReAct loop class).

## Modules

| Module | Role |
|--------|------|
| `execution_loop.py` | `run_edit_test_fix_loop` and related; critic/retry paths when not in minimal ReAct edit |
| `syntax_validator.py` | Project-level syntax check after patch |
| `retry_guard.py` | Retry policy by failure type |

## Config

`config/agent_runtime.py` — `MAX_EDIT_ATTEMPTS`, `MAX_PATCH_LINES`, `ENABLE_SANDBOX`, **`REACT_MODE`**, etc.

## Tests

`tests/test_execution_loop.py`, trajectory tests under `tests/test_agent_trajectory.py`.
