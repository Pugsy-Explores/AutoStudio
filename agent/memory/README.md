# Memory Subsystem (`agent/memory/`)

State and persistence primitives for agent runs.

## Responsibilities

- Define the in-memory **`AgentState`** used to carry context, step results, and execution artifacts across the run.
- Provide **step result** structures (`StepResult`) suitable for tracing and post-run analysis.
- Persist and load **task summaries** for later retrieval and “similar task” lookup.

## Public API

Exports from `agent/memory/__init__.py`:

- `AgentState` (`agent/memory/state.py`)
- `StepResult` (`agent/memory/step_result.py`)
- Task persistence: `save_task`, `load_task`, `list_tasks` (`agent/memory/task_memory.py`)

## Invariants

- State should remain a **single source of truth** for runtime context (avoid hidden globals).
- Persisted task records must be stable enough to support evaluation and debugging across versions.

