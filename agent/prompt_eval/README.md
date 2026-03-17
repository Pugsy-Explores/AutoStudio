# Prompt Evaluation (`agent/prompt_eval/`)

Prompt quality and regression evaluation utilities. This package is intentionally separated from production routing/execution to keep evaluation tooling lightweight and safe.

## Responsibilities

- Provide helpers to **log** prompt failures, **classify** failure modes, and **summarize/cluster** recurring issues.
- Enable iterative prompt improvements without changing datasets and prompts in the same change (evaluation hygiene).

## Submodules

- `failure_analysis/`: failure logging, pattern classification, clustering.

