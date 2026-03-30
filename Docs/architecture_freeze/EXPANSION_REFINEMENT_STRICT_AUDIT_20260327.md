# Expansion / Refinement Strict Audit (2026-03-27)

## Executive Verdict

- **Overall:** The loop is **moderately robust**, but still **naive in expansion semantics**.
- **Strong areas:** hard controls, deterministic loop structure, stopping gates, duplicate prevention hooks.
- **Weak areas:** weak gap-to-target semantics, shallow practical graph depth, coarse usefulness attribution.

## Current Flow

### Current Expansion Flow

1. `ModeManager._run_explore_plan_execute/_run_plan/_run_deep_plan` -> `ExplorationRunner.run`
2. `ExplorationRunner.run` (v2) -> `ExplorationEngineV2.explore` -> `_explore_inner`
3. `QueryIntentParser.parse` (initial) -> `_discovery` (symbol/regex/text batched search)
4. Optional query retry: `_classify_initial_failure_reason` -> parser retry -> `_discovery`
5. `_enqueue_ranked` (optional scoper + selector) -> `pending_targets`
6. Loop per target: dedupe -> `inspect_packet(read_snippet)` -> analyzer -> decision mapper
7. Decision adjustment: gap-driven decision + utility stop check + refine cooldown
8. Branch:
  - **Expand:** `GraphExpander.expand(max_nodes,max_depth)` -> enqueue expanded targets
  - **Refine:** discovery rerun + enqueue ranked
  - **Stop:** based on stop logic
9. Terminate on explicit reasons (`max_steps`, `pending_exhausted`, `stalled`, `primary_symbol_sufficient`, etc.)
10. Return `ExplorationResult` with completion metadata

### Entry Points

- `ModeManager._run_explore_plan_execute`: primary ACT entry; planner is gated by exploration completion.
- `ExplorationRunner.run`: routes into v2 engine when enabled.
- `ExplorationEngineV2.explore`: main expansion/refinement control loop entry.

### Control Paths

- **Analyzer -> loop:** `UnderstandingAnalyzer.analyze` -> `EngineDecisionMapper.to_exploration_decision` -> `_next_action`
- **Expand path:** `_should_expand` -> `GraphExpander.expand` -> `_enqueue_targets`
- **Refine path:** `_should_refine` -> `_run_discovery_traced(phase=refine)` -> `_enqueue_ranked`
- **Stop path:** `_should_stop_pre/_should_stop/_update_utility_and_should_stop`

### Graph Traversal Behavior

- Primary: `GraphExpander.expand` uses `fetch_graph(symbol, top_k=max_nodes)` and maps to callers/callees/related.
- Fallback: if graph returns empty, it runs search with `"<symbol> callers callees definition"`.
- Important gap: `max_depth` is passed through but traversal is effectively shallow in current adapter behavior.

### Query Refinement Hooks

- `_classify_initial_failure_reason(...)`
- `EXPLORATION_MAX_QUERY_RETRIES`
- `QueryIntentParser.parse(previous_queries, failure_reason)`
- `_has_retry_improvement` + telemetry emission

## Behavioral Audit (Live Evidence)

- **Run command:** `python3 scripts/live_expansion_refinement_loop_eval.py`

### Case A: Partial understanding -> should expand

- **Mapped run:** `gap_driven_expansion`
- **Actions:** `expand`
- **Iterations:** `1`
- **Repeated work:** `dedupe_skips=1`
- **Termination:** `primary_symbol_sufficient`
- **Result:** expected expansion behavior observed

### Case B: Insufficient -> should refine

- **Mapped run:** `refine_cooldown`
- **Actions:** `refine -> expand -> expand`
- **Iterations:** `2`
- **Repeated work:** `dedupe_skips=1`
- **Termination:** `primary_symbol_sufficient`
- **Cooldown enforcement:** `cooldown_forced_expands=1`
- **Result:** refine path is reachable and controlled by cooldown logic

### Case C: Sufficient -> should stop

- **Mapped run:** `gap_filtering`
- **Actions:** `expand`
- **Iterations:** `1`
- **Termination:** `primary_symbol_sufficient`
- **Result:** sufficient-stop behavior observed

### Overall Run Summary

- Cases run: `5`
- Utility-based early stops: `1`
- Cases with dedupe skips: `5`
- Stable action paths across repeated runs: `5`

## Hypotheses Validation


