# AutoStudio Master Architecture (Reference Model)

Version: **AutoStudio v1 Control Architecture**

Purpose:
A repository-aware autonomous software engineering agent that plans, executes, evaluates, and iteratively improves solutions until the task goal is satisfied.

---

# 1. System Philosophy

AutoStudio is a **closed-loop autonomous system**.

The agent must **continue operating until the task goal is satisfied or a hard safety limit is reached.**

The system therefore operates on three nested loops:

```
Goal Loop        (task success)
Attempt Loop     (plan success)
Step Loop        (tool success)
```

These loops form the **control hierarchy of the system**.

---

# 2. Core Control Loops

## 2.1 Goal Loop (Trajectory Loop)

Purpose:
Ensure the agent keeps trying until the **user goal is satisfied**.

Responsibility:

• evaluate overall success
• invoke critic when failure occurs
• generate retry hints
• trigger new attempts

Conceptual flow:

```
while not goal_satisfied:

    attempt_result = run_attempt()

    evaluation = evaluate_goal(attempt_result)

    if evaluation.success:
        return SUCCESS

    hints = critic(attempt_result)

    update_state_with_hints(hints)

    if max_attempts reached:
        return FAILURE
```

Owned by:

```
agent.meta.trajectory_loop   # conceptual
agent.orchestrator.agent_controller.run_attempt_loop  # Mode 1 implementation (Phase 5)
```

**Mode 1 (Phase 5):** The goal/attempt loop is implemented in `run_attempt_loop()`: up to `MAX_AGENT_ATTEMPTS`; each attempt runs `run_deterministic(..., retry_context=...)`; after each attempt, `GoalEvaluator.evaluate()`; on failure, `Critic.analyze()` (deterministic + LLM strategy hint), `RetryPlanner.build_retry_context()` (previous_attempts, critic_feedback, strategy_hint); planner receives retry_context (strategy hint, previous attempt plans, diversity guidance). TrajectoryMemory holds attempt data; critic uses a trajectory summary (not raw step results). See [PHASE_5_ATTEMPT_LOOP.md](PHASE_5_ATTEMPT_LOOP.md).

This loop **owns goal completion**.

Nothing below this layer decides task success.

---

# 2.2 Attempt Loop (Plan Execution)

Purpose:
Execute a plan produced by the planner.

This loop operates inside a **single attempt**.

Conceptual flow:

```
plan = planner(state)

while not plan_exhausted:

    step = next_step(plan)

    result = execute_step(step)

    if result.classification == FATAL_FAILURE:
        break  # stop attempt immediately, no replanning

    if result.failed:
        plan = replanner(state)
        continue

    if step_invalid:
        plan = replanner(state)
        continue

    record_step_in_state()

if plan_exhausted or result.classification == FATAL_FAILURE:
    return attempt_result
```

Owned by:

```
agent.orchestrator.execution_loop   # shared step loop (Phase 3)
agent.orchestrator.deterministic_runner  # get_plan → execution_loop(enable_goal_evaluator=True)
agent.orchestrator.agent_loop       # deprecated run_agent: get_plan → execution_loop(enable_step_retries=True)
```

Important invariant:

```
Plan completion does NOT equal task success.
```

Additional Mode 1 guarantees:

- AgentState is the **single source of truth** for `completed_steps` and `step_results`. The deterministic runner derives `completed_steps`, `patches_applied`, and `files_modified` from `AgentState` **after** validation; steps that fail validation are never marked as completed.
- Every step produces a `StepResult` with a `classification` field: `SUCCESS`, `RETRYABLE_FAILURE`, or `FATAL_FAILURE`.
- When a step is classified as `FATAL_FAILURE`, the deterministic loop terminates immediately without replanning.

**Phase 4 — Plan identity:** Step identity is `(plan_id, step_id)`. Every plan has a unique `plan_id`; replanned plans get a new `plan_id`. `completed_steps` stores `(plan_id, step_id)` so that after replanning, `next_step()` only considers steps completed for the **current** plan—fixing the bug where a replanned plan reusing ids 1,2,3 would incorrectly skip step 1 if the previous plan had completed step 1.

