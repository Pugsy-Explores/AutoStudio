## Phase 16 — Failure Pattern Mining Framework

(principal engineer plan)

**Implementation status:** Implemented. See [phase_16_failure_mining.md](phase_16_failure_mining.md) and [Docs/FAILURE_MINING.md](../Docs/FAILURE_MINING.md).

Goal:

```
run 200–300 tasks
collect trajectories
cluster failures
identify top root causes
feed improvements into prompts / retrieval / retry policies
```

This follows the same trajectory-analysis methodology used in SWE-agent research.

---

# Architecture Overview

```
Task Runner
     ↓
Trajectory Collector
     ↓
Failure Extractor
     ↓
Failure Clustering
     ↓
Root Cause Report
     ↓
Prompt / Retrieval Improvements
```

---

# Step 1 — Create Failure Mining Package

Create directory:

```
agent/failure_mining/
```

Modules:

```
dataset_runner.py
trajectory_loader.py
failure_extractor.py
failure_clusterer.py
root_cause_report.py
failure_taxonomy.py
```

---

# Step 2 — Define Failure Taxonomy

Create:

```
agent/failure_mining/failure_taxonomy.py
```

Define **standard failure categories**.

Research shows coding-agent failures usually fall into a few patterns. ([NeurIPS Proceedings][2])

Example taxonomy:

```python
FAILURE_TYPES = [
    "retrieval_miss",
    "wrong_file_localization",
    "incorrect_patch",
    "syntax_error_patch",
    "test_failure",
    "tool_error",
    "timeout",
    "hallucinated_api",
    "premature_completion",
]
```

This ensures all failures get **normalized labels**.

---

# Step 3 — Dataset Runner

Create:

```
agent/failure_mining/dataset_runner.py
```

Responsibilities:

```
load task dataset
run run_autonomous()
store trajectories
store evaluation results
```

Config:

```
MAX_TASKS = 300
MAX_RETRIES = 3
```

Output folder:

```
.agent_memory/failure_runs/
```

Each run:

```
{
  task_id
  success
  attempts
  trajectory_file
}
```

---

# Step 4 — Trajectory Loader

Create:

```
agent/failure_mining/trajectory_loader.py
```

Function:

```
load_trajectories(directory)
```

Returns:

```
List[Trajectory]
```

Each trajectory contains:

```
steps
diagnosis
retry_strategy
evaluation
attempts
```

---

# Step 5 — Failure Extractor

Create:

```
agent/failure_mining/failure_extractor.py
```

Purpose:

Convert trajectories into **failure events**.

Example output:

```
FailureRecord:
  task_id
  attempt
  failure_type
  failing_step
  retry_strategy
  prompt_tokens
  repo_tokens
```

Logic:

```
if diagnosis.failure_type:
    use that
else:
    infer from trace
```

---

# Step 6 — Failure Clustering

Create:

```
agent/failure_mining/failure_clusterer.py
```

Cluster failures by:

```
failure_type
retry_strategy
step_type
prompt_tokens
```

Output statistics:

```
retrieval_miss: 32%
incorrect_patch: 27%
wrong_file_localization: 19%
syntax_error_patch: 11%
tool_error: 6%
timeout: 5%
```

This is the **most important report**.

---

# Step 7 — Root Cause Analyzer

Create:

```
agent/failure_mining/root_cause_report.py
```

This generates a **human readable report**.

Example:

```
Failure Analysis Report

Total tasks: 300
Success rate: 38%

Top failure patterns:

1. Retrieval miss (32%)
   cause: repo_context missing file
   fix: increase MAX_REPO_SNIPPETS

2. Incorrect patch (27%)
   cause: patch generation prompt
   fix: add stricter patch schema

3. Wrong file localization (19%)
   cause: retrieval ranking
   fix: increase symbol_graph expansion
```

Save to:

```
reports/failure_analysis.md
```

---

# Step 8 — Add LLM Failure Labeling (optional but powerful)

Create:

```
agent/failure_mining/failure_judge.py
```

Use a small model to label failures.

Prompt:

```
Given the agent trajectory and evaluation result,
classify the failure type.

Choose from:
retrieval_miss
wrong_file_localization
incorrect_patch
syntax_error_patch
tool_error
timeout
premature_completion
```

This matches how SWE-bench studies label failure modes. ([Scale][3])

---

# Step 9 — Create Mining Script

Create:

```
scripts/run_failure_mining.py
```

Pipeline:

```
run dataset_runner
load trajectories
extract failures
cluster failures
generate root cause report
```

Command:

```
python scripts/run_failure_mining.py --tasks 300
```

---

# Step 10 — Add Evaluation Metrics

Extend:

```
dev/evaluation/metrics.md
```

Add:

```
success_rate
retry_success_rate
avg_attempts
retrieval_miss_rate
patch_error_rate
localization_error_rate
```

---

# Step 11 — Add CI Guardrail

Extend:

```
scripts/run_prompt_ci.py
```

Add:

```
failure regression test
```

Example:

```
retrieval_miss_rate < 40%
patch_error_rate < 25%
```

If exceeded → CI fails.

---

# Step 12 — Add Tests

Create:

```
tests/test_failure_mining.py
```

Tests:

```
trajectory parsing
failure extraction
clustering accuracy
report generation
```

---

# Step 13 — Dataset

Create dataset:

```
tests/failure_mining_tasks.json
```

Include:

```
100 bug fixes
50 refactors
50 feature tasks
100 navigation tasks
```

Total:

```
300 tasks
```

---

# What this phase produces

After one run you will get:

```
reports/failure_analysis.md
reports/failure_stats.json
```

Example output:

```
Total tasks: 300
Success: 112
Success rate: 37%

Top failures:
retrieval_miss: 34%
incorrect_patch: 28%
wrong_file_localization: 18%
syntax_error_patch: 10%
tool_error: 6%
timeout: 4%
```

This tells you **exactly where to improve**.

---

# Why this matters

Studies of coding-agent trajectories show that **failed runs are longer and more chaotic**, and analyzing those trajectories reveals consistent root causes.

Without failure mining you end up doing:

```
random prompt tuning
```

With it you do:

```
data-driven improvements
```

---

# Principal Engineer Advice

Run this loop repeatedly:

```
run 300 tasks
mine failures
fix top 3 root causes
repeat
```

After **3 iterations** your system will improve dramatically.
