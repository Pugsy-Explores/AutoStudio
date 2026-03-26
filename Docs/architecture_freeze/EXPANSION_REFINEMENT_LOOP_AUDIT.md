# Expansion / Refinement Loop Audit

Date: 2026-03-26  
Scope: `agent_v2` exploration loop only (architecture, control flow, behavior)

## Executive Verdict

The current expansion/refinement loop is **functional but not production-grade**.  
It has solid scaffolding (state machine, limits, dedupe, stop reasons), but expansion quality is still naive in key places:

- analyzer signals are only partially used for control
- expansion path utility is not scored
- depth semantics are weak
- some completion paths can mark runs complete with shallow evidence

### Senior Engineer Converged Improvements (Critical)

1. Make expansion **gap-driven** from analyzer gaps (not relevance-only branching)
2. Add **ExpansionPrioritizer** before enqueue
3. Add **utility feedback loop** (`delta understanding`) with diminishing-returns stop
4. Fix `pending_exhausted` completion semantics
5. Prevent duplicates **before** enqueue (not generate then reject)
6. Strengthen semantic depth semantics (distance-aware)
7. Keep only 3 expansion modes: graph, semantic search, refinement

---

## Step 1: Current Module Flow

### Relevant Files

- `agent_v2/runtime/runtime.py` - runtime entry
- `agent_v2/runtime/mode_manager.py` - mode orchestration and planner gate
- `agent_v2/runtime/exploration_runner.py` - exploration execution entry
- `agent_v2/exploration/exploration_engine_v2.py` - core loop
- `agent_v2/exploration/query_intent_parser.py` - intent + retry refinement hook
- `agent_v2/exploration/understanding_analyzer.py` - understanding stage
- `agent_v2/exploration/decision_mapper.py` - understanding -> decision mapping
- `agent_v2/exploration/graph_expander.py` - expansion adapter
- `agent_v2/exploration/inspection_reader.py` - bounded read execution
- `agent_v2/exploration/candidate_selector.py` - ranking/selection
- `agent_v2/exploration/exploration_scoper.py` - optional scope reduction
- `agent_v2/schemas/exploration.py` - loop contracts/schemas
- `agent_v2/config.py` - loop limits and thresholds

### Current Expansion Flow

1. `AgentRuntime` -> `ModeManager` -> `ExplorationRunner` -> `ExplorationEngineV2.explore`
2. Parse query intent (`QueryIntentParser.parse`)
3. Run discovery (`_run_discovery_traced` -> `_discovery`)
4. Optional query retry (single retry, accepted only on candidate/top-score improvement)
5. Queue candidates via `_enqueue_ranked`
6. Loop:
   - pop target
   - inspect via bounded read (`inspect_packet`, must be `read_snippet`)
   - analyze (`UnderstandingAnalyzer`)
   - map decision (`EngineDecisionMapper`)
   - branch: expand / refine / stop
7. Stop via termination reasons and build `ExplorationResult`
8. Planner receives result through `ModeManager`

### Entry Points

- `AgentRuntime.explore`
- `ModeManager.run`
- `ExplorationRunner.run`
- `ExplorationEngineV2.explore`

### Control Paths

- no relevant candidate -> `no_relevant_candidate`
- partial/high relevance -> expand path
- wrong target/low relevance -> refine path
- sufficient -> stop path
- queue drained -> `pending_exhausted`
- duplicate/no-new-evidence pressure -> `stalled`
- step cap -> `max_steps`

---

## Step 2: Behavioral Audit (Live Runs)

Method: executed real repo-derived runs and forced analyzer outputs per case to isolate loop behavior.

### Case A (partial understanding -> should expand)

- Instruction: trace callers/callees of `PlanExecutor`
- Forced analyzer: `relevance=high`, `evidence_sufficiency=partial`
- Observed:
  - `expand_calls=2`
  - `refine_calls=0`
  - `iterations=2`
  - `termination_reason=pending_exhausted`
  - duplicate rejections occurred (`already_explored=2`)

### Case B (insufficient -> should refine)

- Instruction: understand retry behavior in `QueryIntentParser`
- Forced analyzer: `relevance=low`, `evidence_sufficiency=insufficient`
- Observed:
  - `expand_calls=0`
  - `refine_calls=2`
  - `iterations=5`
  - `termination_reason=max_steps`
  - repeated enqueue rejection signals (`already_explored`, `already_pending`)

### Case C (sufficient -> should stop)

- Instruction: find `ModeManager` definition
- Forced analyzer: `sufficient=true`
- Observed:
  - `expand_calls=0`
  - `refine_calls=0`
  - `iterations=1`
  - quick completion path (`pending_exhausted`)

---

## Step 3: Hypothesis Validation

