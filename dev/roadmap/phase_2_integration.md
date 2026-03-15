# Phase 2 — Component Integration Testing

Test each stage interacting with the next stage. Not unit tests — integration tests.

## Implementation Status (Completed)

| Stage | Component | Test Class | Status |
|-------|-----------|------------|--------|
| 1 | Repository indexing | TestStage1RepoIndexing | Done — symbols.json, repo_map.json, index.sqlite verified |
| 2 | Symbol graph | TestStage2SymbolGraph | Done — get_symbol_dependencies wired to GraphStorage (BUG-006) |
| 3 | Repo map | TestStage3RepoMap | Done — lookup StepExecutor → executor.py |
| 4 | Retrieval pipeline | TestStage4RetrievalPipeline | Done — run_retrieval_pipeline populates ranked_context |
| 5 | Context builder | TestStage5ContextBuilder | Done — assemble_reasoning_context format verified |
| 6 | Planner | TestStage6Planner | Done — validate_structure, extract_actions on dataset |
| 7 | Editing pipeline | TestStage7EditingPipeline | Done — plan_diff → to_structured_patches → execute_patch + rollback |
| 8 | Full agent loop | TestStage8FullAgentLoop | Done — run_agent produces trace and plan |

Run: `pytest tests/test_phase2_integration.py -v`

## Detailed Plan Status (10 Steps)

| Step | Description | Implementation |
|------|-------------|-----------------|
| 1 | Router → Planner Integration | TestStep1RouterPlannerIntegration (router_eval.run_eval) |
| 2 | Planner → Execution Loop | TestStage8FullAgentLoop |
| 3 | Execution → Retrieval Pipeline | TestStage4RetrievalPipeline |
| 4 | Retrieval → Context Builder | TestStage5ContextBuilder |
| 5 | Observability | TestStep5Observability (trace contains step_executed) |
| 6 | Explain Gate Safety | TestStep6ExplainGate |
| 7 | Editing Pipeline Integration | TestStage7EditingPipeline |
| 8 | First Real Task Set | dev/evaluation/test_tasks.md (5 tasks); dev/evaluation/metrics.md |
| 9 | Log Failures | scripts/report_bug.py; dev/evaluation/failure_patterns.md |
| 10 | Exit Criteria | scripts/verify_phase2_exit.py (--mock) |

Run exit verification: `python scripts/verify_phase2_exit.py --mock`

## Test Order (Critical)

Use this exact sequence.

### 1️⃣ Repository indexing

**Test:** `index_repo(path)`

**Verify:**
- symbols.json
- repo_map.json
- index.sqlite

### 2️⃣ Symbol graph

Test queries like:

- find callers of function
- find imports
- find inheritance

Verify graph edges exist.

### 3️⃣ Repo map

**Test:** lookup symbol → file

**Example:** `StepExecutor` → `agent/execution/executor.py`

### 4️⃣ Retrieval pipeline

Test each stage separately. Example debug:

```
query rewrite → ok
repo map lookup → ok
anchor detection → ok
graph expansion → ok
regex search → ok
vector search → ok
context ranking → ok
```

Failures usually appear here.

### 5️⃣ Context builder

**Verify:**
- context not empty
- context within token limit
- context relevant

### 6️⃣ Planner

Run planner dataset:

```bash
python -m planner.planner_eval
```

**Check:** planner_accuracy, step validity

### 7️⃣ Editing pipeline

Test patching on toy repo. Example: "Add logging to function foo"

**Verify:** diff planner → patch generator → AST patcher → validator → executor

Rollback must work.

### 8️⃣ Full agent loop

Run:

```bash
python -m agent "Explain StepExecutor"
```

You should get complete trace logs.


Detailed plan - 
Phase 2 — Integration Stabilization
Goal

Verify that all subsystems interact reliably.

Exit condition:

10–15 tasks run successfully without pipeline crashes
Step 1 — Router → Planner Integration

Test that routing decisions correctly invoke the planner.

Run:

python -m router_eval.router_eval

Verify:

router returns correct intent

planner receives the instruction

planner generates valid structured steps

Example expected pipeline:

User: Explain StepExecutor

Router → EXPLAIN
Planner → SEARCH StepExecutor
Planner → EXPLAIN execution

Common failures:

router misclassification

planner step formatting errors

Step 2 — Planner → Execution Loop

Now confirm the planner steps are executable.

Run:

tests/test_agent_loop.py

Check:

step dispatcher receives step

step execution returns result

validator approves step

loop continues

Pipeline should look like:

planner step
→ dispatcher
→ tool execution
→ validator
→ next step
Step 3 — Execution → Retrieval Pipeline

This is usually where systems break.

Run:

tests/test_retrieval_pipeline.py

Verify:

query rewrite
repo_map lookup
anchor detection
symbol expansion
regex search
vector search
context ranking
context pruning

Check outputs manually.

The key invariant:

ranked_context must not be empty

If retrieval returns nothing, you must debug:

query rewrite

anchor detection

repo map lookup

Step 4 — Retrieval → Context Builder

Test:

tests/test_context_builder_v2.py

Verify:

context is ordered by relevance

token budget is respected

no duplicate snippets

LLM input should contain:

top-ranked snippets
relevant symbol context
minimal noise
Step 5 — Execution → Observability

Now confirm the trace system captures everything.

Run an agent query:

python -m agent "Explain AgentState"

Then run:

python scripts/replay_trace.py

Trace must include:

router decision
planner steps
retrieval results
context ranking
model calls
execution outputs

If traces are incomplete, fix observability now.

Step 6 — Explain Gate Safety

Test:

tests/test_explain_gate.py

This ensures the agent does not explain without context.

Example:

User: Explain StepExecutor

System should automatically add:

SEARCH StepExecutor

before explanation.

Step 7 — Editing Pipeline Integration

Now test the editing system separately.

Run:

tests/test_editing_pipeline.py
tests/test_patch_validator.py

Verify flow:

diff planner
→ patch generator
→ AST patcher
→ patch validator
→ patch executor

Critical checks:

patch is syntactically valid

rollback works

file changes localized

Step 8 — First Real Task Set

Now run real scenarios.

Use tasks from:

dev/evaluation/test_tasks.md

Start with 5 tasks:

Explain AgentState
Explain StepExecutor
Find where retry logic exists
Explain retrieval_pipeline
Explain context_ranker

Record results in:

dev/evaluation/metrics.md
Step 9 — Log Failures

When something breaks:

Run:

python scripts/report_bug.py "description"

Move bug through lifecycle:

backlog → in_progress → resolved

Update:

dev/evaluation/failure_patterns.md

This builds your debugging knowledge base.

Step 10 — Phase 2 Exit Criteria

You can move to Phase 3 when:

10–15 tasks succeed end-to-end

Requirements:

no crashes

retrieval returns context

planner steps valid

traces complete

What Phase 2 Is NOT

Do not add:

autonomous mode
multi-agent planning
VSCode integration
test generation
project scaffolding

Those come later.

Right now you are building system stability.