Attempt loop only reports **attempt outcome**.

---

# 2.3 Step Loop (Tool Execution)

Purpose:
Execute a single step robustly.

This loop manages **tool retries and mutations**.

Conceptual flow:

```
for attempt in policy.max_attempts:

    tool_input = mutate(step, attempt_history)

    result = tool(tool_input)

    if result.success:
        return result

return failure
```

Owned by:

```
agent.execution.policy_engine
```

This layer handles:

• search retries
• edit retries
• infra retries
• mutation strategies

---

# 3. Core System Pipeline

The runtime flow of the system:

```
User Instruction
        │
        ▼
Instruction Router
        │
        ▼
Planner
        │
        ▼
Attempt Loop (deterministic runner)
        │
        ▼
StepExecutor.execute_step
        │
        ▼
Step Dispatcher
        │
        ▼
Policy Engine
        │
        ▼
Tool Execution
        │
        ▼
State Update
        │
        ▼
Validator
        │
        ▼
Replanner (if needed)
        │
        ▼
Goal Evaluator
        │
        ▼
Critic + Retry Planner (retry_context: previous_attempts, critic_feedback, strategy_hint)
        │
        ▼
Next Attempt (planner receives retry_context; trajectory summary for critic)
```

**Implementation (Phase 5):** `agent_controller.run_attempt_loop` orchestrates this. Critic is hybrid (deterministic failure_reason + LLM analysis/strategy_hint); trajectory summarization feeds the LLM (≤1000 chars). Planner prompt order: [Strategy Hint] → [Previous Attempts] → [Planning Guidance] → [Instruction]. See [PHASE_5_ATTEMPT_LOOP.md](PHASE_5_ATTEMPT_LOOP.md).

---

# 4. Component Architecture

## 4.1 Planner

Purpose:
Convert instruction into structured plan.

Output:

```
Plan:
    steps:
        id
        action
        description
```

Constraints:

• one action per step
• dependencies respected
• minimal plan size

Planner must not assume repository state.

---

## 4.2 Dispatcher

Purpose:
Route steps to the correct execution path.

Actions:

```
SEARCH
EDIT
INFRA
EXPLAIN
```

Dispatcher responsibilities:

• validate step input
• resolve tool via tool graph
• call policy engine

---

## 4.3 Policy Engine

Purpose:
Execute steps with retry policies.

Policies define:

```
max_attempts
retry_conditions
mutation_strategy
```

Example:

```
SEARCH:
    retry_on empty_results
    mutation query_rewrite

EDIT:
    retry_on symbol_not_found
    mutation symbol_retry
```

---

## 4.4 Retrieval System

Purpose:
Locate relevant code.

Pipeline:

```
repo_map_lookup
      │
anchor detection
      │
hybrid search
      │
BM25
Graph
Vector
Grep
      │
rank fusion
      │
symbol expansion
      │
reference lookup
      │
reranker
      │
context builder
      │
context pruner
```

Output:

```
ranked_context
context_snippets
retrieved_symbols
retrieved_files
```

---

## 4.5 Editing Pipeline

Purpose:
Modify repository safely.

Stages:

```
diff planner
conflict resolver
patch generator
patch validator
patch executor
test repair loop
```

Constraints:

```
max_files
max_patch_lines
syntax_validation
rollback_on_failure
```

---

## 4.6 Validator

Purpose:
Verify step correctness.

Validator checks:

• step result validity
• rule compliance
• context consistency

Validator operates at **step level only**.

---

## 4.7 Replanner

Purpose:
Adjust plan after step failure.

Inputs:

```
failed_step
step_result
error
state
```

Output:

```
new_plan
```

Replanner only modifies **remaining plan**.

---

## 4.8 Goal Evaluator

Purpose:
Determine if the user goal is satisfied.

Examples:

```
tests pass
file created
function implemented
refactor complete
instruction fulfilled
```

Evaluator may use:

• rule-based checks
• model-based reasoning (future phases)

This is the **task completion authority**.

### Current Phase 4 baseline (Mode 1 — deterministic runner)

