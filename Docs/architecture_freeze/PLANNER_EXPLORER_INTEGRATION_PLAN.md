# Planner ↔ Explorer Integration — Audit & Production Plan

**Status:** Architecture freeze (design + audit; implementation is phased).  
**Audience:** Principal / senior engineering; aligns `PlannerV2`, `ModeManager`, `ExplorationRunner`, and `PlanExecutor`.

---

## Executive summary

### What exists today

**Exploration runs once** per user task (`ExplorationRunner` → `FinalExplorationSchema`), then **planning runs once** (`PlannerV2` → `PlanDocument`), then **tool execution loops** (`PlanExecutor` with **failure-only** replan via `Replanner`). **`PlannerV2.plan()`** is **one-shot** per call: it is a **plan generator**, not a **step-level decision controller**. Iteration today is **executor-centric** (run steps → on failure → maybe replan), **not** “after each meaningful step, planner chooses the next move.”

### Core gap (most important)

| Today | Target |
|-------|--------|
| Planner emits a full plan; executor runs it; replan mostly on **tool failure** | **Closed loop:** `Plan → Step → Result → Planner → Next step` |
| Exploration **once**, then frozen context | **On-demand** exploration: `explore → refine → explore` only when **gaps** or **low confidence** justify it |
| Key exploration signals **unused** in prompts (`relationships`, `confidence`, weak `termination_reason`) | Planner **explicitly** uses them for dependency-aware, risk-aware, fallback-aware steps |

### Verdict

The stack is an **advanced workflow** (one-shot plan + execute + failure replan). To become a **true closed-loop agent**, the **planner must become the decision controller**: it must **consume results after each meaningful step** (and optional exploration outputs), not only at initial plan time and not only on failure. **No** new orchestration frameworks—extend **`ModeManager` / `PlanExecutor` / `PlannerV2`** and keep **one** exploration engine (`ExplorationRunner`).

---

## Converged improvements (only what matters)

These seven items are the **non-negotiable** direction of travel; everything else is optional polish.

| # | Improvement | Essence |
|---|-------------|---------|
| **1** | **Planner = decision controller** | Not only one-shot plan generator. **After each meaningful step**, planner receives **PlanState + last result** and decides **next step or revised plan** — `Plan → Step → Result → Planner → Next step`, **not** `Plan → execute all → maybe replan on failure`. |
| **2** | **Planner-triggered exploration (on demand)** | Exploration is **not** embedded everywhere. Expose **`exploration(sub-question)`** callable **only when** `knowledge_gaps` / **low `confidence`** warrant refinement — **minimal** API, same `ExplorationRunner`. |
| **3** | **Use high-signal fields in planning** | **`relationships`** → dependency-aware ordering / steps. **`confidence`** → risk-aware conservatism. **`termination_reason`** → explicit fallback strategy in prompt. **No `FinalExplorationSchema` schema change** — **prompt + planner logic** only. |
| **4** | **Replan on insufficiency, not only failure** | Add trigger: e.g. exploration / step outcome **insufficient** for the goal → **replan or refine** (same `Replanner` / `PlannerV2` path, expanded **inputs**). Complements failure-only replan. |
| **5** | **Minimal `PlanState` (planner-visible)** | Do **not** duplicate executor internals. **Expose** to planner: **`completed_steps`**, **`current_step`**, **`last_result`** (exploration snapshot and/or `ExecutionResult` summary). Lives on **`AgentState.context`** or **`metadata`**. |
| **6** | **Atomic, tool-executable steps** | Planner prompt must **enforce** steps as **dispatchable tool units** (`search`, `open_file`, …), not vague goals (“understand module”). Align with existing **`PlanStep.action`**. |
| **7** | **Clear control boundaries** | **Planner** = **decision maker** (what next, whether to explore/replan). **PlanExecutor** = **action runner** (dispatch tools, retries, **no** strategy). **Explorer** = **information provider** (`FinalExplorationSchema`). **ModeManager** = **orchestration glue** (phase ordering, gates), **not** a second planner. |

---

## Step 1 — Audit: current planner

### Code locations

