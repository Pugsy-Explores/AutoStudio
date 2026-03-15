Phase 9 — Hierarchical Multi-Agent Orchestration

**Status: Complete.** Implemented in `agent/roles/` with supervisor, planner, localization, edit, test, critic agents; `run_multi_agent()`; `tests/multi_agent_tasks.json`; `scripts/run_multi_agent_eval.py`.

Phase 9 introduces role-specialized agents coordinated by a supervisor.

Goal:

single autonomous agent
→ hierarchical agent team

The architecture mirrors how real software teams work.

Core Architecture

Current (Phase 8):

goal
→ autonomous agent
→ evaluate
→ critic
→ retry

Phase 9:

goal
↓
supervisor agent
↓
specialized agents
    ├─ planner_agent
    ├─ localization_agent
    ├─ edit_agent
    ├─ test_agent
    └─ critic_agent
↓
final patch

The supervisor coordinates them.

Infrastructure Rule (Critical)

Do not duplicate infrastructure.

All agents must reuse existing systems:

dispatcher
retrieval pipeline
editing pipeline
trace logger
policy engine
execution limits

Only orchestration changes.

New Module

Create:

agent/roles/

Inside:

planner_agent.py
localization_agent.py
edit_agent.py
test_agent.py
critic_agent.py
supervisor_agent.py

Each agent is thin and wraps existing tools.

Agent Roles
1. Supervisor Agent

Responsibilities:

accept goal
select next agent
coordinate workflow
collect results
stop when goal achieved

Supervisor loop:

goal
→ assign agent
→ receive result
→ update state
→ choose next agent

Supervisor controls orchestration.

2. Planner Agent

Responsibilities:

convert goal → task plan
define acceptance criteria
define checkpoints

Example output:

1 locate failing test
2 identify faulty module
3 edit implementation
4 run tests

Planner runs once per goal.

3. Localization Agent

Purpose:

identify relevant files
identify relevant symbols
identify bug location

Uses:

repo_map
symbol_graph
retrieval_pipeline

Output:

candidate files
relevant symbols
4. Edit Agent

Purpose:

generate patch
apply edits
validate patch

Uses:

editing_pipeline
patch_validator
patch_executor
5. Test Agent

Purpose:

run tests
verify patch
report failures

Tools:

terminal_adapter
filesystem_adapter

Outputs:

PASS
FAIL
ERROR
6. Critic Agent

Purpose:

analyze failures
diagnose cause
suggest retry strategy

Inputs:

trace
patch
test results

Outputs:

diagnosis
retry instruction
Execution Flow

Example task:

Fix failing test

Execution:

Supervisor
↓
Planner agent
↓
Localization agent
↓
Edit agent
↓
Test agent
↓
Critic agent (if failure)
↓
retry edit
↓
final result
Communication Model

Agents communicate through shared state.

State object:

AgentWorkspace
{
  goal
  plan
  candidate_files
  patches
  test_results
  trace
}

Supervisor passes workspace to each agent.

Safety Rules

Mandatory limits:

max_agent_steps = 30
max_patch_attempts = 3
max_runtime = 120s
max_file_edits = 10

Supervisor enforces limits.

Trace Requirements

Each agent must emit trace events:

agent_started
agent_completed
agent_failed
handoff

Trace example:

Supervisor → Planner
Planner → Localization
Localization → Edit
Edit → Test
Test → Critic

This maintains full observability.

Benchmark Expansion

Extend dataset:

tests/multi_agent_tasks.json

Add tasks such as:

Fix failing test suite
Refactor module across files
Add feature requiring config + code
Repair failing integration tests

Each task measures:

goal_success
patch_validity
attempts
agents_used
latency
Metrics

Add to metrics system:

agent_delegations
retry_depth
critic_accuracy
localization_accuracy
patch_success_rate

These go to:

reports/eval_report.json
Phase 9 Exit Criteria

Phase 9 is complete when:

multi-agent tasks ≥ 30
goal success ≥ 70%
critic loop improves success
localization accuracy ≥ 80%
no runaway loops
Resulting Architecture

After Phase 9, AutoStudio becomes:

Interface Layer
CLI / session / commands

Agent Orchestration Layer
supervisor_agent
role agents

Agent Runtime Layer
planner
autonomous loop
critic loop

Knowledge Layer
repo_map
symbol_graph
retrieval

Action Layer
editing pipeline
terminal tools
filesystem tools

Reflection Layer
evaluator
critic
retry planner

Observability Layer
trace logger
metrics
replay
Principal Engineer Guidance

Build Phase 9 in this order:

supervisor agent

planner agent

localization agent

edit agent

test agent

critic agent

Do not start with 5 agents at once.

Add them gradually.