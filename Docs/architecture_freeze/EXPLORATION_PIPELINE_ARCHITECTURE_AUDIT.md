# Exploration Pipeline — Full Architectural Audit

Code-backed architectural audit of the exploration phase in `agent_v2`
(`ExplorationEngineV2`, `ModeManager`, `ExplorationRunner`, planner boundary).

Audit version: **2026-03 Re-Audit (Principal Engineer)**.
This document keeps the prior full-audit presentation format while incorporating the latest findings.

---

## 0. Scope, Method, and Evidence Backbone

### Scope

- `agent_v2` exploration control and data planes
- planner gate boundary (exploration -> planning)
- exploration prompt model-routing (Option B)

### Method

- static audit with direct source inspection
- symbol-level tracing of execution path and stop conditions
- schema and config contract checks

### Source Index

- `agent_v2/runtime/runtime.py` (entrypoint and mode dispatch integration)
- `agent_v2/runtime/mode_manager.py` (planner gating and mode-specific flow)
- `agent_v2/runtime/exploration_runner.py` (v1/v2 routing, stage wiring, prompt model-name propagation)
- `agent_v2/exploration/exploration_engine_v2.py` (state machine and loop semantics)
- `agent_v2/exploration/query_intent_parser.py` (intent parsing contract)
- `agent_v2/exploration/exploration_scoper.py` (breadth reduction semantics)
- `agent_v2/exploration/candidate_selector.py` (single/batch selection semantics)
- `agent_v2/exploration/understanding_analyzer.py` (decision semantics and fallback)
- `agent_v2/exploration/inspection_reader.py` (bounded read extraction)
- `agent_v2/exploration/graph_expander.py` (expansion boundary and fallback path)
- `agent_v2/schemas/exploration.py` (Schema-4 contract and validators)
- `agent_v2/config.py` (single-source exploration budgets/policies)
- `agent/models/model_config.py` (task -> model key -> prompt display-model name)

---

## 1. Executive Assessment

The exploration architecture remains fundamentally sound: bounded state-machine execution with
a hard contract boundary before planning. Residual risk is concentrated in semantic/control
behavior rather than mechanical retrieval correctness.

### Current top risks

1. **Primary-symbol anchoring bias** can terminate around the first symbol instead of the most
   causally relevant symbol.
2. **Scoper empty-selection semantics** still pass through all candidates.
3. **Completion dual-path** (`pending_exhausted` => complete) can progress planning without an
   explicit `sufficient` decision.
4. **Cross-boundary dependency** remains (`agent_v2` importing legacy `agent.*` graph adapter).

### Confirmed improvements since prior audit

1. Exploration budgets/limits centralized in `agent_v2/config.py`.
2. `ModeManager` supports config-driven partial-plan gating (`allow_partial_for_plan_mode`).
3. Option B prompt routing is correctly wired for exploration stages using display-model names.

---

## 2. Control Plane vs Data Plane

### Control Plane (decision logic / gating)

- Mode routing and planner invocation centralized in `ModeManager.run(...)`.
  - Evidence: `mode_manager.py` L117-L126, L135-L263
- Exploration completion gate before planner execution:
  - `_exploration_is_complete(...)` with config override for bounded incomplete reasons.
  - Evidence: `mode_manager.py` L54-L83
- Exploration transition logic in v2 engine:
  - `_should_stop_pre`, `_should_stop`, `_should_expand`, `_should_refine`, `_next_action`
  - Evidence: `exploration_engine_v2.py` L918-L999

### Data Plane (bounded search artifacts)

- Mutable loop state in `ExplorationState`:
  - `pending_targets`, `explored_location_keys`, `excluded_paths`, `steps_taken`, `backtracks`
  - Evidence: `schemas/exploration.py` L141-L158
- Evidence accumulation and bounded transformation to `ExplorationResult`:
  - ordering/cap in engine: `exploration_engine_v2.py` L1032-L1058
  - schema bounds: `schemas/exploration.py` L93-L99

---

## 3. End-to-End Flow (Verified)

### 3.0 ASCII sequence calls (system-level)

```text
User Instruction
   |
   v
AgentRuntime.run(state, mode)
   |
   v
ModeManager.run(state, mode)
   |
   +--> exploration_runner.run(instruction, state)
   |       |
   |       +--> QueryIntentParser.parse(...)
   |       +--> ExplorationEngineV2.explore(...)
   |       |      |
   |       |      +--> discovery/search/scoping/selection/inspection/analyze/expand-refine
   |       |      +--> ExplorationResult(completion_status, items, summary, metadata)
   |       |
   |       '--> return ExplorationResult
   |
   +--> _exploration_is_complete(exploration_result)?
           |
           +--> NO  -> block planner (mode-specific failure path)
           |
           '--> YES -> planner.plan(..., exploration=ExplorationResult)
                         |
                         '--> execution / response
```