1. **No structured expansion strategy (graph vs search vs refine unclear)**  
   - Status: **partial**  
   - Evidence: branching exists, but expansion strategy is shallow and not utility-planned.

2. **Expansion not grounded in analyzer signals (gaps/findings ignored)**  
   - Status: **true**  
   - Evidence: decision mapping primarily uses `relevance/sufficient`; no direct gap-driven target prioritization.

3. **No prioritization of expansion paths**  
   - Status: **true**  
   - Evidence: expanded targets are queued with dedupe, not utility-ranked by gap coverage.

4. **Risk of uncontrolled expansion (no depth/budget limits)**  
   - Status: **partial**  
   - Evidence: global limits exist, but depth semantics in graph expansion are weak.

5. **Repeated exploration of same files/symbols**  
   - Status: **partial**  
   - Evidence: exact revisits are blocked, but duplicate candidates are still generated then rejected.

6. **Weak or missing stopping criteria**  
   - Status: **partial**  
   - Evidence: stopping criteria exist, but `pending_exhausted` can yield complete status with shallow coverage.

7. **No scoring of expansion usefulness**  
   - Status: **true**  
   - Evidence: no branch utility score to decide whether expansion/refine improved understanding.

---

## Step 4: Control & State Audit

- Visited set: **yes** (`explored_location_keys`), plus `seen_files`, `seen_symbols`, `expanded_symbols`
- Depth tracking: **partial** (`max_depth` passed, weak traversal semantics)
- Iteration limit: **yes** (`EXPLORATION_MAX_STEPS`)
- Backtrack/refine limit: **yes** (`EXPLORATION_MAX_BACKTRACKS`)
- Stagnation control: **yes** (`EXPLORATION_STAGNATION_STEPS`)
- Determinism:
  - loop orchestration: **deterministic**
  - parser/selector/scoper/analyzer internals: **LLM-driven**

---

## Step 5: Output Contract Audit

### Inputs into Expansion/Refinement

- instruction
- parsed intent (`QueryIntent`)
- current target and loop state
- analyzer output (mapped to `ExplorationDecision`)

### Outputs Produced

- next actions (`expand`, `refine`, `stop`)
- new `ExplorationTarget` entries (from expansion/discovery)
- final `ExplorationResult` metadata (`termination_reason`, counts, completion status)

### Contract Gaps

- analyzer `knowledge_gaps` are not strongly wired into expansion target selection
- no explicit schema for expansion utility scoring
- limited clarity around how expansion usefulness is measured over time

---

## Step 6: Failure Modes

- refine churn ending in `max_steps` without convergence
- duplicate/low-value expansion targets generated then dropped (wasted work)
- brittle relation typing in expander (`caller`/`callee` classification heuristics)
- possible over-optimistic completion when queue drains

---

## Step 7: Recommended Design

### Structured Expansion Modes

- **graph mode**: typed caller/callee/related expansion with stronger relation semantics
- **semantic mode**: fallback when graph confidence is weak
- **refinement mode**: bounded query rewrite and rediscovery strategy

### Gap-Driven Expansion (Primary Control Rule)

- Current issue: expansion is primarily a derivative of coarse relevance/status.
- Converged rule: expansion must be driven by `analyzer.knowledge_gaps` first.
- Routing examples:
  - gap indicates missing caller/callee chain -> use **graph mode**
  - gap indicates missing config/dependency flow -> use **semantic mode**
  - gap indicates wrong abstraction/target -> use **refinement mode**
- If no actionable gap is present, do not expand aggressively; prefer controlled stop/refine.

### Prioritization Strategy

- add `ExpansionPrioritizer` before enqueue:
  - `score = f(gap_coverage, novelty, relation_confidence)`
  - rank by score descending
  - drop below floor when queue pressure is high
- rank expansion candidates by analyzer gap coverage
- prefer unseen high-signal symbols/files
- penalize paths with high dedupe rejection history

### Budget Controls

- max depth
- max nodes per expand
- max refine rounds
- max total expanded targets
- branch utility floor (stop low-gain branches)

### Utility Feedback Loop

- track per-step utility delta (`delta_understanding`) from analyzer outputs
- persist rolling utility history in exploration state
- if utility delta ~ 0 for `K` consecutive steps:
  - stop branch
  - or escalate to alternate mode once, then stop
- intent: eliminate refine churn and useless expansions

### Stopping Criteria

- stop when sufficient + primary objective evidence satisfied
- stop on diminishing returns (`no meaningful evidence delta` for K steps)
- stop on branch utility drop below threshold
- update queue-exhaustion semantics:
  - `pending_exhausted AND sufficient` -> complete
  - `pending_exhausted AND not sufficient` -> incomplete/escalate

### State Tracking Improvements

