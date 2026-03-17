# Failure Analysis (`agent/prompt_eval/failure_analysis/`)

Failure logging and analysis utilities for prompt iteration.

## Responsibilities

- **Log** failures in a structured format for later inspection.
- **Classify** failures into stable categories/patterns.
- **Cluster** failures to surface top recurring issues.

## Public API

Exports from `agent/prompt_eval/failure_analysis/__init__.py`:

- Logging: `FailureRecord`, `log_failure`
- Classification: `classify_failure`
- Clustering: `cluster_failures`, `top_failures`