### 3.1 Runtime entry and mode dispatch

1. `AgentRuntime.run(...)` initializes `AgentState`, runtime metadata, and trace context.
   - Evidence: `runtime.py` L123-L155
2. `ModeManager.run(...)` routes execution by mode (`act`, `plan_execute`, `plan`, `deep_plan`).
   - Evidence: `mode_manager.py` L117-L126
3. In all exploration-backed modes, `exploration_runner.run(...)` executes before planner.
   - Evidence: `mode_manager.py` L150-L153, L201-L204, L238-L241

### 3.2 Planner gate boundary

1. Planner calls are blocked or allowed via `_exploration_is_complete(...)`.
   - Evidence: `mode_manager.py` L54-L83, L154-L158, L205-L209, L242-L246
2. Only after gate pass is `ExplorationResult` passed into planner.
   - Evidence: `mode_manager.py` L160-L166, L211-L217, L248-L254

Boundary conclusion: planner execution is explicitly downstream of exploration completion policy.

### 3.2.1 ASCII sequence calls (gate contract)

```text
ModeManager
   |
   +--> exploration = exploration_runner.run(...)
   |
   +--> ok = _exploration_is_complete(exploration)
   |
   +--> if not ok:
   |       raise/return "exploration_incomplete_for_mode"
   |
   '--> if ok:
           planner.plan(..., exploration)
```

### 3.3 ExplorationRunner orchestration workflow

1. Resolve model and prompt model-name mappings per exploration stage.
   - model selection: `get_model_for_task(...)`
   - prompt variant selection: `get_prompt_model_name_for_task(...)`
   - Evidence: `exploration_runner.py` L173-L179
2. Initialize stage components (intent parser, scoper, selector, analyzer, inspection, graph expansion).
3. Route execution to v2 engine (or v1 fallback path) using config-driven toggles.
   - Evidence: `exploration_runner.py` (v1/v2 branch points and stage wiring)

### 3.3.1 ASCII sequence calls (runner and stage wiring)

```text
ExplorationRunner.run(...)
   |
   +--> resolve stage model keys (task -> model)
   +--> resolve stage prompt model names (task -> display name)
   +--> construct stage components:
   |      parser / scoper / selector / analyzer / reader / expander
   |
   +--> if v2 enabled:
   |      ExplorationEngineV2.explore(...)
   |
   '--> else:
          legacy/v1 exploration path
```

### 3.4 V2 engine detailed workflow (`ExplorationEngineV2`)

#### A) Initialization phase

1. Create `ExplorationState` and loop-local trackers (`evidence`, `termination_reason`, stagnation counters).
2. Parse instruction into structured intent via `QueryIntentParser`.
3. Run initial discovery to seed candidates.
   - Evidence: `exploration_engine_v2.py` (state init + parser + initial discovery entry)

#### B) Discovery phase

1. Build symbol/regex/text query channels from intent with configured caps.
2. Execute batch search via dispatcher and merge per-location results.
3. Rank merged candidates and apply top-k limits.
4. Filter through enqueue eligibility (`_may_enqueue`) before queueing.
   - Evidence: `exploration_engine_v2.py` L600-L734 (discovery pipeline)

#### C) Scoping + selection phase

1. Reduce breadth via `ExplorationScoper.scope(...)` (when enabled and above threshold).
2. Rank scoped candidates via `CandidateSelector.select_batch(...)`.
3. Convert ranked candidates into `ExplorationTarget` entries and enqueue.
4. Handle no-candidate sentinel path from selector.
   - Evidence: `exploration_engine_v2.py` L735-L839

#### D) Main exploration loop phase

Loop is bounded by `EXPLORATION_MAX_STEPS`.

Per iteration:
1. Exit on terminal reason (`no_relevant_candidate`) or empty queue (`pending_exhausted`).
2. Pop target; skip duplicates with stagnation accounting.
3. Run pre-stop guard (`_should_stop_pre`).
4. Inspect target via bounded read path (`InspectionReader`).
5. Enforce read policy (`read_snippet` only).
6. Update explored sets, evidence identity keys, and primary symbol anchor.
7. Analyze meaningful evidence (`UnderstandingAnalyzer`) or synthesize partial decision.
8. Run post-analysis stop guard (`_should_stop`).
9. If continue: branch to expand/refine/next based on transition helpers.
   - Evidence: `exploration_engine_v2.py` L292-L498

