# Phase 5 — Metrics Dashboard

Measure the system scientifically. Agent evaluation requires structured metrics across reliability and tool usage.

## Track Metrics

- task_success_rate
- retrieval_recall
- planner_accuracy
- edit_success_rate
- avg_latency

## Store Results

`reports/eval_report.json`

This becomes your agent scoreboard.


Details - 
Phase 5 — Capability Expansion

This is where AutoStudio starts to become a real coding assistant, not just an evaluation engine.

The goal is:

reliable infrastructure
→ real developer capabilities

Right now your scenarios are mostly micro-tasks.

Phase-5 introduces real developer workflows.

Phase-5 Objective

Enable the system to perform end-to-end coding tasks, not just single operations.

Examples:

fix a bug
add logging
refactor code
generate a module
create a small project

Coding-agent benchmarks like SWE-bench evaluate systems using exactly these types of tasks—agents receive a repository and an issue description and must produce a patch that fixes the issue.

Your Phase-5 moves toward that capability.

Phase-5 Architecture

Your pipeline stays exactly the same:

router
→ planner
→ execution loop
→ retrieval
→ context
→ editing
→ validation

What changes is task complexity.

Step 1 — Introduce “Developer Tasks”

Create a new dataset:

tests/dev_tasks.json

Categories:

1️⃣ Bug Fixing

Example tasks:

Fix missing import
Fix variable name bug
Fix incorrect return value
Fix failing test

These simulate GitHub issues.

2️⃣ Feature Implementation

Example:

Add retry configuration
Add logging middleware
Add CLI flag
Add config validation

These require multiple edits.

3️⃣ Refactoring

Example:

Extract function
Rename class across modules
Move logic to new module

These test graph navigation.

4️⃣ Code Generation

Example:

Create new module
Create simple CLI project
Generate API wrapper

Now the system must create code, not just modify it.

Step 2 — Multi-Step Task Planning

Your planner must now produce longer plans.

Example:

Task:

Add retry configuration to executor

Expected plan:

1 SEARCH executor implementation
2 READ configuration system
3 EDIT executor to use retry config
4 UPDATE config file
5 VALIDATE changes

Your existing planner can already support this.

You just need richer prompts.

Step 3 — Multi-File Editing Support

Verify editing pipeline handles:

multi-file patches

Tests to add:

tests/test_multifile_edits.py

Example task:

Add retry limit constant to config
Use it inside executor

Expected patch:

config/
executor/
Step 4 — Long-Horizon Tasks

Real development tasks often span many files.

Benchmarks show that even strong agents struggle with long-horizon multi-file tasks.

So start small:

2-file edits
3-file edits

Not 20-file refactors yet.

Step 5 — Capability Metrics

Extend metrics.

Current metrics:

task_success
retrieval_success
edit_success
latency

Add:

files_modified
steps_per_task
patch_size

This helps measure task complexity.

Step 6 — Capability Benchmark

Extend your scenario suite.

Instead of:

25 tasks

Move toward:

40–60 tasks

Groups:

understanding
navigation
editing
bug fixing
feature addition
refactoring
Step 7 — Evaluate Patch Correctness

Right now success likely means:

agent finished without crash

But Phase-5 success should mean:

patch actually correct

Approach:

run test suite
or static validation

This mirrors SWE-bench methodology.

Step 8 — Improve Planner Prompt

Your next big gains will come from planner prompt improvements, not architecture.

File:

agent/prompts/planner_system.yaml

Enhancements:

multi-step reasoning examples
edit planning examples
navigation planning examples
Step 9 — Add “Hello-World Project Generation”

This is a fun but powerful capability test.

Example tasks:

Create a Python CLI project
Create a FastAPI service
Create a simple library

Pipeline:

planner → terminal tool → file creation

Your system already supports:

filesystem tool
terminal tool

So the capability exists.

Phase-5 Exit Criteria

You move to Phase-6 when:

≥60 developer tasks tested
≥75% success rate
multi-file edits stable
bug-fix tasks working

---

## Implementation Status (Phase 5 Capability Expansion)

- [x] tests/dev_tasks.json — 40 developer tasks (bug_fixing, feature_addition, refactoring, code_generation)
- [x] agent/prompts/planner_system.yaml — multi-step few-shot examples
- [x] tests/test_multifile_edits.py — two-file, three-file, ast.parse, rollback
- [x] agent/memory/step_result.py — files_modified, patch_size
- [x] scripts/run_capability_eval.py — 8 metrics → reports/eval_report.json
- [x] tests/agent_scenarios.json — extended to 40 (G6, G7, G8)