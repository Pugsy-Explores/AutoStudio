# Phase 16 — Failure Pattern Mining Framework

## Goal

Run 200–300 tasks, collect trajectories, cluster failures, identify top root causes, and feed improvements into prompts/retrieval/retry policies.

## Architecture

```
Task Runner (dataset_runner)
     ↓
Trajectory Collector (trajectory_loader)
     ↓
Failure Extractor (failure_extractor)
     ↓
Failure Clustering (failure_clusterer)
     ↓
Root Cause Report (root_cause_report)
     ↓
Prompt / Retrieval Improvements
```

## Implemented Components

### agent/failure_mining/

- **failure_taxonomy.py** — FAILURE_TYPES including retrieval_miss, wrong_file_localization, incorrect_patch, syntax_error_patch, test_failure, tool_error, timeout, hallucinated_api, premature_completion, hallucinated_symbol, loop_failure
- **trajectory_loader.py** — Loads all trajectories (success + failure), tags each with status
- **failure_extractor.py** — Converts trajectories to FailureRecords; loop detection (≥3 consecutive identical steps); hallucinated_symbol detection (symbol in description not in repo graph)
- **dataset_runner.py** — Runs tasks via run_autonomous(), stores status, attempts, trajectory_length in .agent_memory/failure_runs/
- **failure_clusterer.py** — Clusters by failure_type, step_type, retry_strategy, prompt_tokens, (failure_type, step_type)
- **root_cause_report.py** — Generates reports/failure_analysis.md and reports/failure_stats.json; metrics: avg_steps_success, avg_steps_failure, loop_failure_rate
- **failure_judge.py** — Optional LLM labeling via call_small_model for unknown failure types

### Scripts and Data

- **scripts/run_failure_mining.py** — Full pipeline: dataset_runner → trajectory_loader → failure_extractor → root_cause_report
- **tests/failure_mining_tasks.json** — 300 tasks (100 bug fixes, 50 refactors, 50 feature, 100 navigation)
- **tests/test_failure_mining.py** — Unit tests for all modules

### Extended

- **dev/evaluation/metrics.md** — Phase 16 metrics section
- **scripts/run_prompt_ci.py** — Failure regression guardrails (retrieval_miss_rate < 40%, patch_error_rate < 25%)

## Usage

```bash
python scripts/run_failure_mining.py --tasks 300
python scripts/run_failure_mining.py --skip-run   # Analyze existing trajectories only
python scripts/run_failure_mining.py --use-judge  # LLM-relabel unknown types
```

## Output

- `reports/failure_analysis.md` — Human-readable report
- `reports/failure_stats.json` — JSON metrics for CI guardrails

## Separation from prompt_eval

`agent/failure_mining/` is a separate package from `agent/prompt_eval/failure_analysis/`. Do not merge. The former is trajectory-scoped (agent behavior at scale); the latter is prompt-quality scoped.
