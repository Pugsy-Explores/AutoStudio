# Planner ↔ exploration orchestration refactor (Anthropic-style loop)

Staff-engineer plan: align `agent_v2` orchestration with a single planner authority, minimal staged changes, **no exploration internals rewrite**.

This document supersedes informal “Cursor plan” drafts: corrections below are **normative** for implementation.

---

## 1. Current architecture (summary)

### Control flow

```
AgentRuntime → ModeManager.run(state, mode)
  → ExplorationRunner.run(instruction)     # always first on ACT/plan/deep_plan
  → _exploration_is_complete()             # ModeManager gate (can RuntimeError)
  → V2PlannerAdapter.plan(..., exploration=...)
  → [optional] ModeManager._run_act_controller_loop (controller + sub-explore + run_one_step)
  → PlanExecutor.run / run_one_step
```

### Ownership (today)

| Area | Owner |
|------|--------|
| First exploration | **ModeManager** |
| Block planning on “incomplete” exploration | **ModeManager** (`_exploration_is_complete`) |
| Sub-exploration | **ModeManager** honors `plan_doc.controller` but **overrides** with budget / `_sub_exploration_gates_ok` |
| Plan JSON + `controller` | **PlannerV2** (LLM) |
| Tool execution | **PlanExecutor** |

---

## 2. Problems (specific)

1. **ModeManager** encodes policy (gate, sub-explore budget, insufficiency replan instead of explore) — **parallel** to planner intent.
2. **Initial exploration** is not “planner-owned”; it always runs before first `plan`, but **gating** crashes instead of adapting.
3. **`PlanDocument.controller`** + **ModeManager branches** + **executor statuses** — fragmented control; easy to interpret ad hoc.
4. **Sub-exploration** skips the same completion semantics as the first pass (inconsistent).
5. **`completion_status` vs `termination_reason`** mismatch can block planning even when exploration is semantically sufficient.

---

## 3. Target architecture

- **Planner** is the single **decision** authority: what to do next (explore / act / replan / stop).
- **Exploration** is a **pure context provider** (`run(query) -> FinalExplorationSchema`); it does not choose planner actions.
- **Execution** applies a **concrete step**; it does not plan.
- **ModeManager** becomes a **thin entrypoint** (mode switch + trace wiring only), **no** control-loop logic.

### Plan vs decision (do not mix)

| Concept | Role |
|---------|------|
| **`PlanDocument`** | The **plan**: steps, dependencies, metadata — **not** the runtime control choice for this tick. |
| **`PlannerDecision`** | The **control** for this iteration: explore / act / replan / stop — **single** structured object. |

Collapse today’s **`PlanDocument.controller`** into **`PlannerDecision`** at the boundary: LLM output still may embed fields in JSON, but **runtime** consumes **`PlannerDecision` only**, not ad-hoc `interpret(plan_doc)`.

---

## 4. Explicit decision contract (must do)

Do **not** use vague `interpret(plan_doc)`. Introduce a **first-class** structure:

```python
# Conceptual — exact module/path TBD (e.g. agent_v2/schemas/planner_decision.py)

class PlannerDecision:
    type: Literal["explore", "act", "replan", "stop"]
    step: Optional[PlanStep]      # when type == "act" (or step_id resolver — match executor)
    query: Optional[str]          # when type == "explore"
    context: Optional[ReplanContext | ExplorationInsufficientContext]  # when type == "replan" / signal from exploration
```

**Mapping rule:** `PlannerV2` (or a thin `PlannerDecisionMapper` next to it) **maps** validated LLM JSON → **`PlannerDecision`**. Runtime **only** switches on `decision.type`.

Executor-implicit states (`run_one_step` → success / failed_step / progress) remain **facts**; they feed **`planner.plan(..., planner_input=...)`** or **`planner.decide(...)`** — they must not be “interpreted” with scattered `if` branches without going through **`PlannerDecision`**.

### 4.1 PlannerDecision pipeline invariant (non-negotiable)

This is where teams usually slip; without discipline, **ModeManager-style chaos returns within weeks**.

**Required chain (no shortcuts):**

```text
LLM output → validated (PlanDocument / structured parse) → PlannerDecision → runtime switch on decision.type only
```

**Forbidden:**

```text
LLM output → partially parsed → runtime “interprets” controller / steps ad hoc
```

| Do | Do not |
|----|--------|
| Validate planner JSON at a **single boundary**, then build a **complete** `PlannerDecision` | Let orchestration read `plan_doc.controller` or loose dict fields in multiple places |
| Branch **only** on `PlannerDecision` fields | Reconstruct intent with scattered `if` / string matching outside the mapper |
| On validation failure, use **explicit** repair (replan, typed fallback decision) | Best-effort `getattr(..., None)` and guess the next action in the loop |

**Enforcement rule:** The orchestration loop (`PlannerTaskRuntime` / equivalent) may import **`PlannerDecision` only** for control flow — not “`PlanDocument` + hope.” If `PlannerDecision` becomes optional, fuzzy, or runtime-filled when missing, you have recreated implicit multi-authority control.

---

## 5. Initial exploration — **always bootstrap once** (correction)

**Wrong abstraction (do not use):**

```text
if should_run_initial_exploration(state):
    exploration = run_exploration(...)
```

That reintroduces **hidden policy** outside the planner.

**Correct pattern:**

