# Principal Engineer Plan: Externalize Policy Configuration to config/

**Status:** Plan  
**Owner:** Principal Engineer  
**Related:** `agent/execution/policy_engine.py`, `agent/execution/step_dispatcher.py`, `agent/prompt_system/guardrails/safety_policy.py`, `config/policy_config.yaml`

---

## 1. Objective

- **Move all execution and safety policy configuration** (retry tables, failure dispatch, default safety policy) **out of Python code** into data files under `config/`.
- Introduce a **single policy config loader** so the execution engine and guardrails **always read policy from config**, not from hard-coded dicts.
- Preserve existing behavior by using a **default `policy_config.yaml`** that matches the current in-code constants.

---

## 2. Design

### 2.1 Config Surface

- New data file: `config/policy_config.yaml`
  - `policy_engine` section:
    - Per-action policies (`SEARCH`, `EDIT`, `INFRA`, `EXPLAIN`) with `max_attempts`, `mutation`, and `retry_on`.
    - `failure_recovery_dispatch`: failure type → recovery action mapping.
    - `search_memory_snippet_max`: snippet length cap for search memory.
  - `safety_policy` section:
    - `allowed_tools`
    - `forbidden_operations`
    - `forbidden_patterns`

### 2.2 Loader Module

- New module: `config/policy_config.py`
  - `load_execution_policy_table(path: str | Path | None = None) -> ExecutionPolicyTable`
    - Reads YAML/JSON (path → `POLICY_CONFIG_PATH` env → default `config/policy_config.yaml`).
    - Provides:
      - `policies: dict[str, dict[str, Any]]`
      - `failure_recovery_dispatch: dict[str, str]`
      - `search_memory_snippet_max: int`
  - `load_safety_policy_defaults(path: str | Path | None = None) -> SafetyPolicyDefaults`
    - Provides:
      - `allowed_tools: tuple[str, ...]`
      - `forbidden_operations: tuple[str, ...]`
      - `forbidden_patterns: tuple[str, ...]`

### 2.3 Call Sites

- `agent/execution/policy_engine.py`:
  - Replace inline `POLICIES` dict with `POLICIES = load_execution_policy_table().policies`.
  - Replace `FAILURE_RECOVERY_DISPATCH` with table-backed mapping.
  - Replace `_SEARCH_MEMORY_SNIPPET_MAX` constant with value from config.
- `agent/prompt_system/guardrails/safety_policy.py`:
  - Replace hard-coded defaults with `load_safety_policy_defaults()` and use them in `SafetyPolicy` field defaults.

---

## 3. Implementation Steps

1. Add `config/policy_config.yaml` mirroring existing in-code policy values.
2. Add `config/policy_config.py` with YAML/JSON loader and typed views.
3. Refactor `policy_engine` to read `POLICIES`, `FAILURE_RECOVERY_DISPATCH`, and `_SEARCH_MEMORY_SNIPPET_MAX` from the loader.
4. Refactor `SafetyPolicy` defaults to use `load_safety_policy_defaults()`.
5. Update / extend `tests/test_policy_engine.py` to assert behavior using config-backed policies (no behavior change).
6. Add a short note to `config/README.md` documenting `policy_config.yaml`.

---

## 4. Testing

- **Unit:**
  - Run `python -m pytest AutoStudio/tests/test_policy_engine.py -v` and ensure all tests pass.
  - Add lightweight tests for `config/policy_config.py` if needed (parsing + default values).

---

## 5. Summary

This change externalizes policy configuration into `config/policy_config.yaml`, keeps the execution engine and guardrails aligned with architecture rules (single source of truth, observable, configurable), and preserves the existing behavior by default.

