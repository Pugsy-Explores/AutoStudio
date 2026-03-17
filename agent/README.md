# Agent Module (`agent/`)

AutoStudio’s primary runtime subsystem: **turn a user instruction into a safe, observable, deterministic sequence of steps** (with retrieval-before-reasoning) and, when enabled, wrap that deterministic runner with bounded retry/attempt loops.

This module **does not** own the “execution engine” architecture; it implements the agent-facing orchestration, policies, retrieval integration, and workflow UX on top of the existing infrastructure.

## Responsibilities

- **Orchestration**: instruction → attempt loop → deterministic runner → results.
- **Tooling + safety integration**: ensure every action is traceable and policy-checked.
- **Retrieval integration**: enforce “retrieval before reasoning” by driving the retrieval subsystem before LLM reasoning on code.
- **Observability**: structured trace events, metrics, and artifacts (e.g., `.agent_memory/traces`).
- **Workflow UX**: CLI entrypoints, interactive session, multi-agent workflow (issue → task → PR/CI/review).

## Key entrypoints

- **CLI**: `agent/cli/entrypoint.py` (`autostudio` console script via `pyproject.toml`)
  - `autostudio edit <instruction>`: single-shot controller run
  - `autostudio explain <symbol>`: explanation path
  - `autostudio trace|debug`: trace replay tools
  - `autostudio issue|fix|pr|review|ci`: higher-level workflow commands
- **Controller**: `agent/orchestrator/agent_controller.py`
  - `run_controller(...)`: main programmatic entry
  - `run_attempt_loop(...)`: Phase 5 attempt-level loop around deterministic runner

## Execution flow (high-level)

1. **Bootstrap**: controller starts trace + best-effort startup checks (e.g. retrieval daemon ensure).
2. **Repo map**: best-effort `repo_graph.build_repo_map(...)` for high-level context.
3. **Task memory**: optional similar-task retrieval from task index.
4. **Attempt loop** (bounded by `config.agent_config.MAX_AGENT_ATTEMPTS`):
   - Run deterministic runner (plan → retrieval → action).
   - Evaluate goal completion; if failed, produce critic feedback and build retry context.
5. **Persist + observe**: save task summary, finish trace, record UX metrics.

See also: `Docs/AGENT_LOOP_WORKFLOW.md`, `Docs/PHASE_5_ATTEMPT_LOOP.md`, `Docs/OBSERVABILITY.md`.

## Major subpackages (orientation)

- **`agent/cli/`**: CLI UX and session wiring — see [`agent/cli/README.md`](cli/README.md)
- **`agent/orchestrator/`**: controller + deterministic loop + replanning — see [`agent/orchestrator/README.md`](orchestrator/README.md)
- **`agent/execution/`**: dispatcher, policy engine, tool graph, step executor — see [`agent/execution/README.md`](execution/README.md)
- **`agent/routing/`**: instruction routing before planner — see [`agent/routing/README.md`](routing/README.md)
- **`agent/retrieval/`**: hybrid retrieval pipeline — see [`agent/retrieval/README.md`](retrieval/README.md)
- **`agent/retrieval/localization/`**: graph-guided localization helpers — see [`agent/retrieval/localization/README.md`](retrieval/localization/README.md)
- **`agent/runtime/`**: edit→test→fix safety loop — see [`agent/runtime/README.md`](runtime/README.md)
- **`agent/tools/`**: tool adapters invoked via dispatcher — see [`agent/tools/README.md`](tools/README.md)
- **`agent/models/`**: model router + client surface — see [`agent/models/README.md`](models/README.md)
- **`agent/memory/`**: `AgentState`, step results, task persistence — see [`agent/memory/README.md`](memory/README.md)
- **`agent/observability/`**: trace logging + metrics helpers — see [`agent/observability/README.md`](observability/README.md)
- **`agent/prompt_system/`**: prompts, context engineering, guardrails, retries — see [`agent/prompt_system/README.md`](prompt_system/README.md)
- **`agent/prompt_eval/`**: prompt failure analysis tooling — see [`agent/prompt_eval/README.md`](prompt_eval/README.md)
- **`agent/intelligence/`**: solution memory + experience retrieval — see [`agent/intelligence/README.md`](intelligence/README.md)
- **`agent/repo_intelligence/`**: repo-scale architecture/impact helpers — see [`agent/repo_intelligence/README.md`](repo_intelligence/README.md)
- **`agent/roles/`**: multi-agent orchestration — see [`agent/roles/README.md`](roles/README.md)
- **`agent/strategy/`**: fallback strategy exploration — see [`agent/strategy/README.md`](strategy/README.md)
- **`agent/autonomous/`**: autonomous loop entrypoint — see [`agent/autonomous/README.md`](autonomous/README.md)
- **`agent/meta/`**: critic + retry planning (attempt loop support) — see [`agent/meta/README.md`](meta/README.md)

## Configuration

Configuration is centralized under `config/` and imported here as needed (e.g. attempt limits, runtime safety limits, retrieval toggles).

Start-of-run checks live in `config/startup.py` and can be bypassed with `SKIP_STARTUP_CHECKS=1` (tests/mocks only).

## Extension points (safe ways to add capability)

- **New tool**: add under `agent/tools/` and ensure it is executed only via dispatcher/policy engine, with full trace logging.
- **New retrieval stage**: extend `agent/retrieval/` without reordering the existing pipeline (order is contractually immutable).
- **New retry strategy**: add under `agent/meta/` / `agent/prompt_system/retry_strategies/` and keep deterministic stop conditions.

## Invariants / guardrails

- **Retrieval precedes reasoning**: any code reasoning must be backed by repository context from retrieval.
- **No direct tool selection**: agent reasoning produces structured steps; dispatcher performs tool execution.
- **Observable decisions**: every step must be traceable via the trace logger.

