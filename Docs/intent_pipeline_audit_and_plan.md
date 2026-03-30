# Intent pipeline — audit, gaps, and implementation plan

This document captures the exploration–planner drift audit, design decisions, and an **updated** implementation plan. It incorporates mandatory fixes for single-source intent, planner/gap salience, explore-query alignment, minimal analyzer surface area, and replan robustness.

---

## 1. Executive summary

**Problem:** The planner expands scope and triggers unnecessary exploration loops because task intent is weak and **not carried** coherently to the planner. The planner optimizes for gap completion instead of answering the user’s question.

**Direction:** Stronger intent representation (parser + prompt), **one canonical intent store**, intent threaded into planner `context_block`, **prompt-level** rules for gaps and explore queries, and a **minimal** analyzer field (`gaps_relevant_to_intent` on `UnderstandingResult` only).

**Non-goals:** Redesign exploration engine control flow, decision mapper heuristics, or new objective systems.

---

## 2. Audit — components

### 2.1 QueryIntentParser (`agent_v2/exploration/query_intent_parser.py`)

| Aspect | Detail |
|--------|--------|
| **Created** | First call in `ExplorationEngineV2._explore_inner` on raw `instruction`; later calls during intent bootstrap / refine with `previous_queries`, `failure_reason`, `context_feedback`, `refine_context`. |
| **Registry** | `exploration.query_intent_parser` (e.g. `agent/prompt_versions/exploration.query_intent_parser/models/qwen2.5-coder-7b/v1.yaml`). |
| **Schema today** | `symbols`, `keywords`, `regex_patterns`, `intents` (retrieval-oriented strings), `relationship_hint`. |
| **Gap** | No structured **user task** (explain / debug / navigate / modify), scope, or focus. Field name `intents` collides conceptually with “user intent.” |

### 2.2 ExplorationEngineV2 (`agent_v2/exploration/exploration_engine_v2.py`)

| Aspect | Detail |
|--------|--------|
| **State** | `ExplorationState` holds `instruction` and operational fields; intent was **not** a durable, planner-visible artifact. |
| **Usage** | `intent` is loop-local: discovery, enqueue, analyzer, mapper. Not exported on `FinalExplorationSchema`. |
| **Analyzer call** | `UnderstandingAnalyzer.analyze(..., intent=", ".join(intent.intents) or "no intent", ...)` — the `intent` string is **retrieval `intents`**, not task classification. |

### 2.3 Analyzer (`UnderstandingAnalyzer`, `UnderstandingResult`, `exploration.analyzer` prompt)

| Aspect | Detail |
|--------|--------|
| **Input** | `{instruction}`, `{intent}` (joined retrieval intents), `context_blocks`. |
| **Output** | Relevance, confidence, sufficiency, `knowledge_gaps`, etc. |
| **Gap** | No structured link from gaps to **user task**; planner only sees flat gaps in `ExplorationSummary`. |

### 2.4 Planner-facing artifacts

| Artifact | Today | Gap |
|----------|--------|-----|
| `FinalExplorationSchema` | `instruction`, evidence, `exploration_summary`, metadata | No canonical task intent on contract (before this work). |
| `PlannerPlanContext` | `exploration`, `insufficiency`, `replan`, `session` | No first-class intent. |
| `exploration_to_planner_context` | Maps exploration → context | Drops intent if not on exploration. |
| `PlannerV2` `_compose_*_context_block` | Overall, key findings, **all** `knowledge_gaps`, confidence, session | No USER TASK INTENT block; full gap list is **highly salient** vs. intent. |

### 2.5 Replan path (`planner_task_runtime.py`, `ReplanContext`)

| Aspect | Detail |
|--------|--------|
| **Failure / insufficiency replan** | Often `exploration_summary=None`. |
| **Risk** | Planner loses exploration-shaped context **and** any intent snapshot unless threaded explicitly. |

### 2.6 Confusing / conflicting behavior

- **Naming:** `QueryIntent.intents` vs “user intent” — analyzer “High-level intent” is retrieval-focused, not task type.
- **Sub-exploration gate** (`_sub_exploration_gates_ok`): Any non-empty gap can trigger more exploration — amplifies loops if gaps are broad. **Do not change gate logic** per architecture constraints; mitigate via **prompt + analyzer hint list** (prompt-driven).

---

## 3. Design — minimal schema extension

### 3.1 Extend `QueryIntent` (`agent_v2/schemas/exploration.py`)

Add **optional** fields (defaults `None` / omitted), inferred from **instruction** in the query-intent parser prompt; on refine/bootstrap re-parse, **merge** from previous `QueryIntent` when the model omits them (sticky user task — not a sufficiency heuristic).

