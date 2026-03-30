# Exploration refactor — implementation plan

**Status:** Planning + alignment only (no code changes in this document).  
**Goal:** Convert the implicit exploration system into an explicit, contract-driven pipeline aligned to the architecture below.  
**Constraints:** Surgical changes; no redesign of unrelated systems; no new frameworks; extend existing modules and schemas.

**Related (planner / runtime orchestration, not exploration internals):** [planner_exploration_orchestration_plan.md](./planner_exploration_orchestration_plan.md) — ModeManager → `PlannerTaskRuntime`, `PlannerDecision`, always-on initial exploration, exploration-as-signal.

---

## 1. Architecture summary (target)

```text
QueryIntentParser
→ Retrieval (_discovery)
→ Scoper (LLM-assisted compression)
→ Selector (signal provider)
→ Inspect
→ Analyzer (semantic only)
→ EngineDecisionMapper (ONLY control)
→ STOP | EXPAND | REFINE
```

**System intent:** A bounded, stateful **context acquisition** system — not a ReAct loop, not a planner, not a self-improving loop.

**Core principles:**

1. **Single control authority:** `EngineDecisionMapper` only.
2. **Separation of concerns:** stages produce data; only the mapper produces control.
3. **Bounded execution:** fixed caps on expansion depth/passes and refine cycles.
4. **Explicit data contracts:** typed inputs/outputs per stage; no reliance on side channels.
5. **No hidden heuristics controlling flow:** orchestration is visible and mapper-driven.

**Loop constraints (mandatory):**

| Constraint | Value |
|------------|--------|
| `max_expansion_depth` | 1 |
| `max_expansion_passes` | 1 |
| `max_refine_cycles` | 1–2 (config single source) |

**Mandatory mapper logic (pseudo):**

```python
if analyzer.is_sufficient:
    STOP
elif relationship_hint != "none" and not expanded_once:
    EXPAND
elif selector.coverage_signal in ("weak", "fragmented", "empty") and refine_count < limit:
    REFINE
else:
    STOP
```

**Note:** **`good`** does not trigger REFINE from coverage alone. **`empty`** = no candidates (distinct from **weak** — see §7). **`fragmented`** = inconsistent/partial selection — **REFINE** when budget allows (same branch as weak). **`is_sufficient=false` + `good`** → final `else: STOP` (§1.1).

### 1.1 STOP when understanding is incomplete but coverage is good (intentional)

**Scenario:** `is_sufficient == False` and `coverage_signal == "good"`.

**Outcome:** Falls through to `else: STOP` — **not** REFINE.

**Why this looks risky:** Relevant files were selected (good coverage), but the analyzer says understanding is not yet sufficient — it can feel like “stop too early.”

**Why behavior stays STOP (minimal fix, documented — not accidental):**

- Exploration remains **bounded**; the **planner** downstream can use returned gaps / partial understanding.
- **REFINE** is reserved for **weak** (or **empty** — see §6) coverage signals, not for “good files, incomplete semantics.”
- Changing this to auto-REFINE would add an implicit loop and blur selector vs analyzer roles.

**Implementation requirement:** In `EngineDecisionMapper.decide_control` (or equivalent), add a **short comment** above the final branch, e.g.:

```text
# Intentional: is_sufficient=False + coverage_signal=good → STOP.
# Bounded exploration; planner may continue from gaps/partial understanding.
```

Unit test: one case asserting `STOP` for `(is_sufficient=False, coverage_signal=good, relationship_hint=none, refine_count=0)`.

---

### 1.2 Keyword injection (retrieval) — strict rule

**Rule:** Keyword injection into retrieval (`ex_state.discovery_keyword_inject` / equivalent) is **only** applied when the **EngineDecisionMapper** has chosen **REFINE** for the current transition, and the engine is about to run a **REFINE-phase** `_discovery`.

**Must not:** Reintroduce gap-driven or analyzer-driven injection on the default retrieval path.

**Why:** Prevents hidden refinement inside retrieval, retrieval drift, and implicit loops.

**Plan alignment:** `_discovery` reads inject fields **only** when the call stack is explicitly “post-REFINE”; initial and EXPAND-adjacent discovery runs use **QueryIntent** queries only (plus non-control eligibility filters).

---

## 2. Current system mapping

### 2.1 Files and roles

