# Prompt Versioning (`agent/prompt_system/versioning/`)

Utilities for storing, retrieving, and comparing prompt versions, plus basic experiment support (A/B tests).

## Responsibilities

- Maintain a stable interface to **fetch prompts by version**.
- List available versions for auditability and reproducibility.
- Support **A/B testing** to compare prompt variants under controlled conditions.

## Public API

Exports from `agent/prompt_system/versioning/__init__.py`:

- `get_prompt`, `list_versions`
- `run_ab_test`, `ABTestResult`

## Invariants

- Prompt changes should remain reproducible (versioned) to support evaluation/regression workflows.

