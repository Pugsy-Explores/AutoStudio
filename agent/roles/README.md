# Roles / Multi-Agent Orchestration (`agent/roles/`)

Hierarchical multi-agent coordination (“Phase 9”): role-specialized agents orchestrated by a supervisor, operating over the same shared infrastructure as the single-agent deterministic pipeline.

## Responsibilities

- Define the **workspace** representation shared across role agents.
- Provide the **supervisor** entrypoint that runs multi-agent solve (`run_multi_agent`).

## Public API

Exports from `agent/roles/__init__.py`:

- `AgentWorkspace` (`agent/roles/workspace.py`)
- `run_multi_agent(...)` (`agent/roles/supervisor_agent.py`)

## Invariants

- Role agents must reuse: dispatcher, retrieval pipeline, editing pipeline, trace logger.
- Avoid introducing a parallel execution engine; coordination is additive on top of shared infrastructure.