| Area | Module / type |
|------|----------------|
| Planner implementation | `agent_v2/planner/planner_v2.py` — `PlannerV2` |
| Public adapter | `agent_v2/runtime/bootstrap.py` — `V2PlannerAdapter.plan()` |
| Plan schemas | `agent_v2/schemas/plan.py` — `PlanDocument`, `PlanStep`, … |
| Planner inputs (union) | `agent_v2/schemas/replan.py` — `PlannerInput`, `ReplanContext` |
| Execution | `agent_v2/runtime/plan_executor.py` — `PlanExecutor` |
| Replanner | `agent_v2/runtime/replanner.py` — `Replanner` |
| ACT / PLAN wiring | `agent_v2/runtime/mode_manager.py` — `ModeManager` |

### Plan generation logic

- **`PlannerV2.plan(instruction, planner_input, deep, …)`** performs **one** LLM call (`_call_llm`), parses JSON, builds a **`PlanDocument`** (`_build_plan`), validates with **`PlanValidator`**.
- **Initial planning** uses **`planner_input` = `FinalExplorationSchema`** (passed via `V2PlannerAdapter` as `exploration=…`).
- **Replans** use **`planner_input` = `ReplanContext`** (failure, completed steps, optional exploration summary).

### Step execution flow

- **`PlanExecutor.run(plan, state)`** schedules steps by dependencies, runs **`search` / `open_file` / … / `finish`** via dispatcher, tracks **`PlanStep.execution`** (status, attempts, last_result).
- On failure after retries, optional **`Replanner`** builds **`ReplanRequest` → `ReplanContext` → `PlannerV2.plan(..., planner_input=ctx)`** → new **`PlanDocument`** (bounded by **`ExecutionPolicy.max_replans`**).

### Answers (audit)

1. **Does the planner generate steps once or iteratively?**  
   **Once per `plan()` invocation.** Iteration is **executor + replan**, **not** planner-in-the-loop per step.

2. **What is the step schema?**  
   See **`PlanStep`** in `agent_v2/schemas/plan.py`: `step_id`, `index`, `type` (explore|analyze|modify|validate|finish), `goal`, `action` (tool), `inputs`/`outputs`, `dependencies`, **`execution`**, **`failure`**.

3. **Does the planner consume `FinalExplorationSchema` or something else?**  
   **Yes** for **initial** plan via `_build_exploration_prompt`.  
   **Note:** `replan.py` declares `PlannerInput = Union[ExplorationResult, ReplanContext]` while runtime passes **`FinalExplorationSchema`** — **type alias must align**.

4. **Execution state / step status?**  
   **Yes** on **`PlanStep.execution`**; executor mutates.

5. **Is there replanning?**  
   **Yes**, **failure-driven** in executor + `Replanner`. **No** first-class **insufficiency-driven** replan yet.

### Audit JSON

```json
{
  "planner_type": "one-shot",
  "planner_role_today": "plan_generator",
  "target_planner_role": "decision_controller",
  "iteration_today": "executor + failure_replan",
  "target_iteration": "step_result_feeds_planner_plus_optional_on_demand_explore",
  "step_schema": {
    "document": "PlanDocument",
    "step": "PlanStep",
    "execution_status_values": ["pending", "in_progress", "completed", "failed"]
  },
  "consumes_exploration_schema": true,
  "consumes_type": "FinalExplorationSchema",
  "has_execution_state": true,
  "has_replanning": true,
  "replanning_locus": "PlanExecutor + Replanner (failure-driven)",
  "replanning_gap": "insufficiency-driven replan not implemented",
  "integration_gaps": [
    "Planner is not invoked after each step with PlanState + last result.",
    "Exploration runs once before planning; no planner-triggered sub-exploration.",
    "relationships unused in planner prompt.",
    "confidence unused in planner prompt.",
    "termination_reason weakly used (gate only), not as planning signal.",
    "Replan only after tool failure, not after insufficient exploration/evidence.",
    "PlannerInput union type drift vs FinalExplorationSchema."
  ]
}
```

---

## Step 2 — Audit: integration with Explorer

