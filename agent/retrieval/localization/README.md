# Graph-Guided Localization (`agent/retrieval/localization/`)

Localization utilities that use dependency traversal and execution-path analysis to narrow “where to look” before running broader retrieval. Intended to reduce search cost and improve precision on large repos.

## Responsibilities

- Traverse dependencies to identify candidate modules/symbols.
- Build coarse execution paths to explain likely call flows.
- Rank localization candidates to seed downstream retrieval.

## Public API

Exports from `agent/retrieval/localization/__init__.py`:

- `traverse_dependencies`
- `build_execution_paths`
- `rank_localization_candidates`
- `localize_issue`

## Invariants

- Localization should be additive and advisory; it must not reorder or bypass the core retrieval pipeline stages.