- **Always** run **one** initial exploration for the task instruction (bootstrap context).
- **No** “maybe explore” flag at runtime.

```text
exploration = run_exploration(state.instruction)
plan = planner.plan(instruction, exploration=exploration, ...)
```

Further exploration is **only** when **`PlannerDecision.type == "explore"`** with `decision.query`.

Rationale: Anthropic-style flow is **always get initial context → then decide**, not optional first pass.

---

## 6. Exploration validity — **signal, not assert** (correction)

**Wrong:**

```text
assert_exploration_allowed(exploration)  # crashes on partial / edge cases
```

**Correct:**

- Compute **`exploration_valid`** (or richer **`ExplorationSignal`**: complete / partial / failed / low_confidence).
- If **not** sufficient for planning policy, **do not** raise in the orchestration layer by default.
- **Feed planner:** e.g. `planner_input` = `exploration_insufficient` / structured context so **`planner.plan(...)`** adapts (replan, ask for narrower query, or proceed with caveats per prompt).

Goal: **never crash → always adapt** where possible; reserve hard failures for true invariants (e.g. missing `instruction`).

---

## 7. Target loop (normative pseudo-code)

This is the **actual** target loop to implement (via `PlannerTaskRuntime` or equivalent — name TBD).

```python
def planner_loop(state):
    # 1) Always bootstrap once — no should_run_initial_exploration
    exploration = run_exploration(state.instruction)

    # 2) First plan from exploration context
    plan = planner.plan(instruction, exploration=exploration)

    while True:
        # 3) Explicit decision — NOT ad-hoc interpret(plan_doc)
        decision = planner.decide(plan, state)

        if decision.type == "explore":
            exploration = run_exploration(decision.query)
            # Signal path if exploration weak — planner handles on next plan()
            plan = planner.plan(..., exploration=exploration, plan_state=...)
            continue

        if decision.type == "act":
            result = executor.run_one_step(decision.step)
            if result.success:
                return result
            plan = planner.plan(..., replan_context=result)
            continue

        if decision.type == "replan":
            plan = planner.plan(..., replan_context=decision.context)
            continue

        if decision.type == "stop":
            return final_output
```

**Notes for implementation alignment with today’s code:**

- Today **`planner.plan`** is a single LLM call; **`planner.decide`** may be **derived** from `PlanDocument` + rules (same JSON as now) **or** a second small call — **staged**: first implement **`decide`** as a **pure function** `plan_document_to_decision(plan, state)` to avoid doubling LLM cost.
- **`run_one_step`** return shape stays **PlanExecutor**’s; map failures to **`replan_context`** as today.

---

## 8. Refactor steps (surgical, ordered)

1. Add **`PlannerDecision`** schema + **`plan_document_to_decision`** (or mapper from `PlanDocument` + executor cursor) — **tests** on golden JSON.
2. Introduce **`PlannerTaskRuntime`** (name TBD): move body of `ModeManager._run_explore_plan_execute` + `_run_act_controller_loop` here; **ModeManager** delegates.
3. **Initial exploration:** always `run_exploration(instruction)` once at loop entry; remove any “optional first explore” flags.
4. Replace **crash gate** with **signal → `planner.plan(..., exploration_insufficient)`**; align **`completion_status` / `termination_reason`** in metadata layer (surgical, not engine internals).
5. **Sub-exploration:** same validity signaling as initial pass; increment **`sub_explorations_used`** in runtime only.
6. **Collapse controller:** stop reading `plan_doc.controller` in ModeManager; read **`PlannerDecision`** from mapper only.
7. Keep **ExplorationRunner**, **PlanExecutor**, **ExplorationEngineV2** internals **unchanged**.

---

## 9. Risks and mitigation

| Risk | Mitigation |
|------|------------|
| Behavioral drift | Stage 1: move code verbatim → extract `PlannerDecision`; behavior match tests |
| **`PlannerDecision` becomes loose** | Code review rule: orchestration **only** switches on `PlannerDecision`; mapper unit tests for invalid LLM JSON |
| `planner.decide` vs one-shot | Phase 1: `decide` = deterministic map from existing `PlanDocument` + state |
| Prompt changes | Collapse `controller` into `PlannerDecision` mapping in code first; prompt later |
| Observability | Preserve `state.metadata` counters (`planner_controller_calls`, etc.) in runtime |

---

## 10. Contracts (unchanged intent)

- **Planner → exploration:** `instruction` or `query` string (and `obs` / trace).
- **Exploration → planner:** **`FinalExplorationSchema`**; optional thin **`PlannerExplorationView`** (understanding, relevant_files, relationships, gaps, confidence) for prompts only.
- **Planner → executor:** explicit **`PlanStep`** (or step id resolved by runtime).

---

## 11. Verdict vs earlier draft

| Area | Fix |
|------|-----|
| Initial exploration | **Always once** — not `should_run_initial_exploration` |
| Decision structure | **`PlannerDecision`** — not vague `interpret(plan_doc)` |
| **PlannerDecision strictness** | **`LLM → validated → PlannerDecision → switch`** — never partial parse + runtime interpretation |
| Exploration gating | **Signal → planner** — not `assert` / crash |
| Control clarity | **`PlanDocument` = plan, `PlannerDecision` = control** — collapse old `controller` into `PlannerDecision` |

---

*Document version: 1.1 — adds PlannerDecision pipeline invariant (2026).*
