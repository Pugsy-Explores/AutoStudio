# Integration Tests (`tests/integration/`)

Integration-level tests for AutoStudio.

## Responsibilities

- Validate multi-module wiring (controller → planner → retrieval → editing/runtime) under realistic configurations.
- Exercise external-service boundaries when enabled (e.g., model endpoints, retrieval daemon).

## Notes

These tests may be slower or require environment setup. See `pyproject.toml` pytest markers (`integration`, `slow`).

