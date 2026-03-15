Phase 11 Architecture

Add a new layer:

agent/intelligence/

Modules:

solution_memory.py
task_embeddings.py
experience_retriever.py
developer_model.py
repo_learning.py
Component 1 — Solution Memory

Purpose:

Store successful solutions.

Example stored record:

task: "fix retry bug in executor"
files_modified: ["executor.py"]
patch_pattern: "retry condition fix"
success: true

Saved in:

.agent_memory/solutions/

Before solving a new task:

retrieve similar solution
adapt strategy
Component 2 — Task Embeddings

Each task becomes a vector:

goal
files touched
patch summary

Stored in:

.agent_memory/task_index.faiss

This enables:

similar task search
Component 3 — Experience Retriever

Pipeline becomes:

goal
↓
retrieve similar tasks
↓
adapt plan
↓
execute

This dramatically improves reliability.

Component 4 — Developer Model

The system should learn developer preferences.

Example stored profile:

preferred test framework: pytest
logging style: structured
code style: type hints required

Saved as:

developer_profile.json

This makes the agent behave like a teammate.

Component 5 — Repo Learning

Over time the system learns:

frequent bug areas
common refactor patterns
architecture constraints

Stored in:

repo_knowledge.json
Execution Flow After Phase 11

Task:

Fix failing test

Execution becomes:

Supervisor
↓
Experience retriever
↓
Planner adapts strategy
↓
Localization
↓
Edit
↓
Test
↓
Critic
↓
Store trajectory

The system improves with each task.

Metrics to Add

Update evaluation system:

solution_reuse_rate
experience_improvement
repeat_failure_rate
developer_acceptance

These measure real improvement.

Phase 11 Exit Criteria

You finish Phase 11 when:

solution reuse ≥ 25%
repeat failures ↓
retry depth ↓
task success ↑