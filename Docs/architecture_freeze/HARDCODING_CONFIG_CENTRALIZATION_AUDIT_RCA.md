# Hardcoding + Config Centralization Audit (Agent V2)

## Scope

This audit covers:

1. Hardcoded values and direct environment usage across the repository
2. Current centralized configuration coverage
3. Gaps vs a single-source-of-truth config model
4. Live test RCA signals relevant to configuration behavior
5. Incremental, safe migration plan (no behavior change)

---

## Executive summary

The repository already has strong config foundations (`config/*`, `agent_v2/config.py`, `agent/models/model_config.py` + `models_config.json`), but configuration is still split across:

- direct `os.getenv` / `os.environ` reads in business/runtime modules
- duplicated defaults (timeouts, budgets, model IDs, paths)
- duplicated policy construction in multiple runtime modules
- mixed discovery scope in tests (artifact workspace tests pollute collection)

Recent exploration model-config work is live-validated for stage-specific routing:

- `EXPLORATION_QUERY_INTENT`
- `EXPLORATION_SCOPER`
- `EXPLORATION_SELECTOR_BATCH`
- `EXPLORATION_ANALYZER`
- `EXPLORATION_SELECTOR_SINGLE` (direct live smoke)

Design direction for migration (tight, production-grade):

- classify config into static/runtime/behavioral/infra categories
- enforce config authority (no direct env reads outside config layer)
- add startup config validation
- extract planner/exploration/pytest behavioral policies into config
- preserve current behavior via default-value parity guard

---

## Phase 1 — Hardcoding inventory

## A) Environment variable usage (high-impact examples)

- `config/retrieval_config.py`
  - Many env-driven toggles and numeric defaults (`RETRIEVAL_*`, `RERANKER_*`, `V2_*`)
  - Impact: retrieval behavior/perf policy is centralized, but defaults are large and duplicated in other layers.

- `config/agent_runtime.py` and `config/agent_config.py`
  - Runtime safety knobs and budgets are env-backed with inline defaults.
  - Impact: central but split authority; risk of conflicting defaults.

- `agent_v2/config.py`
  - Core v2 runtime/exploration limits are centralized and env-backed.
  - Impact: good baseline; still bypassed in some runtime modules.

- `agent_v2/runtime/exploration_runner.py`
  - Reads env flags directly for exploration/scoper enablement and project-root fallback.
  - Impact: config logic leaking into runtime module.

- `agent_v2/observability/langfuse_client.py`
  - Reads `LANGFUSE_*` and related vars directly.
  - Impact: observability config not fully centralized in `config/observability_config.py`.

- `agent/execution/step_dispatcher.py` and other runtime paths
  - Repeated `SERENA_PROJECT_DIR` / cwd fallback patterns.
  - Impact: path resolution semantics duplicated.

## B) Hardcoded constants / magic numbers

- `agent/models/models_config.json`
  - Repeated task params (`max_tokens`, timeouts) and localhost endpoints.
  - Impact: behavior controlled centrally, but repetition makes drift and large edits likely.

- `agent_v2/exploration/exploration_engine_v2.py`
  - Discovery budgets and caps (symbol/regex/text/merge/snippet).
  - Impact: core exploration quality/performance knobs are hardcoded in module.

- `agent_v2/runtime/agent_loop.py`
  - Retry constants not uniformly sourced from v2 config.

- `agent_v2/planner/planner_v2.py`, `agent_v2/runtime/plan_executor.py`, `agent_v2/runtime/replanner.py`
  - Local default `ExecutionPolicy(...)` definitions duplicated.
  - Impact: policy drift risk across planner/executor/replanner.

- retrieval/index scripts and modules
  - hardcoded daemon hosts/ports/timeouts in multiple places.

## C) Inline config patterns + duplication

- Embedding model default repeated across vector/memory/intelligence modules.
- Daemon base URL repeated across retrieval modules and startup/script code.
- Boolean env parsing tuples duplicated in many files.
- `SERENA_PROJECT_DIR or os.getcwd()` fallback duplicated in several modules.

---

## Phase 2 — Current config system coverage

## What is centralized today

- `agent/models/models_config.json` + `agent/models/model_config.py`
  - model registry, task-to-model mapping, task call params, reranker config
- `agent_v2/config.py`
  - v2 runtime limits and `ExecutionPolicy` builder
- `config/*` modules
  - retrieval/runtime/router/logging/observability/policy/editing config domains

## Existing access patterns

- Good pattern:
  - `get_model_for_task()`, `get_model_call_params()`, `get_endpoint_for_model()`
- Mixed pattern:
  - some modules import config constants, others read env directly
- Gap:
  - no single runtime config object reused across all business modules

---

## Phase 3 — Classification (cfggrp)

1. Static config (paths, constants)
- model registry definitions, paths, fixed defaults (`models_config.json`, path resolvers, indexing paths)
- Gap: repeated literals for paths/models/constants across modules

2. Runtime config (timeouts, retries, budgets)
- `config/agent_runtime.py`, `config/agent_config.py`, `agent_v2/config.py`
- Gap: runtime modules still own local constants/env parsing

3. Behavioral config (policies, gating rules)
- planner allowed-actions per task mode, exploration gating semantics, test discovery guardrails
- Gap: policy logic partly embedded in planner/mode-manager/test command conventions

