# Expansion Loop — Targeted Fixes (Design)

Minimal design for three issues: weak gap→target mapping, shallow graph traversal, redundant upstream candidate generation. **No implementation** — architecture and direction only.

---

## Current State Analysis

### Gap → expansion (current flow)

1. `UnderstandingAnalyzer` returns `UnderstandingResult` with `knowledge_gaps` (strings).
2. `EngineDecisionMapper` maps understanding → `ExplorationDecision` (status, needs, next_action).
3. `_apply_gap_driven_expansion` (when `ENABLE_GAP_DRIVEN_EXPANSION`): filters gaps (generic markers, `attempted_gaps`), merges `_needs_from_gaps(accepted)` into `decision.needs`, sets `next_action` to `expand`, records `attempted_gaps` and `gap_expand_attempts`.
4. `_needs_from_gaps`: substring rules — `caller`→callers, `callee`→callees, `defin`→definition; else `more_code`.
5. `_next_action`: expand / refine / stop from decision; partial + callers/callees in needs → expand.
6. `_should_expand`: expansion only if (action expand OR sufficient-but-no-relationships), symbol present, symbol ∉ `expanded_symbols`, and (needs ∩ {callers,callees} OR status partial); then adds symbol to `expanded_symbols`.
7. `GraphExpander.expand(symbol, file_path, state, max_nodes, max_depth)` runs; targets enqueue via `_enqueue_targets` (priority score + edge dedupe).

### Depth handling (current)

| Aspect | Behavior |
|--------|----------|
| **Per call** | `GraphExpander.expand` receives `max_depth` (default `EXPLORATION_EXPAND_MAX_DEPTH`) and echoes it in result metadata; `fetch_graph` is a single lookup (`top_k=max_nodes`), not a multi-hop walk. |
| **Across loop** | `ExplorationState` has no semantic depth counter; depth is not accumulated across steps. Per-symbol expansion is tracked via `expanded_symbols` (at most one graph expand per symbol name in current logic). |
| **vs max steps** | Outer loop bounded by `EXPLORATION_MAX_STEPS` (`steps_taken`), independent of graph depth semantics. |

### Candidate generation points

| Point | Role |
|-------|------|
| `_discovery` | Batched SEARCH (symbol/regex/text); merge by `(canonical_path, symbol_dedupe_key)`; top `DISCOVERY_MERGE_TOP_K`; `_may_enqueue` filters explored/excluded/pending. |
| `GraphExpander.expand` | `fetch_graph(symbol)` OR fallback SEARCH string; produces `ExplorationTarget` list — can overlap files/symbols already in queue or seen. |
| `_enqueue_ranked` (initial + refine) | Selector returns ranked candidates → `ExplorationTarget(discovery)`; `_enqueue_targets` adds to pending. |
| `_enqueue_targets` (expansion) | Scores and dedupes via `seen_relation_edges` and `_may_enqueue`; can still receive targets that duplicate discovery pool if graph/search returns same `(file,symbol)` under different ordering/canonicalization. |

### Focused answers

1. **How gaps trigger expansion:** Gap strings pass quality filter → `_needs_from_gaps` augments `decision.needs` → gap-driven layer forces `next_action=expand` when any gap is accepted; `_should_expand` still requires callers/callees in needs (or partial) and a symbol not yet in `expanded_symbols`.

2. **GraphExpander inputs:** `symbol`, `file_path`, `state`, `max_nodes`, `max_depth`. No structured gap type or direction; fallback SEARCH uses a fixed template query.

3. **Depth:** Per-call parameter and metadata only; not a global semantic depth across the exploration loop.

4. **Duplicate upstream sources:** Graph/fallback returning targets already pending or explored; refine discovery overlapping initial merge keys; expansion targets overlapping discovery-selected symbols; edge-hash dedupe does not prevent all duplicate `(file,symbol)` appearances.

---

## Gap → Action Mapping

### Gap categories (substring rules, no taxonomy explosion)

| Category | Match hints |
|----------|-------------|
| **caller** | caller, callers, call site, who calls |
| **callee** | callee, callees, calls, downstream |
| **usage** | usage, used, reference, references, where used |
| **definition** | defin, definition, where defined, locate |
| **config** | config, setting, env, flag |
| **flow** | flow, path, sequence, pipeline (only if no higher-priority match) |

### Mapping logic

| Condition | Action |
|-----------|--------|
| Gap matches **caller** | Ensure `needs` contains `callers`; `next_action` expand; graph direction **callers only** (see GraphExpander note). |
| Gap matches **callee** | Ensure `needs` contains `callees`; `next_action` expand; graph direction **callees only**. |
| Gap matches **usage** | Refine path: run discovery with intent augmented by one extra keyword from gap token (engine scratch list merged at `_discovery` only — no parser change). |
| Gap matches **definition** | Same as usage OR expand with definition-oriented graph row filter if available; else discovery refine. |
| Gap matches **config** | Refine discovery with keyword `config` or file basename if present in gap. |
| Gap matches **flow** only | Expand callers + callees with caps, or single expand with `direction=both` if expander supports. |
| **No category match** | Keep current `_needs_from_gaps` fallback (`more_code`) + existing stop/utility behavior. |

### Required changes (minimal)

