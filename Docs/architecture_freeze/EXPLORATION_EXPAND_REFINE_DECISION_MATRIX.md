# Exploration Expand vs Refine — Staff Engineer Audit & Decision Matrix

**Scope:** `ExplorationEngineV2._explore_inner` control plane after the analyzer produces `UnderstandingResult`, through `EngineDecisionMapper`, gap-driven overrides, and the expand/refine branches.

**Primary source:** `agent_v2/exploration/exploration_engine_v2.py`, `agent_v2/exploration/decision_mapper.py`, `agent_v2/config.py`.

---

## 1. Execution order (single inspection step)

Order matters: later stages can override earlier `action` values.

1. **Analyzer** → `UnderstandingResult` (includes `knowledge_gaps`, `relevance`, `sufficient`, `evidence_sufficiency`, `summary`).
2. `**EngineDecisionMapper.to_exploration_decision`** → baseline `ExplorationDecision` (`status`, `needs`, `next_action`, `reason`).
3. `**_apply_gap_driven_decision**` (if `ENABLE_GAP_DRIVEN_EXPANSION`) → may replace `next_action` / `needs` using **analyzer gaps ∪ memory gap descriptions** (see §3).
4. `**_should_stop` / `_update_utility_and_should_stop`** → may terminate the loop (not expand/refine).
5. `**_next_action(decision)**` → derive `action` ∈ {`expand`, `refine`, `stop`} when `decision.next_action` is unset (see §4).
6. `**_apply_refine_cooldown**` → may flip `refine` → `expand` if refine was used last step and expand is still eligible.
7. **Refine oscillation guard** → may flip `refine` → `expand` if intent would repeat.
8. **Refine → expand coercion (memory relationship signal)** → if `action == "refine"` and memory gaps imply caller/callee/flow **and** graph expansion is mechanically viable, set `action = "expand"` and augment `needs`.
9. `**_should_expand(...)`** → if true, run graph expand and `continue` (skip refine for this iteration).
10. `**_should_refine(...)**` → if true, run `QueryIntentParser.parse` + discovery enqueue.

---

## 2. Input sources (what feeds decisions)


| Input                                                     | Role                                                                                                                              |
| --------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `UnderstandingResult.relevance`                           | Mapper: `low` → `wrong_target` + refine bias; `high` influences mapper `next_action` for partial.                                 |
| `UnderstandingResult.sufficient` / `evidence_sufficiency` | Mapper: sufficient → stop path.                                                                                                   |
| `UnderstandingResult.knowledge_gaps[]`                    | Gap-driven mapping after quality filter; merged with memory gap **descriptions**.                                                 |
| `ExplorationWorkingMemory.get_summary()`                  | `evidence`, `gaps` (typed rows), `relationships`; augments gap text for classification only (read-only for schema).               |
| `ExplorationState`                                        | `seen_*`, `expanded_symbols`, `expansion_depth`, `backtracks`, `attempted_gaps`, pending queue, etc.                              |
| Config / env                                              | `EXPLORATION_EXPAND_MAX_DEPTH`, `EXPLORATION_MAX_STEPS`, `EXPLORATION_MAX_BACKTRACKS`, `ENABLE_*`, utility streak threshold, etc. |


---

## 3. Gap-driven decision (`_apply_gap_driven_decision`)

**Preconditions**

- `ENABLE_GAP_DRIVEN_EXPANSION` must be true; else mapper output is unchanged.

**Build “combined” gap strings**

1. **Analyzer gaps** that pass filters: not in `attempted_gaps` (if quality filter on), not “generic” (short / boilerplate markers).
2. **Memory gap descriptions** from `memory.get_summary()["gaps"]` (deduped with analyzer list).

If **no** combined gaps → return **mapper decision unchanged**.

**Per-gap category** via `_classify_gap_category` (substring rules)


| Category bucket                                             | Typical substrings / rule                             |
| ----------------------------------------------------------- | ----------------------------------------------------- |
| `caller`                                                    | “caller”, “call site”, “who calls”                    |
| `callee` / `flow`                                           | “callee”, “flow”, “sequence”, “pipeline”              |
| `usage` / `definition` / `config` / `usage_symbol_fallback` | “usage”, “defin…”, “config”, symbol-like tokens, etc. |