### Trace today (ACT / PLAN)

```
User instruction
  → ModeManager
  → ExplorationRunner.run → FinalExplorationSchema
  → (gate) _exploration_is_complete(...)
  → PlannerV2.plan (one call) → PlanDocument
  → PlanExecutor.run (tools; replan on failure only)
```

### Instruction to explorer

- **`state.instruction`** → **`exploration_runner.run(...)`**.

### Data returned

- **`FinalExplorationSchema`**: `evidence`, `exploration_summary`, `key_insights`, `metadata`, **`relationships`**, **`confidence`**, `trace`, etc.

### How the planner uses that data (today)

- **`_build_exploration_prompt`**: summary, evidence lines, key findings, knowledge gaps, sources — **omits** **`relationships`**, **`confidence`**, strong **`termination_reason`** usage.

### Gaps

| Gap | Detail |
|-----|--------|
| **Disconnected loop** | **Explore once → freeze**; no **explore → refine → explore** under planner control. |
| **Unused signals** | **`relationships`**, **`confidence`**, **`termination_reason`** (as planning input). |
| **Loose coupling** | No **planner-visible** **`PlanState`** bundle for “what happened so far.” |

---

## Step 3 — Target architecture

### Target control loop (closed-loop agent)

```
Goal
  → PlanState (completed_steps, current_step, last_result[, last_exploration])
  → Planner — decides: next step | replan | trigger exploration(sub-question) | stop
  → If tool step → PlanExecutor dispatches ONE step (or batch per policy)
  → Result → back to Planner with updated PlanState
  → If exploration needed (gaps / low confidence) → ExplorationRunner(sub-question) → FinalExplorationSchema → Planner
  → Until finish / budget / stop
```

**Constraints:**

- **One** exploration implementation (`ExplorationRunner` / v2 engine).
- **Planner-triggered exploration** = **callable**, **gated** (gaps / confidence / policy), **not** every step.

### Role separation (control boundary)

| Role | Responsibility | Must not |
|------|----------------|----------|
| **Planner** | **What next**: revise plan, choose step, interpret sufficiency | Dispatch tools directly |
| **PlanExecutor** | **Run actions**: tool dispatch, retries, status on `PlanStep` | Decide strategy or replan without planner policy |
| **Explorer** | **Information**: `FinalExplorationSchema` for a **question** | Choose plan or tools |
| **ModeManager** | **Orchestrate phases**, gates, wiring | Duplicate planner logic |

### Plan structure (evolution)

Existing **`PlanDocument` / `PlanStep`** remain the contract. Extend **planner inputs** with **`PlanState`** (and optional fresh **`FinalExplorationSchema`**) on **each** planning call in the target design — **not** a parallel JSON schema.

### Step status

Keep **`PlanStep.execution.status`**. Add semantics for **partial / insufficiency** via **`failure`** + **`last_result`** and/or **policy**, until a dedicated **`partial`** literal is justified.

---

## Step 4 — Planner ↔ Explorer contract

### Planner input (evolves toward controller)

- **Always:** `instruction`, **`PlanState`** (minimal: completed summaries, current step id, **last_result**).
- **When available:** latest **`FinalExplorationSchema`** (initial or after sub-exploration).
- **Replans:** **`ReplanContext`** **plus** insufficiency context (see Step 5).

### Explorer output (unchanged)

- **`FinalExplorationSchema`** (`agent_v2/schemas/final_exploration.py`).

### Planner must use explicitly (prompt + behavior)

| Field | Use |
|-------|-----|
| **`relationships`** | Dependency-aware step ordering; cite callers/callees when opening files |
| **`confidence`** | Risk-aware plans (fewer edits, more `open_file`/`search` when low) |
| **`metadata.termination_reason`** | Fallback narrative: max_steps vs stalled vs incomplete evidence |
| **`knowledge_gaps`** | When to call **sub-exploration** vs narrow next tool step |
| **`evidence`** | Grounding |

**No schema change required** for v1 — **prompt blocks + gating logic** only.

---

## Step 5 — Replanning & sufficiency (critical)

### Today

