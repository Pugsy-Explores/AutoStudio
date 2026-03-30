# ReAct Simplification Plan

## Goal

Align runtime with: **Model decides → Orchestrator runs loop → Dispatcher calls tool → Tools execute → State stores history.** No hidden mutation, blocking pre-judgement, or invisible retries.

---

## Phase 1: Simplify observation builder

**File:** `agent/orchestrator/execution_loop.py`

**Change:** `_build_react_observation` returns readable text instead of heavy nested JSON.

| Action   | Before                            | After                               |
|----------|-----------------------------------|-------------------------------------|
| SEARCH   | `json.dumps({success, results_count, preview: [{file,snippet}...]})` | Plain text: `Found N results. file1: snippet...` |
| READ     | `str(content)[:8000]`             | Keep (already readable)              |
| EDIT     | `json.dumps({success, error, test_output})` | Plain text: success/error + short summary |
| RUN_TEST | Already mixed text                | Keep; ensure no heavy JSON           |

**Rationale:** Model benefits from readable observations. Nested JSON adds noise.

---

## Phase 2: ReAct search — no aggressive filtering

**File:** `agent/execution/step_dispatcher.py`

**Change:** Add `_search_react(query, state)` used only by `_dispatch_react`. It:

- Calls `search_candidates(query, state, artifact_mode="code")` first
- Falls back to `search_code(query)` if empty
- Does **not** call `filter_and_rank_search_results` (let model decide relevance)
- Returns raw `{results, query}` with top 20–30 results

**Rationale:** Current `_search_fn` uses `filter_and_rank_search_results` which may hide relevant data. ReAct path should pass through more; model chooses.

---

## Phase 3: Dispatcher ReAct path — already pure

**Verification only.** `_dispatch_react` already:

- No `validate_step_input`
- No policy engine
- No query rewriting
- No retry/replan logic
- Direct action → tool mapping

**No code change.** Confirm no accidental policy/validate calls on ReAct path.

---

## Phase 4: Config cleanup (deferred)

**File:** `config/agent_runtime.py`

**Deferred:** Add deprecation comments for flags used only by legacy deterministic flow when doing larger config cleanup. ReAct path does not use: `MAX_STAGNATION`, `RETRY_QUERY_MAX_LEN`, `RETRY_SUGGESTION_MAX_LEN`.

---

## Phase 5: State — minimal

**Verification only.** ReAct loop already uses:

- `instruction`
- `react_history`
- `react_finish`, `react_mode`

**No change.** Do not add `failure_state`, `attempted_patches`, or stagnation tracking to ReAct path.

---

## Out of scope (future cleanup)

- **Planner removal:** Keep modules; ReAct path never calls them. Remove in later PR.
- **Policy engine removal:** Same. Bypassed when `react_mode`.
- **Context pruner:** Not in ReAct hot path. Simplify in retrieval refactor.
- **Critic/semantic feedback:** Edit pipeline uses it; convert to passive-only in edit refactor.

---

## Files to modify

1. `agent/orchestrator/execution_loop.py` — simplify `_build_react_observation`
2. `agent/execution/step_dispatcher.py` — add `_search_react`, use in `_dispatch_react`
3. `config/agent_runtime.py` — deprecation comments (optional, low priority)

---

## Tests

- `tests/test_react_schema.py` — must pass
- `tests/test_prompt_regression.py` — must pass
- Manual: `REACT_MODE=1 python3 scripts/run_react_live.py "search for validate_action"`
