# Architecture freeze

**Normative contracts:** See **`README.md`** in this folder. **`SCHEMAS.md`** is authoritative for structured types (`PlanDocument`, `ExplorationResult`, execution/replan schemas). This document is **narrative context** (flows, principles); where it differs from `SCHEMAS.md`, **schemas win**.

No fluff, no half-baked ideas. This is **production-grade**, aligned with real agent systems (planner–executor separation, staged reasoning, controlled execution).

---

## 0. Executive summary

We are **replacing ReAct as the control plane** with a **Planner-Centric System** while keeping your execution engine.

---

## Final system

```text
User Input
   ↓
AgentRuntime
   ↓
ModeManager
   ↓
Exploration Phase
   ↓
ACT loop (PlannerTaskRuntime)
   → optional: answer synthesis → answer validation (model client)
   → PlannerV2 (plan materialize / refresh)
   → Plan Executor (controlled loop)
   ↓
Tools (search / open_file / edit / shell)
   ↓
Trace + Output
```

---

## 1. Architecture (frozen)

### High-level component diagram

```text
+---------------------+
|    VSCode / CLI     |
+----------+----------+
           ↓
+---------------------+
|   AgentRuntime      |
+----------+----------+
           ↓
+---------------------+
|    ModeManager      |
+----------+----------+
           ↓
   (ACT MODE PIPELINE)
           ↓
+---------------------+
| Exploration Runner  |
+----------+----------+
           ↓
+---------------------+
| PlannerTaskRuntime  |
| (ACT: decide → act / |
|  explore / plan /    |
|  synthesize+validate)|
+----------+----------+
           ↓
+---------------------+
| PlannerV2           |
| (Plan Document)     |
+----------+----------+
           ↓
+---------------------+
| Plan Executor       |
| (Controlled Loop)   |
+----------+----------+
           ↓
+---------------------+
| Tool Layer          |
+---------------------+
```

---

## 2. Core principle (lock this)

### Single source of control

```text
PLAN = source of truth
```

**Not:**

```text
LLM decides next step ❌
```

**But:**

```text
Plan decides next step ✅
LLM only fills arguments
```

---

## 3. Component definitions (frozen)

### 3.1 Exploration Runner

#### Purpose

Structured **information gathering phase** before planning.

#### Allowed actions

```text
search
open_file
shell (read-only commands)
```

#### Forbidden

```text
edit
write
patch
```

#### Output

**Normative:** **`ExplorationResult`** in **`SCHEMAS.md` Schema 4** (`items`, `summary`, `knowledge_gaps`, etc.). The minimal `findings[]` sketch below is **deprecated** — do not implement against it.

```python
# Deprecated illustration only — use SCHEMAS.md ExplorationResult
ExplorationResult = {
  "findings": [
    {
      "source": "file_path or search_result",
      "summary": "...",
      "relevance": "...",
    }
  ]
}
```

#### Constraints

- max_steps: 3–6
- must produce summaries (not raw dumps)

**Staged engine (Phase 12.5):** **`PHASE_12_5_EXPLORATION_ENGINE_V2.md`** — optional **`ExplorationEngineV2`** behind **`ExplorationRunner`**; output remains **Schema 4 `ExplorationResult`** only (**`CONTRACT_LAYER.md`**).

---

### 3.2 Planner (critical)

#### Input

**Normative:** **`PlannerInput`** = **`ExplorationResult | ReplanContext`** (`SCHEMAS.md` Schema 4b–4c). Narrative shape:

```python
{
  "instruction": str,
  "exploration": ExplorationResult | ReplanContext,  # see SCHEMAS.md
}
```

#### Output (strict schema)

**Normative machine contract:** **`PlanDocument`** JSON (`SCHEMAS.md` Schema 1). The markdown outline below is **non-normative** documentation only (human-readable preview of the same content).

```markdown
# Plan

## 1. Understanding
- What is the task?

## 2. Sources
- file: agent_loop.py
- file: dispatcher.py

## 3. Plan Steps
1. [explore] Identify X
2. [analyze] Understand Y
3. [modify] Change Z
4. [validate] Run tests

## 4. Risks
- Possible failure points

## 5. Completion Criteria
- When is task done?
```

#### Properties

- human-readable (markdown preview)
- structured (**`PlanDocument`** is the executable contract)
- deterministic sections

---

### 3.3 Plan Executor

#### Purpose