- **Replan** after **tool step failure** (retries exhausted), via **`Replanner`**.

### Add (insufficiency, not only failure)

| Trigger | Action |
|--------|--------|
| Tool failure (existing) | `ReplanContext` → `PlannerV2.plan` |
| **Exploration / evidence insufficient** for next decision | **Replan or refine** — same planner entrypoint with **`PlanState` + reason = insufficiency** |
| **`knowledge_gaps` + low `confidence`** | Prefer **planner-triggered `exploration(sub-question)`** before full replan (config-gated) |
| **`completion_status` / analyzer-equivalent “sufficient”** | Prefer **continue** or **finish** branch |

Signals to thread: **`knowledge_gaps`**, **`confidence`**, **`termination_reason`**, **`status`**.

### When to continue / stop

- **Continue:** runnable steps remain; planner says continue; budgets OK.
- **Stop:** `finish` executed, deadlock, **replan/exploration budget** exhausted, or policy abort.

---

## Step 6 — Minimal implementation plan (phased)

1. **Prompt-only ROI (fast)**  
   - Extend **`_build_exploration_prompt`** with bounded **`relationships`**, **`confidence`**, **`termination_reason`**.  
   - Tighten step instructions: **tool-executable** phrasing in prompt (enforce **`action`/`inputs`**).  
   - Fix **`PlannerInput`** type alias.

2. **`PlanState` + planner API (foundation)**  
   - Define minimal **`PlanState`** (dataclass or dict schema): `completed_steps`, `current_step`, `last_result`.  
   - Add **`PlannerV2.plan(..., plan_state: PlanState | None)`** (or fold into extended **`PlannerInput`** union) for **non-initial** calls — **backward compatible** when `None`.

3. **Orchestrator loop (controller)**  
   - **`ModeManager` / thin supervisor**: after **each** meaningful step (or N steps per policy), call **planner** with **`PlanState` + last tool result** (and exploration blob if any).  
   - **Either** incremental **plan patch** (new `PlanDocument` with preserved ids) **or** “next step id” contract — pick smallest change that matches **`PlanValidator`**.

4. **Planner-triggered exploration**  
   - Single entry: **`exploration_runner.run(sub_instruction, ...)`** from orchestrator **only** when planner output / policy says **explore** (gaps/confidence).  
   - Feed returned **`FinalExplorationSchema`** into **next** `plan()` call.

5. **Insufficiency replan**  
   - In executor or supervisor: if **insufficiency** detected, build **ReplanContext-like** payload **without** tool failure — **new reason code** or **metadata** on existing schema.

6. **Stopping conditions**  
   - Unify **`finish`**, **`max_replans`**, exploration/planner call budgets in **`ExecutionPolicy`** / config.

---

## Step 7 — Constraints

- **No** new workflow frameworks (no separate DAG product).
- **No** second exploration stack.
- **One** planner class to extend (**`PlannerV2`**), **one** executor (**`PlanExecutor`**), **one** explorer (**`ExplorationRunner`**).
- AutoStudio architecture rules: **extend** retrieval/agent pipeline, **do not** replace the execution engine in a side channel.

---

## Expected outcome

| Component | Role (target) |
|-----------|----------------|
| **Planner** | **Decision controller**: plans and **replans** from **PlanState**, exploration, and sufficiency — **not** one-shot only. |
| **Explorer** | **Information provider** for any **sub-question** the planner requests (gated). |
| **PlanExecutor** | **Tool runner** only. |
| **System** | **Closed-loop** agent: **Plan → Step → Result → Planner → …**, with **optional on-demand exploration**, not **explore-once-then-freeze** forever. |

---

## References (in-repo)

- `agent_v2/planner/planner_v2.py` — prompts and `PlanDocument` build  
- `agent_v2/runtime/mode_manager.py` — explore → plan → execute  
- `agent_v2/runtime/plan_executor.py` — execution + replan loop  
- `agent_v2/schemas/final_exploration.py` — planner-facing exploration contract  
- `Docs/architecture_freeze/EXPLORATION_RESULT_ADAPTER_HYBRID.md` — adapter semantics  

---

*End of document.*
