# Context Engineering (`agent/prompt_system/context/`)

Utilities for fitting repository/task context into model budgets with minimal information loss.

## Responsibilities

- **Token counting**: estimate prompt size (`count_tokens`, `count_prompt_tokens`).
- **Budgeting**: allocate budgets across prompt sections (`ContextBudget`, `PromptBudgetManager`).
- **Ranking & limiting**: select highest-value context (`rank_context`, `rank_and_limit`).
- **Pruning**: remove low-signal content and apply sliding windows (`prune`, `prune_sections`, `apply_sliding_window`).
- **Compression & summarization**: compress large blocks while preserving intent (`compress`, `summarize_large_block`).

## Public API

See exports in `agent/prompt_system/context/__init__.py`.

## Invariants

- Budgets are contractual: avoid “silent overflow” that truncates model inputs unpredictably.
- Ranking/pruning should be deterministic given the same inputs and configuration.

