
Here is a **codebase-grounded implementation plan** for the chat-aware planning architecture described in the freeze doc. I audited `agent_v2` (exploration, planner, runtime, schemas) and aligned gaps with the document’s intent.

---

## CONTROL OWNERSHIP RULE (non-negotiable)

**Problem avoided:** introducing `TaskPlannerService` alongside `PlannerTaskRuntime` can imply **two control surfaces** (both “owning” the loop), which causes conflicting decisions, duplicated logic, and hard debugging.

**Single rule:**

| Component | Role |
|-----------|------|
| **`PlannerTaskRuntime`** | **Sole owner of the execution loop.** It owns: iteration control, branching (`explore` / `act` / `replan` / `stop`), tool invocation (via `PlanExecutor` / `ExplorationRunner`), **enforcement of stopping** (budgets, `stop_policy`, gates), and **all mutations** of runtime state (`AgentState`, working-memory attachment, session memory updates after steps). |
| **`TaskPlannerService`** (or thin `TaskPlanner` / `PlannerAction` provider) | **Decision provider only.** **Pure** in control terms: **stateless** with respect to the loop—it **returns the next intended action** (e.g. `PlannerAction`) given inputs **supplied by the runtime** (snapshots of conversation summary, working memory, last exploration). It **does not** run loops, **does not** mutate state, and **does not** invoke tools. |

**Pattern:** **one control loop** (`PlannerTaskRuntime`) **·** **multiple decision sources** (today: `PlannerV2` + `PlanDocument.engine`; future: optional small-model `TaskPlannerService` **or** rules—runtime still applies the decision the same way). Same structure as Anthropic-style agents: **orchestrator runs the loop;** models return **proposals**, not side effects.

**Corollary:** Working-memory and conversation-memory **writes** happen **inside the runtime** (or dedicated memory helpers **called by the runtime**), not inside `TaskPlannerService`.

---

## 1. Existing components (what you already have)

| Area | Location / role |
|------|-----------------|
| **Exploration engine** | `ExplorationEngineV2` (`exploration_engine_v2.py`); orchestration of cap-K, refinement, inner `engine_loop_steps` lives here. |
| **Pipeline stages** | `QueryIntentParser`, `ExplorationScoper`, `CandidateSelector`, `UnderstandingAnalyzer` (`exploration/`). |
| **Runner / entry** | `ExplorationRunner.run()` → `FinalExplorationSchema` (`runtime/exploration_runner.py`). |
| **Analyzer output (actual)** | `UnderstandingResult` (internal); **planner contract** is `FinalExplorationSchema` (`schemas/final_exploration.py`): `evidence`, `exploration_summary` (incl. `knowledge_gaps`), `confidence` (`high` \| `medium` \| `low`), `metadata.engine_loop_steps`, etc. — not the freeze doc’s literal `{ understanding, gaps, signals }` JSON, but **semantically mappable**. |
| **Orchestration** | `PlannerTaskRuntime` (`runtime/planner_task_runtime.py`): exploration → optional synthesis → `PlannerV2` → `PlanExecutor`; optional **controller loop** when `get_config().planner_loop.controller_loop_enabled`. |
| **Outer-loop decisions** | `PlannerDecision` + `planner_decision_from_plan_document()` (`schemas/planner_decision.py`, `runtime/planner_decision_mapper.py`): `explore` \| `act` \| `replan` \| `stop`. |
| **Session-ish memory** | `SessionMemory` (`runtime/session_memory.py`): intent anchor, compressed recent steps, last tool/decision — **in-process only**, not durable chat history. |
| **Exploration-stage working memory** | `ExplorationWorkingMemory` (`exploration/exploration_working_memory.py`): evidence/gaps/relationships for **one exploration run**, not full task scratchpad. |
| **Answer synthesis** | `answer_synthesizer.py` + `maybe_synthesize_to_state()` — runs **after** initial (and sub-) exploration, **before** planner calls in the current pipeline. |
| **Tool routing** | `Dispatcher` + `PlanExecutor` + tool policy (`runtime/dispatcher.py`, `plan_executor.py`, `tool_policy.py`). |
| **Stopping / budgets** | `PlannerLoopConfig`: `max_planner_controller_calls`, `max_sub_explorations_per_task`; gates like `_sub_exploration_gates_ok()`; exploration metadata `engine_loop_steps`. |

