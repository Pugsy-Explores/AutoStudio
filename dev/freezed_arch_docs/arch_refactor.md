* what stays
* what breaks
* what must be refactored
* what must be deleted or isolated



# 🧠 Architecture Review v2 — Component by Component

Based on your codebase + PRD.

---

# 1. 🧭 Top-Level Runtime

## Current

```text
run_controller
  → run_hierarchical
    → execution_loop (ReAct)
```

## Problems

* Controller owns:

  * setup
  * execution
  * memory
  * orchestration

👉 This is **not a runtime**, it’s a script pipeline

---

## Target (FROZEN)

```text
AgentRuntime
  → ModeManager
  → Loop
  → Dispatcher
```

---

## Decision

### ❗ You DO NOT delete run_controller yet

### ✅ You REPOSITION it:

```text
run_controller = thin wrapper over AgentRuntime
```

---

## Action

* Strip logic from `run_controller`
* Move execution ownership into runtime

---

## Risk

🟡 Medium — touches entrypoint but manageable

---

# 2. 🔁 execution_loop (CORE)

## Current

* Handles:

  * LLM call
  * validation
  * dispatch
  * observation
  * history

## Problems

❌ God function
❌ No state abstraction
❌ No lifecycle hooks

---

## Target

```text
Loop
  → get_next_action(state)
  → dispatcher.execute(action)
  → observation_builder(result)
  → state.update()
```

---

## Required Refactor

### Split into:

1. Loop controller
2. Action generator (_react_get_next_action)
3. State updater
4. Observation builder (separate file)

---

## Critical Change

Replace:

```python
react_history: list
```

With:

```python
AgentState
```

---

## Risk

🔴 High — this is core

👉 Must be done carefully without behavior regression

---

# 3. 🧠 AgentState (NEW — CRITICAL)

## Current

* implicit state via:

  * react_history
  * local variables
  * controller context

---

## Target

```python
AgentState:
  instruction
  history
  context
  current_plan
  step_results
  metadata
```

---

## Where it plugs in

* passed to:

  * loop
  * dispatcher
  * tools

---

## Why this matters

Without this:

❌ no PLAN mode
❌ no extensibility
❌ no workflows

---

## Risk

🟡 Medium (additive, not destructive)

---

# 4. 🧭 ModeManager (NEW)

## Current

❌ Does not exist

---

## Target

```text
ModeManager
  → ACT → execution_loop
  → PLAN → planner
  → DEEP_PLAN → advanced planner
```

---

## Important

### DO NOT integrate planner deeply yet

Just:

* reuse existing planner module
* call it directly

---

## Risk

🟢 Low

---

# 5. 🛠 Tool System

## Current

From your RCA :

* spread across:

  * actions.py
  * react_schema.py
  * tool_graph
  * dispatcher
  * prompt YAML

---

## Target

```text
ToolRegistry (single source)
```

---

## What changes

### Replace:

* `react_schema.ALLOWED_ACTIONS`
* dispatcher if/elif
* YAML duplication

---

### With:

```text
registry → validation + dispatch + prompt
```

---

## What stays

* actual tool logic (search, edit, etc.)

---

## Critical Rule

👉 Registry = definition only
👉 No execution logic leakage

---

## Risk

🟡 Medium

---

# 6. 🚀 Dispatcher

## Current

* `_dispatch_react`
* `dispatch`
* ToolGraph routing
* policy engine

---

## Problems

❌ Two execution systems
❌ Hardcoded logic
❌ tightly coupled

---

## Target

```text
Dispatcher
  → tool = registry.get(action)
  → tool.handler(args, state)
```

---

## What to do

### For now:

* Refactor ONLY ReAct path
* Leave legacy untouched

---

## Future

* legacy can be removed later

---

## Risk

🟡 Medium

---

# 7. 🔍 Observation System

## Current

* `_build_react_observation`
* large if/elif

---

## Problem

