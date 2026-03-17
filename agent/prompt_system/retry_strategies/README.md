# Retry Strategies (`agent/prompt_system/retry_strategies/`)

Standardized retry approaches for when a model response is invalid, low-confidence, or insufficient. Strategies are designed to be **bounded**, **deterministic**, and **observable**.

## Responsibilities

- Define a common retry interface (`RetryStrategy`) and context object (`RetryContext`).
- Provide concrete strategies, including:
  - stricter prompt formatting
  - more context within budgets
  - different model selection
  - critic-feedback-informed retries

## Public API

See exports in `agent/prompt_system/retry_strategies/__init__.py`.

## Invariants

- Retries must obey global attempt caps and must not accumulate unbounded “hint history”.
- Every retry decision should be traceable (why this strategy, what changed).

