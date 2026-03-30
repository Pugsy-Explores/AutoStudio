# RCA: audit12 Real Execution — RecursionError and Query Stub Behavior

**Date:** 2025-03-20  
**Context:** `python3 -m tests.agent_eval.runner --execution-mode real --suite audit12`  
**Environment:** Python 3.12, macOS (darwin 24.6.0)  
**Assumption:** Packages (rank-bm25, numpy, onnxruntime, transformers) are installed.

---

## Executive Summary

Three distinct phenomena appear in the terminal output. Two are **environmental** (RecursionError from numpy/import loader), one is **intentional** (query rewriter stub). The run was interrupted by the user (KeyboardInterrupt).

| Issue | Severity | Root Cause | Status |
|-------|----------|------------|--------|
| rank_bm25 RecursionError | Degraded (non-fatal) | numpy + Python 3.12 import loader | Graceful fallback |
| Reranker RecursionError | Degraded (non-fatal) | Same numpy/loader in inference path | Graceful fallback |
| rewriter `{"steps": []}` | Expected | Offline eval stub | By design |
| KeyboardInterrupt | User action | User Ctrl+C | N/A |

---

## 1. rank_bm25 RecursionError (Installed but Unusable)

### Observed

```
[bm25] rank_bm25 import failed with RecursionError (numpy/loader); bm25 unavailable.
[retrieval_pipeline] rank_bm25 import failed with RecursionError (numpy/import loader); bm25 marked unavailable
```

### Root Cause

- `rank_bm25` depends on `numpy`.
- In Python 3.12, with certain import contexts (nested loaders, `unittest.mock`, `ThreadPoolExecutor`), importing `numpy` or its transitive deps can raise `RecursionError` instead of `ImportError`.
- Known upstream: NumPy + `importlib.LazyLoader` / Python 3.12+ recursion handling changes (numpy/numpy#26093, cpython#112282).
- The agent_eval run uses `unittest.mock.patch` and `ThreadPoolExecutor` in `execution_loop`, creating a nested import context that triggers this.

### Why “packages are installed” doesn’t fix it

The packages are installed; the failure is **runtime import behavior**, not missing deps. The RecursionError occurs when Python’s import machinery hits recursion limits during numpy/rank_bm25 loading in this specific context.

### Current Behavior

- `retrieval_pipeline.run_retrieval_pipeline` and `bm25_retriever` catch `RecursionError` and set `bm25_available = False`.
- BM25 is skipped; retrieval continues with vector search and other stages.
- No crash; degraded retrieval quality.

### Remediation Options

1. **Environment isolation (recommended for CI/eval):**
   - Run agent_eval in a fresh subprocess or minimal venv where numpy loads cleanly.
   - Avoid running under heavy mocking/threading before first numpy import.

2. **Import order:**
   - Import numpy (and optionally rank_bm25) at process startup, before any mocks or threads.
   - Example: add `import numpy` in `tests/agent_eval/runner.py` or `conftest.py` before `offline_llm_stubs` / `run_structural_agent_real`.

3. **Python / numpy version:**
   - Try `numpy>=2.1` (includes LazyLoader fixes).
   - Or pin to a known-good numpy version for Python 3.12.

4. **Upstream:**
   - Track numpy/numpy#26093 and related fixes; upgrade when resolved.

---

## 2. Reranker RecursionError (Inference Path)

### Observed

```
[retrieval_pipeline] reranker inference failed — using retriever-score ordering: RecursionError: maximum recursion depth exceeded
```

### Root Cause

- Reranker uses `onnxruntime`, `transformers`, and `numpy` (via `cpu_reranker.py`).
- `_reranker.rerank()` triggers inference, which uses numpy arrays.
- The same numpy/import recursion issue can surface during inference (e.g. when numpy is first used in a nested context).

### Current Behavior

- Exception is caught in `retrieval_pipeline.py` (lines 657–666).
- Pipeline falls back to retriever-score ordering.
- No crash; degraded ranking quality.

### Remediation

Same as §1: pre-import numpy/onnxruntime/transformers before mocks and threads, or run in a cleaner subprocess. The reranker failure is a downstream effect of the same import/recursion environment.

---

## 3. Query Rewriter Returns `{"steps": []}`

### Observed

```
[workflow] rewriter query: {"steps": []}
[workflow] SEARCH attempt 1/5 query='{"steps": []}'
```

### Root Cause

- **Intentional.** `tests/agent_eval/real_execution.py` patches `agent.retrieval.query_rewriter.call_reasoning_model` with `_stub_reasoning_json`, which returns `'{"steps": []}'`.
- Purpose: keep agent_eval offline (no LLM calls). The stub satisfies the JSON contract but produces empty search steps.

### Impact

- Search may use weak or empty queries.
- For repair tasks (e.g. `core12_mini_repair_calc`), search still returns results (e.g. `src/calc/ops.py`) because the retrieval pipeline has fallbacks (e.g. vector search, graph expansion).
- Documented in `Docs/STAGE17_EXEC_HARDENING_CLOSEOUT.md` line 115.

### Remediation

- For real LLM behavior: run without `offline_llm_stubs` or use a real model.
- For eval: consider a richer stub that returns non-empty `steps` with task-specific queries to better exercise retrieval.

---

## 4. KeyboardInterrupt

The run was stopped with Ctrl+C. The traceback is from `execution_loop`’s `ThreadPoolExecutor` and `future.result(timeout=...)`; this is normal for user interruption.

---

## Recommendations (Priority)

1. **Pre-import numpy before mocks** in agent_eval entry points to avoid RecursionError in rank_bm25 and reranker.
2. **Upgrade numpy** to a version with LazyLoader/recursion fixes if available.
3. **Optional:** Improve the query rewriter stub to return non-empty `steps` for more realistic retrieval during offline eval.
4. **Document** in `DEPS_MISSING_RCA.md` that RecursionError can also occur in the reranker inference path, not only at import.

---

## References

- `Docs/DEPS_MISSING_RCA.md` — rank_bm25 RecursionError
- `tests/agent_eval/real_execution.py` — `_stub_reasoning_json`, `offline_llm_stubs`
- `agent/retrieval/retrieval_pipeline.py` — BM25 probe (465–478), reranker exception handling (657–666)
- `agent/retrieval/bm25_retriever.py` — RecursionError handling (119–125)
- numpy/numpy#26093 — RecursionError with LazyLoader
- cpython#112282 — recursion limit changes in 3.12