- retain `(file, symbol, read_source)` visited identity
- add relation-edge visited tracking
- add per-target attempt counts and utility history
- move duplicate prevention earlier:
  - edge hash + target key check before enqueue
  - avoid generating candidates that are already guaranteed to be rejected
- strengthen depth semantics using semantic distance:
  - same file -> depth 0
  - same module -> depth 1
  - cross module -> depth 2

---

## Step 8: Implementation Plan

1. Wire `analyzer.knowledge_gaps` directly into expansion mode selection (no new module yet).
2. Add minimal prioritization (`gap_coverage + novelty`) before queue insertion.
3. Add utility tracking and delta-based stop (`delta_understanding` with `K`-step threshold).
4. Fix completion condition for `pending_exhausted` (complete only when sufficient).
5. Add pre-enqueue duplicate prevention (target hash + seen relation-edge registry).
6. Add `ExpansionPrioritizer` full scoring (`gap_coverage`, `novelty`, `relation_confidence`).
7. Add semantic depth policy and enforce per-depth budgets.
8. Add regression tests for partial/insufficient/sufficient + churn + queue exhaustion.

---

## Structured Output (Requested Format)

```json
{
  "current_flow": {
    "status": "functional_with_structural_gaps",
    "entry_points": [
      "AgentRuntime.explore",
      "ModeManager.run",
      "ExplorationRunner.run",
      "ExplorationEngineV2.explore"
    ],
    "control_paths": [
      "expand",
      "refine",
      "stop",
      "pending_exhausted",
      "stalled",
      "max_steps"
    ]
  },
  "validated_hypotheses": [
    {
      "hypothesis": "No structured expansion strategy (graph vs search vs refine unclear)",
      "status": "partial",
      "evidence": "Branching exists, but strategy depth/utility planning is limited."
    },
    {
      "hypothesis": "Expansion not grounded in analyzer signals (gaps/findings ignored)",
      "status": "true",
      "evidence": "Gap semantics not used as direct expansion prioritization signals."
    },
    {
      "hypothesis": "No prioritization of expansion paths",
      "status": "true",
      "evidence": "No utility rank before enqueue."
    },
    {
      "hypothesis": "Risk of uncontrolled expansion (no depth/budget limits)",
      "status": "partial",
      "evidence": "Caps exist; depth semantics are weak."
    },
    {
      "hypothesis": "Repeated exploration of same files/symbols",
      "status": "partial",
      "evidence": "Revisit blocked, but duplicate generation still occurs."
    },
    {
      "hypothesis": "Weak or missing stopping criteria",
      "status": "partial",
      "evidence": "Stops exist; `pending_exhausted` can still be optimistic."
    },
    {
      "hypothesis": "No scoring of expansion usefulness",
      "status": "true",
      "evidence": "No branch utility score to guide continuation."
    }
  ],
  "control_gaps": [
    "weak depth semantics",
    "missing utility scoring",
    "limited analyzer-gap grounding",
    "late duplicate rejection",
    "narrow retry acceptance logic"
  ],
  "failure_modes": [
    "refine churn to max_steps",
    "duplicate expansion targets",
    "relation classification brittleness",
    "possible shallow complete status on queue exhaustion"
  ],
  "recommended_design": {
    "modes": ["graph", "semantic_search", "refinement"],
    "planner": "analyzer.gaps -> mode selection",
    "prioritization": "score = f(gap_coverage, novelty, relation_confidence)",
    "budget_controls": ["max_depth", "max_nodes", "max_iterations", "max_refines", "utility_floor"],
    "stopping": [
      "sufficient+objective_met",
      "diminishing_returns",
      "utility_floor",
      "pending_exhausted AND sufficient"
    ],
    "state_tracking": [
      "visited_nodes",
      "visited_edges",
      "attempt_counts",
      "branch_utility_history",
      "pre_enqueue_duplicate_hashes"
    ]
  },
  "implementation_plan": [
    "wire analyzer.gaps into expansion mode selection",
    "add minimal gap+novelty prioritization",
    "add utility delta tracking and diminishing-returns stop",
    "fix pending_exhausted completion semantics",
    "add pre-enqueue duplicate filtering",
    "add full ExpansionPrioritizer with relation confidence",
    "enforce semantic depth policy and budgets",
    "add branch-behavior regression tests"
  ]
}
```

---

## Converged Design (Final)

```text
Analyzer -> gaps
        -> ExpansionPlanner (mode + priority seed)
        -> ExpansionPrioritizer
        -> Queue
        -> Execution
        -> Utility Tracker
        -> Stop / Continue
```

Notes:
- Keep system simple: only 3 modes (`graph`, `semantic_search`, `refinement`).
- Do not introduce extra modes unless required by measured failures.
