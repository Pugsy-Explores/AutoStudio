# Exploration Signal-Driven Refactor Plan

Scope: **refactor only** (no redesign), using existing loop, analyzer, memory schema, and expander contracts.

---

## Step 1 - Exact Code Points (Located)

- `agent_v2/exploration/exploration_engine_v2.py`
  - `_target_priority_score`
  - `_enqueue_targets`
  - `_enqueue_ranked`
  - `_apply_gap_driven_decision`
  - `_should_expand`
  - refine branch in `_explore_inner` (`if self._should_refine(...)`)
  - expansion branch in `_explore_inner` (`if self._should_expand(...)`)
  - memory write/access touchpoints:
    - `memory.add_gap(...)`
    - `memory.add_relationships_from_expand(...)`
    - `memory.add_expansion_evidence_row(...)`
    - `ex_state.attempted_gap_targets` read/write in `_prefilter_expansion_targets`
    - `ex_state.gap_bundle_key_for_expansion`, `ex_state.expand_direction_hint`, `ex_state.attempted_gaps`
- `agent_v2/exploration/graph_expander.py`
  - `GraphExpander.expand` (called by engine; internals intentionally unchanged per requirement)

---

## Step 8 - Output Plan (Mandatory Format)

```json
{
  "functions_to_modify": [
    "agent_v2/exploration/exploration_engine_v2.py::_enqueue_targets",
    "agent_v2/exploration/exploration_engine_v2.py::_explore_inner (expand branch post-expand filtering)",
    "agent_v2/exploration/exploration_engine_v2.py::_explore_inner (refine branch intent rewrite)",
    "agent_v2/exploration/exploration_engine_v2.py::_apply_gap_driven_decision",
    "agent_v2/exploration/exploration_engine_v2.py::_should_refine (trigger tightening)",
    "agent_v2/exploration/exploration_engine_v2.py::_target_priority_score (compatibility stub or categorical adapter)"
  ],
  "exact_changes": [
    {
      "function": "_enqueue_targets",
      "before": "Uses edge dedupe + _may_enqueue + novelty-biased score sort.",
      "after": "Replace numeric scoring order with deterministic categorical tiers: Tier 1 = gap-aligned relation bucket (based on active direction/gap type), Tier 2 = related fallback bucket, Tier 3 = remaining eligible targets. Within each tier, preserve stable insertion order. Apply hard suppression before tiering: (a) skip if (gap_bundle_key, file, symbol) exists in attempted_gap_targets, (b) skip if same relation edge already covered for active gap. Keep existing queue and dedupe calls.",
      "reason": "Implements signal-driven traversal using only categorical signals and memory constraints."
    },
    {
      "function": "_explore_inner (expand branch after graph_expander.expand call)",
      "before": "Expanded targets are prefiltered for dedupe/attempts, but direction_hint is effectively soft because graph output may include related/cross-bucket candidates.",
      "after": "Add strict engine-side routing from existing expansion buckets: if direction_hint=callers keep only callers when non-empty, else keep related only; if direction_hint=callees keep only callees when non-empty, else keep related only; if direction_hint=both keep callers+callees, fallback to related only when both empty. Do not blend primary with fallback buckets.",
      "reason": "Direction becomes a hard routing decision while keeping GraphExpander unchanged."
    },
    {
      "function": "_explore_inner (refine branch)",
      "before": "Refine mostly reruns discovery on current intent.",
      "after": "Refine always performs reinterpretation: call intent_parser.parse with previous_queries=intent and failure_reason set deterministically by trigger (`wrong_target` -> `low_relevance`; stagnation/no-improvement -> `insufficient_context`; explicit low relevance remains `low_relevance`). Replace current intent before discovery call.",
      "reason": "Refine becomes true intent rewrite, not retry replay."
    },
    {
      "function": "_apply_gap_driven_decision",
      "before": "Maps accepted gaps to next_action/needs with mixed legacy paths.",
      "after": "Tighten to deterministic mapping using existing categories: caller gap -> next_action=expand + direction callers; callee/flow gap -> expand with corresponding direction; usage/definition/config gaps -> refine with keyword inject retained. Keep accepted-gap filtering and existing fields only.",
      "reason": "Ensures gap -> direction -> action is categorical and explicit."
    },
    {
      "function": "_should_refine (trigger tightening)",
      "before": "Refine when action=refine or wrong_target (bounded by backtracks).",
      "after": "Keep existing bound; restrict refine triggers to deterministic set: wrong_target OR explicit low_relevance path OR stagnation/no_improvement signal. No random/always refine behavior.",
      "reason": "Aligns refine entry with convergence signals only."
    },
    {
      "function": "_target_priority_score",
      "before": "Numeric novelty score helper.",
      "after": "Do not use numeric ranking semantics. Either (a) convert helper to categorical rank key adapter used by `_enqueue_targets`, or (b) bypass in `_enqueue_targets` and keep as compatibility no-op for untouched call sites.",
      "reason": "Enforces no-floats/no-weights rule while minimizing structural churn."
    }
  ],
  "no_change_confirmations": [
    "No change to loop structure (analyze -> decision -> expand/refine/stop).",
    "No change to `_should_expand` gate logic (kept as-is).",
    "No change to analyzer implementation (`UnderstandingAnalyzer`) or decision mapper architecture.",
    "No change to `GraphExpander.expand` signature or internals.",
    "No new modules, classes, abstractions, or frameworks.",
    "No change to memory schema (`ExplorationWorkingMemory`, `ExplorationState` fields).",
    "No new config flags required for this refactor.",
    "No change to dispatcher/tool contracts (`search_batch`, `read_snippet`, `graph_lookup`)."
  ],
  "risk_assessment": [
    "Risk: Strict bucket routing can reduce recall when graph labels are incomplete. Mitigation: allow fallback bucket only when primary bucket is empty (never blended).",
    "Risk: Deterministic refine triggers may under-trigger in edge cases. Mitigation: include stagnation/no_improvement as explicit refine trigger.",
    "Risk: Hard no-revisit constraints can hide legitimate second-pass reads. Mitigation: scope constraints by (gap_bundle_key, file, symbol) and existing relation-edge identity, not global file ban.",
    "Risk: Removing numeric ordering changes queue shape. Mitigation: retain stable ordering and existing dedupe/guard rails for predictability."
  ],
  "test_plan": [
    "Unit: extend `tests/test_exploration_engine_v2_control_flow.py` for direction enforcement in engine branch (callers/callees keep rules and related fallback).",
    "Unit: add refine-branch test asserting reinterpretation happens before discovery and uses deterministic failure_reason by trigger.",
    "Unit: add tier-order test proving categorical enqueue order: gap-aligned bucket > related fallback > remaining eligible.",
    "Unit: add hard-constraint suppression test for attempted gap-target triad and already-covered relation edges (no revisit).",
    "Regression: existing tests for `_should_expand` remain unchanged and passing.",
    "Regression: `_apply_gap_driven_decision` tests updated to deterministic categorical mapping without numeric scoring assumptions.",
    "Behavioral: run existing live eval script `scripts/live_expansion_refinement_loop_eval.py` and confirm fewer redundant hops and faster convergence under same step/backtrack caps."
  ]
}
```

