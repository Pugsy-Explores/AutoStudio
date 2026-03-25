# Tests (`tests/`)

Unit and integration tests for routing, retrieval, editing, **`agent_v2`** runtime phases, and observability.

## Primary runtime adapter

**`tests/utils/runtime_adapter.py`** defines:

- `run_agent(instruction, mode)` → `create_runtime().run(...)` and returns `state`
- `run_controller` / `run_hierarchical` / `run_deterministic` — set `SERENA_PROJECT_DIR`, call **`agent_v2`** runtime, shape legacy dict output

This is the **compatibility layer** for tests and scripts that historically imported a monolithic `run_controller`. It is **not** `agent.orchestrator`.

## Notable suites

| Test | Focus |
|------|--------|
| `test_mode_manager.py` | `ModeManager` never calls `AgentLoop.run` for act/plan/deep_plan |
| `test_agent_v2_phases_live.py` | Live LLM phases for v2 |
| `test_agent_v2_loop_retry.py` | `AgentLoop` retry/stop behavior |
| `integration/` | E2E wiring — see `integration/README.md` |
| `evals/` | Benchmark harnesses |

## Fixtures

`tests/fixtures/`, `tests/agent_eval/fixtures/` — mini repos and pinned snapshots for retrieval and evals.