**Important mismatch with the freeze “thin Task Planner”:** today’s **`PlannerV2`** is a **full plan generator** (`PlanDocument`, steps, tool specs), not a small structured router with `{ action, sub_task, tool }` only. The **thin control surface** is closer to **`PlannerDecision`** + **`planner_decision_mapper`** + **`PlannerTaskRuntime._run_act_controller_loop`**.

---

## 2. Missing or partial vs the freeze

| Freeze concept | Status |
|----------------|--------|
| **Task Planner (thin decision provider)** | Partially approximated by controller loop + mapper; **not** a separate small model/router with the freeze I/O contract. When added, it must remain **decision-only**; **see CONTROL OWNERSHIP RULE**. |
| **Sub-step planner** | **No dedicated module**; sub-explore uses `decision.query` → `ExplorationRunner.run(query)` directly. |
| **Task-level working memory** | **Missing** as a single schema (`current_goal`, `sub_tasks`, `completed_steps`, `tool_outputs`, `iteration_count`, …). State is spread across `AgentState`, `SessionMemory`, `PlanDocument`, and context dicts. |
| **Conversation memory (persistent)** | **Missing**; `SessionMemory` is ephemeral and not a summarized multi-turn transcript store. |
| **Stopping aligned to analyzer “sufficient / partial / insufficient”** | **Partial**; you use `confidence`, gaps, and budgets — **no single normalized “understanding” enum** consumed by a dedicated policy module. |
| **Synthesis “callable anytime by planner”** | **Fixed ordering** today (post-exploration, pre-planner on each explore path); not a planner-invoked phase. |

---

## 3. Architecture integration (where things should sit)

Recommended layering **without refactoring `ExplorationEngineV2`**:

```text
Chat / CLI
  → ConversationMemoryStore (load at start; persist at end — called BY runtime)
  → PlannerTaskRuntime (ONLY execution loop owner)
       each tick: build inputs → ask decision provider(s) → apply result → mutate state → enforce stop/budgets
       decision provider MAY include:
         - TaskPlannerService (pure: snapshot in → PlannerAction out), and/or
         - PlannerV2 (PlanDocument) — existing path
       memory: WorkingMemory + SessionMemory updates happen HERE (or helpers invoked from here), not in TaskPlannerService
  → ExplorationRunner → FinalExplorationSchema (unchanged)
  → PlanExecutor / Dispatcher (unchanged)
```

**Principle:** keep **exploration** and **tool execution** as today; add **memory boundaries** and optionally **split “routing” from “plan authoring”** so small models can propose the next action while a larger model still produces `PlanDocument` when needed—**without** a second loop owner.

---

## 4. Data contracts (concrete proposals)

### 4.1 Task planner (thin) — proposed I/O

**Ownership:** this is the **output shape of a decision provider**, not a runner. **`PlannerTaskRuntime`** maps `PlannerAction` → effects and existing `PlannerDecision`.

Align with the freeze but map to existing `PlannerDecision`:

```json
{
  "action": "plan | explore | synthesize | stop",
  "sub_task": "string",
  "tool": "exploration | code | search | none",
  "rationale_trace_id": "optional"
}
```

**Mapping:**  
`explore` → current `PlannerDecision(type="explore", query=…)`; `synthesize` → either trigger `maybe_synthesize_to_state` or set engine decision to stop after synthesis; `stop` → `type="stop"`; `plan` → delegate to existing plan generation (or `act` when steps exist).

### 4.2 Working memory (per top-level instruction)

```json
{
  "current_goal": "string",
  "sub_tasks": ["..."],
  "completed_steps": [{"kind": "explore|act|synthesize", "summary": "..."}],
  "tool_outputs": [{"tool": "...", "summary_ref": "id"}],
  "accumulated_context": ["short refs or ids, not raw code"],
  "analyzer_snapshots": [{"confidence": "...", "gaps_nonempty": true}],
  "iteration_count": 0,
  "last_exploration_id": "uuid"
}
```

Store under e.g. `state.context["task_working_memory"]` or a typed object on `AgentState` to satisfy “single source of truth” direction.

### 4.3 Conversation memory (persistent)

```json
{
  "session_id": "string",
  "turns": [{"role": "user|assistant", "text_summary": "...", "ts": "..."}],
  "rolling_summary": "string",
  "last_final_answer_summary": "string"
}
```

**Explicitly exclude** large code blobs (per freeze). Persist via pluggable backend (file/SQLite/Redis) later; start with interface + in-memory impl for tests.

### 4.4 Normalized “analyzer outcome” for policy (bridge to freeze)

