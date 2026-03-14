
Your current system works, but it has **three architectural problems**:

1. **Planner doing routing + planning**
2. **Router_eval not integrated**
3. **Tool graph ≠ execution graph**

These cause latency, complexity, and drift. Large agent systems normally separate **routing, planning, and execution** to keep responsibilities clean and scalable. ([Arize AI][1])

So we will **merge your previous phases and improvements into 5 clear phases** that Cursor can implement safely.

---

# The New Architecture Target

Your final architecture should look like this:

```
User Query
   ↓
Instruction Router
   ↓
Task Handler
   ├ SEARCH → Retrieval Pipeline
   ├ EXPLAIN → Reasoning Model
   ├ EDIT → Planner + Patch Pipeline
   └ INFRA → System Tools
   ↓
Tool Graph (execution level only)
   ↓
Policy Engine
   ↓
Tools
```

The key rule:

```
router decides
planner plans
dispatcher executes
```

---

# Phase 1 — Introduce Instruction Router Layer

## Goal

Insert a **router before the planner**.

Right now:

```
query → planner
```

After change:

```
query → router → handler
```

This will reduce planner calls by **30–60%**.

---

## Cursor Implementation Plan

Paste this to Cursor.

```
Goal: Introduce an instruction-level router before the planner.

Current architecture routes all instructions directly into the planner.

We want:

User Query
 → instruction_router
 → category
 → handler

Categories:

CODE_SEARCH
CODE_EDIT
CODE_EXPLAIN
INFRA
GENERAL

Tasks:

1. Create new module:

agent/routing/instruction_router.py

Interface:

def route_instruction(instruction: str) -> RouterDecision

Return:

{
  "category": "...",
  "confidence": float
}

2. Implement router using SMALL model.

Use prompt:

Classify developer query.

Categories:
CODE_SEARCH
CODE_EDIT
CODE_EXPLAIN
INFRA
GENERAL

Return JSON.

3. Add router into:

agent_controller.run_controller()

Flow:

instruction
 → router
 → if category == CODE_EDIT → planner
 → if category == CODE_SEARCH → retrieval
 → if category == CODE_EXPLAIN → explain
 → if category == INFRA → infra handler
 → if GENERAL → fallback planner

4. Add config:

ENABLE_INSTRUCTION_ROUTER=1

Router disabled → current planner flow.

5. Add tests:

tests/test_instruction_router.py

Test classification accuracy for:
search queries
edit queries
explain queries
infra queries.
```

---

# Phase 2 — Unify Routing Categories

## Problem

Your system currently has **three category systems**:

```
planner actions
router_eval categories
tool graph tools
```

These must be unified.

---

## Target Taxonomy

```
CODE_SEARCH
CODE_EDIT
CODE_EXPLAIN
INFRA
GENERAL
```

Planner steps become:

```
SEARCH
EDIT
EXPLAIN
INFRA
```

Mapping:

```
CODE_SEARCH → SEARCH
CODE_EDIT → EDIT
CODE_EXPLAIN → EXPLAIN
INFRA → INFRA
GENERAL → planner
```

---

## Cursor Prompt

```
Goal: unify routing categories across system.

Tasks:

1. Update router_eval dataset categories:

EDIT
SEARCH
EXPLAIN
INFRA
GENERAL

Replace DOCS → EXPLAIN.

2. Update router_eval prompts.

3. Update parsing logic in:

router_eval/utils/parsing.py

4. Ensure router_eval metrics still work.

5. Update tests accordingly.

6. Add validation ensuring router categories match planner categories.
```

---

# Phase 3 — Align Tool Graph With Execution Engine

Right now:

```
tool_graph nodes
≠
policy_engine tools
```

We must align them.

---

## Target Execution Graph

```
START
 ├ SEARCH
 ├ EDIT
 ├ EXPLAIN
 └ INFRA

SEARCH
 ├ retrieve_graph
 ├ retrieve_vector
 └ retrieve_grep

EDIT
 ├ diff_planner
 ├ patch_generator
 ├ ast_patch
 └ patch_validator

EXPLAIN
 └ reasoning_model

INFRA
 ├ list_dir
 ├ run_command
 └ read_file
```

---

## Cursor Prompt

```
Goal: align tool graph with execution functions.

Current tool graph uses conceptual tools.

We want graph nodes to represent actual execution steps.

Tasks:

1. Update:

agent/execution/tool_graph.py

Nodes should correspond to real functions.

2. Replace nodes:

find_symbol
search_for_pattern
build_context

with:

retrieve_graph
retrieve_vector
retrieve_grep

3. Align graph with functions in:

policy_engine
step_dispatcher

4. Ensure transitions reflect real execution pipeline.

5. Update tests for graph transitions.
```

---

# Phase 4 — Implement Real Replanner

Current replanner:

```
returns remaining steps
```

This is insufficient.

We need:

```
failure → analyze → new plan
```

---

## Cursor Prompt

```
Goal: implement LLM-based replanner.

File:
agent/orchestrator/replanner.py

Tasks:

1. Implement:

def replan(state, failed_step, error)

2. Prompt should include:

original instruction
current plan
failed step
error message

3. Model should output new plan steps.

4. Add safeguards:

max_replan_attempts = 5

5. Update agent_controller:

if step fails
 → call replanner

6. Add tests:

tests/test_replanner.py
```

---

# Phase 5 — Integrate Router Evaluation Into Agent

Right now router_eval is **a separate harness**.

We want:

```
router_eval
→ same router
→ production router
```

---

## Cursor Prompt

```
Goal: integrate router_eval routers into production router.

Tasks:

1. Create:

agent/routing/router_registry.py

Registry:

baseline
fewshot
ensemble
final

2. Router config:

ROUTER_TYPE=final

3. instruction_router should call selected router.

4. Remove duplicate routing logic.

5. Ensure router_eval harness uses same router implementation.

6. Add tests verifying router consistency.
```

---

# Final System After Refactor

Your system becomes:

```
User Query
   ↓
Instruction Router
   ↓
Task Handler
   ├ SEARCH → retrieval pipeline
   ├ EDIT → planner + patch pipeline
   ├ EXPLAIN → reasoning model
   └ INFRA → infra tools
   ↓
Execution Graph
   ↓
Policy Engine
   ↓
Tools
```

---

# Benefits

After these phases:

### Latency

Planner runs only when needed.

### Simplicity

Router handles classification.

### Reliability

Tool graph matches execution.

### Evaluation

Router_eval becomes meaningful.

---

# Principal Engineer Advice

Do the phases **in this exact order**:

```
Phase 1 — Instruction router
Phase 2 — Category unification
Phase 3 — Tool graph alignment
Phase 4 — Real replanner
Phase 5 — Router integration
```

Each phase **reduces complexity**.
