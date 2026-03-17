# Repo Intelligence (`agent/repo_intelligence/`)

Repo-scale understanding helpers intended for **large, multi-module codebases**. This layer builds higher-level representations (architecture maps, summary graphs, impact analyses) that can be consumed by planning and retrieval without expanding context naively.

## Responsibilities

- Build an **architecture map** and **repo summary graph**.
- Perform **impact analysis** to prioritize where to look and what to change.
- Provide **long-horizon planning** helpers for multi-step goals.
- Compress large contexts into bounded, decision-ready summaries.

## Public API

Exports from `agent/repo_intelligence/__init__.py`:

- `build_repo_summary_graph`
- `build_architecture_map`
- `analyze_impact`
- `compress_context`
- `plan_long_horizon`

## Invariants

- Outputs should be bounded and reproducible enough to support deterministic planning/execution.
- Must not bypass retrieval; it should guide retrieval/planning, not replace it.