Derive from `FinalExplorationSchema` **without** changing the engine:

- `understanding`: `sufficient` if `confidence == "high"` and gaps empty or non-blocking; `insufficient` if low confidence or critical gaps; else `partial`.

Document this mapping in one module (`exploration_outcome_policy.py` or similar) so stopping rules stay **testable and stable** for 7B/14B.

---

## 5. Execution loop (pseudo-level)

**Owner:** `PlannerTaskRuntime` (or a single outer function it calls). **`TaskPlannerService.decide(...)` appears only as a callee** inside that loop—never as the loop body’s owner.

```text
# Pseudocode: loop lives in PlannerTaskRuntime — not in TaskPlannerService

on_user_message(msg):
  conv = load_conversation_memory(session_id)                    # runtime I/O
  wm = init_or_reset_working_memory_if_new_top_level_instruction(msg)  # runtime

  iteration = 0
  while iteration < MAX_OUTER_ITERATIONS and not runtime_should_stop(...):   # runtime enforces
    snapshot = build_decision_snapshot(msg, conv.summary, wm, state)       # runtime builds inputs

    action = task_planner_service.decide(snapshot)   # PURE: next action only (no mutation)
    # Alternative: action derived from planner_v2 PlanDocument.engine — same runtime shell

    if action == explore:
      sub = sub_step_planner.to_query(action.sub_task)   # single-shot; no loop here
      exploration = exploration_runner.run(sub, ...)     # runtime invokes tools
      runtime_record_exploration(wm, state, exploration) # runtime mutates state
      maybe_synthesize_to_state(state, exploration, ...)
      iteration += 1
      if stop_policy.should_stop(exploration, wm): break  # runtime enforces
      continue

    if action == synthesize:
      run_answer_synthesis(...)            # runtime
      runtime_record_synthesis(wm, ...)
      break or continue per runtime policy

    if action == plan / act:
      plan_doc = planner_v2(...)           # existing
      run executor until stop or failure     # runtime / PlanExecutor
      runtime_record_steps(wm, ...)
      break or replan per runtime

    if action == stop:
      break

  finalize_answer(...)                     # runtime
  append_to_conversation_memory(...)       # runtime
  return response
```

This matches the freeze **appendix**: **one primary bounded loop** owned by **`PlannerTaskRuntime`**, **ExplorationEngine inner bounded loop** unchanged inside `ExplorationEngineV2`, **no loops inside QIP/Scoper/Selector/Analyzer** beyond what already exists in the engine. The thin task planner is **not** a second loop—it is a **replaceable decision function** inside the runtime’s loop.

---

## 6. Proposed module layout (incremental)

| Path | Purpose |
|------|---------|
| `agent_v2/planning/task_planner.py` | **Decision provider only:** `decide(snapshot) -> PlannerAction` (LLM or rules). **No** loop, **no** state mutation. |
| `agent_v2/planning/sub_step_planner.py` | Single-shot sub-task → query string / `PlannerPlanContext` fragment (no loop). |
| `agent_v2/memory/working_memory.py` | Task working memory **model**; **mutations** called from `PlannerTaskRuntime`. |
| `agent_v2/memory/conversation_memory.py` | Protocol + rolling summary; **load/save** invoked by runtime at boundaries. |
| `agent_v2/planning/stop_policy.py` | `should_stop(...)` — **evaluated and enforced** by `PlannerTaskRuntime`, not by `TaskPlannerService`. |
| `agent_v2/runtime/planner_task_runtime.py` | **Sole control loop** (extend, don’t fork): wire WM, conv memory, stop policy, and decision providers. |

Avoid `orchestrator/` as a duplicate of `PlannerTaskRuntime` unless you later extract a name for clarity.

---

## 7. Step-by-step implementation plan (phased)

### Phase 1 — Minimal “Task Planner” surface
- Introduce **typed `PlannerAction`** (freeze-aligned) and a **mapper** ↔ existing `PlannerDecision`, applied **only inside `PlannerTaskRuntime`** (or a single helper it owns).
- Implement **`TaskPlannerService`** (or equivalent) as **`decide(snapshot) -> PlannerAction` only** — verify it does not import loop runners or mutate `AgentState`.
- Add **unit tests** for mapping and for “no duplicate explore with same query” (anti-pattern from appendix).

### Phase 2 — Working memory
- Implement **WorkingMemory** model; update it from `PlannerTaskRuntime` at: after exploration, after executor step, on replan.
- Thread **iteration counters** and **last exploration fingerprint** (query hash) into metadata for telemetry and stop policy.

