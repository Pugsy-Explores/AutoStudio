# Orchestrator Subsystem (`agent/orchestrator/`)

**Legacy helpers** for replanning and step validation. The **top-level controller** that used to live here is **gone**.

## Current state

- **`run_controller`** in `agent/orchestrator/__init__.py` **raises** `RuntimeError` — use **`agent_v2.runtime.bootstrap.create_runtime`** or **`tests.utils.runtime_adapter.run_controller`**.
- **`run_agent`** in the same `__init__.py` delegates to **`create_runtime().run(..., mode="act")`**.
- **`plan_resolver.py`**, **`goal_evaluator.py`**, etc. remain for **legacy REACT_MODE=0 / deterministic** code paths inside `agent/execution` if referenced; they are **not** the primary entry for the planner-centric `agent_v2` runtime.

## Exports (see `__init__.py`)

- `replan` — `replanner.py`
- `validate_step` — `validator.py`
- `run_agent` — thin wrapper to **`agent_v2`**

## Responsibilities

- **Replan helpers** — `replanner.py`, `replan_recovery.py` (may be used by older flows).
- **Validation** — `validator.py` for step shape checks.
- **No longer:** main `run_controller` / `run_hierarchical` implementation (see `tests/utils/runtime_adapter.py`).