**Strict priority (first match wins for *action type*)**

1. If **any** combined gap is `caller` → `next_action = expand`, `needs = [callers]`, `expand_direction_hint = callers`.
2. Else if **any** is `callee` or `flow` → `next_action = expand`, `needs = [callees]`, `expand_direction_hint = callees`.
3. Else if **any** is usage/definition/config/usage_symbol_fallback → `next_action = refine`, `needs = [more_code]`, keyword inject for discovery.
4. Else → **return mapper decision unchanged** (no automatic “partial → refine” from this function).

**Important:** Caller branch is evaluated **before** callee; mixed gaps in one step favor **callers** first.

---

## 4. Mapper baseline (`EngineDecisionMapper`)


| Condition                                            | `status`       | `needs`                | `next_action`                                |
| ---------------------------------------------------- | -------------- | ---------------------- | -------------------------------------------- |
| `sufficient` OR `evidence_sufficiency == sufficient` | `sufficient`   | `[]`                   | `stop`                                       |
| `relevance == low`                                   | `wrong_target` | `["different_symbol"]` | `refine`                                     |
| Else (`partial` path)                                | `partial`      | `["more_code"]`        | `expand` if `relevance == high`, else `stop` |


**Note:** Gap-driven logic runs **after** this and can override `next_action` / `needs`.

---

## 5. `_next_action` (when `decision.next_action` is null or not in expand/refine/stop)

Handled in `ExplorationEngineV2._next_action`:


| Condition                                           | `action`  |
| --------------------------------------------------- | --------- |
| `decision.next_action` ∈ {`expand`,`refine`,`stop`} | use as-is |
| `partial` and (`callers` or `callees` in `needs`)   | `expand`  |
| `wrong_target` or `different_symbol` in `needs`     | `refine`  |
| Else                                                | `stop`    |


---

## 6. When does **expand** actually run? (`_should_expand`)

All must hold for **true** (and this method **mutates** `ex_state.expanded_symbols` on success):


| Gate           | Rule                                                                     |
| -------------- | ------------------------------------------------------------------------ |
| Intent         | `action == "expand"` **OR** (`sufficient` and `not relationships_found`) |
| Symbol         | `target.symbol` non-empty                                                |
| Depth          | `expansion_depth < EXPLORATION_EXPAND_MAX_DEPTH`                         |
| Dedup          | `target.symbol` not already in `expanded_symbols`                        |
| Needs / status | `({"callers","callees"} & needs)` **OR** `status == partial`             |


If any fails → **no expand** this iteration (fall through to refine/stop logic).

---

## 7. When does **refine** actually run? (`_should_refine`)

Evaluated **only if** expand branch did not run.


| Rule                                                      | Result                                                   |
| --------------------------------------------------------- | -------------------------------------------------------- |
| `backtracks >= EXPLORATION_MAX_BACKTRACKS`                | **false** (no refine)                                    |
| `status == wrong_target`                                  | **true** (refine allowed)                                |
| `"low relevance"` in `decision.reason` (case-insensitive) | **true**                                                 |
| `action != refine`                                        | **false**                                                |
| Memory relationship gaps + expand still viable            | **false** (block refine — relationship expand preferred) |
| Else                                                      | **true** iff `status == partial`                         |


**Refine uses** `QueryIntentParser.parse` with `context_feedback` (evidence, known entities, gaps, relationships) when invoked from the loop.

---

## 8. Overrides & interactions


| Mechanism                                      | Effect                                                                                                                                  |
| ---------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| **Refine cooldown** (`ENABLE_REFINE_COOLDOWN`) | After a refine step, next `refine` may become `expand` if expand is still eligible.                                                     |
| **Intent oscillation**                         | Consecutive refine with same intent signature → force `expand`.                                                                         |
| **Refine → expand coercion**                   | If `action == refine` but memory gap text classifies as caller/callee/flow **and** symbol/depth/expansion allowed → coerce to `expand`. |
| **Utility stop** (`ENABLE_UTILITY_STOP`)       | Repeated non-improving analyzer signatures → `no_improvement_streak` stop (independent of expand/refine choice).                        |
| **Termination stability (relaxed recovery)**   | If queue empty and unresolved memory gaps, one relaxed discovery pass (separate from expand/refine matrix).                             |


