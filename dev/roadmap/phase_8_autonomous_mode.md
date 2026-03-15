Phase 8 — Autonomous Mode + Self-Improving Loop

Phase 8 introduces Mode 2 autonomous execution and a self-correction loop so the agent can detect failures and retry with improved strategies.

This phase builds on the deterministic infrastructure and adds a closed feedback loop.

Architecture Overview

Mode 1 (existing deterministic pipeline):

instruction
→ planner
→ execution loop
→ retrieval
→ editing
→ validation
→ result

Mode 2 (autonomous + reflection):

goal
→ observe state
→ choose structured action
→ dispatcher executes
→ update memory
→ evaluate result
→ critique
→ retry strategy
→ repeat

This forms a closed-loop agent architecture.

Infrastructure Reuse (No New Core Systems)

The autonomous system must reuse existing infrastructure.

Reused components:

retrieval pipeline
editing pipeline
trace logger
policy engine
dispatcher
execution safeguards

The reflection layer operates on top of the same execution system.

New Module

Create:

agent/meta/

Modules:

evaluator.py
critic.py
retry_planner.py
trajectory_store.py

These modules add self-evaluation and retry logic.

Step 1 — Evaluator [DONE]

The evaluator determines whether the task succeeded.

Checks include:

did tests pass
did patch apply
did execution succeed
did the goal complete

Outputs:

SUCCESS
FAILURE
PARTIAL

The evaluator reads:

trace logs
execution results
test results
Step 2 — Critic [DONE]

The critic analyzes the failure.

Inputs:

goal
trace
retrieval results
execution outputs
patch results

Outputs:

diagnosis

Example diagnoses:

retrieval returned wrong file
planner step incorrect
patch invalid
missing dependency
Step 3 — Retry Strategy [DONE]

Based on the critic output, the retry planner chooses the next strategy.

Possible strategies:

rewrite retrieval query
expand search scope
generate new plan
retry edit with different patch
search symbol dependencies

Retry limits:

max_attempts = 3

Each attempt produces a new trajectory.

Step 4 — Trajectory Memory [DONE]

Store execution trajectories.

Structure:

goal
steps
failure reason
successful strategy

Benefits:

experience reuse
failure pattern detection
future learning

Trajectory store location:

agent/meta/trajectory_store.py
Step 5 — Autonomous Loop (Final Form) [DONE]

The autonomous loop becomes:

goal
↓
agent run
↓
evaluate result
↓
critic analysis
↓
retry strategy
↓
new attempt

Example:

attempt 1 → failure
critic → wrong module retrieved

attempt 2 → new retrieval strategy
patch applied

attempt 3 → tests pass
Step 6 — Benchmark Expansion [DONE]

Extend autonomous benchmarks:

tests/autonomous_tasks.json

Task types:

bug fixing
feature addition
refactoring
test repair
configuration updates

Each task measures:

goal completion
attempt count
edit success
Step 7 — Metrics [DONE]

Add reflection metrics.

attempts_per_goal
retry_success_rate
critic_accuracy
trajectory_reuse

These go into:

reports/autonomous_eval_report.json (via scripts/run_autonomous_eval.py)
Phase 8 Exit Criteria

Phase 8 completes when:

autonomous success rate ≥ 60%
retry improves success rate
critic diagnoses failures correctly
agent recovers from failed attempts

The system must demonstrate self-correction capability.

Resulting Architecture

After Phase 8, AutoStudio becomes a closed-loop autonomous coding agent.

Final architecture:

Interface Layer
CLI / session / commands

Agent Layer
router
planner
autonomous loop

Knowledge Layer
repo map
symbol graph
retrieval pipeline

Action Layer
editing pipeline
filesystem tools
terminal tools

Reflection Layer
evaluator
critic
retry planner
trajectory memory

Observability Layer
trace logger
metrics
replay
Principal Engineer Recommendation

Do not implement this all at once.

Build in this order:

evaluator

critic

retry planner

trajectory memory

Then enable retries.