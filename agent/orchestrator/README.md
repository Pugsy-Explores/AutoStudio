# Orchestrator Subsystem (`agent/orchestrator/`)

Top-level agent coordination for the deterministic pipeline: controller, main agent loop, replanning, and step validation. This is where “instruction → plan → execute” is assembled and wrapped with attempt-level retries.

## Responsibilities

- **Controller**: `run_controller(...)` is the main programmatic entrypoint; it sets up traces, optional bootstraps, memory hooks, and returns a task summary.
- **Agent loop**: execute planned steps deterministically.
- **Replanning**: adjust plan on failures within bounded limits.
- **Step validation**: ensure the planner output matches the allowed action schema before execution.

## Public API

Exports from `agent/orchestrator/__init__.py`:

- `run_controller` (`agent/orchestrator/agent_controller.py`)
- `run_agent` (`agent/orchestrator/agent_loop.py`)
- `replan` (`agent/orchestrator/replanner.py`)
- `validate_step` (`agent/orchestrator/validator.py`)

## Invariants

- Do not bypass the execution layer: all tools/actions must go through dispatcher + policy engine.
- Keep stop conditions deterministic (retry caps, runtime limits).
- Preserve “retrieval before reasoning” by requiring retrieval context before code reasoning.