| File | Primary responsibilities (today) |
|------|-----------------------------------|
| `agent_v2/exploration/exploration_engine_v2.py` | Orchestration: `_explore_inner`, `_discovery`, `_enqueue_ranked`, main `while` loop, inspect, context build, analyzer invocation, **gap-driven decisions**, utility stop, refine cooldown, oscillation handling, expand/refine branches |
| `agent_v2/exploration/query_intent_parser.py` | `QueryIntentParser.parse` → `QueryIntent` (symbols, keywords, regex, intents); refine context in prompt |
| `agent_v2/exploration/exploration_scoper.py` | `ExplorationScoper.scope` — LLM index-based subset after file aggregation |
| `agent_v2/exploration/candidate_selector.py` | `select` / `select_batch` — returns candidates or `None`; batch fallback to top-k on match failure |
| `agent_v2/exploration/understanding_analyzer.py` | `UnderstandingAnalyzer.analyze` → `UnderstandingResult` (legacy coercions include control-shaped fields) |
| `agent_v2/exploration/decision_mapper.py` | `EngineDecisionMapper.to_exploration_decision` — maps understanding → `ExplorationDecision` with `next_action` |
| `agent_v2/exploration/exploration_working_memory.py` | Evidence, gaps, relationships; `get_summary()`; `ingest_discovery_candidates` |
| `agent_v2/exploration/graph_expander.py` | Graph/search expansion adapter |
| `agent_v2/schemas/exploration.py` | `QueryIntent`, `ExplorationCandidate`, `UnderstandingResult`, `ExplorationDecision`, `ExplorationState`, etc. |
| `agent_v2/runtime/exploration_runner.py` | Wires LLM + engine; `explore()` entry |

### 2.2 Key functions (current)

- **Intent:** `QueryIntentParser.parse`
- **Retrieval:** `ExplorationEngineV2._discovery`, `_run_discovery_traced`, `_discovery_rerank_candidates`
- **Enqueue path:** `_enqueue_ranked` → scoper (optional) → `CandidateSelector.select_batch`
- **Inspect:** `InspectionReader.inspect_packet`
- **Analysis prep:** `_build_context_blocks_for_analysis`
- **Analyzer:** `UnderstandingAnalyzer.analyze`
- **Decision:** `EngineDecisionMapper.to_exploration_decision` + engine helpers `_next_action`, `_apply_gap_driven_decision`, `_should_expand`, `_should_refine`, `_update_utility_and_should_stop`, `_apply_refine_cooldown`, `_intent_oscillation_detected`
- **Memory:** `ExplorationWorkingMemory.add_evidence`, `add_gap`, `get_summary`, `ingest_discovery_candidates`
- **Result:** `ExplorationResultAdapter.build` → `FinalExplorationSchema`

---

## 3. Violations (vs target architecture)

### 3.1 Mixed responsibilities

- **Engine (`exploration_engine_v2.py`):** Implements control via `_next_action`, `_apply_gap_driven_decision`, `_should_expand`, `_should_refine`, pre/post stop, utility stop — **not** single mapper authority.
- **Decision mapper (`decision_mapper.py`):** Maps analyzer output to `next_action` / `needs` — overlaps with engine and with target “mapper only” contract.
- **Gap-driven logic:** Engine uses gaps from memory/analyzer to force expand/refine — **memory-influenced flow**, violates “memory must not control flow directly.”
- **Retrieval:** `_may_enqueue` / `_may_enqueue_file_candidate` mix **eligibility** with exploration state — acceptable as shaping if documented, but must not encode sufficiency (today it is “filter,” not “sufficiency”).

### 3.2 Control flow leaks

- **Analyzer → loop:** Understanding drives `ExplorationDecision` and `next_action` before mapper is sole authority.
- **Gap-driven expand/refine:** `_apply_gap_driven_decision` changes actions without going through the mandatory mapper predicate set.
- **Multiple layers:** `_next_action` + gap-driven + mapper + cooldown + oscillation.

### 3.3 Hidden heuristics

- Utility stop (`ENABLE_UTILITY_STOP`, `_update_utility_and_should_stop`).
- Refine cooldown (`ENABLE_REFINE_COOLDOWN`, `_apply_refine_cooldown`).
- Intent oscillation → refine→expand coercion.
- Relaxed recovery discovery when pending empty + memory gaps.
- Stagnation / duplicate-target handling (must be bounded; may remain as **queue hygiene**, not as “retry discovery”).
- Selector **silent fallback** to top-k when model output does not match (`select_batch`).
- Dead / unused: initial query retry block (when `initial_refinement_reason` is always `None`); **`seen_files`** passed to `select_batch` but unused.

---

## 4. Refactor plan (file-by-file)

### 4.1 `agent_v2/schemas/exploration.py`

**Change**

