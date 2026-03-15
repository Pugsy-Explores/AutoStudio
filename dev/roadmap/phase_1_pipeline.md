# Phase 1 — Pipeline Convergence (Immediate Goal)

**First goal:** Make one instruction flow successfully through the entire pipeline.

Not 50 features. Not autonomous mode. Just one full successful run.

## Target Example Task

Start with something trivial:

```
Explain how AgentState works
```

## Expected Pipeline

```
query
→ router
→ planner
→ execution loop
→ retrieval pipeline
→ context ranking
→ explanation
→ output
```

## Logs to Verify

You should see logs for:

- router decision
- planner steps
- retrieval results
- context ranking
- model call
- final output

If that works consistently, the engine is alive.


Detailed guide - 
Phase 1 — Pipeline Convergence (Start Now)

Your target outcome:

One query successfully runs through the full system.

Not editing yet.
Not autonomous mode.
Just Explain → Retrieval → Context → Reasoning.

Step 1 — Minimal End-to-End Command

Run this:

python -m agent "Explain AgentState"

This must trigger:

router
→ planner
→ execution loop
→ retrieval pipeline
→ context ranking
→ reasoning
→ output

Expected trace:

router decision
planner steps
retrieval results
context ranking
model call
final output

If this works even once, the engine is alive.

Step 2 — Trace Verification

Check:

agent/observability/trace_logger.py

Run:

scripts/replay_trace.py

You must see:

planner decisions
retrieval stages
model inputs
model outputs
execution results

If traces are incomplete → fix observability.

This is critical.

**Step 2 done:** Trace observability extended: reasoning stage now captures question (model input), first_200_chars/error (model output); agent_loop logs step_executed events; replay_trace shows events.

Step 3 — Retrieval Pipeline Validation

Your biggest risk is retrieval failure.

Test:

python tests/test_retrieval_pipeline.py

Then manually test:

Example query:

Explain StepExecutor

Check each stage:

query rewrite
repo_map lookup
anchor detection
symbol expansion
regex search
vector search
context ranking
context pruning

Expected result:

ranked_context != []

If empty → retrieval bug.

**Step 3 done:** All retrieval tests pass. Added test_retrieval_pipeline_ranked_context_step_executor; added scripts/validate_retrieval_pipeline.py for manual validation.

Step 4 — Planner Integration

Run:

python -m planner.planner_eval

Check:

planner_accuracy
step format
step validity

Planner output must look like:

1 SEARCH StepExecutor implementation
2 EXPLAIN execution flow

If planner generates invalid steps → fix prompt or validation.

**Step 4 done:** Fixed circular import (plan_resolver lazy-imports planner); planner_eval runs; added tests/test_planner_eval.py; added --limit for quick validation.

Step 5 — Execution Loop Integration

Test:

tests/test_agent_loop.py
tests/test_explain_gate.py

Verify loop behavior:

step executed
result validated
next step triggered

Important check:

ExplainGate triggers SEARCH automatically

**Step 5 done:** test_agent_loop mocks dispatch for fast runs; test_explain_gate unit-tests ensure_context_before_explain.
Step 6 — Dispatcher and Tool Graph

Critical files:

agent/execution/step_dispatcher.py
agent/execution/tool_graph_router.py

Run:

tests/test_tool_graph.py

Verify:

step type → correct tool

Example mapping:

SEARCH → retrieval_pipeline
EXPLAIN → reasoning model
EDIT → editing pipeline

**Step 6 done:** test_step_type_maps_to_correct_tool, test_action_to_preferred_tool_mapping added.
Step 7 — Context Builder

Run:

tests/test_context_builder_v2.py

Check:

context size
context ordering
relevance

Your LLM input must contain:

retrieved code
relevant snippets
minimal noise

**Step 7 done:** test_context_ordering_preserved, test_context_contains_retrieved_code_and_snippets added.
Step 8 — First Full System Test

Now run a real scenario.

Example:

python -m agent "Explain how StepExecutor works"

Expected pipeline:

router → EXPLAIN
planner → SEARCH + EXPLAIN
retrieval → code snippets
context builder → context
model → explanation

If this works:

Phase 1 is almost complete.

**Step 8 done:** Full pipeline verified. Run `python -m agent "Explain how StepExecutor works"` (requires model endpoint; may take 2–3 min for planner + query rewrite + retrieval + context ranking + EXPLAIN).

Phase 1 Exit Criteria

You move to Phase 2 only when:

3 explanation tasks succeed end-to-end

Example tasks:

Explain AgentState
Explain StepExecutor
Explain retrieval_pipeline

Success means:

no crashes
retrieval returns context
model produces explanation
trace logs generated