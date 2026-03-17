# Strategy Exploration (`agent/strategy/`)

Fallback strategy exploration used when normal retries are exhausted or when a failure mode suggests alternative approaches.

## Responsibilities

- Generate bounded alternative strategies for solving a task (`explore_strategies`).
- Keep strategy outputs compatible with the deterministic planning/execution pipeline (i.e., strategies translate into structured steps, not ad-hoc tool calls).

## Public API

- `explore_strategies(...)` (exported in `agent/strategy/__init__.py`)