- Add `relationship_hint: Literal["none", "callers", "callees", "both"]` to `QueryIntent` (default `"none"`).
- Introduce explicit structs (or extend existing models) for:
  - **Scoper output:** `{ "scoped_candidates": list[ExplorationCandidate] }` (align name with JSON; same Pydantic list).
  - **Selector output:** `selected_candidates`, `selection_confidence` (`high|medium|low`), `coverage_signal` (`good|weak|fragmented|empty`) — see §6 for semantics of **empty** vs **weak**.
- Extend or replace **`UnderstandingResult`** fields to match: `understanding`, `relevant_files`, `relationships`, `confidence` (string enum), `gaps`, `is_sufficient` — preserve backward compatibility for adapter either by parallel fields or one migration with adapter updates in the same PR.
- Replace consumer-facing `ExplorationDecision` with a **control enum** + optional reason: e.g. `STOP | EXPAND | REFINE` only, no `next_action` from analyzer.

**Remove**

- Control semantics from analyzer output shape (no `next_action` / `needs` driving loop from LLM).

**Keep**

- `ExplorationCandidate`, `ExplorationTarget`, `ReadPacket`, `ContextBlock` where still valid.

**Simplify**

- Single source of truth for refine limit and expansion flags on `ExplorationState` (counters), not scattered flags.

---

### 4.2 `agent_v2/exploration/query_intent_parser.py`

**Change**

- Prompt/registry: require `relationship_hint` in JSON; validate enum.
- `_coerce_for_query_intent` (or equivalent): map aliases if needed.

**Remove**

- Any behavior that looks like expansion/stop (should not exist today).

**Keep**

- `parse` signature; refine path may remain for **REFINE** cycles only, fed by explicit `context_feedback` from memory summary (mapper-approved REFINE only).

---

### 4.3 `agent_v2/exploration/exploration_scoper.py`

**Change**

- Return type: structured **`scoped_candidates`** (wrapper or tuple) consistent with contract; internal LLM behavior unchanged in spirit (**compression**).
- Document that scoper **may** rank/filter — aligned with updated spec.

**Remove**

- Any future temptation to read memory (none today — keep absent).

**Keep**

- File aggregation + index expansion back to full `ExplorationCandidate` rows.

---

### 4.4 `agent_v2/exploration/candidate_selector.py`

**Change**

- `select_batch` returns **selector result object** with all three output fields; no bare `None` without mapping to `coverage_signal` + `selection_confidence`.
- **Remove silent fallback** to `top[:limit]` — replace with explicit signals: use **`coverage_signal="empty"`** when there are zero candidates to score; **`weak`** when retrieval returned something but selection/coverage is poor; **`fragmented`** when match/parse issues — per §6.
- Emit **`empty`** whenever `scoped_candidates` is empty or selection yields no rows (distinct from **weak**).
- **Fix or remove** unused `seen_files`: either wire into prompt for diversity or delete parameter.

**Keep**

- JSON extraction, explored-location block, top-K cap.

---

### 4.5 `agent_v2/exploration/understanding_analyzer.py`

**Change**

- Prompt + `_coerce_understanding` to emit **only** semantic fields listed in spec; remove legacy branches that map to `status`/`needs`/`next_action` as control.
- `confidence` as `high|medium|low` string (or map to existing downstream).

**Remove**

- Analyzer as control plane.

---

### 4.6 `agent_v2/exploration/decision_mapper.py`

**Change**

- **Replace** `to_exploration_decision` with a single function, e.g. `decide_control(...)`, inputs:
  - `UnderstandingResult` (semantic),
  - selector result (confidence + coverage),
  - `relationship_hint` from `QueryIntent`,
  - `memory_summary` dict (for transparency only if required by product — **must not** add new branches unless spec extended),
  - `expanded_once: bool`, `refine_count: int`, `refine_limit: int`.
- Implement **exactly** the mandatory predicate order (§1).

**Remove**

- Inference of expand/refine from `needs` / `relevance` alone.

---

### 4.7 `agent_v2/exploration/exploration_engine_v2.py`

**Change**

- Replace ad-hoc loop with **bounded state machine**: one linear pipeline per “round” with explicit counters for `expanded_once`, `expansion_passes`, `refine_count`.
- After inspect + analyzer, call **only** `EngineDecisionMapper.decide_control`.
- **EXPAND:** call `GraphExpander` once per pass budget; enqueue targets; respect `max_expansion_depth` and `max_expansion_passes`.
- **REFINE:** increment `refine_count`; call `QueryIntentParser.parse` with feedback; re-run `_discovery` → scoper → selector → … up to limit.
- **STOP:** build result from memory.