Today, the Goal Evaluator is implemented as a **deterministic, rule-based module** (`agent/orchestrator/goal_evaluator.py`) that runs at the end of the attempt loop, after the plan is exhausted.

It currently considers a goal satisfied when there is **meaningful progress**:

```
- Any EDIT step succeeded
- OR any StepResult has patch_size > 0
- OR any StepResult has non-empty files_modified
- OR the instruction asks to "explain" and a corresponding EXPLAIN step succeeded
```

If the plan is exhausted and the Goal Evaluator reports `goal_not_satisfied`, the deterministic runner:

• triggers a **replan** with error="goal_not_satisfied"  
• respects MAX_REPLAN_ATTEMPTS and other safety limits  
• logs: goal_evaluation, goal_not_satisfied, goal_unresolved, goal_completed

This closes the loop for Mode 1 without changing the higher-level architecture.

---

## 4.9 Critic

Purpose:
Diagnose why the attempt failed.

Outputs:

```
root_cause
failure_type
recommendations
```

Example failure types:

```
retrieval_miss
bad_plan
bad_patch
missing_context
tool_failure
```

---

## 4.10 Retry Planner

Purpose:
Generate hints for next attempt.

Hints may include:

```
rewrite_query
expand_context
force_plan
target_files
```

These hints update **agent state** before next attempt.

---

# 5. State Model

Agent state is the **shared memory of the system**.

State contains:

```
instruction
current_plan
completed_steps
step_results
context
trajectory
```

Context includes:

```
retrieved_files
retrieved_symbols
ranked_context
search_memory
patches
tool_memories
retry_hints
```

State must evolve after every step.

---

# 6. System Invariants

The architecture enforces these invariants:

### invariant 1

```
task success is determined only by Goal Evaluator
```

---

### invariant 2

```
plan completion ≠ task completion
```

---

### invariant 3

```
all tool execution passes through StepExecutor → dispatcher → policy engine
```

---

### invariant 4

```
state is the single source of truth
    - AgentState.completed_steps is the authoritative record of which steps finished successfully
    - Aggregated metrics (patches_applied, files_modified) are derived from AgentState.step_results
```

---

### invariant 5

```
every failure produces feedback
```

---

# 7. Edge Cases

The system must handle the following cases.

---

## Retrieval failures

Example:

```
search returns empty
```

Mitigation:

```
query rewrite
vector search
grep fallback
```

---

## Planner hallucination

Example:

```
plan references nonexistent files
```

Mitigation:

```
validator rejection
replanner
```

---

## Patch failure

Example:

```
syntax error
tests fail
```

Mitigation:

```
repair loop
rollback
retry
```

---

## Infinite loops

Mitigation:

```
max_attempts
max_runtime
max_replans
```

---

## Context explosion

Mitigation:

```
context pruning
compression
token budgeting
```

---

# 8. Observability

The system must log:

```
plan
step execution
tool calls
patches
failures
retrieval metrics
latency
```

All events must be written to:

```
agent_trace.jsonl
```

This enables replay and failure analysis.

---

# 9. Safety Limits

The system must enforce limits:

```
max_attempts
max_runtime
max_steps
max_patch_lines
max_files_modified
max_tool_calls
```

Limits prevent runaway agents.

---

# 10. Final System Diagram

Conceptually:

```
User
 │
 ▼
Planner
 │
 ▼
Attempt Loop (deterministic runner)
 │
 ▼
StepExecutor
 │
 ▼
Dispatcher
 │
 ▼
Policy Engine
 │
 ▼
Tools
 │
 ▼
State Update
 │
 ▼
Validator
 │
 ▼
Replanner
 │
 ▼
Attempt Result
 │
 ▼
Goal Evaluator
 │
 ▼
Critic
 │
 ▼
Retry Planner
 │
 ▼
Next Attempt
```

This loop continues until:

```
goal satisfied
OR
safety limit reached
```

---

# Final Principal Engineer Advice

Your codebase already implements **almost all of this architecture**.

The main transition required is:

```
deterministic_runner
      ↓
attempt executor
```

and

```
trajectory_loop
      ↓
system control loop
```
