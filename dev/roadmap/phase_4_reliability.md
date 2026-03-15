# Phase 4 — Failure Analysis

Run 100+ tasks and analyze failures.

## Common Failure Types

### Retrieval failure

**Symptom:** planner: SEARCH StepExecutor → retrieval: empty

**Fix:**
- query rewriting
- repo_map tuning
- symbol expansion

### Context explosion

**Symptom:** Too much code passed to model.

**Fix:**
- context pruning
- ranking weights

### Planner hallucination

**Symptom:** Planner generates invalid steps.

**Fix:**
- prompt constraints
- few-shot examples
- step validation

### Editing failure

**Symptom:** Patch validator rejects patch.

**Fix:**
- diff planner constraints
- AST patch rules


Detailed plan - 
Phase 4 — Reliability Engineering
Current State

You now have:

deterministic execution engine

integrated pipeline

scenario benchmark (40 tasks)

evaluation harness

metrics logging

bug tracking

trace replay

That means you have crossed the architecture → system transition.

Now we must cross the next barrier:

working system → reliable system
Objective of Phase 4

The objective is system robustness.

Your system must survive:

retrieval failure
planner hallucination
tool failure
patch rejection
infinite loops
model errors

Without crashing.

Phase 4 Architecture

Your reliability architecture will look like this:

instruction
→ router
→ planner
→ execution loop
→ tool execution
→ validator
→ recovery policy
→ retry / replan / fallback

The new part is:

recovery policy
Step 1 — Failure Recovery Policies

Open:

agent/execution/policy_engine.py

Add explicit failure strategies.

Every step result must be classified:

SUCCESS
RETRYABLE_FAILURE
FATAL_FAILURE

Example logic:

retrieval empty → rewrite query → retry
invalid step → replanner
patch rejected → retry edit
tool error → fallback tool
Step 2 — Automatic Replanning

You already have:

agent/orchestrator/replanner.py

But Phase 4 requires strict limits.

Add configuration:

max_replans = 3
max_step_retries = 2

Behavior:

step fails
→ replanner
→ new plan
→ resume execution
Step 3 — Execution Safeguards

Add hard safety limits.

Inside the execution loop enforce:

max_steps = 20
max_tool_calls = 50
max_runtime = 60 seconds

Without this, agents can spiral.

Step 4 — Retrieval Guardrails

Your biggest failure class will be empty context.

Add fallback logic inside retrieval pipeline:

repo_map lookup
→ graph retrieval
→ vector search
→ grep search

Guarantee:

at least 1 snippet returned

If nothing found:

fallback to file search
Step 5 — Patch Safety Hardening

Editing is dangerous.

Verify pipeline:

diff planner
→ patch generator
→ AST patcher
→ validator
→ executor

Add new checks:

max file changes
max patch size
syntax validation
rollback verification
Step 6 — Trace Integrity

Your trace system must capture everything.

Verify trace contains:

router decision
planner steps
retrieval stages
context ranking
model calls
tool execution
patch application
errors

Trace replay must reproduce the trajectory.

Step 7 — Failure Pattern Mining

Now run your scenario suite repeatedly:

python scripts/run_principal_engineer_suite.py --scenarios

Run it 10–20 times.

Aggregate failures.

Update:

dev/evaluation/failure_patterns.md

Example:

Pattern: retrieval empty
Cause: query rewrite failure

Pattern: invalid edit step
Cause: planner prompt
Step 8 — Latency Profiling

Now inspect:

reports/eval_report.json

Add latency buckets:

retrieval latency
planner latency
editing latency
total runtime

Goal:

<10s average task runtime
Step 9 — Regression Suite

Add tests for every resolved bug.

Folder already exists:

dev/bugs/regression_tests/

Each bug fix must produce:

test_bug_XXX.py

This prevents regressions.

Step 10 — Stress Testing

Now run scenario suite with randomness.

Examples:

different models
different seeds
different queries

Measure:

variance
stability
repeatability

Real agent evaluation systems do this because single runs are unreliable indicators of performance.

Phase 4 Metrics

Update:

dev/evaluation/metrics.md

Add:

retry_rate
replan_rate
failure_rate
latency_avg
Phase 4 Exit Criteria

You exit Phase 4 when:

scenario success rate ≥ 80%
no pipeline crashes
retry logic working
replan logic working
trace complete
What Phase 4 Is NOT

Do NOT start:

autonomous mode
VSCode integration
multi-agent
feature generation
project scaffolding

Those belong later.

Right now:

reliability > capability