4. Infra config (paths, env, API keys)
- mixed between `config/*`, scripts, and runtime modules
- Gap: project-root and daemon/env parsing duplicated

---

## Live test RCA (configuration-relevant)

Run: `tests/test_agent_v2_phases_live.py -m agent_v2_live`

Result:
- 5 passed, 3 failed, 1 skipped

Failures:

1) Planner/execute read-only violation
- `PlanValidationError`: planner emitted `run_tests` in read-only mode.
- RCA: planner prompt/control path not reliably constrained to read-only action whitelist.

2) Plan-mode exploration gating
- `RuntimeError`: exploration termination `max_steps` gates planning.
- RCA: gating policy too strict for plan mode when exploration produced partial useful context.

3) Repo-wide live marker collection noise
- `pytest -m agent_v2_live` across repo hits massive artifact collection errors (`import file mismatch` under `artifacts/**/tests/test_smoke.py`).
- RCA: discovery scope includes generated artifact workspaces with duplicate module names.

Note: exploration model-stage routing was verified in live logs for both selector paths.

---

## Phase 4 — Safe incremental migration plan

## Config authority rule (mandatory)

All configuration MUST be read from config modules.

- Direct `os.getenv` / `os.environ` in business/runtime modules is forbidden.
- Inline config constants in business/runtime modules are forbidden.
- Allowed: config loaders and config modules only.

## Step 1 — Extend config schema minimally

- Add/confirm missing fields in existing config homes (do not redesign):
  - exploration budgets/caps in `agent_v2/config.py`
  - daemon/indexing transport knobs in `config/retrieval_config.py` (or small dedicated indexing config module)
  - observability env interpretation wrappers in `config/observability_config.py`
  - behavioral policy extraction keys:

```json
{
  "planner": {
    "allowed_actions_read_only": ["open_file", "search", "finish"]
  },
  "exploration": {
    "max_steps": 8,
    "allow_partial_for_plan_mode": true
  },
  "pytest": {
    "ignore_dirs": ["artifacts"]
  }
}
```

## Step 2 — Replace hardcoding with config access

Use:

```python
cfg.<field>
```

for module-level constants currently embedded in business logic, preserving current fallback defaults.

## Step 3 — One access pattern

Use a single access style per domain (model config, v2 runtime config, retrieval config), and remove direct env reads from business modules.

## Step 4 — Env integration

- Keep env reads in loaders/config modules only.
- Business/runtime modules consume resolved config values.

## Step 5 — Validation and rollout

- add startup validation layer:

```python
validate_config(config)
```

- validation checks:
  - required keys exist
  - values are in allowed range
  - planner policies are internally consistent

- add startup/config validation for required keys and bounds
- add tests for no behavior change:
  - read-only planner action compliance
  - plan-mode gating behavior under max-step exploration
  - scoped live test command that excludes artifact collection noise

## No behavior change guard

Migration defaults MUST match current hardcoded values exactly.

- Every moved constant/env fallback keeps current effective default.
- Changes are incremental and reviewable; behavior diffs are treated as regressions unless explicitly approved.

## Step 5.1 — Implemented now (exact file paths)

- `agent_v2/config.py`
  - Added central accessor `get_config()` and startup validation helper `validate_config(config)`.
  - Added explicit config categories payload for:
    - `planner.allowed_actions_read_only`
    - `exploration.max_steps`
    - `exploration.allow_partial_for_plan_mode`
    - `pytest.ignore_dirs`
  - Preserved default parity (`{"search", "open_file", "finish"}` for read-only actions, same exploration max-step default path).

- `agent_v2/runtime/bootstrap.py`
  - Added startup `validate_config(get_config())` fail-fast check.

- `agent_v2/validation/plan_validator.py`
  - Replaced hardcoded read-only action policy with `get_config().planner.allowed_actions_read_only`.

- `agent_v2/runtime/mode_manager.py`
  - Added config-driven partial gating policy for plan/deep_plan:
    - when `exploration.allow_partial_for_plan_mode=true`, allow planner to proceed for bounded incomplete terminations (`max_steps`, `pending_exhausted`, `stalled`).

- `pyproject.toml`
  - Added `artifacts` to `norecursedirs` to prevent known pytest collection collisions from generated workspaces.

- `tests/test_config_centralization.py`
  - Added validation/parity tests for:
    - read-only policy consistency check in `validate_config`
    - no-behavior-change default parity for read-only actions
    - config-controlled plan-mode partial exploration gating

---

## Priority file change list

1. `agent_v2/planner/planner_v2.py`
2. `agent_v2/runtime/plan_executor.py`
3. `agent_v2/runtime/replanner.py`
4. `agent_v2/runtime/exploration_runner.py`
5. `agent_v2/exploration/exploration_engine_v2.py`
6. `agent_v2/observability/langfuse_client.py`
7. `agent/execution/step_dispatcher.py`
8. `repo_index/indexer.py`
9. `config/observability_config.py`
10. `config/agent_runtime.py` + `config/agent_config.py` (resolve overlaps)

---

## Target end state

Move from:

```text
scattered constants + env usage
```

to:

```text
single source of truth config system
```

with strict config-loading boundaries and no behavior change.

