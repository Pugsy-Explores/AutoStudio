# AutoStudio Documentation

## Authoritative for runtime behavior

| Resource | Use |
|----------|-----|
| **[`README.md`](../README.md)** (repo root) | Current **`agent_v2`** architecture, modes, entrypoints |
| **[`agent_v2/README.md`](../agent_v2/README.md)** | Module breakdown |
| **`Docs/architecture_freeze/`** | **Design intent** and contracts; **`SCHEMAS.md`** for structured types. If a narrative doc disagrees with code, **code wins** unless you are doing design work |

## Architecture freeze (reference)

- [`architecture_freeze/ARCHITECTURE_FREEZE.md`](architecture_freeze/ARCHITECTURE_FREEZE.md) — planner-centric system
- [`architecture_freeze/SCHEMAS.md`](architecture_freeze/SCHEMAS.md) — `PlanDocument`, `ExplorationResult`, execution schemas

## Deep dives (may predate `agent_v2` defaults)

| Document | Notes |
|----------|--------|
| [`RETRIEVAL_ARCHITECTURE.md`](RETRIEVAL_ARCHITECTURE.md) | Hybrid retrieval — still used by `agent/retrieval` |
| [`REACT_ARCHITECTURE.md`](REACT_ARCHITECTURE.md) | Describes ReAct JSON actions — still relevant for **exploration** and **dispatch**, not as the sole control plane |
| [`CONFIGURATION.md`](CONFIGURATION.md) | Env vars |
| [`AGENT_CONTROLLER.md`](AGENT_CONTROLLER.md) | **Stale** in places — `run_controller` path removed from `agent/orchestrator` |
| [`AGENT_LOOP_WORKFLOW.md`](AGENT_LOOP_WORKFLOW.md) | Legacy pipeline narrative |

Prefer root **`README.md`** for “what runs today.”