### Phase 3 — Conversation memory
- Add **ConversationMemory** protocol + default **in-memory** implementation.
- **Summarization** job: after each assistant reply, append short summary (existing or new small prompt); cap size.
- CLI/session: pass `session_id` from caller when available.

### Phase 4 — Stopping logic
- Implement **`stop_policy`** using normalized outcome from `FinalExplorationSchema` + `wm.iteration_count` + “repeated partial” detection (compare gap sets / confidence streaks). **`PlannerTaskRuntime`** calls `should_stop` and applies budgets—**not** `TaskPlannerService`.
- Align config with freeze: outer **2–4** iterations; keep **inner** exploration iterations as today (`engine_loop_steps` / engine internals).

### Phase 5 — Synthesis integration
- Today: synthesis is **always** after exploration in the hot path. **Option A (minimal):** document as “synthesis after each exploration” and add planner flag to **skip** when unnecessary. **Option B (freeze-faithful):** expose **`invoke_synthesis`** as an explicit branch from Task Planner after low-signal explore.
- Ensure **conversation memory** stores **final answer summary**, not raw synthesis dumps.

---

## 8. Constraints checklist

| Constraint | Approach |
|------------|----------|
| **Do not refactor exploration engine** | All new logic sits in planning/memory/runtime glue; derive policies from `FinalExplorationSchema`. |
| **Small models (7B/14B)** | Thin router prompts small; keep `PlannerV2` prompts bounded; reuse existing normalization (`llm_input_normalize.py`). |
| **Thin planner** | Separate **routing** artifact from **PlanDocument** authoring if you split phases; avoid growing `PlannerV2` with chat memory until contracts are stable. |
| **Single control loop** | **`PlannerTaskRuntime`** remains the only execution loop owner; **`TaskPlannerService`** is a **pure decision provider** (see CONTROL OWNERSHIP RULE). |

---

## 9. Staff verdict

You are **closer to the freeze than the doc’s “MISSING” list suggests**: bounded **controller loop**, **sub-explorations**, **session compression**, and **answer synthesis** already exist. The **real gap** is **explicit task working memory + durable conversation memory + a single stop/analyzer policy layer + optional separation of “route” vs “plan.”** Implement those as **extensions inside `PlannerTaskRuntime`**, with **`TaskPlannerService` as decision-only**, to avoid dual control surfaces and match Anthropic-style **one loop, multiple decision providers**.

---

## Implementation status (codebase)

| Area | Modules | Flags / env |
|------|-----------|-------------|
| Thin task planner (decision-only) | `agent_v2/planning/task_planner.py`, `agent_v2/schemas/planner_action.py`, `agent_v2/planning/decision_snapshot.py`, `agent_v2/planning/planner_action_mapper.py` | `AGENT_V2_ENABLE_THIN_TASK_PLANNER=1` — records `state.metadata["thin_planner_action"]` after exploration; does not replace `PlannerV2` |
| Task working memory | `agent_v2/memory/task_working_memory.py` — `state.context["task_working_memory"]` | — |
| Conversation memory (in-process) | `agent_v2/memory/conversation_memory.py` — `state.context["conversation_memory_store"]`; session id `state.metadata["chat_session_id"]` | — |
| Stop / understanding policy | `agent_v2/planning/exploration_outcome_policy.py` | `AGENT_V2_ENABLE_EXPLORATION_STOP_POLICY=1` — tightens sub-explore gate via `sub_exploration_allowed` |
| Synthesis skip | `agent_v2/exploration/answer_synthesizer.py` (`maybe_synthesize_to_state`) | `AGENT_V2_SKIP_ANSWER_SYNTHESIS_WHEN_SUFFICIENT=1` |
| Runtime integration | `agent_v2/runtime/planner_task_runtime.py` — sole control loop; duplicate explore query guard; `PlannerEngineOutput.decision` extended with `synthesize` / `plan` | See `ChatPlanningConfig` on `get_config().chat_planning` |
| Tests | `tests/test_planner_action_mapper.py`, `tests/test_planning_import_boundaries.py`, `tests/test_task_working_memory.py`, `tests/test_exploration_outcome_policy.py`, `tests/test_conversation_memory.py`, `tests/test_answer_synthesis_skip.py` | — |

**Metadata (debug):** `task_working_memory_version`, `conversation_memory_turns`, `stop_reason` (when policy applies), `thin_planner_action` (when thin planner enabled).