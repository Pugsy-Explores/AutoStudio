# Agent Runtime — Edit→Test→Fix Loop

Runtime safety layer for the EDIT path: single repair mechanism with snapshot rollback, syntax validation, and deterministic stop conditions.

## Purpose

- **No git dependency:** Rollback is file-snapshot based; works in CI, zip archives, and non-git repos.
- **Syntax before tests:** After patch apply, project syntax is validated (e.g. `py_compile`, `go build`, `cargo check`); on failure, rollback and return without running tests.
- **Controlled retries:** Base instruction is fixed at loop start; retry hints are applied as `base_instruction + "\nRetry hint: " + hint` (no accumulation). Strategy explorer runs only when retries are exhausted.
- **Observable:** Execution loop metrics (attempts, failures, syntax_validation_failures, rollback_count, strategy_explorer_usage) are recorded; see Docs/OBSERVABILITY.md.

## Modules

| Module | Role |
|--------|------|
| `execution_loop.py` | `run_edit_test_fix_loop`: snapshot → apply patch → validate syntax → run tests; on failure: rollback, retry guard, critic + retry_planner; optional sandbox (ENABLE_SANDBOX). |
| `syntax_validator.py` | `validate_project(project_root, modified_files?)`: manifest-based (pyproject.toml / package.json / go.mod / Cargo.toml) syntax check. |
| `retry_guard.py` | `should_retry_strategy(failure_type, attempt, max_attempts)`: e.g. syntax_error/timeout retry once; unknown stop. |

## Config

All behaviour is driven by `config/agent_runtime.py`: MAX_EDIT_ATTEMPTS, MAX_PATCH_FILES, MAX_PATCH_LINES, MAX_SAME_ERROR_RETRIES, MAX_STRATEGIES, TEST_TIMEOUT, ENABLE_SANDBOX. See Docs/CONFIGURATION.md.

## Tests

`tests/test_execution_loop.py`: successful patch, syntax error (skip tests + rollback), retry success, repeated-failure stop, rollback restore verification.
