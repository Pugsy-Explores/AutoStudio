# Developer Workflow Integration (`agent/workflow/`)

End-to-end workflow helpers that wrap the agent into a developer-style loop: issue parsing, solution execution, PR generation, CI run, and patch review.

## Responsibilities

- Parse issues into actionable tasks.
- Run solve loops (single or multi-agent) and persist workflow artifacts.
- Generate PR-ready summaries from patches + tests.
- Run CI checks against a target project root.
- Produce review feedback on patches.

## Key entrypoints

- Used via CLI commands in `agent/cli/entrypoint.py` (`autostudio issue|fix|pr|review|ci`).
- Orchestrated by `agent/workflow/workflow_controller.py` (see package docstring in `agent/workflow/__init__.py`).

## Invariants

- Workflow must reuse shared infrastructure (dispatcher, retrieval, editing, trace logging).
- Keep side effects explicit and traceable (saved workflow artifacts, CI outputs, review notes).

