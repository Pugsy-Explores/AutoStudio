# Autonomous Subsystem (`agent/autonomous/`)

Goal-driven exploration loop (“Mode 2”). This package contains the autonomous runner entrypoint that can execute multi-step exploration under strict limits, while **reusing the same infrastructure** as the deterministic pipeline (dispatcher, retrieval pipeline, editing pipeline, trace logging).

## Responsibilities

- Provide the **autonomous run entrypoint** (`run_autonomous`) used when controller mode is set to `autonomous`.
- Keep autonomous behavior **additive**: it must not introduce a second execution engine; it must route all actions through the existing dispatcher/policy/safety layers.

## Public API

- `run_autonomous(...)` from `agent/autonomous/agent_loop.py`

## Invariants

- Must preserve: **retrieval before reasoning**, **dispatcher-only tool execution**, **full trace logging**, and **bounded loop limits** (max steps/edits/runtime).