**Engine**

- Extend `_needs_from_gaps` with an ordered list of `(predicate on gap.lower(), needs patch, optional direction hint)` stored on state as plain fields: `expand_direction_hint` ∈ {callers, callees, both}, optional `refine_keyword` / scratch keywords for `_discovery` merge only.
- After `_apply_gap_driven_decision`, set/clear `expand_direction_hint` so the expand branch can pass hint into `GraphExpander` without changing analyzer/parser.
- For usage/definition/config without parser changes: merge at most 1–2 extra keywords into the **local** keyword list used only inside `_discovery` for that phase (e.g. `state.exploration_refine_keywords`).

**GraphExpander**

- Optional args: `direction_hint` (callers | callees | both), optional `skip_symbols` / `skip_files` from engine.
- When splitting rows into callers/callees/related, **filter** by `direction_hint` instead of always concatenating all buckets.
- No new classes: function args + existing `ExplorationTarget` list.

---

## Depth Design

### State fields

- Add to `ExplorationState`: `expansion_depth` (int, default 0); optionally `last_expansion_parent_key` for debugging only.

### Increment rules

- On **successful** `GraphExpander.expand` that enqueues **at least one** new target passing `_may_enqueue` **and** (new canonical file **or** new symbol vs current target): increment `expansion_depth` by 1.
- If expand returns only targets all skipped by prefilter: **do not** increment.
- Inspect/read without graph expand: **do not** increment (depth = graph-hop semantics).

### Global cap

- Before `GraphExpander.expand`: if `expansion_depth >= EXPLORATION_EXPAND_MAX_DEPTH` → skip graph expand (no-op or force refine/stop per existing decision); log reason in telemetry only.

### Enforcement points

- `ExplorationEngineV2`: immediately before `_graph_expander.expand(...)`.
- Optional: inside `_should_expand` return false when depth cap hit (so cooldown/refine can still run).

### Compatibility

- Keep `EXPLORATION_MAX_STEPS` as outer bound; depth cap is orthogonal and stricter for **graph hops** only.
- Keep `expanded_symbols` but do not rely on it as the only multi-hop proxy — use `expansion_depth` for hops across different symbols.

---

## Redundancy Reduction

### Sources

- GraphExpander returns rows already in `pending_targets` or `explored_location_keys`.
- Refine discovery re-merges overlapping top-k with prior candidates.
- Fallback SEARCH in GraphExpander is broad and repeats symbols/files seen in discovery.

### Prefilter rules

- **Before** `_enqueue_targets` from expansion: drop `ExplorationTarget` if `(canonical_file, symbol)` ∈ `explored_location_keys` or already in `pending_targets` (reuse `_may_enqueue` per target).
- Skip expand call if post-filter would yield zero rows (avoid empty work).
- Track `attempted_gap_target` pairs: `(normalized_gap, canonical_file, symbol)`; skip re-expanding the same gap to the same target.

### Insertion points

- `ExplorationEngineV2`: immediately after `expanded = graph_expander.expand(...)` and **before** `_enqueue_targets`.
- `GraphExpander.expand`: early filter of `graph_rows` using skip sets from engine (single pass).

### Principle

Keep `_may_enqueue` and `seen_relation_edges`; prefilter reduces wasted tool calls and queue churn **upstream**.

---

## Minimal Design Confirmation

| Constraint | Met |
|------------|-----|
| No `ExpansionRequest` class | Yes |
| No scoring redesign | Yes |
| No contract-breaking changes | Yes (additive state fields only) |
| Localized to `ExplorationEngineV2` + minimal `GraphExpander` | Yes |
| Analyzer / parser untouched | Yes |

---

## Implementation Plan (tight)

| Step | Files | Action | Risk |
|------|-------|--------|------|
| 1 | `agent_v2/schemas/exploration.py`, `exploration_engine_v2.py` | Add `expansion_depth`, `expand_direction_hint`, optional refine keyword scratch, `attempted_gap_targets` (tuple set). | Pydantic defaults must stay backward compatible. |
| 2 | `exploration_engine_v2.py` | Deterministic gap category → needs + `direction_hint` in gap-driven path; pass hint into expand branch. | Over-triggering expand — mitigate with depth cap + prefilter. |
| 3 | `graph_expander.py`, `exploration_engine_v2.py` | Direction filter + skip sets on `expand()`; increment `expansion_depth` when enqueue accepts new hop; block when depth ≥ max. | Graphs with only “related” rows — document fallback. |
| 4 | `exploration_engine_v2.py` | Prefilter expanded targets before `_enqueue_targets`; optional keyword merge at `_discovery` for usage/config gaps. | Fewer candidates — monitor `termination_reason`. |
| 5 | `tests/test_exploration_engine_v2_control_flow.py` | Tests: depth increments once per hop; depth blocks expand; prefilter drops duplicates; direction filters callers vs callees. | Mock graph rows to avoid flake. |

---

## Expected Outcome

- Expansion becomes **directed** (not semi-random from a single generic expand).
- **Multi-hop** behavior via global `expansion_depth` and cap vs `EXPLORATION_EXPAND_MAX_DEPTH`.
- **Less duplicate work** via prefilter + gap-target memory, with existing dedupe unchanged as fallback.
