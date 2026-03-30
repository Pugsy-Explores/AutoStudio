# `agent_v2/exploration/` — ExplorationEngineV2 and staged retrieval

---

## 1. Purpose

**Does:** Run **read-only** repository exploration as a **bounded state machine** (`ExplorationEngineV2`): query intent (QIP), discovery (batched SEARCH), optional **scoping** (`ExplorationScoper`), **candidate selection** (`CandidateSelector`), inspection/read, **understanding analysis** (`UnderstandingAnalyzer`), optional graph expansion, and packaging into **`FinalExplorationSchema`**. Supplies **`answer_synthesizer.maybe_synthesize_to_state`** for post-exploration user-facing text (config-gated).

**Does not:** Execute plan steps (`edit`, `run_tests` as plan actions), own the ACT controller loop, or call PlannerV2.

---

## 2. Responsibilities (strict)

```text
✔ owns
  ExplorationEngineV2 inner loop (step counter, stagnation, refine cycles, termination)
  QueryIntentParser, CandidateSelector, UnderstandingAnalyzer, ExplorationScoper wiring
  ExplorationWorkingMemory (per-engine run), adapters to FinalExplorationSchema
  answer_synthesizer (post-exploration synthesis to state.context)

❌ does not own
  PlannerTaskRuntime / PlanExecutor
  TaskWorkingMemory (memory/task_working_memory.py) — updated by runtime after exploration returns
```

---

## 3. Control flow (core pipeline)

One **outer exploration run** walks the engine loop until a termination condition fires. Stages are orchestrated inside `ExplorationEngineV2` (exact ordering is method-driven; conceptually):

```mermaid
flowchart LR
  QIP[QueryIntentParser]
  DISC[Discovery / SEARCH channels]
  SCP[ExplorationScoper optional]
  SEL[CandidateSelector]
  INS[Inspector / InspectionReader]
  ANA[UnderstandingAnalyzer]
  OUT[FinalExplorationSchema]
  QIP --> DISC --> SCP --> SEL --> INS --> ANA --> OUT
```

**Note:** Graph expansion (`GraphExpander`), slice grouping, and refine loops are interleaved per engine state — see `exploration_engine_v2.py` for the authoritative control graph.

---

## 4. Loop behavior

| Loop | Where | Bounds (defaults from `agent_v2/config.py`) |
|------|--------|-----------------------------------------------|
| **Engine main loop** | `ExplorationEngineV2` | `EXPLORATION_MAX_STEPS` (5), `EXPLORATION_STAGNATION_STEPS` (3), `EXPLORATION_MAX_REFINE_CYCLES` (2), `EXPLORATION_MAX_BACKTRACKS` (2), utility stop / gap-driven expansion flags. |
| **Sub-exploration** | `PlannerTaskRuntime` re-invokes `ExplorationRunner` | `AGENT_V2_MAX_SUB_EXPLORATIONS_PER_TASK` (2) — runtime, not engine. |

**Termination examples:** pending queue exhausted, low relevance retry threshold, symbol-aware early exit events (`_emit_exploration_phase_events`), primary symbol sufficient.

---

## 5. Inputs / outputs

- **In:** `instruction: str` (via `ExplorationRunner.run`), `obs`, optional Langfuse trace handles.
- **Out:** `FinalExplorationSchema` — includes `exploration_summary`, `confidence`, `exploration_id`, evidence items, optional `metadata.engine_loop_steps`.

**Downstream consumers:** `PlannerTaskRuntime` copies summary into `state.context`; `exploration_to_planner_context` builds `PlannerPlanContext` for PlannerV2.

---

## 6. State / memory interaction

**Reads:** Retrieval/dispatcher via injected `dispatcher`; query intent may be read from/written to agent state (`write_query_intent_to_agent_state`).

**Writes:** `state.exploration_result` set by runtime; engine may attach exploration metadata. **`TaskWorkingMemory`** is updated **after** exploration returns in `PlannerTaskRuntime._record_task_memory_after_exploration`, not inside the engine module.

**Must not store:** Unbounded full-repo text in engine outputs — snippets are capped (`EXPLORATION_SNIPPET_MAX_CHARS`, `MAX_SNIPPET_CHARS` on class).

---

## 7. Edge cases

- **No relevant candidates:** Engine emits termination (e.g. `no_relevant_candidate` family) — partial `FinalExplorationSchema` still returned.
- **Low relevance:** Retries subject to `EXPLORATION_RETRY_LOW_RELEVANCE_THRESHOLD` and `EXPLORATION_MAX_QUERY_RETRIES` (default 0).
- **Symbol-aware path disabled:** `AGENT_V2_ENABLE_SYMBOL_AWARE_EXPLORATION=0` — falls back to non-outline selection behavior inside engine branches.
- **Scoper off:** `AGENT_V2_ENABLE_EXPLORATION_SCOPER=0` — scoper not applied.

---

## 8. Integration points

- **Upstream:** `ExplorationRunner` constructs engine with shared `Dispatcher`.
- **Downstream:** `exploration_planning_input.call_planner_with_context`, `answer_synthesizer.maybe_synthesize_to_state`, `planning/exploration_outcome_policy` (normalize understanding).

---

## 9. Design principles

- **Retrieval before reasoning:** Discovery and reads precede analyzer LLM calls.
- **Small-model budgets:** Scoper K, selector top-K, analyzer prompts bounded in config.
- **Deterministic shell:** Engine decision mapping via `EngineDecisionMapper` for internal transitions (distinct from planner `PlannerDecision`).

---

## 10. Anti-patterns

- Driving exploration from PlannerV2 directly — breaks single ownership (`ExplorationRunner` / runtime only).
- Increasing evidence rows without updating answer synthesis caps (`ANSWER_SYNTHESIS_MAX_EVIDENCE_ITEMS` max 8).

---

## Module map

| Module | Role |
|--------|------|
| `query_intent_parser.py` | QIP — structured query intent |
| `exploration_scoper.py` | Prompt-budget scoping of candidates |
| `candidate_selector.py` | Batch / symbol-aware selection |
| `understanding_analyzer.py` | Analyzer stage LLM |
| `exploration_engine_v2.py` | Orchestrator |
| `answer_synthesizer.py` | Optional V1 answer text into `state.context` |
| `exploration_working_memory.py` | Engine-internal queue / dedupe |
| `llm_input_normalize.py` | Input shaping for small models |

---

## Config surface (exploration subset)

See `agent_v2/config.py` for full list. Common keys:

| Env | Default | Purpose |
|-----|---------|---------|
| `AGENT_V2_ENABLE_EXPLORATION_ENGINE_V2` | `1` | Engine v2 path (legacy flag name; v2 is standard when on). |
| `AGENT_V2_EXPLORATION_MAX_STEPS` | `5` | Max engine loop iterations. |
| `AGENT_V2_EXPLORATION_SCOPER_K` | `20` | Scoper prompt budget. |
| `AGENT_V2_EXPLORATION_SELECTOR_TOP_K` | `10` | Selector cap. |
| `AGENT_V2_ENABLE_ANSWER_SYNTHESIS` | `1` | Post-exploration synthesis. |
| `AGENT_V2_ENABLE_EXPLORATION_SCOPER` | `1` | Scoper on/off. |
| `AGENT_V2_ENABLE_SYMBOL_AWARE_EXPLORATION` | `1` | Outline + graph context path. |
