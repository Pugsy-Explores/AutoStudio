# RCA: Dependencies "Not Found" When Installed

## Root Cause

1. **rank_bm25 shows "not installed" even when installed**
   - `rank_bm25` depends on `numpy`. In some environments (nested import loaders, certain numpy builds), importing `rank_bm25` raises `RecursionError` instead of `ImportError`.
   - Code only caught `ImportError`; `RecursionError` propagated and crashed, or was misreported.
   - **Fix:** Catch `RecursionError` in `bm25_retriever.py` and `conftest.py`; log a clear message that the package is installed but unusable, with remediation steps.

2. **conftest exit on missing deps before tests run**
   - `pytest_sessionstart` imported `rank_bm25`; when it raised `RecursionError`, pytest crashed (only `ImportError` was caught).
   - **Fix:** Catch `RecursionError` in conftest; treat as fatal (exit) — deps are mandatory, no degraded mode.

3. **install script used `pip` directly**
   - `pip` may not be in PATH (e.g. venv not activated, or `pip` not installed as standalone).
   - **Fix:** Use `python3 -m pip` so the same Python that runs tests is used for install.

4. **Other retrieval packages: same pattern**
   - `chromadb`, `sentence_transformers`, `repo_graph`, `tree_sitter_python` can pull in numpy/torch and raise `RecursionError` during import.
   - Code only caught `ImportError`; `RecursionError` would crash.
   - **Fix:** Catch `RecursionError` alongside `ImportError` in all optional-import paths.

## Fixes Applied

| File | Change |
|------|--------|
| `agent/retrieval/bm25_retriever.py` | Catch `RecursionError` in `build_bm25_index` and `search_bm25`; log clear message |
| `agent/retrieval/vector_retriever.py` | Catch `RecursionError` in `_check_vector_available` |
| `agent/retrieval/graph_retriever.py` | Catch `RecursionError` in `retrieve_symbol_context` (repo_graph import) |
| `agent/retrieval/context_builder.py` | Catch `RecursionError` in `build_call_chain_context` (repo_graph import) |
| `repo_index/parser.py` | Catch `RecursionError` in `_get_parser` (tree_sitter_python import) |
| `tests/conftest.py` | Catch `RecursionError` in session import check; exit (deps mandatory) |
| `scripts/install_test_deps.sh` | Use `python3 -m pip`; ensure rank-bm25 and tree-sitter-python |
| `scripts/run_tests.sh` | **New** — install then pytest |
| `tests/test_bm25_retriever.py` | Add `test_build_index_recursion_error_returns_false` |

## Usage

```bash
# Always install before running tests (fresh clone, CI)
bash scripts/install_test_deps.sh

# Or use run_tests.sh (install + pytest in one step)
bash scripts/run_tests.sh tests/ -q
```
