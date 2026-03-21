# Plan Step Query Contract — Next Stage Design

Principal-engineer design for the upstream cut to make the plan-step query contract honest and production-real. No implementation in this doc.

---

## 1. CURRENT CONTRACT DIAGNOSIS

**Step schema:** Steps have `action`, `description`, `reason`; `query` exists only on `SEARCH_CANDIDATES` (docs seed, planner docs fallback).

**Current usage:**
- **SEARCH step:** Only `description`. Router short-circuit sets `description: instruction` (full NL). Policy passes `description` to rewriter and `_search_fn`.
- **SEARCH_CANDIDATES step:** Uses `query or description` (`step_dispatcher.py` 725). Docs seed sets both; `query` holds the retrieval string.
- **EXPLAIN step:** Uses `description` for inject search (`step_dispatcher.py` 831). `_shape_query_for_explain_retrieval(description)` only when code lane and no `query`.
- **Planner-emitted SEARCH:** LLM outputs `description` only. Fallback SEARCH uses `description: "Locate items mentioned in: {instruction[:200]}..."` — wrapper around raw text.
- **Docs-seed first step:** Has `query` and `description`; retrieval uses `query`.

**Overload:** `description` is used for human intent, plan rationale, and retrieval for SEARCH/EXPLAIN. Retrieval also receives NL instructions directly.

**Query behavior:** Real `query` only on `SEARCH_CANDIDATES` (docs). SEARCH steps lack it.

**Worst lie / biggest gap:** Router short-circuit SEARCH (`plan_resolver.py` 275–284): full instruction in `description` → rewriter → hybrid. No early distillation at plan time. Planner fallback is similar.

---

## 2. CHOSEN NEXT STAGE

**Single stage:** Add `query` as optional first-class field for SEARCH (and EXPLAIN when it triggers retrieval); wire all retrieval consumers to prefer `query or description`; populate `query` where we can without LLM or planner changes.

**Scope:**
- SEARCH short-circuit: set `query` with deterministic heuristic.
- Planner fallback SEARCH: set `query` with same heuristic.
- Policy and EXPLAIN: read `query or description`; rewriter/shaping unchanged except input.
- No planner prompt/schema changes; no routing changes.

**Rationale:** Schema and execution already treat `query` as optional (`validate_step_input` 38, `SEARCH_CANDIDATES` 725). The change is to emit `query` where we control the plan and to read it where we execute retrieval, using a cheap heuristic so SEARCH short-circuit and fallback no longer pass raw NL as the only retrieval input.

---

## 3. PRODUCTION-HONEST CONTRACT AFTER THE CUT

| Step type | NL description field | Retrieval query field | `query` required? | Consumer behavior | If `query` missing | Rewriter | Status |
|-----------|----------------------|------------------------|-------------------|-------------------|--------------------|----------|--------|
| **SEARCH** | `description` | `query` | Optional | Policy: `retrieval_input = step.get("query") or step.get("description")` → rewriter → `_search_fn` | Use `description` | Yes, on `retrieval_input` | First-class where populated |
| **SEARCH_CANDIDATES** | `description` | `query` | Optional (already has it for docs) | `step_dispatcher` 725: `query or description` | Use `description` | No (candidates path) | Unchanged |
| **EXPLAIN (inject search)** | `description` | `query` | Optional | `step_dispatcher` 831: `base = step.get("query") or step.get("description")`; shaping only if `query` absent | Use `description` + shaping | No | First-class when `query` present |
| **Planner SEARCH** | `description` | `query` | Optional | Same as SEARCH | Fallback to `description` | Yes | Partial (fallback populates `query`) |
| **Docs-seed first** | `description` | `query` | Present | Unchanged | N/A | No | Unchanged |

**Contract rule:** Retrieval uses `query` when present; otherwise `description`. `description` stays the human-readable step text.

---

## 4. EXACT CODE CHANGES

### 4.1 `agent/retrieval/query_rewriter.py`
- Add public function: `heuristic_condense_for_retrieval(text: str) -> str`
- Delegate to existing `_heuristic_rewrite_no_llm` (or equivalent filler stripping)
- Export in `agent/retrieval/__init__.py`

### 4.2 `agent/orchestrator/plan_resolver.py`
- SEARCH short-circuit branch (~276–284): change step from:
  ```python
  {"id": 1, "action": "SEARCH", "description": instruction, "reason": "..."}
  ```
  to:
  ```python
  {
      "id": 1,
      "action": "SEARCH",
      "description": instruction,
      "query": heuristic_condense_for_retrieval(instruction),
      "reason": "Routed by unified production router",
  }
  ```
- Import: `from agent.retrieval.query_rewriter import heuristic_condense_for_retrieval`
- If heuristic returns empty string, set `query: instruction.strip()[:500]` to avoid empty retrieval

### 4.3 `planner/planner.py`
- In `_build_controlled_fallback_plan`, code-lane SEARCH step (~134–140): add `query`:
  ```python
  from agent.retrieval.query_rewriter import heuristic_condense_for_retrieval
  # ...
  "query": heuristic_condense_for_retrieval(instruction) or instruction[:200].strip(),
  ```
- Keep `description` as-is (including the "Locate items mentioned in:" wrapper) for humans
- Docs-lane fallback: already has `query`; no change