| Hypothesis                                                           | Status      | Evidence                                                                                                                                                 |
| -------------------------------------------------------------------- | ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| No structured expansion strategy (graph vs search vs refine unclear) | **false**   | Explicit branches exist via `_should_expand/_should_refine`, graph expansion, and refine-discovery loop; confirmed in live runs.                         |
| Expansion not grounded in analyzer signals                           | **partial** | Gap-driven bridge exists (`_apply_gap_driven_decision`), but expander input is still only `(symbol, file)` (no typed gap payload).                       |
| No prioritization of expansion paths                                 | **partial** | Local prioritization exists (`_target_priority_score`), but no gap-weighted graph/path-level prioritization.                                             |
| Uncontrolled expansion risk (no depth/budget limits)                 | **false**   | Hard limits exist: steps, backtracks, node/depth caps, stagnation threshold, utility no-improvement streak.                                              |
| Repeated exploration of same files/symbols                           | **partial** | Duplicate prevention exists (`explored_location_keys`, queue dedupe, edge dedupe), but live runs show persistent pre-filter redundancy (`dedupe_skips`). |
| Weak/missing stopping criteria                                       | **false**   | Multiple explicit stop criteria implemented and observed (`no_improvement_streak`, `primary_symbol_sufficient`, etc.).                                   |
| No expansion usefulness scoring                                      | **partial** | Global utility signal exists, but no per-path/per-edge usefulness score used for future expansion selection.                                             |


## Control and State Audit

- **Visited set:** present (`explored_location_keys`, `seen_files`, `seen_symbols`, `seen_relation_edges`)
- **Depth tracking:** partial (`max_depth` exists in config/signature, but traversal is not fully depth-realized)
- **Iteration limits:** present (`EXPLORATION_MAX_STEPS`, `EXPLORATION_MAX_BACKTRACKS`, `EXPLORATION_STAGNATION_STEPS`)
- **Deterministic vs LLM-driven:** hybrid (deterministic loop; LLM-based intent parsing, selection/scoping, analyzer)

## Output Contract Audit

### Expansion Input

- Receives analyzer output: **indirectly** (through decision/action shaping)
- Receives instruction: **yes**
- Current expander contract: `(symbol, file_path, state, max_nodes, max_depth)`
- Gap: no typed, structured expansion-request object

### Expansion Output

- New queries: **not directly**
- New targets: **yes**
- Graph expansions: **yes**
- Refine path can regenerate discovery query intent via parser retry/refine

### Schema Gaps

- Missing typed `ExpansionRequest` contract (reason, direction, priority, budget context)
- Missing per-target provenance/score in `ExplorationTarget`
- Gap semantics are string-based (no typed taxonomy or confidence weight)

## Control Gaps

- Depth budget not fully enforced as true multi-hop traversal.
- Expansion selection is only loosely tied to analyzer gap semantics.
- No per-mode budget ledger (graph vs refine vs semantic) to avoid mode imbalance.
- Usefulness tracked globally, not attached to specific expansion paths.
- Fallback expansion query is generic and can reduce explainability/relevance.

## Failure Modes

- **Redundant expansion generation:** duplicates produced upstream then filtered (`dedupe_skips` in all live cases).
- **Premature completion:** `primary_symbol_sufficient` can stop after narrow evidence.
- **Relevance drift:** generic fallback query may fetch off-target nodes.
- **Depth under-exploration:** configured depth is not fully reflected in behavior.

## Recommended Design (Architecture Only)

### Structured Expansion Modes

- **Graph mode:** typed request with `focus_symbol`, `anchor_file`, `direction`, `hop_budget`.
- **Semantic-search mode:** used for conceptual gaps or low graph confidence.
- **Refinement mode:** explicit failure-reason-driven reroute for wrong-target/low relevance.

### Prioritization

- Prioritize using gap alignment + novelty + graph confidence + recency penalty.
- Example scoring form:
  - `priority = 0.4*gap_match + 0.25*novelty + 0.2*graph_conf + 0.15*target_quality - revisit_penalty`

### Budget Controls

- Enforce true depth budget in traversal logic.
- Add per-iteration + global node budgets, plus per-mode caps.
- Keep global step cap; add mode-level ceilings.

### Stopping Criteria

- Stop when analyzer says sufficient **and** critical gap classes are resolved.
- Add diminishing-returns stop based on per-path utility trend.
- Hard-stop when budget exhausted with explicit rationale.

### State Tracking

- Extend visited metadata with first/last-seen and last utility contribution.
- Track explored path signatures (`anchor -> edge -> target`) and outcomes.

## Implementation Plan (No Prompt Changes)

1. Define typed contracts: `ExpansionRequest`, `ExpansionTargetScored`, `GapSignal`.
2. Refactor `GraphExpander` for true bounded multi-hop traversal.
3. Add deterministic mode selector (graph/search/refine) from analyzer + state signals.
4. Add per-target usefulness scoring and persist in exploration state.
5. Add strategy budget ledger and expose in `ExplorationResult.metadata`.
6. Tighten completion gate to include gap-resolution criteria.
7. Extend live harness with path-utility and budget-consumption regressions.

## Production-Grade Requirements

- Typed expansion contracts
- True depth-aware traversal
- Gap-aligned path prioritization
- Per-path usefulness scoring
- Explicit strategy budget ledger

