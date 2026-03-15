# Phase 7 — Reliability Hardening

Before adding new features, add safety systems.

## Examples

- step timeout
- max steps
- retry policies
- tool validation
- context guardrails

Production agents require strong guardrails to maintain reliability.

Phase 7 — Autonomous Exploration Mode

This is the next phase already hinted in your architecture rules.

But here’s the critical principle:

Do not replace the deterministic pipeline.

You add a new decision loop on top.

Architecture becomes:

Mode 1
instruction
→ planner
→ execution loop

Mode 2
goal
→ observe state
→ choose action
→ execute tool
→ update state
→ repeat

Both use the same:

dispatcher
retrieval pipeline
editing pipeline
trace logger
policy engine
Architecture for Phase-7

Add a new component:

agent/autonomous/

Inside it:

goal_manager.py
action_selector.py
agent_loop.py
state_observer.py
Autonomous loop structure
goal
↓
observe repo state
↓
decide action
↓
execute tool
↓
observe result
↓
update memory
↓
repeat

Actions are still structured actions:

SEARCH
READ
EDIT
RUN_TEST
NAVIGATE

This keeps your system deterministic.

Step 1 — Goal Definition

Example goal:

Fix failing test

The agent converts this into actions:

SEARCH failing test
READ test
SEARCH implementation
EDIT code
RUN_TEST
Step 2 — Observation System

Your observation layer will reuse:

repo_map
symbol_graph
retrieval results
execution trace

This gives the agent situational awareness.

Step 3 — Action Selection

Use a small model (7B class) to choose next action.

Input:

goal
recent steps
repo context

Output:

NEXT_ACTION
Step 4 — Safety Limits

Autonomous loops must include strict limits:

max_steps
max_tool_calls
max_runtime
max_edits

You already added these in Phase 4.

Perfect.

Step 5 — Autonomous Benchmarks

Add new dataset:

tests/autonomous_tasks.json

Example tasks:

Fix failing test
Add retry logic
Implement missing feature

Success metric:

goal achieved
tests pass
Phase-7 Exit Criteria
autonomous loop stable
goal completion rate ≥ 40%
no runaway loops
editing pipeline safe

40% success is already strong for autonomous agents.

---

## Implementation Status (Completed)

### Part 1 — Reliability Hardening

| Task | Status | Location |
|------|--------|----------|
| Per-step timeout | Done | `config/agent_config.py` MAX_STEP_TIMEOUT_SECONDS; `agent/orchestrator/agent_loop.py` ThreadPoolExecutor around executor.execute_step |
| Tool input validation | Done | `agent/execution/policy_engine.py` validate_step_input, InvalidStepError; called from `step_dispatcher.dispatch` |
| Context guardrail | Done | `config/agent_config.py` MAX_CONTEXT_CHARS; `agent/execution/step_dispatcher.py` truncation + context_guardrail_triggered trace event |

### Part 2 — Autonomous Module

| Component | Status | Location |
|-----------|--------|----------|
| goal_manager | Done | `agent/autonomous/goal_manager.py` |
| state_observer | Done | `agent/autonomous/state_observer.py` |
| action_selector | Done | `agent/autonomous/action_selector.py` |
| agent_loop | Done | `agent/autonomous/agent_loop.py` |
| __init__ | Done | `agent/autonomous/__init__.py` exports run_autonomous |

### Part 3 — Benchmark Dataset

| Item | Status | Location |
|------|--------|----------|
| autonomous_tasks.json | Done | `tests/autonomous_tasks.json` (3 tasks) |

### Part 4 — Config

| Item | Status |
|------|--------|
| action_selection task | Done | `models_config.json` task_models + task_params |