# SEARCH test matrix audit (Stages 41–46.1)

## 1. Audit — prior coverage

| Area | Had | Gap |
|------|-----|-----|
| Policy SEARCH retries, rewrite cap, file_search/list_dir honesty | `tests/test_policy_engine.py` | `_is_valid_search_result` edge cases (missing file, empty snippet rules), blank/whitespace retrieval, `chosen_tool` in history, `_run_once` vs `_execute_search`, rewriter arity |
| `get_initial_search_variants` | `test_policy_engine.py` | Whitespace-only base → `[]` |
| `repo_map_lookup` + typo tier | `tests/test_repo_map_lookup.py` | (already strong after 46.1) |
| Validator SEARCH empty | `tests/test_validator.py` | Parity with policy for `file_search` / `list_dir` markers |
| `query_rewriter` condense | — | `heuristic_condense_for_retrieval` |
| `step_dispatcher._search_fn` | — | Hybrid vs sequential, repo_map context, `file_search` / `list_dir` markers, filter preserving markers |
| Live eval | `routing_contract` | SEARCH-focused tasks + checker |

## 2. Test plan (easy → hard)

### Pure unit

- `_is_valid_search_result`: missing `file`, empty snippet on `.py` vs non-py, `retrieval_fallback` markers, malformed row.
- `heuristic_condense_for_retrieval`: filler strip, whitespace-only, symbol-like tokens preserved.
- `get_initial_search_variants`: whitespace-only → `[]`.

### Policy integration

- `chosen_tool` appears in `attempt_history[].tool`.
- Whitespace-only description → no deterministic variants → rewriter runs on first policy attempt.
- `max_total_attempts=0` → `_run_once` SEARCH path (rewriter arity must accept optional `state`).

### Dispatcher / retrieval integration

- Hybrid returns hits → short-circuit.
- Hybrid empty → sequential → `search_code` path (mocks + `filter_and_rank` passthrough).
- `repo_map_candidates` set when `repo_map.json` present.
- `file_search` / `list_dir` fallback shapes.
- Markers preserved through filter when results non-empty.

### Validator / config

- Validator parity tests calling `_is_valid_search_result` (same helper as policy).

### Live / offline eval

- Suite `search_stack`: mini repo `mr01_arch`, tasks `ss_*`, checker `check_search_stack.py`.

## 3. Implementation notes

- **`tests/test_search_stack_matrix.py`** — matrix for validity helpers, condense, `_search_fn`.
- **`tests/test_policy_engine.py`** — extended classes + `get_initial_search_variants` whitespace test + `_identity_rewrite` accepts optional `state` (matches production `rewrite_query_fn` signature; fixes `_run_once` tests).
- **`tests/test_validator.py`** — `TestSearchValidityMatchesPolicyEngine`.
- **`tests/agent_eval/suites/search_stack.py`**, **`tests/agent_eval/check_search_stack.py`**, **`suite_loader.py`**, README + runner docstrings.

## 4. How to run

```bash
# Focused SEARCH matrix (offline)
python3 -m pytest tests/test_search_stack_matrix.py tests/test_policy_engine.py tests/test_validator.py tests/test_repo_map_lookup.py -q

# Broader retrieval/policy
python3 -m pytest tests/test_policy_engine.py tests/test_retrieval_pipeline.py -q --tb=no -m "not slow"

# SEARCH eval suite (offline; produces artifacts)
python3 -m tests.agent_eval.runner --suite search_stack --execution-mode offline --output artifacts/agent_eval_runs/latest
python3 -m tests.agent_eval.check_search_stack --run-dir artifacts/agent_eval_runs/latest

# Live (same suite; real runtime — not fakeable with unit tests)
python3 -m tests.agent_eval.runner --suite search_stack --execution-mode live_model --output artifacts/agent_eval_runs/latest
```

## 5. Remaining gaps (why)

- **BM25 / RRF branches inside `hybrid_retrieve`** — covered indirectly by slow integration and eval; full branch matrix would duplicate `search_pipeline` internals.
- **Real Serena / embedding quality** — live `search_stack` + manual triage; unit tests stay mocked.
- **Cache hit behavior** — only lightly observable; `RETRIEVAL_CACHE_SIZE` scenarios need dedicated env + isolated cache if tightened later.