Execute plan **step-by-step with control**

#### Key rule

```text
Executor CANNOT invent new steps
```

#### Execution loop

```text
for step in plan:
    → determine action type
    → LLM fills arguments
    → dispatch tool
    → record result
```

#### Step mapping

| Plan Type | Tool             |
| --------- | ---------------- |
| explore   | search/open_file |
| analyze   | open_file        |
| modify    | edit             |
| validate  | run_tests        |

---

### 3.4 AgentLoop (frozen)

#### Role

Execution engine only

#### Change

- remove action selection responsibility
- keep execution responsibility

---

### 3.5 State model (extended)

**Add:**

```python
state.current_plan
state.plan_index
state.exploration_results
```

**Keep:**

```python
state.history
state.step_results
```

---

## 4. Sequence flow (frozen)

### Full flow

```text
User Input
   ↓
Exploration Phase
   ↓
ACT controller (TaskPlanner decisions)
   ↓
[optional] Synthesize compressed answer into state.context
   ↓
[optional] Validate answer (rules + LLM via model client); on fail → replan / explore / cap rounds
   ↓
PlannerV2 → Plan Document (bootstrap or refresh)
   ↓
Executor:
   step 1 → tool → result
   step 2 → tool → result
   step 3 → tool → result
   ↓
Finish
```

---

## 5. Execution paths

### 5.1 Happy path

```text
Task: "Explain AgentLoop"

Exploration:
  search → open_file

Plan:
  explore → analyze → finish

Execution:
  open_file → finish
```

**Result**

```text
Correct, no edit
```

---

### 5.2 Iterative path (recovery)

```text
Step 3: modify → fails

Executor:
  → retries once
  → still fails

Planner invoked again:
  → revises plan
```

**Key behavior**

```text
Plan revision allowed (controlled)
```

---

### 5.3 Bad path (current system)

```text
search → open_file → edit → edit → edit
```

**Eliminated because:**

```text
edit not in plan → cannot execute
```

---

### 5.4 Edge cases (critical)

#### Case 1 — Missing info

```text
Plan step fails → missing file
```

**Behavior**

```text
Executor triggers:
→ exploration retry
→ planner update
```

---

#### Case 2 — Plan wrong

```text
Plan incorrect
```

**Behavior**

```text
Executor detects failure
→ triggers replanning
```

---

#### Case 3 — Infinite loop

```text
Repeated failures
```

**Behavior**

```text
MAX_PLAN_REVISIONS = 2
→ abort safely
```

---

#### Case 4 — Over-exploration

```text
Too many reads
```

**Behavior**

```text
exploration capped
```

---

## 6. Control rules (frozen)

**Rule 1**

```text
NO execution without plan
```

**Rule 2**

```text
NO edit unless plan contains modify step
```

**Rule 3**

```text
Plan is immutable during execution
(unless failure triggers revision)
```

**Rule 4**

```text
Exploration is read-only
```

---

## 7. Trace model (upgraded)

```text
Plan
 ↓
Step
 ↓
Action
 ↓
Result
```

**ACT extensions (same trace channel):** synthesis events, `answer_validation` payloads in `state.context`, and eval **`PipelineCapture`** (`eval/`) for benchmark runs — all observable; no parallel execution engine.

**Example:**

```text
[Plan Step 1] explore → search → ✓
[Plan Step 2] analyze → open_file → ✓
[Plan Step 3] modify → edit → ✓
```

---

## 8. What this solves

**Before**

```text
Uncontrolled ReAct
```

**After**

```text
Controlled, deterministic execution
```

---

## 9. What we are not doing

- No hardcoded rules
- No hacky guards
- No “respond tool”
- No retry guessing

---

## 10. Final principal verdict

You are now building:

```text
Tier 1: ReAct (what you had)
Tier 2: Plan-Execute (what you move to)
Tier 3: Devin-class (future: multi-agent + replanning)
```

**Naming note:** The lines above are **control-plane maturity**, not the **`eval/` tiered harness** (Tiers 1–4: component vs pipeline benchmarks, `PipelineCapture`, live executor). The two “tier” vocabularies are orthogonal.

---

## Next step

We proceed ONLY after this is accepted.

**Choose next:**

- **“Implement Exploration Runner”**
- **“Define Plan Schema (strict JSON version)”**
- **“Modify AgentLoop to PlanExecutor”**

This is now a **production-grade foundation**.