#### D.1 ASCII sequence calls (single loop iteration)

```text
while steps_taken < EXPLORATION_MAX_STEPS:
   |
   +--> if terminal_reason or pending empty: break
   |
   +--> target = pop_next_target()
   +--> if duplicate(target): stagnation++; maybe stop; continue
   |
   +--> if _should_stop_pre(...): break
   |
   +--> inspected = InspectionReader.inspect(target)
   +--> enforce read policy (read_snippet only)
   +--> update state/evidence/anchor
   |
   +--> decision = UnderstandingAnalyzer.analyze(inspected) or partial fallback
   +--> if _should_stop(..., decision): break
   |
   +--> if _should_expand(...): expand + enqueue
   +--> elif _should_refine(...): discovery(refine) + enqueue
   '--> else: continue
```

#### E) Completion and result materialization phase

1. Resolve completion status and termination reason.
2. Build bounded, ordered `ExplorationResult` items.
3. Populate metadata and summary fields for planner handoff.
   - Evidence: `exploration_engine_v2.py` L500-L588, L1032-L1058

### 3.5 Stop-condition and transition workflow (decision table view)

- **Stop-pre**: short-circuits before heavy inspection work when terminal preconditions hold.
- **Stop-post**: evaluates sufficiency after inspection/analyzer output.
- **Expand**: triggered when relationship/context expansion criteria are met.
- **Refine**: triggered when exploration needs another discovery pass under bounded backtracks.
- **Next**: default path continues queue consumption.
  - Evidence: `exploration_engine_v2.py` L918-L999

### 3.6 ASCII sequence calls (decision helper flow)

```text
inspect/analyze result
   |
   +--> _should_stop_pre?  ----YES----> stop
   |            |
   |            NO
   |
   +--> _should_stop?      ----YES----> stop
   |            |
   |            NO
   |
   +--> _should_expand?    ----YES----> expand -> enqueue -> next loop
   |            |
   |            NO
   |
   +--> _should_refine?    ----YES----> refine discovery -> enqueue -> next loop
   |            |
   |            NO
   |
   '--> _next_action -> continue next pending target
```

---

## 4. Invariants and Contracts

### Hard-enforced invariants

- **Step boundedness** via `EXPLORATION_MAX_STEPS`
  - Evidence: `exploration_engine_v2.py` L292, L963-L986
- **Backtrack boundedness**
  - Evidence: `exploration_engine_v2.py` L949-L954
- **Location dedup** `(canonical_path, symbol)`
  - Evidence: `exploration_engine_v2.py` L300-L309, L846-L855
- **Read policy enforcement** (`read_snippet` required)
  - Evidence: `exploration_engine_v2.py` L362-L366
- **Schema-4 structural constraints** (`items<=6`, `total_items` exact, summary consistency)
  - Evidence: `schemas/exploration.py` L57-L67, L93-L99

### Soft / policy-driven invariants

- `completion_status` semantics drive planner gate behavior.
  - Evidence: engine sets at `exploration_engine_v2.py` L500-L517; gate reads at `mode_manager.py` L54-L83
- Config may allow planning to continue on bounded incomplete reasons.
  - Evidence: `mode_manager.py` L73-L81; `config.py` L74-L77, L91-L105

---

## 5. Source-Backed Findings (Severity Ordered)

### Critical

1. **Primary symbol anchoring can bias completion**
   - `primary_symbol` is first-write-wins and not revised when wrong-target exclusions occur.
   - Evidence:
     - set once: `exploration_engine_v2.py` L370-L372
     - wrong-target exclusion: L417-L418
     - stop dependency on anchor: L966-L976, L988-L999
   - Risk: early/incorrect completion around a stale anchor.

### High

2. **Scoper empty-selection remains pass-through**
   - `selected_indices=[]` returns all candidates.
   - Evidence: `exploration_scoper.py` L132-L146
   - Risk: conservative model output becomes aggressive downstream queue fill.

3. **Completion has dual semantic path**
   - `pending_exhausted` can set completion without explicit sufficiency proof.
   - Evidence: `exploration_engine_v2.py` L500-L515
   - Risk: planner receives "complete" exploration with weaker semantic confidence.

4. **Legacy boundary leak in graph expansion**
   - v2 layer imports `agent.retrieval.adapters.graph.fetch_graph`.
   - Evidence: `graph_expander.py` L35-L36
   - Risk: cross-boundary coupling and wider change blast radius.

