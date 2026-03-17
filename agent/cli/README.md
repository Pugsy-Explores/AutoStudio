# CLI Subsystem (`agent/cli/`)

Command-line UX for AutoStudio. This package wires user-facing CLI commands to the orchestrator/controller and workflow helpers, and provides interactive session support.

## Responsibilities

- Define the **`autostudio`** CLI entrypoint (via `pyproject.toml`).
- Provide command handlers for single-shot runs (`edit`, `explain`), trace tooling (`trace`, `debug`), and workflow commands (`issue`, `fix`, `pr`, `review`, `ci`).
- Keep CLI logic thin: it should not implement agent logic; it should call the orchestrator/workflow layers.

## Key entrypoints

- `agent/cli/entrypoint.py`: console script target (`autostudio = agent.cli.entrypoint:main`)
- `agent/cli/__init__.py`: `run_agent_main()` convenience wrapper