| Field | Type |
|-------|------|
| `intent_type` | `Literal["explanation", "debugging", "navigation", "modification"] \| None` |
| `target` | `str \| None` — primary entity (symbol, file, feature, …) |
| `scope` | `Literal["narrow", "component", "system"] \| None` |
| `focus` | `Literal["internal_logic", "relationships", "usage"] \| None` |

Existing fields (`symbols`, `keywords`, `regex_patterns`, `intents`, `relationship_hint`) unchanged for backward compatibility.

### 3.2 Analyzer — single field, one schema

- Add **`gaps_relevant_to_intent: list[str]`** (default empty) **only** on **`UnderstandingResult`**.
- **Do not** add this field to `ExplorationSummary`, `FinalExplorationSchema`, or `PlannerPlanContext` (avoid schema explosion).
- Planner **does not** receive `gaps_relevant_to_intent` in `context_block` for v1. It still sees **full** `knowledge_gaps` in the exploration summary text; **prompt rules** (§4.2) plus the **USER TASK INTENT** block drive prioritization — no extra schema wiring.

**Staff decision for v1:** Keep `gaps_relevant_to_intent` **only** in `UnderstandingResult`; trace/debug via Langfuse / working memory if needed; planner relies on **prompt rules** + intent block only.

---

## 4. Required fixes (do not skip)

### 4.1 Single source of truth — `state.context["query_intent"]`

**Problem:** Multiple independent copies (`ExplorationState.parsed_query_intent`, `FinalExplorationSchema.query_intent`, `PlannerPlanContext.query_intent`, `ReplanContext.query_intent`) → **drift risk** (e.g. exploration intent ≠ replan intent → inconsistent planner behavior).

**Mandatory fix:**

- Define **`state.context["query_intent"]`** as the **only** canonical store (value: `QueryIntent` model or `dict` serializable to the same).
- **ExplorationEngineV2:** Read/update intent **through** `state.context["query_intent"]` (no separate `ExplorationState.parsed_query_intent` field).
- **ExplorationResultAdapter:** When building `FinalExplorationSchema`, **copy** `query_intent` **from** `state.context["query_intent"]` into the schema field **for transport only** (same snapshot, not a second authority). If schema must not duplicate semantically, document that `FinalExplorationSchema.query_intent` is a **read-only mirror** of context at adapter time.
- **`exploration_to_planner_context`:** Set `PlannerPlanContext`’s planner-visible intent from **`state.context["query_intent"]`** when building context (or from exploration mirror if call path has no state — then require call sites to pass state or copy from exploration mirror which was copied from context at `build` time).
- **`ReplanContext`:** Always set `query_intent` by **copying from** `state.context["query_intent"]` when constructing replan (never invent a parallel parse).

**Rule:** No independent lifecycle for intent in multiple places — **write path** is context; everything else is **read/copy at boundaries**.

### 4.2 Planner still sees ALL gaps — prompt-level mitigation (mandatory)

**Problem:** Even with an intent block, **full `knowledge_gaps`** dominate attention; the LLM will still chase every gap.

**Mandatory fix (prompt text only, no code branches):**

In **planner** registry prompts (`planner.decision.v1`, `planner.replan.v1`, and **act** decision/replan equivalents for consistency):

- When gaps are listed:
  - **Prioritize** gaps that are **relevant to the user’s intent** (as stated in the USER TASK INTENT section and instruction).
  - **Ignore or defer** gaps **outside** the requested scope unless they **block** answering the user’s question.

No `if`/`else` gap filtering in Python — rules live in the prompt.

### 4.3 Replan path — always carry `query_intent`

**Problem:** Replan often has `exploration_summary=None` → weak context for debugging-style flows.

**Mandatory fix:**

- **`ReplanContext`** MUST **always** include **`query_intent`** copied from **`state.context["query_intent"]`** (may be `None` only if never set — e.g. tests without exploration; production path after explore should set it).
- Works even when `exploration_summary` is `None`.

---

## 5. Strong improvements (high ROI)

### 5.1 Intent → explore query alignment (prompt-level, not code)

**Problem:** Explore queries drift or restate the original question → **loops**.

**Fix:** In planner prompts, add:

- If `decision = explore`:
  - The **`query` MUST** target **missing information relevant to the intent**.
  - **Do NOT** merely restate the original user question.

### 5.2 Query intent parser prompt (prompt-level)

- Extend JSON schema with `intent_type`, `target`, `scope`, `focus`.
- Instruct model: derive those four **from the Instruction block only**; retrieval fields may use refinement context as today.
- On re-parse with `previous_queries`, merge missing task fields from previous payload.

### 5.3 Analyzer prompt (light touch)