### 4.4 `agent/execution/policy_engine.py`
- `_execute_search` (~311): replace `description = (step.get("description") or "").strip()` with `retrieval_input = (step.get("query") or step.get("description") or "").strip()`
- Use `retrieval_input` where `description` was passed to the rewriter (e.g. ~330) and in fallbacks (~336, 339)
- `_run_once` SEARCH branch (~268): same replacement (`retrieval_input = step.get("query") or step.get("description")`)

### 4.5 `agent/execution/step_dispatcher.py`
- EXPLAIN inject search (code lane, ~831–858):
  - `base = step.get("query") or step.get("description") or ""`
  - Only call `_shape_query_for_explain_retrieval` when `not step.get("query")`; otherwise keep `base`
  - Use `base` (possibly shaped) as `query` for `_search_fn`

### 4.6 `agent/execution/policy_engine.py` — `validate_step_input`
- Already allows `query` for SEARCH/EDIT/EXPLAIN (line 38). No change.

### 4.7 `execution_loop.py`
- No change (uses `description` only for logging).

### 4.8 `planner/planner_utils.py`
- No change; `normalize_actions` does not touch `query`.

### 4.9 Tests
- `tests/test_plan_resolver_routing.py`: in `test_get_plan_search_short_circuit_from_routed_intent`, add `assert "query" in plan["steps"][0]` and assert `query` is a non-empty substring/condensation of the instruction (e.g. not full "Locate the auth module" if heuristic strips filler).
- `tests/test_explain_query_shaping.py`: add a test where the EXPLAIN step has `query` and inject search runs; assert `_search_fn` is called with that `query`, not the shaped `description`.

---

## 5. DESIGN DECISIONS

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Field name | `query` | Matches SEARCH_CANDIDATES; schema already supports it |
| `description` role | Human-readable only | Stays as intent/rationale; retrieval uses `query` when present |
| SEARCH: require `query`? | No | Execution falls back to `description` when `query` absent |
| EXPLAIN inject: same field? | Yes | Use `step.get("query") or step.get("description")`; same contract as SEARCH |
| Planner SEARCH normalization | Only in fallback | Fallback sets `query`; success path left unchanged (no prompt changes) |
| SEARCH_CANDIDATES | Unchanged | Already uses `query`; no edits |
| Docs-seed | Unchanged | Already uses `query`; no edits |

---

## 6. RISKS / EDGE CASES

| Risk | Mitigation |
|------|------------|
| **Old plans without `query`** | `query or description` fallback preserves behavior; no back-compat break |
| **Planner JSON drift** | Planner success path unchanged; no new fields expected from LLM |
| **Heuristic strips too much** | If result is empty, use `instruction.strip()[:500]` (or similar) as fallback |
| **Silent fallback** | Fallback is explicit: `step.get("query") or step.get("description")`; logging can record which was used |
| **EXPLAIN divergence** | EXPLAIN uses same contract; shaping only when `query` absent |
| **Docs-seed** | Docs-seed continues to set `query`; no change to behavior |
| **Tests pass without new field** | `test_get_plan_search_short_circuit_from_routed_intent` must assert `query` presence and non-emptiness |
| **Circular imports** | `plan_resolver` → `query_rewriter`; `planner` → `query_rewriter`. Both are acyclic today |

---

## 7. EXACT TEST PLAN

**Strict (unit/integration):**
1. `test_get_plan_search_short_circuit_emits_query`: With `INTENT_SEARCH`, `get_plan` yields a SEARCH step with `query`; `query` is non-empty and differs from raw instruction when heuristic removes filler.
2. `test_planner_fallback_search_has_query`: `_build_controlled_fallback_plan` code-lane SEARCH step has `query`.
3. `test_explain_inject_uses_query_when_present`: EXPLAIN step with `query` triggers inject search; mock `_search_fn` is called with that `query`, not shaped `description`.
4. `test_explain_inject_shapes_when_query_absent`: EXPLAIN step without `query`; inject search uses shaped result (or `description` when shaping returns `None`).
5. `test_docs_seed_unchanged`: Docs-seed first step still has `query` and `description`; `search_candidates` behavior unchanged.
6. `test_missing_query_fallback`: SEARCH step with only `description`; policy uses `description` for rewriter and `_search_fn` (mock to confirm).

**Softer (live model):**
- No new live-model tests; heuristic is deterministic.

---

## 8. PR-STYLE IMPLEMENTATION ORDER

1. **`agent/retrieval/query_rewriter.py`** — Add and export `heuristic_condense_for_retrieval` (wrapper around existing heuristic). Add simple unit test.

2. **`agent/orchestrator/plan_resolver.py`** — SEARCH short-circuit: add `query` via heuristic; handle empty-heuristic fallback.

3. **`planner/planner.py`** — In `_build_controlled_fallback_plan`, add `query` for code-lane SEARCH step.

4. **`agent/execution/policy_engine.py`** — `_execute_search` and `_run_once`: use `retrieval_input = step.get("query") or step.get("description")` and pass to rewriter/fallbacks.

5. **`agent/execution/step_dispatcher.py`** — EXPLAIN inject: `base = step.get("query") or step.get("description")`; apply shaping only when `query` absent.

6. **Tests** — Add/update `test_plan_resolver_routing`, `test_explain_query_shaping`, and policy/planner tests as above.

7. **Verify** — Run existing plan_resolver, retrieval, and execution tests; confirm no regressions.
