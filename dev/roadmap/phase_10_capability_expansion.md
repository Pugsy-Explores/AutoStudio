Phase 10 — Repository-Scale Intelligence
Objective

Transform AutoStudio from:

task-level coding agent

into:

repository-scale engineering system

Capable of handling:

100k–1M LOC repos
large dependency graphs
multi-module refactors
architecture-level reasoning
Core Architecture Shift

Current (Phase 9):

goal
→ supervisor
→ planner
→ localization
→ edit
→ test
→ critic

Phase-10 introduces a Repository Intelligence Layer.

goal
↓
supervisor
↓
repo intelligence layer
↓
role agents
↓
execution
New Subsystem

Add:

agent/repo_intelligence/

Modules:

repo_summary_graph.py
architecture_map.py
impact_analyzer.py
context_compressor.py
long_horizon_planner.py

This layer gives the agent system-level understanding.

Component 1 — Repository Summary Graph

Purpose:

Create a high-level map of the entire repo.

Structure:

repo_summary_graph
{
  modules
  services
  key classes
  dependencies
}

Built from:

symbol_graph
repo_map
dependency_extractor

The agent should be able to answer:

what are the main modules?
what are entrypoints?
which modules depend on each other?
Component 2 — Architecture Map

Purpose:

Extract system architecture automatically.

Example output:

ArchitectureMap
{
  controllers
  services
  data_layers
  utilities
}

This helps the agent reason about:

system boundaries
layering
service interactions
Component 3 — Impact Analyzer

Purpose:

Predict impact of edits.

Example:

edit executor.py
→ affected files:
   policy_engine.py
   test_executor.py

Uses:

symbol_graph
dependency_graph
call_graph

This prevents breaking changes.

Component 4 — Context Compression

Problem:

Large repos exceed context limits.

Solution:

context_compressor

Input:

ranked_context
repo_summary
task_goal

Output:

compressed_context

Techniques:

function summaries
symbol summaries
dependency summaries

This lets the agent reason over large codebases.

Component 5 — Long-Horizon Planner

Current planner handles:

small edits
single feature

Phase-10 planner handles:

multi-module changes
architecture refactors
large features

Example plan:

1 identify modules
2 update service interface
3 update implementations
4 update tests
5 run validation
New Dataset

Add:

tests/repository_tasks.json

Tasks like:

refactor module architecture
rename API across repo
add feature touching multiple services
update configuration across modules

These simulate real software engineering work.

Metrics

Extend metrics system.

Add:

localization_accuracy
impact_prediction_accuracy
context_compression_ratio
long_horizon_success_rate

These measure repo-scale reasoning.

Execution Flow Example

Task:

Add caching to service layer

Phase-10 execution:

Supervisor
↓
Repo intelligence builds architecture map
↓
Planner identifies service modules
↓
Localization finds relevant files
↓
Edit agent updates code
↓
Impact analyzer checks dependencies
↓
Test agent runs tests
↓
Critic verifies result
Safety Layer

Add limits:

max_repo_scan_files = 200
max_architecture_nodes = 500
max_context_tokens = model_limit

These prevent runaway scans.

Phase-10 Exit Criteria

Phase-10 is complete when:

repository tasks ≥ 40
success ≥ 75%
impact prediction ≥ 80%
multi-module edits stable

At this point AutoStudio becomes capable of repository-scale engineering tasks.

Final Architecture (After Phase-10)
Interface Layer
CLI / chat / editor

Orchestration Layer
supervisor agent
role agents

Reflection Layer
evaluator
critic
retry planner

Repository Intelligence Layer
repo_summary_graph
architecture_map
impact_analyzer
context_compressor

Knowledge Layer
repo_map
symbol_graph
retrieval

Execution Layer
editing pipeline
terminal tools
filesystem tools

Observability Layer
trace logger
metrics
replay
Principal Engineer Advice

Do not rush Phase-10.

Implement in this order:

repo_summary_graph

architecture_map

impact_analyzer

context_compressor

long_horizon_planner

Each step unlocks more repository intelligence.