❌ tightly coupled to loop
❌ not extensible

---

## Target

```text
observation_builder.py
```

---

## Design

```python
build_observation(action, result)
```

---

## Optional later

* per-tool formatter

---

## Risk

🟢 Low

---

# 8. 🧱 Primitive Layer (NEW)

## Current

You already have pieces:

* terminal_adapter
* filesystem_adapter
* etc.

---

## Target

```text
PrimitiveLayer
  → Shell
  → Editor
  → Browser
```

---

## Mapping

| Primitive | Existing             |
| --------- | -------------------- |
| shell     | terminal_adapter     |
| editor    | editing + filesystem |
| browser   | context7 / future    |

---

## Key change

👉 Tools must call primitives
👉 NOT raw system calls

---

## Risk

🟡 Medium (gradual refactor)

---

# 9. 🧪 Editing Pipeline

## Current

* `_edit_react`
* `_generate_patch_once`
* validation
* repair loop

---

## Verdict

✅ KEEP IT

---

## Only change

* wrap inside tool handler

---

## Risk

🟢 Low

---

# 10. 🔍 Retrieval System

## Current

* extremely advanced pipeline

---

## Verdict

✅ KEEP IT AS-IS

---

## Only change

* expose via `search` tool

---

## Risk

🟢 Low

---

# 11. 🧠 Planner

## Current

* exists
* unused in ReAct

---

## Target

* used in:

  * PLAN mode
  * DEEP PLAN mode

---

## DO NOT

* integrate into loop

---

## Risk

🟢 Low

---

# 12. 🧪 Multi-Agent + Workflow System

## Current

* exists (Phase 9–12)

---

## Verdict

❌ REMOVE FROM CORE

👉 isolate completely

---

## Why

* conflicts with runtime
* different abstraction

---

## Risk

🔴 High if mixed
🟢 Low if isolated

---

# 🚨 Final Gap Analysis

| Area                | Status        |
| ------------------- | ------------- |
| Runtime abstraction | ❌ missing     |
| State system        | ❌ missing     |
| Tool system         | ❌ fragmented  |
| Loop design         | ⚠️ overloaded |
| Dispatcher          | ❌ duplicated  |
| Modes               | ❌ missing     |
| Primitives          | ⚠️ implicit   |
| Workflow layer      | ❌ conflicting |

---

# 🧭 Migration Plan (STRICT ORDER)

## Phase 1 — Tool Registry

* unify tools
* no behavior change

---

## Phase 2 — Dispatcher Refactor

* registry-based dispatch

---

## Phase 3 — AgentState

* introduce state object

---

## Phase 4 — Loop Refactor

* split execution_loop
* plug in state

---

## Phase 5 — ModeManager

* add PLAN + DEEP PLAN

---

## Phase 6 — Primitive Layer

* clean shell/editor abstraction

---

## Phase 7 — Isolation

* remove legacy + workflow from core

---

# ⚠️ Critical Warnings

### 1. Do NOT refactor everything at once

You will break the system.

---

### 2. Do NOT touch retrieval/editing deeply

They are already working.

---

### 3. Do NOT introduce new features mid-refactor

Only structure changes.

---

# ⚡ Cursor Prompt (Start Phase 1–2)

```text
Goal: Begin architecture migration with minimal risk.

Tasks:
1. Implement ToolRegistry and register existing ReAct tools.
2. Refactor _dispatch_react to use registry lookup.
3. Refactor validate_action to use registry.
4. Ensure zero behavior change.

Constraints:
- Do NOT modify execution_loop structure yet.
- Do NOT introduce AgentState yet.
- Do NOT touch legacy dispatch path.

Output:
- Unified tool system with no duplication.
```

---

# 🧠 Final Principal Take

You are **very close to a real system**, but:

> Right now it’s a collection of powerful parts, not a coherent runtime.

This refactor:

* turns it into a **platform**
* not just an agent

---
