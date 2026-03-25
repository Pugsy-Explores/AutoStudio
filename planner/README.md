# Planner Module (`planner/`)

**Standalone legacy planner** used for **evaluation and offline JSON plan generation** (`planner_eval`). It produces a simple `{ "steps": [...] }` plan with coarse actions (`EDIT`, `SEARCH`, `EXPLAIN`, `INFRA`).

## Production path

The **live** planner is **`agent_v2/planner/planner_v2.py` (`PlannerV2`)**, which emits a strict **`PlanDocument`** consumed by **`ModeManager`** and **`PlanExecutor`**. Production flows do **not** import `planner.plan` from this package.

## This package

1. **Input:** User instruction string.
2. **LLM:** `PLANNER_SYSTEM_PROMPT` (see `Docs/PROMPT_ARCHITECTURE.md`).
3. **Output:** JSON steps with `id`, `action`, `description`, `reason`.

## Evaluation

- `python -m router_eval` is separate; for planner metrics use **`planner_eval`** scripts in this directory (see `planner_eval.py` and docs in this folder).
- Metrics: step count accuracy, action sequence accuracy, latency.

## When to use

- **Benchmarking** legacy plan format.
- **Comparing** router + planner datasets without spinning full `agent_v2`.

For **architecture** of the current system, see [`README.md`](../README.md) and [`docs/architecture_freeze/PHASE_4_PLANNER_V2.md`](../Docs/architecture_freeze/PHASE_4_PLANNER_V2.md).