**Remove**

- `_apply_gap_driven_decision` (or strip to logging-only gap recording).
- `_next_action` as separate policy.
- `_update_utility_and_should_stop` as stop authority.
- `_apply_refine_cooldown`, `_intent_oscillation_detected` coercion.
- Relaxed recovery as implicit discovery — fold into **REFINE** only if weak coverage and refine budget (mapper).

**Simplify**

- `_should_expand` / `_should_refine` → inlined into mapper outputs or thin helpers **without** extra predicates.
- Termination: single set of terminal reasons (`stop`, `max_refine`, `max_expand`, `empty_retrieval`, etc.).

**Keep**

- `_discovery` (multi-source, merge, rerank); **keyword injection only on REFINE-phase discovery** (§1.2), never on implicit/gap-driven paths.
- `_enqueue_ranked` pattern but fed by new selector output.
- Inspect + `_build_context_blocks_for_analysis`.
- `ExplorationWorkingMemory` writes **after** evidence-producing steps; **no** gap-driven routing.

---

### 4.8 `agent_v2/exploration/exploration_working_memory.py`

**Change**

- None required for control if engine stops using gaps for routing.
- Ensure `get_summary()` remains stable for **optional** display and REFINE context only.

**Remove**

- Any engine reads of gaps for **expand** decisions (today: gap-driven expansion).

**Keep**

- Evidence, gaps, relationships storage; caps.

---

### 4.9 `agent_v2/runtime/exploration_runner.py`

**Change**

- Wire updated selector/analyzer/mapper signatures.
- No new abstractions.

---

### 4.10 Tests

**Change**

- Update `tests/test_exploration_working_memory.py`, retrieval/exploration tests, fixtures referencing `UnderstandingResult` / selector behavior.
- Add mapper unit tests for predicate order and counters.

---

## 5. Heuristic cleanup (classification)

| Heuristic / mechanism | Classification |
|------------------------|----------------|
| `_apply_gap_driven_decision` | **REMOVE** (control) |
| `ENABLE_UTILITY_STOP` / `_update_utility_and_should_stop` | **REMOVE** (as stop authority); optional **KEEP** as telemetry only |
| `ENABLE_REFINE_COOLDOWN` / `_apply_refine_cooldown` | **REMOVE** |
| `_intent_oscillation_detected` | **REMOVE** |
| Relaxed recovery discovery (`relaxed_recovery`) | **REMOVE** or **SIMPLIFY** into mapper `REFINE` only |
| Initial intent retry block (dead) | **REMOVE** or wire to mapper-driven REFINE |
| Stagnation / duplicate explored key | **SIMPLIFY** — keep minimal drain behavior without extra “discovery retries” |
| Selector unmatched fallback to top-k | **REMOVE** / **SIMPLIFY** → explicit weak/fragmented signal |
| `seen_files` unused in selector | **REMOVE** or **SIMPLIFY** (wire) |
| `_next_action` helper | **REMOVE** |
| Pre/post `_should_stop` tied to “sufficient” | **SIMPLIFY** → mapper **STOP** only (or single post-mapper stop) |
| Keyword inject | **REMOVE** gap-driven injection; **KEEP** only for **REFINE-phase** `_discovery` after mapper returns REFINE (§1.2) |
| Multiple termination_reason strings | **SIMPLIFY** → enumerated set |

---

## 6. Data contract alignment

| Stage | Explicit inputs | Explicit outputs |
|-------|-------------------|------------------|
| QueryIntentParser | `instruction`, optional refine context | `QueryIntent` **including** `relationship_hint` |
| Retrieval | `QueryIntent`, `state`, `ex_state` (eligibility filters only). **Keyword injection:** `ex_state` inject fields **only** in REFINE-phase calls (§1.2). | `list[ExplorationCandidate]`, trace records |
| Scoper | `instruction`, candidates | `{ scoped_candidates }` |
| Selector | `instruction`, intent string, scoped candidates, `explored_location_keys`, `limit` | `selected_candidates`, `selection_confidence`, `coverage_signal` (`good\|weak\|fragmented\|empty`) |
| Inspect | target, `state` | `ReadPacket`, `ExecutionResult` |
| Analyzer | `instruction`, context blocks | Semantic JSON / `UnderstandingResult` |
| EngineDecisionMapper | analyzer output, selector output, `relationship_hint`, counters, refine limit | `STOP | EXPAND | REFINE` |
| Memory | writes from engine after steps | `get_summary()` for **REFINE** context only, not control |

**Hidden dependencies to eliminate**