- Add a short **`task_intent_summary`** (or equivalent) variable built deterministically from `QueryIntent`’s task fields + instruction (formatting only, no new inference in code).
- Ask model to optionally fill **`gaps_relevant_to_intent`** in JSON; coerce in `_coerce_understanding` / `_coerce_modern`.
- **Do not** change scoring, mapper, or exploration loop control.

### 5.4 Planner `context_block` composition (`PlannerV2`)

- Prepend **USER TASK INTENT** section built from **`query_intent`** available on `PlannerPlanContext` (which was copied from `state.context["query_intent"]` / exploration mirror at boundary).
- Keep **instruction** in registry `{instruction}` as today; intent section is part of **`context_block`** or immediately before exploration facts for salience.

---

## 6. Implementation plan (updated)

### 6.1 Files to modify

| Area | File |
|------|------|
| Schema | `agent_v2/schemas/exploration.py` — `QueryIntent` extension; `UnderstandingResult.gaps_relevant_to_intent` |
| Schema | `agent_v2/schemas/final_exploration.py` — optional **`query_intent`** mirror for transport (document as copy-of-context at adapter time) |
| Schema | `agent_v2/schemas/planner_plan_context.py` — optional `query_intent` (copy from context at build) |
| Schema | `agent_v2/schemas/replan.py` — **`query_intent`** required field **or** optional with default `None` but **always passed** from runtime (prefer always key present) |
| Parser | `agent_v2/exploration/query_intent_parser.py` — merge task fields on refine; `_remove_repeated_queries` preserves them |
| Prompts | `exploration.query_intent_parser` (qwen + root if applicable) |
| Engine | `agent_v2/exploration/exploration_engine_v2.py` — **read/write `state.context["query_intent"]` only**; pass `state` into adapter path |
| Adapter | `agent_v2/exploration/exploration_result_adapter.py` — set `FinalExplorationSchema.query_intent` from **`state.context["query_intent"]`** |
| Planner input | `agent_v2/runtime/exploration_planning_input.py` — populate `PlannerPlanContext.query_intent` from context or exploration mirror |
| Planner | `agent_v2/planner/planner_v2.py` — intent block in `context_block`; no new gap-filtering code |
| Runtime | `agent_v2/runtime/planner_task_runtime.py` — ensure `state.context["query_intent"]` set after parse; **every** `ReplanContext(...)` includes `query_intent` from context |
| Analyzer | `agent_v2/exploration/understanding_analyzer.py` + `exploration.analyzer` YAML |
| Planner registry | `planner.decision.v1`, `planner.replan.v1`, `planner.decision.act`, `planner.replan.act` (qwen paths + any act paths in use) — §4.2, §5.1 |

### 6.2 Explicitly NOT doing

- **`ExplorationState.parsed_query_intent`** — **removed from plan**; use context only.
- **Propagating `gaps_relevant_to_intent`** beyond `UnderstandingResult`.
- **Heuristic gap filtering** in Python for the planner.
- **Changes** to decision mapper logic or exploration termination conditions.

### 6.3 Backward compatibility

- New `QueryIntent` fields optional.
- `state.context` may lack `query_intent` in old tests — handle `None`; replan still passes `query_intent=None` explicitly if needed.
- Merge-on-refine preserves task fields when the model omits them.

### 6.4 Success criteria

- One authoritative intent: **`state.context["query_intent"]`**.
- Planner prompts: intent salience, gap prioritization rules, explore-query rules.
- Replan always carries `query_intent` from context.
- Analyzer: `gaps_relevant_to_intent` only on `UnderstandingResult`.
- No regression in existing tests; new tests optional for context threading and replan payload.

---

## 7. Data flow (canonical)

```
Instruction
  → QueryIntentParser
  → write QueryIntent to state.context["query_intent"]  (merge on refine)

ExplorationEngineV2 loop
  → read intent from state.context["query_intent"]

UnderstandingAnalyzer
  → task_intent_summary from QueryIntent (formatting)
  → optional gaps_relevant_to_intent on UnderstandingResult only

ExplorationResultAdapter.build(..., state)
  → FinalExplorationSchema.query_intent = copy of state.context["query_intent"]

exploration_to_planner_context(..., state)  [signature TBD at implementation]
  → PlannerPlanContext.query_intent = copy from state.context["query_intent"] or exploration mirror

PlannerV2
  → USER TASK INTENT + exploration block; registry prompts enforce gap + explore behavior

ReplanContext
  → query_intent = copy from state.context["query_intent"]  (always set field)
```

---

## 8. Revision history

| Date | Change |
|------|--------|
| 2026-03-29 | Initial audit + design. |
| 2026-03-29 | Updated: single source `state.context["query_intent"]`; mandatory planner prompt rules for gaps and explore queries; `gaps_relevant_to_intent` only on `UnderstandingResult`; `ReplanContext` always carries `query_intent`. |
