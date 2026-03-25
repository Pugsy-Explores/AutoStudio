# CLI Subsystem (`agent/cli/`)

Command-line UX for AutoStudio. Commands are **thin wrappers** around **`agent_v2.runtime.bootstrap.create_runtime()`** and **`format_output`** (`agent_v2/cli_adapter.py`).

## Responsibilities

- Define the **`autostudio`** entrypoint (`pyproject.toml` → `agent.cli.entrypoint:main`).
- **Session / single-shot** — `session.py`, `run_agent.py` call `AgentRuntime.run(instruction, mode=...)` with optional `--mode=`.

## Key files

| File | Role |
|------|------|
| `entrypoint.py` | Main CLI dispatch |
| `run_agent.py` | Run agent with formatted JSON output |
| `session.py` | Interactive REPL; one runtime per turn |

## Modes

Parsed by `agent_v2.cli_adapter.parse_mode`: **`act`**, **`plan`**, **`deep_plan`**, **`plan_execute`**.

## Invariants

- No agent business logic here beyond argument parsing and I/O.
- Do not import removed **`agent.orchestrator.run_controller`**.