---

## 9. Matrix: **inputs → likely outcome** (after all stages)

Legend: **E** = expand path taken (subject to `_should_expand` gates), **R** = refine path taken (subject to `_should_refine`), **—** = neither (stop / terminate / skip).


| Scenario                                                                         | Mapper tendency                         | Gap-driven override                                                                              | Typical result                                        |
| -------------------------------------------------------------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------------------------- |
| High relevance partial, generic gaps only                                        | `next_action` may be `stop` from mapper | Filtered out → unchanged                                                                         | May **stop** unless `_next_action` maps partial+needs |
| Low relevance                                                                    | `wrong_target` → refine                 | If relationship gaps also present, gap layer may still set **expand** first (caller/callee wins) | **E** or **R** depending on combined gap priority     |
| Relationship gap: caller                                                         | —                                       | `expand` + callers                                                                               | **E**                                                 |
| Relationship gap: callee / flow                                                  | —                                       | `expand` + callees                                                                               | **E**                                                 |
| Definition / usage / config gap                                                  | partial                                 | `refine`                                                                                         | **R** (if `action` refine and gates pass)             |
| Sufficient                                                                       | stop                                    | —                                                                                                | Loop may exit via stop rules                          |
| Wrong target + expand viable per memory                                          | refine from mapper/refine gate          | Coercion may set **expand**                                                                      | **E**                                                 |
| Refine chosen but memory has caller/callee/flow and expand viable                | —                                       | Coercion                                                                                         | **E**                                                 |
| Refine chosen, relationship signal, but no symbol / depth cap / already expanded | —                                       | Coercion does not apply                                                                          | **R** or stall depending on `_should_refine`          |


---

## 10. Config / env limits that cap behavior (not in the matrix but decisive)


| Variable                                                                     | Effect on expand/refine                |
| ---------------------------------------------------------------------------- | -------------------------------------- |
| `AGENT_V2_EXPLORATION_EXPAND_MAX_DEPTH`                                      | Stops `_should_expand` after depth cap |
| `AGENT_V2_EXPLORATION_MAX_STEPS`                                             | Caps loop iterations                   |
| `AGENT_V2_EXPLORATION_MAX_BACKTRACKS`                                        | Disables refine when exceeded          |
| `AGENT_V2_EXPLORATION_UTILITY_NO_IMPROVEMENT_STREAK` + `ENABLE_UTILITY_STOP` | Early termination                      |
| `AGENT_V2_ENABLE_GAP_DRIVEN_EXPANSION`                                       | Disables entire gap-driven layer       |
| `AGENT_V2_ENABLE_REFINE_COOLDOWN`                                            | Alters refine→expand alternation       |


---

## 11. Audit conclusions

1. **Expand** is driven by: mapper/high relevance, `_next_action` partial+relationship needs, and **gap-driven caller/callee/flow** (strict priority), then gated by `_should_expand`.
2. **Refine** is driven by: mapper wrong_target, gap-driven definition/usage/config gaps, `_next_action` wrong_target path, and `_should_refine` partial — but **suppressed** when relationship gaps in memory plus graph expansion is still viable.
3. **“Degradation” under stress** in harnesses often appears as `**pending_exhausted`** or utility stop, not necessarily wrong expand/refine selection — candidate queue and env limits dominate at scale.

---

## 12. Code references (anchor points)

- Gap merge + priority: `_apply_gap_driven_decision` — `exploration_engine_v2.py`
- Mapper baseline: `EngineDecisionMapper.to_exploration_decision` — `decision_mapper.py`
- Action resolution: `_next_action` — `exploration_engine_v2.py`
- Expand gate: `_should_expand` — `exploration_engine_v2.py`
- Refine gate: `_should_refine` — `exploration_engine_v2.py`
- Coercion: `_explore_inner` block after oscillation guard — `exploration_engine_v2.py`