- Unused `seen_files`.
- Gap-driven routing from memory.
- Informal `_score_breakdown` on candidates — either document or omit from control (retrieval ordering remains deterministic in list).

---

## 7. Edge case handling

**`coverage_signal` semantics (selector contract):**

| Value | Meaning |
|-------|--------|
| `good` | Enough breadth / diversity for the instruction; selection is usable. |
| `weak` | Retrieval or selection is thin, redundant, or low-confidence vs. intent — **candidate for REFINE** (subject to mapper + budget). |
| `fragmented` | Inconsistent or partial match (e.g. parse/partial index match) — loggable; mapper treats like **weak** for REFINE predicate unless product narrows further. |
| `empty` | **No candidates** to select (empty scoped list, or explicit empty selection) — **distinct from weak**; mapper: `coverage_signal == "empty"` and `refine_count < limit` → **REFINE**; else **STOP** (clear logging: `selector_empty_not_refining`). |

**Rationale:** `empty` vs `weak` avoids mixing “nothing to work with” vs “something weak/incomplete” in mapper and Langfuse.

| Case | Policy (to implement) |
|------|------------------------|
| **Empty retrieval** (discovery returns no files) | Selector should emit `coverage_signal="empty"`, `selected_candidates=[]`. Mapper: **REFINE** if `refine_count < limit`; else **STOP** with `no_results` (or equivalent). |
| **No candidates after scoper** | Selector input empty → emit **`empty`** (not `weak`). Mapper path same as row above. |
| **Selector `no_relevant_candidate` / null** | Map to **`weak`** or **`fragmented`** + low `selection_confidence` as appropriate; **never** silent top-k fallback. |
| **`is_sufficient=false` + `coverage_signal=good`** | **STOP** — intentional (§1.1); planner handles gaps. |
| **Weak coverage** | Mapper: `REFINE` if `refine_count < limit`; else **STOP**. |
| **Empty coverage** | Mapper: `REFINE` if `refine_count < limit`; else **STOP**. |
| **Analyzer low confidence** | Does **not** alone trigger REFINE; combined with **`weak`** / **`empty`** selector signal, mapper may **REFINE** per predicates above. |
| **Policy violation (non-read_snippet)** | **STOP** with explicit termination (existing policy). |

---

## 8. Open questions (blocking until answered)

1. **`confidence` (analyzer):** Spec shows string. Confirm mapping to planner/adapter: discrete bands only, or numeric preserved elsewhere?

2. **Selector-driven REFINE:** Resolved in-plan: **`weak`** and **`empty`** trigger the `coverage_signal` branch of the mapper (see §7). **`good`** never triggers REFINE from coverage alone. **`is_sufficient=false` + `coverage_signal=good`** → **STOP** (§1.1).

3. **EXPAND with `relationship_hint`:** Should expansion direction be **only** from `relationship_hint`, or may graph data override? (Today: expand direction hints + graph.)

4. **`memory_summary` in mapper:** Target says mapper uses “memory summary” in an earlier iteration; mandatory code block lists only `is_sufficient`, `relationship_hint`, `expanded_once`, `coverage_signal`, `refine_count`. Confirm whether **memory** is input to mapper **v1** or deferred to avoid flow leakage.

5. **`max_refine_cycles`:** Set to 1 or 2 as default in `agent_v2/config.py`?

6. **Backward compatibility:** External JSON for `FinalExplorationSchema` / API consumers — major version bump or additive fields only?

7. **Langfuse spans:** Preserve span names (`exploration.scope`, `exploration.select`, etc.) for observability continuity?

---

## 9. Implementation order (suggested)

1. Schemas + `relationship_hint`.
2. Selector structured output + remove silent fallback.
3. Analyzer semantic-only output + coercion cleanup.
4. New `EngineDecisionMapper.decide_control` + unit tests (include §1.1 STOP case; `empty` vs `weak` in §7).
5. Strip engine heuristics (gap, utility, cooldown, oscillation, relaxed recovery).
6. Rewire `_explore_inner` to bounded mapper-driven flow.
7. Runner + adapter + tests.
8. Documentation sweep (`MASTER_README` or architecture freeze pointer if required by repo policy).

---

## 10. Success criteria

- Single function (or cohesive module) that emits **only** STOP/EXPAND/REFINE from explicit inputs.
- No analyzer- or gap-driven branch that bypasses mapper.
- Counters for expansion passes and refine cycles enforced in one place.
- Selector and scoper outputs are **typed** and logged; no unused parameters.
- Bounded termination: no infinite or unbounded Re-discovery except within REFINE budget.

---

*End of plan.*