### Medium

5. **Refine path can spend backtracks without net-new targets**
   - Re-discovery over same intent/state with dedup reapplied.
   - Evidence: `exploration_engine_v2.py` L478-L498, L600-L734
   - Risk: bounded but potentially inefficient convergence.

6. **Stagnation counter conflates non-progress causes**
   - Duplicate dequeues and repeated evidence keys share one counter.
   - Evidence: `exploration_engine_v2.py` L302-L307, L420-L423
   - Risk: weaker telemetry and diagnosis for "stalled".

7. **Provenance flattening in inspection-stage synthetic candidate**
   - Non-expansion paths are normalized to `source="grep"` despite richer upstream channeling.
   - Evidence: `exploration_engine_v2.py` L321-L326 vs L583-L591
   - Risk: source attribution drift in analysis/debugging.

### Low/Medium

8. **`current_target` and `findings` weakly consumed in v2 flow**
   - `current_target` set but minimally used; `findings` not populated by v2 engine.
   - Evidence: set at `exploration_engine_v2.py` L299; schema fields at `schemas/exploration.py` L144, L152
   - Risk: state-model drift and maintenance ambiguity.

9. **Potential config divergence (v1 vs v2 step knobs)**
   - `EXPLORATION_STEPS` (runner v1) and `EXPLORATION_MAX_STEPS` (v2) are separate.
   - Evidence: `config.py` L26-L27; `exploration_runner.py` L68, L234; `exploration_engine_v2.py` L292
   - Risk: inconsistent behavior across modes under differing overrides.

---

## 6. Prompt Routing Audit (Option B)

Status: **Correctly wired for exploration stages**.

1. Stage prompt `model_name` uses display-model resolver, not model-key resolver.
   - Evidence: `exploration_runner.py` L175-L179 (import at L32)
2. Resolver path is task -> model key -> display name (with fallback).
   - Evidence: `model_config.py` `get_prompt_model_name_for_task(...)`
3. Loader now resolves model-specific variant paths as intended:
   - `prompt_versions/<name>/models/<normalized-display-name>/v1.yaml`

Residual risk: display-name changes in `models_config.json` require matching prompt paths;
otherwise runtime falls back to base prompt unless tests catch drift.

---

## 7. What Changed Since Earlier Audit

1. Config centralization materially improved exploration constants and policy knobs.
2. Planner gate now supports explicit policy for partial exploration continuation.
3. Prompt variant routing moved to display-name alignment (Option B), closing key-vs-display mismatch.

---

## 8. Recommendations (Principal Engineer)

### P0 (Immediate)

1. **Fix scoper empty-selection semantics**
   - Valid empty selection should produce empty scoped output (or explicit no-candidate), not pass-through.
2. **Stabilize completion semantics**
   - Require either explicit sufficiency or explicit policy allowing queue-exhaustion completeness.
   - Encode policy in config and lock with tests.

### P1 (Near-term)

3. **Re-anchor primary symbol**
   - Permit anchor reassignment when current anchor path is excluded as wrong target.
4. **Isolate graph boundary**
   - Introduce a v2-side adapter seam; remove direct legacy import in engine-facing code.
5. **Split stagnation accounting**
   - Track duplicate-dequeue and duplicate-evidence as separate counters and expose both in metadata.

### P2 (Hardening)

6. **Harmonize v1/v2 step knobs**
   - Define one authoritative exploration-step policy path.
7. **Clean weakly-used state fields**
   - Either consume `findings/current_target` in planner handoff/telemetry or deprecate.

---

## 9. Verification Checklist

- [x] Mode gate behavior verified in `mode_manager.py`
- [x] Exploration loop behavior verified in `exploration_engine_v2.py`
- [x] Schema invariants verified in `schemas/exploration.py`
- [x] Prompt model routing verified in `exploration_runner.py` + `model_config.py`
- [x] Config centralization verified in `config.py`
- [x] Cross-boundary dependency verified in `graph_expander.py`

---

## 10. Quick Reference (Where to Inspect First)

- Planner gate and mode behavior: `agent_v2/runtime/mode_manager.py`
- Runtime entry + state setup: `agent_v2/runtime/runtime.py`
- Exploration state machine + stop conditions: `agent_v2/exploration/exploration_engine_v2.py`
- Prompt model-name propagation: `agent_v2/runtime/exploration_runner.py`
- Model routing resolver: `agent/models/model_config.py`
- Schema contracts: `agent_v2/schemas/exploration.py`
- Config authority: `agent_v2/config.py`

---
