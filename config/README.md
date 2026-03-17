# Config Module (`config/`)

Centralized, importable configuration for AutoStudio. This module defines **runtime limits, feature flags, and service endpoints** used across planning, retrieval, editing, and agent orchestration.

## Responsibilities

- **Single source of truth** for tunables (attempt counts, budgets, timeouts).
- **Safety limits** (e.g. patch/file budgets, context limits).
- **Service wiring** (retrieval daemon config, reranker toggles, routing/planning model settings).
- **Startup validation/bootstrapping hooks** used by the agent controller.

## Key files

- **Package export**: `config/__init__.py` re-exports the major config modules.
- **Startup bootstrap**: `config/startup.py`
  - `ensure_services_ready()`:
    - best-effort retrieval daemon ensure (when enabled)
    - model bootstrap
    - reranker warm-up (or log fallback)
    - endpoint reachability checks (hard fail when required LLM endpoints are unreachable)
  - `SKIP_STARTUP_CHECKS=1` bypasses bootstrap (tests/mocks only).
  - `RETRIEVAL_DAEMON_AUTO_START=0` disables daemon auto-start.
- **Validation**: `config/config_validator.py`
  - `validate_config()` asserts critical bounds at startup.

## Major config modules

- **`agent_config.py`**: attempt-level limits and task runtime limits.
- **`agent_runtime.py`**: edit→test→fix loop controls (attempt counts, rollback behavior, sandbox toggle).
- **`editing_config.py`**: patch/file-size budgets and edit guardrails.
- **`retrieval_config.py`**: retrieval budgets, reranker/daemon toggles, search caps.
- **`repo_graph_config.py`**: symbol graph + repo map paths and parameters.
- **`router_config.py`**: router behavior toggles (category routing and model settings).
- **`observability_config.py`** / **`logging_config.py`**: trace/metrics and logging defaults.
- **`context_limits.py`** / **`tool_budgets.py`** / **`tool_graph_config.py`**: cross-cutting budgets.

## Usage pattern

- Prefer importing **specific modules** rather than the umbrella `config` package to keep dependencies explicit:
  - `from config.retrieval_config import MAX_CONTEXT_SNIPPETS`
  - `from config.agent_config import MAX_AGENT_ATTEMPTS`

## Invariants / guardrails

- **Budgets are contracts**: patch sizes, file counts, context limits, and retry caps must remain enforced by downstream systems.
- **Bootstrap is defensive**: startup checks should fail loudly only when the system would otherwise produce misleading behavior (e.g. unreachable required LLM endpoints).

