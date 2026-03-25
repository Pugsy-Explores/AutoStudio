# Editing Module (`editing/`)

Safe, validated **patch pipeline**: diff planning → AST patch → validation → execution with rollback.

## Responsibilities

- **`plan_diff`** — propose bounded edits (`diff_planner.py`).
- **`validate_patch`** — reject unsafe or invalid plans (`patch_validator.py`).
- **`execute_patch`** — apply with path/budget guards (`patch_executor.py`).
- **`ast_patcher.py`** — AST-aware apply when applicable.
- **`test_repair_loop.py`** — `run_with_repair` for edit→test repair flows.

## Integration with `agent_v2`

`PlanExecutor` dispatches **EDIT** steps through **`agent/execution/step_dispatcher`**, which invokes editing and runtime helpers. The **plan** is fixed by `PlannerV2`; the editing module does not choose the next high-level step.

## Safety

- Paths must stay under `project_root`; forbidden patterns for secrets/env.
- Budgets from `config/editing_config.py` and `config/agent_runtime.py`.

## Extension

Add validators or language hooks in **`patch_validator.py`** / **`ast_patcher.py`** without changing public call sites in dispatch.
